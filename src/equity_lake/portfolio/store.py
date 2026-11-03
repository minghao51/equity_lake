"""Private append-only SQLite ledger store (ADR-0024 decisions 3-5).

The ledger is a private auxiliary artifact at ``data/portfolio/portfolio.sqlite3``
(``DATA_DIR / "portfolio"``): never under ``data/lake/``, never cataloged, never
pointblank-validated. The Pydantic event model in
:mod:`equity_lake.portfolio.models` is the schema contract at this write boundary.

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

import os
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

PORTFOLIO_SCHEMA_VERSION: Final[int] = 1
PORTFOLIO_DB_FILENAME: Final[str] = "portfolio.sqlite3"
_DIRECTORY_MODE: Final[int] = 0o700
_FILE_MODE: Final[int] = 0o600
_CONNECT_TIMEOUT_SECONDS: Final[float] = 10.0

_SCHEMA_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS ledger_events (
    row_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL CHECK (length(account) > 0),
    external_id TEXT NOT NULL CHECK (length(external_id) > 0),
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    event_type TEXT NOT NULL CHECK (
        event_type IN ('OPENING', 'BUY', 'SELL', 'DEPOSIT', 'WITHDRAWAL', 'FEE', 'CASH_DIVIDEND')
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
                raise LedgerStoreError(f"unsupported private portfolio ledger version {version}")
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
            raise LedgerStoreError(f"unsupported private portfolio ledger version {version}")
        connection.executescript(_SCHEMA_SQL)
        actual = tuple(row[1] for row in connection.execute("PRAGMA table_info(ledger_events)").fetchall())
        if actual != _LEDGER_COLUMN_ORDER:
            # Column names are not echoed: only the contract and the count matter.
            raise LedgerStoreError(
                f"private portfolio ledger column order ({len(actual)} column(s)) does not match the writer contract; refusing to append"
            )
        # PRAGMA arguments cannot be parameterised, so the version is a literal
        # that must match PORTFOLIO_SCHEMA_VERSION.
        connection.execute("PRAGMA user_version=1")


__all__ = [
    "AppendOutcome",
    "LedgerConflictError",
    "LedgerStore",
    "LedgerStoreError",
    "PORTFOLIO_DB_FILENAME",
    "PORTFOLIO_SCHEMA_VERSION",
    "portfolio_db_path",
]
