"""Private append-only SQLite ledger store (ADR-0024 decisions 3-5, ADR-0031).

The ledger is a private auxiliary artifact at ``data/portfolio/portfolio.sqlite3``
(``DATA_DIR / "portfolio"``): never under ``data/lake/``, never cataloged, never
pointblank-validated. The Pydantic event model in
:mod:`equity_lake.portfolio.models` is the schema contract at this write boundary.

Schema **v2** (ADR-0031) accepts the ``SPLIT`` kind, which the v1 ``CHECK`` on
``event_type`` rejected. A constraint is schema, so the upgrade is a table rebuild and
it is **never implicit**: a v1 file is refused on read and write with a message naming
the upgrade, and :func:`migrate_ledger` performs it after writing a verified exact-file
backup (:func:`verify_ledger_file`). Nothing here runs automatically.

Connection mechanics follow the private ``signals/tracking.py`` precedent
(explicit timeout, ``isolation_level=None``, row factory, busy timeout, foreign
keys, ``0700`` directory) with one deliberate difference: the ledger keeps
SQLite's rollback journal instead of WAL. A read-only connection to a WAL
database creates ``-shm``/``-wal`` files, which would break the ADR's
"read/dry-run writes nothing" rule; ADR-0024 decision 3 records connection
mechanics as a non-normative implementation detail.

Money, quantity and price are stored as exact decimal text, never as SQLite
REAL. There is no update path: :meth:`LedgerStore.append` only ever inserts, so
repeated identical content is a no-op and changed content is a visible failure,
both inside a single transaction.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import stat
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Final
from urllib.parse import quote

import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from equity_lake.core.instruments import InstrumentKey
from equity_lake.core.paths import PORTFOLIO_DB_PATH
from equity_lake.portfolio.models import (
    BasisStatus,
    LedgerEvent,
    LedgerEventType,
    PortfolioLedgerError,
    redacted_validation_message,
)

logger = structlog.get_logger(__name__)

PORTFOLIO_SCHEMA_VERSION: Final[int] = 2
#: The only version an upgrade accepts, and the only version this build can rebuild from.
_PORTFOLIO_LEGACY_SCHEMA_VERSION: Final[int] = 1
PORTFOLIO_DB_FILENAME: Final[str] = "portfolio.sqlite3"
_DIRECTORY_MODE: Final[int] = 0o700
_FILE_MODE: Final[int] = 0o600
_CONNECT_TIMEOUT_SECONDS: Final[float] = 10.0
_REBUILD_TABLE: Final[str] = "ledger_events_v2"

#: The live table, schema v2 (ADR-0031). v1 listed every kind but ``SPLIT``; a ``CHECK`` is part
#: of the schema, so adding a kind is a schema change even when no column changes. The upgrade
#: rebuilds this same body under a temporary name (see :func:`_rebuild_to_v2`), so both
#: statements must be edited together — the tests compare the two through ``sqlite_master``.
_SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS ledger_events (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL CHECK (length(account) > 0),
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('OPENING', 'BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL', 'FEE', 'CASH_DIVIDEND', 'SPLIT')
    ),
    external_source TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    market TEXT CHECK (market IS NULL OR market = 'us_equity'),
    mic TEXT,
    symbol TEXT,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    cash_effect TEXT NOT NULL,
    quantity TEXT,
    price TEXT,
    fees TEXT NOT NULL,
    basis TEXT,
    basis_status TEXT NOT NULL CHECK (basis_status IN ('KNOWN', 'UNKNOWN')),
    reverses_external_id TEXT,
    source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    UNIQUE (account, external_id),
    UNIQUE (account, sequence)
);
CREATE INDEX IF NOT EXISTS ledger_events_sequence ON ledger_events (account, sequence);
"""

# Physical column order of ledger_events. Appends are positional, so the order
# is checked against PRAGMA table_info on every writable connection.
_LEDGER_COLUMN_ORDER: Final[tuple[str, ...]] = (
    "row_id",
    "account",
    "external_id",
    "sequence",
    "event_type",
    "external_source",
    "parser_version",
    "effective_at",
    "received_at",
    "recorded_at",
    "market",
    "mic",
    "symbol",
    "currency",
    "cash_effect",
    "quantity",
    "price",
    "fees",
    "basis",
    "basis_status",
    "reverses_external_id",
    "source_digest",
    "content_digest",
)


class LedgerStoreError(PortfolioLedgerError):
    """The private ledger cannot be opened, read or trusted."""


class LedgerConflictError(LedgerStoreError):
    """A stored event contradicts the batch; the whole transaction was rolled back."""


def _version_refusal(version: int) -> str:
    """A version mismatch names the supported versions and the upgrade, never a bare number."""
    if version == _PORTFOLIO_LEGACY_SCHEMA_VERSION:
        return (
            f"private portfolio ledger schema version {version} must be upgraded to {PORTFOLIO_SCHEMA_VERSION} "
            "by the explicit migration (`equity portfolio migrate`); nothing is upgraded implicitly and no "
            "write is performed on an older schema"
        )
    return (
        f"unsupported private portfolio ledger schema version {version}; this build reads and writes version "
        f"{PORTFOLIO_SCHEMA_VERSION} and upgrades {_PORTFOLIO_LEGACY_SCHEMA_VERSION} only via `equity portfolio migrate`"
    )


class AppendOutcome(BaseModel):
    """Counts from one append; ``unchanged`` events were already stored identically."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    inserted: int = Field(ge=0)
    unchanged: int = Field(ge=0)


def portfolio_db_path(data_dir: Path | None = None) -> Path:
    """Private ledger path; ``data_dir`` is the test seam used by ``signals`` too."""
    return PORTFOLIO_DB_PATH if data_dir is None else Path(data_dir) / "portfolio" / PORTFOLIO_DB_FILENAME


def _restrict_permissions(path: Path) -> None:
    """``0600`` for the database and any journal/WAL companion SQLite creates."""
    for candidate in (path, path.with_name(f"{path.name}-journal"), path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
        try:
            os.chmod(candidate, _FILE_MODE)
        except FileNotFoundError:
            continue


def _optional_decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _unusable_database(path: Path, cause: sqlite3.DatabaseError) -> LedgerStoreError:
    """A file that is not (or no longer) a SQLite database is refused, never rewritten."""
    return LedgerStoreError(
        f"private portfolio ledger {path.name!r} is not a usable SQLite database ({type(cause).__name__}); refusing to read or overwrite it"
    )


def _unreadable_stored_event(cause: Exception) -> LedgerStoreError:
    """A stored row that violates the event contract is reported without its values."""
    if isinstance(cause, ValidationError):
        detail = redacted_validation_message(cause)
        return LedgerStoreError(f"private portfolio ledger contains an event that violates the event contract: {detail}; refusing to read it")
    return LedgerStoreError(f"private portfolio ledger contains an unreadable event ({type(cause).__name__}); refusing to read it")


def _event_from_row(row: sqlite3.Row) -> LedgerEvent:
    instrument: InstrumentKey | None = None
    if row["mic"] is not None:
        instrument = InstrumentKey(market=row["market"], mic=row["mic"], symbol=row["symbol"])
    return LedgerEvent(
        parser_version=row["parser_version"],
        source_digest=row["source_digest"],
        account=row["account"],
        external_source=row["external_source"],
        external_id=row["external_id"],
        event_type=LedgerEventType(row["event_type"]),
        effective_at=datetime.fromisoformat(row["effective_at"]),
        received_at=datetime.fromisoformat(row["received_at"]),
        sequence=row["sequence"],
        instrument=instrument,
        currency=row["currency"],
        cash_effect=Decimal(row["cash_effect"]),
        quantity=_optional_decimal(row["quantity"]),
        price=_optional_decimal(row["price"]),
        fees=Decimal(row["fees"]),
        basis=_optional_decimal(row["basis"]),
        basis_status=BasisStatus(row["basis_status"]),
        reverses_external_id=row["reverses_external_id"],
    )


def _row_values(event: LedgerEvent, recorded_at: str) -> tuple[object, ...]:
    """Values in ``_LEDGER_COLUMN_ORDER``; ``row_id`` is assigned by SQLite."""
    instrument = event.instrument
    return (
        None,
        event.account,
        event.external_id,
        event.sequence,
        event.event_type.value,
        event.external_source,
        event.parser_version,
        event.effective_at.isoformat(),
        event.received_at.isoformat(),
        recorded_at,
        None if instrument is None else instrument.market,
        None if instrument is None else instrument.mic,
        None if instrument is None else instrument.symbol,
        event.currency,
        format(event.cash_effect, "f"),
        None if event.quantity is None else format(event.quantity, "f"),
        None if event.price is None else format(event.price, "f"),
        format(event.fees, "f"),
        None if event.basis is None else format(event.basis, "f"),
        event.basis_status.value,
        event.reverses_external_id,
        event.source_digest,
        event.content_digest,
    )


class LedgerStore:
    """One private ledger database; reads are read-only, appends are atomic."""

    def __init__(self, *, path: Path | None = None, data_dir: Path | None = None) -> None:
        if path is not None and data_dir is not None:
            raise ValueError("pass either path or data_dir, not both")
        self.path: Path = Path(path) if path is not None else portfolio_db_path(data_dir)

    def exists(self) -> bool:
        """A stat only: asking whether the ledger exists never creates it."""
        return self.path.exists()

    def load_events(self) -> tuple[LedgerEvent, ...]:
        """Read every stored event in deterministic logical order (read-only)."""
        connection = self._connect(writable=False)
        try:
            rows = connection.execute(
                "SELECT account, external_id, sequence, event_type, external_source, parser_version, "
                "effective_at, received_at, market, mic, symbol, currency, cash_effect, quantity, "
                "price, fees, basis, basis_status, reverses_external_id, source_digest, content_digest "
                "FROM ledger_events ORDER BY account, sequence, external_id"
            ).fetchall()
        except sqlite3.DatabaseError as exc:
            if isinstance(exc, sqlite3.OperationalError):
                raise
            raise _unusable_database(self.path, exc) from exc
        finally:
            connection.close()
        try:
            return tuple(_event_from_row(row) for row in rows)
        except (ValidationError, ValueError, ArithmeticError) as exc:
            raise _unreadable_stored_event(exc) from exc

    def append(self, events: Sequence[LedgerEvent]) -> AppendOutcome:
        """Append a batch in one transaction; identical stored content is a no-op.

        If any row conflicts with stored content or reuses a stored source
        sequence, nothing is written and no earlier row is touched: the
        transaction is rolled back and the conflict is raised.
        """
        batch = tuple(events)
        if not batch:
            return AppendOutcome(inserted=0, unchanged=0)

        connection = self._connect(writable=True)
        recorded_at = datetime.now(UTC).isoformat()
        inserted = 0
        unchanged = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            for event in batch:
                stored = connection.execute(
                    "SELECT content_digest FROM ledger_events WHERE account = ? AND external_id = ?",
                    (event.account, event.external_id),
                ).fetchone()
                if stored is not None:
                    if stored["content_digest"] != event.content_digest:
                        raise LedgerConflictError(
                            f"{event.event_type.value} external_id {event.external_id!r} is already stored with different content; "
                            "corrections are explicit reversal/repair events (ADR-0024 decision 5)"
                        )
                    unchanged += 1
                    continue
                occupied = connection.execute(
                    "SELECT external_id FROM ledger_events WHERE account = ? AND sequence = ?",
                    (event.account, event.sequence),
                ).fetchone()
                if occupied is not None:
                    raise LedgerConflictError(
                        f"{event.event_type.value} external_id {event.external_id!r} reuses stored source sequence "
                        f"{event.sequence} of {occupied['external_id']!r}; ambiguous ordering is rejected"
                    )
                values = _row_values(event, recorded_at)
                connection.execute(
                    "INSERT INTO ledger_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*values,),
                )
                inserted += 1
            connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            # A constraint the event contract allows means the stored schema and this build
            # disagree (a v1 CHECK without SPLIT). Name the mismatch and the upgrade instead of
            # reporting a read failure: SQLite's message names columns and constraints, never
            # bound values.
            raise LedgerStoreError(
                "the private ledger schema refused an event that the event contract allows "
                f"({exc}); the stored schema and this build disagree - run `equity portfolio migrate` "
                "if the ledger is older than this build"
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            _restrict_permissions(self.path)
            connection.close()

        logger.info("portfolio_ledger_append", inserted=inserted, unchanged=unchanged)
        return AppendOutcome(inserted=inserted, unchanged=unchanged)

    def _connect(self, *, writable: bool) -> sqlite3.Connection:
        if writable:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
            # Remove group/other permission bits like the tracking precedent;
            # never add owner permissions to a directory someone else owns.
            os.chmod(self.path.parent, stat.S_IMODE(self.path.parent.stat().st_mode) & _DIRECTORY_MODE)
            connection = sqlite3.connect(self.path, timeout=_CONNECT_TIMEOUT_SECONDS, isolation_level=None)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA busy_timeout=10000")
                connection.execute("PRAGMA foreign_keys=ON")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                self._ensure_schema(connection)
            except BaseException as exc:
                connection.close()
                # An unusable file must fail loudly before any permission or
                # content change; a busy/locked database stays an OperationalError.
                if isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, sqlite3.OperationalError):
                    raise _unusable_database(self.path, exc) from exc
                raise
            _restrict_permissions(self.path)
            return connection

        if not self.path.exists():
            raise LedgerStoreError(f"private portfolio ledger {self.path.name!r} does not exist; read and dry-run paths never create it")
        # Read-only URI: even without the pre-check above, SQLite would refuse to
        # create or modify the file instead of opening a fresh ledger.
        uri = f"file:{quote(self.path.as_posix())}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=_CONNECT_TIMEOUT_SECONDS, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=10000")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != PORTFOLIO_SCHEMA_VERSION:
                raise LedgerStoreError(_version_refusal(version))
        except BaseException as exc:
            connection.close()
            if isinstance(exc, sqlite3.DatabaseError) and not isinstance(exc, sqlite3.OperationalError):
                raise _unusable_database(self.path, exc) from exc
            raise
        return connection

    @staticmethod
    def _ensure_schema(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, PORTFOLIO_SCHEMA_VERSION}:
            raise LedgerStoreError(_version_refusal(version))
        connection.executescript(_SCHEMA_SQL)
        actual = tuple(row[1] for row in connection.execute("PRAGMA table_info(ledger_events)").fetchall())
        if actual != _LEDGER_COLUMN_ORDER:
            # Column names are not echoed: only the contract and the count matter.
            raise LedgerStoreError(
                f"private portfolio ledger column order ({len(actual)} column(s)) does not match the writer contract; refusing to append"
            )
        # PRAGMA arguments cannot be parameterised, so this is a literal that must match
        # PORTFOLIO_SCHEMA_VERSION; `test_a_fresh_ledger_is_created_at_the_declared_version`
        # fails if the two drift apart.
        connection.execute("PRAGMA user_version=2")


class LedgerFileVerification(BaseModel):
    """A verified read of one ledger file; counters and digests only, never stored values."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    schema_version: int = Field(ge=0)
    row_count: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class LedgerMigrationReport(BaseModel):
    """What one ``migrate_ledger`` invocation found and did (ADR-0031 decision 2)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    found_version: int = Field(ge=0)
    target_version: int = Field(ge=0)
    applied: bool
    already_current: bool
    row_count: int = Field(ge=0)
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    backup_filename: str | None = None
    backup_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class LedgerRestoreReport(BaseModel):
    """What one ``restore_ledger`` invocation found and did (ADR-0031 decision 4).

    ``rolled_back_rows`` and ``rolled_back_newest_recorded_at`` describe what the current file
    held and the candidate does not, so the operator can see what was given up. Nothing in this
    report carries a stored value — counters, names and digests only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    candidate_filename: str
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_version: int = Field(ge=0)
    target_existed: bool
    applied: bool
    already_identical: bool
    row_count: int = Field(ge=0)
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    rolled_back_rows: int = Field(ge=0)
    rolled_back_newest_recorded_at: str | None = None
    backup_filename: str | None = None
    backup_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class LedgerCompactionReport(BaseModel):
    """What one ``compact_ledger`` invocation found and did (ADR-0031 decision 5)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    applied: bool
    schema_version: int = Field(ge=0)
    row_count: int = Field(ge=0)
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    before_bytes: int = Field(ge=0)
    after_bytes: int = Field(ge=0)
    backup_filename: str | None = None
    backup_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class LedgerBackupReport(BaseModel):
    """What one ``export_verified_backup`` invocation found and did (ADR-0031 decision 3).

    Nothing here carries a stored value: names, counters and digests only. ``directory`` is the
    operator-chosen destination, echoed back so the report names the private location used.
    ``backup_filename`` stays ``None`` in report-only mode, so a plan never claims a file exists.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    directory: str
    directory_exists: bool
    applied: bool
    schema_version: int = Field(ge=0)
    row_count: int = Field(ge=0)
    record_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)
    backup_filename: str | None = None
    backup_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class LedgerBackupCandidate(BaseModel):
    """One ``.bak`` candidate in a backup directory, read-only and never followed.

    ``verified`` is the result of the same five checks every backup must pass (ADR-0031
    decision 3); a refused candidate still reports its size and modification time so a damaged
    recovery point is visible instead of silent, and ``detail`` is this module's own value-free
    refusal text.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    size_bytes: int = Field(ge=0)
    modified_at: str
    verified: bool
    schema_version: int | None = Field(default=None, ge=0)
    row_count: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    detail: str | None = None


class LedgerBackupSkip(BaseModel):
    """One directory entry an inventory did not read: a symlink or a non-backup file."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    filename: str
    reason: str


class LedgerBackupInventory(BaseModel):
    """A read-only listing of one backup directory (ADR-0031 decision 3). Nothing was changed."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    directory: str
    exists: bool
    candidates: tuple[LedgerBackupCandidate, ...] = ()
    skipped: tuple[LedgerBackupSkip, ...] = ()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _refuse_if_journal(path: Path) -> None:
    """A journal beside the file means a previous write did not finish; never copy that state."""
    if path.with_name(f"{path.name}-journal").exists():
        raise LedgerStoreError(
            "the private portfolio ledger has a rollback journal beside it, so a write did not finish; "
            "refusing to verify, back up or migrate a half-written file"
        )


def _read_only_connection(path: Path) -> sqlite3.Connection:
    """``mode=ro`` URI: verification never creates the file, a journal or a sidecar."""
    connection = sqlite3.connect(f"file:{quote(path.as_posix())}?mode=ro", uri=True, timeout=_CONNECT_TIMEOUT_SECONDS, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    return connection


def _record_fingerprint(connection: sqlite3.Connection) -> tuple[int, str]:
    """``(row_count, digest)`` over the stored identities and content digests.

    The digest lets a copy be compared with its source, and a rebuild with its input, without
    naming a single stored value.
    """
    rows = connection.execute(
        "SELECT account, external_id, content_digest FROM ledger_events ORDER BY account, external_id, content_digest"
    ).fetchall()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row[0]}\x00{row[1]}\x00{row[2]}\n".encode())
    return len(rows), digest.hexdigest()


def verify_ledger_file(path: Path, *, expect_version: int | None = None) -> LedgerFileVerification:
    """Verify one ledger file read-only (ADR-0031 decision 3): integrity, version, contents.

    A file is verified when it opens, its ``user_version`` is one this build understands, SQLite
    ``integrity_check`` returns ``ok``, and the row count and record digest are readable. The
    file SHA-256 is captured so a copy can be compared byte for byte with its source. Nothing is
    written and no journal or sidecar is created.
    """
    path = Path(path)
    if not path.exists():
        raise LedgerStoreError(f"private portfolio ledger {path.name!r} does not exist; verification never creates it")
    if not path.is_file():
        raise LedgerStoreError(f"private portfolio ledger {path.name!r} is not a regular file")
    _refuse_if_journal(path)
    sha256 = _sha256_file(path)
    connection = _read_only_connection(path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {PORTFOLIO_SCHEMA_VERSION, _PORTFOLIO_LEGACY_SCHEMA_VERSION}:
            raise LedgerStoreError(_version_refusal(version))
        if expect_version is not None and version != expect_version:
            raise LedgerStoreError(f"private portfolio ledger {path.name!r} is schema version {version}, expected {expect_version}")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise LedgerStoreError(f"private portfolio ledger {path.name!r} fails SQLite integrity_check; the file is kept, never repaired")
        row_count, record_digest = _record_fingerprint(connection)
    except sqlite3.DatabaseError as exc:
        raise _unusable_database(path, exc) from exc
    finally:
        connection.close()
    return LedgerFileVerification(filename=path.name, schema_version=version, row_count=row_count, sha256=sha256, record_digest=record_digest)


def _require_v1_columns(path: Path) -> None:
    """The upgrade copies the known column order, so the source must match it exactly."""
    connection = _read_only_connection(path)
    try:
        actual = tuple(row[1] for row in connection.execute("PRAGMA table_info(ledger_events)").fetchall())
    except sqlite3.DatabaseError as exc:
        raise _unusable_database(path, exc) from exc
    finally:
        connection.close()
    if actual != _LEDGER_COLUMN_ORDER:
        raise LedgerStoreError(
            f"private portfolio ledger column order ({len(actual)} column(s)) does not match the upgrade contract; refusing to migrate"
        )


def _require_no_leftover_table(path: Path) -> None:
    """A temporary rebuild table means a previous upgrade died: restore, do not retry."""
    connection = _read_only_connection(path)
    try:
        leftover = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (_REBUILD_TABLE,)).fetchone()
    except sqlite3.DatabaseError as exc:
        raise _unusable_database(path, exc) from exc
    finally:
        connection.close()
    if leftover is not None:
        raise LedgerStoreError(f"a previous upgrade left the {_REBUILD_TABLE!r} table behind; restore the verified backup instead of retrying")


def _require_free_space(path: Path) -> None:
    """A rebuild needs room for a second copy of the table plus the backup."""
    free = shutil.disk_usage(path.parent).free
    needed = 2 * path.stat().st_size
    if free < needed:
        raise LedgerStoreError(f"the upgrade needs at least {needed} bytes free beside the ledger and {free} are available; nothing was written")


def _write_verified_backup(
    path: Path, *, source: LedgerFileVerification, directory: Path | None = None, stamp: str | None = None
) -> LedgerFileVerification:
    """Exact-file copy, verified before it may be called a backup (ADR-0031 decision 3).

    ``directory`` places the copy in an operator-chosen private directory (the manual export of
    ADR-0031 decision 3) instead of beside the source; the name, mode, verification and
    no-overwrite rules are identical either way. The copy is written under a temporary name in
    the destination, verified against its source, and only then renamed into place, so a name
    that is already taken is never overwritten and an unverified copy never wears a backup name.
    """
    destination = path.parent if directory is None else directory
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") if stamp is None else stamp
    name = f"{path.name}.{stamp}.v{source.schema_version}.bak"
    backup = destination / name
    temp = destination / f"{name}.tmp"
    if backup.exists():
        raise LedgerStoreError(f"a backup named {backup.name!r} already exists; a backup is never overwritten")
    try:
        shutil.copy2(path, temp)
        os.chmod(temp, _FILE_MODE)
        verified = verify_ledger_file(temp, expect_version=source.schema_version)
        if (verified.sha256, verified.row_count, verified.record_digest) != (source.sha256, source.row_count, source.record_digest):
            raise LedgerStoreError("the ledger backup does not match its source; the copy was discarded and the source is unchanged")
        os.replace(temp, backup)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return verified.model_copy(update={"filename": backup.name})


def _rebuild_to_v2(path: Path, *, expected: LedgerFileVerification) -> None:
    """One transaction: rebuild the table at v2, then prove the record is unchanged."""
    connection = sqlite3.connect(path, timeout=_CONNECT_TIMEOUT_SECONDS, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != _PORTFOLIO_LEGACY_SCHEMA_VERSION:
            raise LedgerStoreError(f"the upgrade expected schema version {_PORTFOLIO_LEGACY_SCHEMA_VERSION} but found {version}; nothing was changed")
        leftover = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (_REBUILD_TABLE,)).fetchone()
        if leftover is not None:
            raise LedgerStoreError(f"a previous upgrade left the {_REBUILD_TABLE!r} table behind; restore the verified backup instead of retrying")
        connection.execute(
            """
CREATE TABLE IF NOT EXISTS ledger_events_v2 (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL CHECK (length(account) > 0),
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('OPENING', 'BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL', 'FEE', 'CASH_DIVIDEND', 'SPLIT')
    ),
    external_source TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    market TEXT CHECK (market IS NULL OR market = 'us_equity'),
    mic TEXT,
    symbol TEXT,
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    cash_effect TEXT NOT NULL,
    quantity TEXT,
    price TEXT,
    fees TEXT NOT NULL,
    basis TEXT,
    basis_status TEXT NOT NULL CHECK (basis_status IN ('KNOWN', 'UNKNOWN')),
    reverses_external_id TEXT,
    source_digest TEXT NOT NULL CHECK (length(source_digest) = 64),
    content_digest TEXT NOT NULL CHECK (length(content_digest) = 64),
    UNIQUE (account, external_id),
    UNIQUE (account, sequence)
)
"""
        )
        created = tuple(row[1] for row in connection.execute("PRAGMA table_info(ledger_events_v2)").fetchall())
        if created != _LEDGER_COLUMN_ORDER:
            raise LedgerStoreError("the upgrade target's column order does not match the writer contract; nothing was changed")
        connection.execute(
            """
INSERT INTO ledger_events_v2 (
    row_id, account, external_id, sequence, event_type, external_source, parser_version,
    effective_at, received_at, recorded_at, market, mic, symbol, currency, cash_effect,
    quantity, price, fees, basis, basis_status, reverses_external_id, source_digest,
    content_digest
)
SELECT
    row_id, account, external_id, sequence, event_type, external_source, parser_version,
    effective_at, received_at, recorded_at, market, mic, symbol, currency, cash_effect,
    quantity, price, fees, basis, basis_status, reverses_external_id, source_digest,
    content_digest
FROM ledger_events
"""
        )
        connection.execute("DROP TABLE ledger_events")
        connection.execute("ALTER TABLE ledger_events_v2 RENAME TO ledger_events")
        connection.execute("CREATE INDEX IF NOT EXISTS ledger_events_sequence ON ledger_events (account, sequence)")
        connection.execute("PRAGMA user_version=2")
        count, digest = _record_fingerprint(connection)
        if (count, digest) != (expected.row_count, expected.record_digest):
            raise LedgerStoreError(
                "the rebuilt table disagrees with the pre-upgrade record (row count or record digest); the transaction was rolled back"
            )
        connection.execute("COMMIT")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise LedgerStoreError("the upgraded ledger fails SQLite integrity_check; the verified backup is the recovery path")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise LedgerStoreError("the upgraded ledger fails foreign_key_check; the verified backup is the recovery path")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        _restrict_permissions(path)
        connection.close()


def migrate_ledger(path: Path | None = None, *, apply: bool = False) -> LedgerMigrationReport:
    """Upgrade a v1 ledger to v2, writing a verified backup first (ADR-0031 decision 2).

    Report-only by default: the plan is computed and returned and **nothing is written** — no
    backup, no directory, no journal. ``apply=True`` writes the verified exact-file backup, then
    rebuilds the table in one transaction, proves the record is unchanged and re-verifies the
    result. A v2 ledger is a verifying no-op. There is no down-migration; the backup is the
    reversal. This never runs automatically and never touches the lake or the catalog.
    """
    target = Path(path) if path is not None else portfolio_db_path()
    source = verify_ledger_file(target)
    if source.schema_version == PORTFOLIO_SCHEMA_VERSION:
        return LedgerMigrationReport(
            filename=source.filename,
            found_version=source.schema_version,
            target_version=PORTFOLIO_SCHEMA_VERSION,
            applied=False,
            already_current=True,
            row_count=source.row_count,
            record_digest=source.record_digest,
        )
    if source.schema_version != _PORTFOLIO_LEGACY_SCHEMA_VERSION:  # pragma: no cover - verify_ledger_file restricts it
        raise LedgerStoreError(_version_refusal(source.schema_version))
    _require_v1_columns(target)
    _require_no_leftover_table(target)
    if not apply:
        return LedgerMigrationReport(
            filename=source.filename,
            found_version=source.schema_version,
            target_version=PORTFOLIO_SCHEMA_VERSION,
            applied=False,
            already_current=False,
            row_count=source.row_count,
            record_digest=source.record_digest,
        )
    _require_free_space(target)
    backup = _write_verified_backup(target, source=source)
    try:
        _rebuild_to_v2(target, expected=source)
    except LedgerStoreError as exc:
        raise LedgerStoreError(f"{exc} The verified backup is {backup.filename}.") from exc
    except Exception as exc:
        raise LedgerStoreError(
            f"the v1->v2 upgrade failed ({type(exc).__name__}); the ledger is unchanged and the verified backup is {backup.filename}"
        ) from exc
    upgraded = verify_ledger_file(target, expect_version=PORTFOLIO_SCHEMA_VERSION)
    if (upgraded.row_count, upgraded.record_digest) != (source.row_count, source.record_digest):
        raise LedgerStoreError(f"the upgraded ledger disagrees with the pre-upgrade record (row count or record digest); restore {backup.filename}")
    logger.info("portfolio_ledger_migrated", rows=upgraded.row_count, backup=backup.filename)
    return LedgerMigrationReport(
        filename=upgraded.filename,
        found_version=source.schema_version,
        target_version=PORTFOLIO_SCHEMA_VERSION,
        applied=True,
        already_current=False,
        row_count=upgraded.row_count,
        record_digest=upgraded.record_digest,
        backup_filename=backup.filename,
        backup_sha256=backup.sha256,
    )


def _account_labels(path: Path) -> tuple[str, ...]:
    """The account label(s) stored in one ledger file (ADR-0024 decision 1: one private book)."""
    connection = _read_only_connection(path)
    try:
        return tuple(row[0] for row in connection.execute("SELECT DISTINCT account FROM ledger_events ORDER BY account").fetchall())
    except sqlite3.DatabaseError as exc:
        raise _unusable_database(path, exc) from exc
    finally:
        connection.close()


def _newest_recorded_at(path: Path) -> str | None:
    """The newest stored booking instant read-only (``None`` for an empty ledger)."""
    connection = _read_only_connection(path)
    try:
        row = connection.execute("SELECT MAX(recorded_at) FROM ledger_events").fetchone()
    except sqlite3.DatabaseError as exc:
        raise _unusable_database(path, exc) from exc
    finally:
        connection.close()
    value = row[0] if row is not None else None
    return str(value) if value is not None else None


def _require_space_beside(path: Path, needed: int, *, what: str) -> None:
    """Refuse before any write when the directory cannot hold the copy plus its backup."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
    free = shutil.disk_usage(path.parent).free
    if free < needed:
        raise LedgerStoreError(f"{what} needs at least {needed} bytes free beside the ledger and {free} are available; nothing was written")


def _replace_atomically(source: Path, target: Path, *, expected: LedgerFileVerification) -> LedgerFileVerification:
    """Copy beside the ledger, prove the bytes, then ``os.replace`` (ADR-0031 decision 4).

    The temporary file never shares a name with a backup, is removed on any failure, and the
    replacement is re-verified so a corrupt copy can never become the ledger.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    temp = target.with_name(f"{target.name}.restore-{stamp}.tmp")
    try:
        shutil.copy2(source, temp)
        os.chmod(temp, _FILE_MODE)
        if _sha256_file(temp) != expected.sha256:
            raise LedgerStoreError("the candidate copy does not match its source byte for byte; the ledger was not replaced")
        os.replace(temp, target)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    os.chmod(target, _FILE_MODE)
    _restrict_permissions(target)
    restored = verify_ledger_file(target, expect_version=expected.schema_version)
    if (restored.sha256, restored.row_count, restored.record_digest) != (expected.sha256, expected.row_count, expected.record_digest):
        raise LedgerStoreError("the replaced ledger disagrees with the candidate (sha256, row count or record digest); restore the backup beside it")
    return restored


def restore_ledger(candidate: Path, path: Path | None = None, *, apply: bool = False) -> LedgerRestoreReport:
    """Replace the ledger with a verified backup (ADR-0031 decision 4).

    Report-only by default: the candidate is verified, the current file is verified, and the
    rollback delta is reported with **nothing written** — no backup, no temporary file, no
    directory, no journal. ``apply=True`` backs up the *current* file first (it may hold events
    the candidate lacks), replaces atomically through a temporary file in the same directory,
    and re-verifies the result.

    Refusals: the candidate is unverified or not the implemented schema version; it is the
    current file itself; its account label differs from the current ledger (one private book,
    ADR-0024 decision 1); a ``-journal`` sits beside either file (a writer did not finish); or
    there is not enough free space for the copy plus its backup.
    """
    target = Path(path) if path is not None else portfolio_db_path()
    candidate_path = Path(candidate)
    if target.exists() and candidate_path.resolve() == target.resolve():
        raise LedgerStoreError("the candidate is the current ledger; a restore needs a verified backup to restore from")
    verified_candidate = verify_ledger_file(candidate_path, expect_version=PORTFOLIO_SCHEMA_VERSION)
    current = verify_ledger_file(target) if target.exists() else None
    if current is not None and _account_labels(candidate_path) != _account_labels(target):
        raise LedgerStoreError(
            "the candidate's account label differs from the current ledger; one private book holds one account "
            "(ADR-0024 decision 1) and no documented account change exists, so nothing was restored"
        )

    rolled_back_rows = 0
    rolled_back_newest: str | None = None
    candidate_newest = _newest_recorded_at(candidate_path)
    if current is not None:
        current_newest = _newest_recorded_at(target)
        rolled_back_rows = max(current.row_count - verified_candidate.row_count, 0)
        rolled_back_newest = current_newest if current_newest != candidate_newest else None
    already_identical = current is not None and current.sha256 == verified_candidate.sha256

    report = LedgerRestoreReport(
        filename=target.name,
        candidate_filename=verified_candidate.filename,
        candidate_sha256=verified_candidate.sha256,
        candidate_version=verified_candidate.schema_version,
        target_existed=current is not None,
        applied=False,
        already_identical=already_identical,
        row_count=verified_candidate.row_count,
        record_digest=verified_candidate.record_digest,
        rolled_back_rows=rolled_back_rows,
        rolled_back_newest_recorded_at=rolled_back_newest,
    )
    if not apply or already_identical:
        return report

    needed = candidate_path.stat().st_size + (target.stat().st_size if current is not None else 0)
    _require_space_beside(target, needed, what="the restore")
    backup = _write_verified_backup(target, source=current) if current is not None else None
    restored = _replace_atomically(candidate_path, target, expected=verified_candidate)
    logger.info(
        "portfolio_ledger_restored",
        rows=restored.row_count,
        candidate=verified_candidate.filename,
        backup=backup.filename if backup is not None else None,
        rolled_back_rows=rolled_back_rows,
    )
    return report.model_copy(
        update={"applied": True, "backup_filename": backup.filename if backup else None, "backup_sha256": backup.sha256 if backup else None}
    )


def compact_ledger(path: Path | None = None, *, apply: bool = False) -> LedgerCompactionReport:
    """Rebuild the ledger file with no free pages, preserving the record (ADR-0031 decision 5).

    Uses ``VACUUM INTO`` a fresh file beside the ledger, verifies it (integrity, version, row
    count and record digest — the record, never the file), then applies the restore procedure of
    decision 4 so the current file is backed up before the replacement is put in place. Nothing
    is deleted: every event survives, ``row_id`` renumbering carries no meaning, and there is no
    retention step.

    Report-only by default and never automatic. Refusals: the ledger is missing, a v1 file (it
    must be migrated first), a ``-journal`` is present, the file is not the implemented version,
    or there is not room for the rebuild plus its backup.
    """
    target = Path(path) if path is not None else portfolio_db_path()
    before = verify_ledger_file(target, expect_version=PORTFOLIO_SCHEMA_VERSION)
    report = LedgerCompactionReport(
        filename=target.name,
        applied=False,
        schema_version=before.schema_version,
        row_count=before.row_count,
        record_digest=before.record_digest,
        before_bytes=target.stat().st_size,
        after_bytes=target.stat().st_size,
    )
    if not apply:
        return report

    _require_space_beside(target, 2 * target.stat().st_size, what="the compaction")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    rebuilt = target.with_name(f"{target.name}.compact-{stamp}.tmp")
    try:
        connection = sqlite3.connect(target, timeout=_CONNECT_TIMEOUT_SECONDS, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute("VACUUM INTO ?", (str(rebuilt),))
        finally:
            connection.close()
        compacted = verify_ledger_file(rebuilt, expect_version=PORTFOLIO_SCHEMA_VERSION)
        if (compacted.schema_version, compacted.row_count, compacted.record_digest) != (
            before.schema_version,
            before.row_count,
            before.record_digest,
        ):
            raise LedgerStoreError("the rebuilt ledger does not preserve the record (version, row count or record digest); nothing was replaced")
        backup = _write_verified_backup(target, source=before)
        restored = _replace_atomically(rebuilt, target, expected=compacted)
        rebuilt.unlink(missing_ok=True)
    except BaseException:
        rebuilt.unlink(missing_ok=True)
        raise
    logger.info("portfolio_ledger_compacted", rows=restored.row_count, before_bytes=report.before_bytes, after_bytes=target.stat().st_size)
    return report.model_copy(
        update={
            "applied": True,
            "after_bytes": target.stat().st_size,
            "backup_filename": backup.filename,
            "backup_sha256": backup.sha256,
        }
    )


def export_verified_backup(output_dir: Path, path: Path | None = None, *, apply: bool = False) -> LedgerBackupReport:
    """Export a verified exact-file copy into an operator-chosen private directory.

    This is the manual local backup of ADR-0031 decision 3 and nothing more: a copy on the same
    machine, taken in a no-writer window, accepted only after the five checks pass. There is no
    scheduled, remote, encrypted or cloud export — that needs its own ADR before it is built —
    and this function never prunes, deletes or overwrites a backup file.

    Report-only by default: the source is verified and the plan is returned with **nothing
    written** — no directory, no file, no temporary file, no journal. ``apply=True`` creates the
    destination directory with ``0700`` when it is missing, copies under a temporary name in
    that directory, verifies the copy against its source and only then renames it into place
    with ``0600``; an existing backup name is never overwritten.

    Refusals, all before the first write: the ledger is missing, is not a regular file, has a
    rollback journal beside it, is locked by a live writer, is not a usable SQLite database, or
    is of an unimplemented schema version (all from :func:`verify_ledger_file`); and the
    destination is a symlink or exists as something other than a directory.
    """
    target = Path(path) if path is not None else portfolio_db_path()
    destination = Path(output_dir)
    source = verify_ledger_file(target)
    if destination.is_symlink():
        raise LedgerStoreError(f"the backup destination {destination.name!r} is a symlink; refusing to write a private copy through it")
    exists = destination.exists()
    if exists and not destination.is_dir():
        raise LedgerStoreError(f"the backup destination {destination.name!r} is not a directory; refusing to write a private copy there")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    planned = destination / f"{target.name}.{stamp}.v{source.schema_version}.bak"
    if apply and planned.exists():
        raise LedgerStoreError(f"a backup named {planned.name!r} already exists in the destination; a backup is never overwritten")
    report = LedgerBackupReport(
        filename=target.name,
        directory=str(destination),
        directory_exists=exists,
        applied=False,
        schema_version=source.schema_version,
        row_count=source.row_count,
        record_digest=source.record_digest,
        sha256=source.sha256,
        size_bytes=target.stat().st_size,
    )
    if not apply:
        return report
    if not exists:
        destination.mkdir(parents=True, exist_ok=True, mode=_DIRECTORY_MODE)
        os.chmod(destination, _DIRECTORY_MODE)
    backup = _write_verified_backup(target, source=source, directory=destination, stamp=stamp)
    logger.info("portfolio_ledger_backup_exported", rows=backup.row_count, names_version=backup.schema_version)
    return report.model_copy(update={"applied": True, "directory_exists": True, "backup_filename": backup.filename, "backup_sha256": backup.sha256})


def inventory_backups(directory: Path) -> LedgerBackupInventory:
    """List the ``.bak`` candidates in one directory, read-only (ADR-0031 decision 3).

    Every candidate is opened read-only through :func:`verify_ledger_file` (any supported schema
    version: a pre-migration ``.v1.bak`` is a legitimate recovery point) and reported with its
    name, size, modification time, schema version, row count, digest and verification result.
    A candidate that fails verification is listed as refused, never repaired and never deleted.

    Nothing here creates, follows, modifies or removes anything: a symlink — to a ledger, to
    another directory or dangling — is reported as skipped and never opened, a non-regular file
    is skipped, and a file whose name is not a ledger backup name is skipped unread. A missing
    directory is reported as absent rather than created; a symlinked or non-directory path is
    refused.
    """
    root = Path(directory)
    if root.is_symlink():
        raise LedgerStoreError(f"the backup directory {root.name!r} is a symlink; refusing to read a private listing through it")
    if not root.exists():
        return LedgerBackupInventory(directory=str(root), exists=False)
    if not root.is_dir():
        raise LedgerStoreError(f"the backup directory {root.name!r} is not a directory; nothing was read")
    candidates: list[LedgerBackupCandidate] = []
    skipped: list[LedgerBackupSkip] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            skipped.append(LedgerBackupSkip(filename=entry.name, reason="symlink; never followed or read"))
            continue
        if not entry.is_file():
            skipped.append(LedgerBackupSkip(filename=entry.name, reason="not a regular file"))
            continue
        if not entry.name.endswith(".bak"):
            skipped.append(LedgerBackupSkip(filename=entry.name, reason="not a ledger backup name"))
            continue
        stats = entry.stat()
        modified_at = datetime.fromtimestamp(stats.st_mtime, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            verified = verify_ledger_file(entry)
        except LedgerStoreError as exc:
            candidates.append(
                LedgerBackupCandidate(filename=entry.name, size_bytes=stats.st_size, modified_at=modified_at, verified=False, detail=str(exc))
            )
            continue
        candidates.append(
            LedgerBackupCandidate(
                filename=entry.name,
                size_bytes=stats.st_size,
                modified_at=modified_at,
                verified=True,
                schema_version=verified.schema_version,
                row_count=verified.row_count,
                sha256=verified.sha256,
            )
        )
    return LedgerBackupInventory(directory=str(root), exists=True, candidates=tuple(candidates), skipped=tuple(skipped))


__all__ = [
    "AppendOutcome",
    "LedgerBackupCandidate",
    "LedgerBackupInventory",
    "LedgerBackupReport",
    "LedgerBackupSkip",
    "LedgerConflictError",
    "LedgerCompactionReport",
    "LedgerFileVerification",
    "LedgerMigrationReport",
    "LedgerRestoreReport",
    "LedgerStore",
    "LedgerStoreError",
    "PORTFOLIO_DB_FILENAME",
    "PORTFOLIO_SCHEMA_VERSION",
    "compact_ledger",
    "export_verified_backup",
    "inventory_backups",
    "migrate_ledger",
    "portfolio_db_path",
    "restore_ledger",
    "verify_ledger_file",
]
