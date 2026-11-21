"""Private ledger storage boundary and the v1 -> v2 upgrade (ADR-0031).

The split contract (ADR-0029 Part II) passed 20 tests and three review rounds while the
store's v1 ``CHECK`` still rejected ``SPLIT``: every split test exercised validation, replay,
marks and the preview, and none appended through ``LedgerStore``. These tests hold the seam
that was missing — a real append, reload and replay — plus the explicit migration that a
constraint change requires, and the refusals around it.

Every test names the rule it guards, so removing that rule makes the test fail.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

import equity_lake.portfolio.store as store_module  # type: ignore[import-untyped]
from equity_lake.portfolio import (
    LedgerStore,
    LedgerStoreError,
    parse_csv_text,
    replay_ledger,
)
from equity_lake.portfolio.store import (
    PORTFOLIO_SCHEMA_VERSION,
    migrate_ledger,
    verify_ledger_file,
)

HEADER = (
    "account,external_source,external_id,event_type,effective_at,received_at,sequence,market,mic,symbol,"
    "currency,cash_effect,quantity,price,fees,basis,basis_status,reverses_external_id\n"
)
AS_OF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)
TABLE = "ledger_events"

#: The v1 table as it shipped (ADR-0024 decision 3), pinned as a literal fixture: the migration
#: must keep working on files a v1 build wrote, so this schema must not drift with the code.
_V1_DDL = """
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


def _opening(seq: int = 1) -> str:
    return (
        f"main,fixture,evt-{seq:04d},OPENING,2024-01-0{seq}T15:00:00Z,2024-01-0{seq}T21:00:00Z,{seq},"
        f"us_equity,XNAS,AAA,USD,1000.00,10,100.00,0,1000.00,KNOWN,"
    )


def _split(seq: int = 2, ratio: str = "10", reverses: str = "") -> str:
    return (
        f"main,fixture,evt-{seq:04d},SPLIT,2024-01-0{seq}T15:00:00Z,2024-01-0{seq}T21:00:00Z,{seq},"
        f"us_equity,XNAS,AAA,USD,0,{ratio},,0,,KNOWN,{reverses}"
    )


def _rows(*rows: str) -> str:
    return HEADER + "".join(row if row.endswith("\n") else f"{row}\n" for row in rows)


def _write_v1_ledger(path: Path, *, rows: int = 2) -> Path:
    """A ledger written by the shipped v1 schema, exactly as a v1 build left it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(_V1_DDL)
        for index in range(1, rows + 1):
            connection.execute(
                "INSERT INTO ledger_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    None,
                    "main",
                    f"evt-{index:04d}",
                    index,
                    "OPENING",
                    "fixture",
                    "fixture-v1",
                    f"2024-01-0{index}T15:00:00+00:00",
                    f"2024-01-0{index}T21:00:00+00:00",
                    f"2024-01-0{index}T21:00:00+00:00",
                    "us_equity",
                    "XNAS",
                    "AAA",
                    "USD",
                    "1000.00",
                    "10",
                    "100.00",
                    "0",
                    "1000.00",
                    "KNOWN",
                    None,
                    "0" * 64,
                    f"{index:x}" * 64,
                ),
            )
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    finally:
        connection.close()
    return path


def _user_version(path: Path) -> int:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    finally:
        connection.close()


def _event_type_check(path: Path) -> str:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        ddl = connection.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (TABLE,)).fetchone()[0]
    finally:
        connection.close()
    match = re.search(r"event_type IN \(([^)]*)\)", ddl)
    assert match is not None
    return match.group(1)


# ---------------------------------------------------------------------------
# The storage boundary: the seam every earlier split test missed
# ---------------------------------------------------------------------------


def test_the_store_persists_a_split_and_replays_the_adjusted_quantity(tmp_path: Path) -> None:
    """A split survives append, reload and replay — not just in-memory validation.

    Falsified by: any schema whose ``CHECK`` omits ``SPLIT`` (the v1 constraint), which makes
    ``append`` raise and is exactly the gap three review rounds did not see.
    """
    store = LedgerStore(data_dir=tmp_path)
    outcome = store.append(parse_csv_text(_rows(_opening(), _split())))
    assert outcome.inserted == 2

    stored = store.load_events()
    assert [event.event_type.value for event in stored] == ["OPENING", "SPLIT"]
    projection = replay_ledger(stored, account="main", as_of=AS_OF)
    assert projection.positions[0].quantity == Decimal("100")  # 10 shares x ratio 10
    assert projection.positions[0].basis == Decimal("1000")

    again = store.append(parse_csv_text(_rows(_opening(), _split())))
    assert (again.inserted, again.unchanged) == (0, 2)  # idempotent re-import


def test_a_fresh_ledger_is_created_at_the_declared_version(tmp_path: Path) -> None:
    """The creation literal and ``PORTFOLIO_SCHEMA_VERSION`` cannot drift apart.

    Falsified by: writing the wrong ``user_version`` when a database is created, which would
    make a fresh ledger look like an old one (or the reverse) to every version gate.
    """
    store = LedgerStore(data_dir=tmp_path)
    store.append(parse_csv_text(_rows(_opening())))
    path = tmp_path / "portfolio" / "portfolio.sqlite3"

    assert PORTFOLIO_SCHEMA_VERSION == 2  # pinned: the DDL literal and the upgrade target
    assert _user_version(path) == PORTFOLIO_SCHEMA_VERSION


def test_a_v1_ledger_refuses_writes_and_names_the_upgrade(tmp_path: Path) -> None:
    """A v1 file is never upgraded implicitly, and the refusal names the migration.

    Falsified by: accepting ``user_version`` 1 in ``_ensure_schema``, which would append rows
    under a schema whose ``CHECK`` rejects the kind the batch contains.
    """
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    before = path.read_bytes()
    store = LedgerStore(path=path)

    with pytest.raises(LedgerStoreError, match="upgraded to 2"):
        store.append(parse_csv_text(_rows(_opening(), _split())))
    with pytest.raises(LedgerStoreError, match="portfolio migrate"):
        store.load_events()  # reads refuse too: a half-understood schema is never read
    assert path.read_bytes() == before  # nothing was written


def test_a_schema_the_contract_outgrew_is_reported_as_a_mismatch(tmp_path: Path) -> None:
    """A constraint rejection names the schema/contract disagreement, never a read failure.

    Falsified by: letting ``sqlite3.IntegrityError`` escape to the CLI's generic handler, which
    reported "could not be read … retry once it is free" for a file that is readable and whose
    retry can never succeed.
    """
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3", rows=1)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA user_version=2")  # pretend the gate opened: only the CHECK differs
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(LedgerStoreError) as caught:
        LedgerStore(path=path).append(parse_csv_text(_rows(_split(seq=2))))
    message = str(caught.value)
    assert "schema refused an event that the event contract allows" in message
    assert "portfolio migrate" in message
    assert "could not be read" not in message


# ---------------------------------------------------------------------------
# Verification and the explicit upgrade
# ---------------------------------------------------------------------------


def test_migrate_reports_without_writing_by_default(tmp_path: Path) -> None:
    """The default run is a report: no backup, no journal, not one byte changed.

    Falsified by: copying the file or upgrading before ``apply`` is requested, which would make
    the plan itself a write.
    """
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    before = path.read_bytes()

    report = migrate_ledger(path)

    assert (report.found_version, report.target_version) == (1, PORTFOLIO_SCHEMA_VERSION)
    assert report.applied is False and report.already_current is False
    assert report.row_count == 2
    assert path.read_bytes() == before
    assert _user_version(path) == 1
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no backup, no journal


def test_migrate_writes_a_verified_backup_then_upgrades_the_record(tmp_path: Path) -> None:
    """``apply`` backs up and verifies first, rebuilds, then proves the record is unchanged.

    Falsified by: skipping the backup, skipping its verification, or letting the rebuilt table
    differ in row count or record digest from the source.
    """
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    source = verify_ledger_file(path)
    assert source.schema_version == 1

    report = migrate_ledger(path, apply=True)

    assert report.applied is True and report.already_current is False
    assert report.backup_filename is not None and report.backup_filename.endswith(".v1.bak")
    assert report.row_count == source.row_count
    assert report.record_digest == source.record_digest  # the record is unchanged by the upgrade

    backup = verify_ledger_file(path.with_name(report.backup_filename), expect_version=1)
    assert backup.sha256 == source.sha256  # the backup is the pre-upgrade file, byte for byte
    assert backup.record_digest == source.record_digest

    upgraded = verify_ledger_file(path, expect_version=PORTFOLIO_SCHEMA_VERSION)
    assert (upgraded.row_count, upgraded.record_digest) == (source.row_count, source.record_digest)
    assert _event_type_check(path) == _event_type_check_fresh(tmp_path)

    # The point of the whole stage: a split can now be persisted on the upgraded ledger.
    store = LedgerStore(path=path)
    assert store.append(parse_csv_text(_rows(_split(seq=3)))).inserted == 1
    assert len(store.load_events()) == 3


def _event_type_check_fresh(tmp_path: Path) -> str:
    """The same ``CHECK`` fragment from a ledger this build creates fresh."""
    fresh = LedgerStore(data_dir=tmp_path / "fresh")
    fresh.append(parse_csv_text(_rows(_opening())))
    return _event_type_check(tmp_path / "fresh" / "portfolio" / "portfolio.sqlite3")


def test_the_upgrade_preserves_every_stored_value(tmp_path: Path) -> None:
    """Every column of every row survives the rebuild byte for byte in value terms.

    Falsified by: a copy statement whose column order or coverage drifts from the table (a swapped
    or dropped column), which the identity digest alone cannot see.
    """
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3", rows=3)
    before = _all_rows(path)

    migrate_ledger(path, apply=True)

    assert _all_rows(path) == before


def _all_rows(path: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return [tuple(row) for row in connection.execute("SELECT * FROM ledger_events ORDER BY row_id").fetchall()]
    finally:
        connection.close()


def test_the_upgrade_refuses_without_room_for_a_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rebuild needs space beside the ledger; without it the plan stops before any write."""
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    before = path.read_bytes()
    monkeypatch.setattr(store_module.shutil, "disk_usage", lambda _path: SimpleNamespace(total=0, used=0, free=1))

    with pytest.raises(LedgerStoreError, match="free beside the ledger"):
        migrate_ledger(path, apply=True)
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".bak")] == []


def test_a_backup_that_does_not_match_its_source_is_discarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A copy is not a backup until it verifies; a damaged copy is deleted and nothing upgrades.

    Falsified by: trusting the copy without comparing it with its source, which would leave a
    corrupt file named like the recovery path.
    """
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    real_copy = store_module.shutil.copy2

    def corrupting_copy(source: Any, target: Any, **kwargs: Any) -> Any:
        result = real_copy(source, target, **kwargs)
        Path(str(target)).write_bytes(Path(str(target)).read_bytes()[:120])
        return result

    monkeypatch.setattr(store_module.shutil, "copy2", corrupting_copy)

    with pytest.raises(LedgerStoreError, match="not a usable SQLite database|does not match its source"):
        migrate_ledger(path, apply=True)
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".bak")] == []  # discarded
    assert _user_version(path) == 1  # nothing was upgraded


def test_migrate_is_a_verifying_no_op_on_a_current_ledger(tmp_path: Path) -> None:
    """A v2 ledger is reported current: no backup, no rebuild, no write."""
    store = LedgerStore(data_dir=tmp_path)
    store.append(parse_csv_text(_rows(_opening())))
    path = tmp_path / "portfolio" / "portfolio.sqlite3"

    report = migrate_ledger(path, apply=True)

    assert report.already_current is True and report.applied is False
    assert report.backup_filename is None
    assert _user_version(path) == PORTFOLIO_SCHEMA_VERSION


@pytest.mark.parametrize(
    ("prepare", "match"),
    [
        (lambda path: _set_version(path, 7), "unsupported private portfolio ledger schema version 7"),
        (lambda path: (path.parent / f"{path.name}-journal").write_bytes(b"x"), "rollback journal"),
        (lambda path: _drop_a_column(path), "column order"),
        (lambda path: _add_leftover_table(path), "ledger_events_v2"),
    ],
)
def test_migrate_refuses_what_it_cannot_verify(tmp_path: Path, prepare: object, match: str) -> None:
    """Every refusal happens before a write: version, journal, column drift and leftovers.

    Falsified by: dropping any one precondition, which would rebuild a file it cannot verify.
    """
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    prepare(path)  # type: ignore[operator]
    before = path.read_bytes()

    with pytest.raises(LedgerStoreError, match=match):
        migrate_ledger(path, apply=True)
    # Every precondition is checked before the first write: not one byte moved, no backup made.
    assert path.read_bytes() == before
    assert [p.name for p in path.parent.iterdir() if p.name.endswith(".bak")] == []


def _set_version(path: Path, version: int) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(f"PRAGMA user_version={version}")  # noqa: S608 - test fixture, literal int
        connection.commit()
    finally:
        connection.close()


def _drop_a_column(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("ALTER TABLE ledger_events DROP COLUMN reverses_external_id")
        connection.commit()
    finally:
        connection.close()


def _add_leftover_table(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("CREATE TABLE ledger_events_v2 (anything TEXT)")
        connection.commit()
    finally:
        connection.close()


def test_verification_refuses_a_damaged_copy(tmp_path: Path) -> None:
    """A truncated copy is refused, so a bad backup can never look like a good one."""
    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    damaged = tmp_path / "portfolio" / "portfolio.sqlite3.damaged.bak"
    damaged.write_bytes(path.read_bytes()[:120])

    with pytest.raises(LedgerStoreError):
        verify_ledger_file(damaged)


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_the_migrate_command_reports_then_upgrades(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``portfolio migrate`` is report-only by default and upgrades only with ``--apply``."""
    from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]

    path = _write_v1_ledger(tmp_path / "portfolio" / "portfolio.sqlite3")
    monkeypatch.setattr(store_module, "PORTFOLIO_DB_PATH", path)
    runner = CliRunner()

    report = runner.invoke(app, ["portfolio", "migrate"])
    assert report.exit_code == 0
    assert "Report only" in report.stdout
    assert "nothing was written" in report.stdout
    assert _user_version(path) == 1

    upgraded = runner.invoke(app, ["portfolio", "migrate", "--apply"])
    assert upgraded.exit_code == 0
    assert "verified backup" in upgraded.stdout
    assert _user_version(path) == PORTFOLIO_SCHEMA_VERSION


def test_the_migrate_command_refuses_a_missing_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing ledger is a refused request, not an empty file to create."""
    from equity_lake.cli.__main__ import app

    missing = tmp_path / "portfolio" / "portfolio.sqlite3"
    monkeypatch.setattr(store_module, "PORTFOLIO_DB_PATH", missing)

    result = CliRunner().invoke(app, ["portfolio", "migrate"])

    assert result.exit_code == 1
    assert "does not exist" in result.stderr
    assert not missing.exists()
