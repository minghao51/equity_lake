"""RS-P01 synthetic rehearsal: the private-ledger workflow proved end to end.

The stage's real-instance rehearsal is `blocked-input` — no operator canonical CSV, statements
or marks exist in this checkout — so this module rehearses the *same* workflow on committed
synthetic fixtures only. It claims nothing about a real private account.

Each test is one rehearsal step from
[the residual plan](../../docs/plans/20261122-01-residual-spec-and-implementation-plan.md) §2.6/§5:

1. a dry run writes no private state (the whole private tree is hashed before and after);
2. a second identical import is a no-op with a stable record digest;
3. statement reconciliation reproduces agreement and reports a deliberate mismatch;
4. `restore` and `compact` run on isolated copies first, the production copy's digest unchanged,
   and a restore reproduces the recorded bytes and the replay fingerprint;
5. a fresh v2 ledger needs no migration (and a no-op `--apply` never manufactures a backup),
   while a legacy v1 fixture is upgraded only on a *legacy copy*, never in place.

Fixtures are committed under ``tests/fixtures/portfolio/`` and are invented: a neutral ``main``
account label, an invented instrument, invented amounts. Nothing reads a broker export, the lake,
``data/`` or the network, and the production copy in step 4 is a temp directory — the repository's
``data/portfolio`` is never created or touched.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import stat
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]
from equity_lake.core.paths import PORTFOLIO_DIR  # type: ignore[import-untyped]
from equity_lake.portfolio import LedgerStore, replay_ledger, verify_ledger_file  # type: ignore[import-untyped]
from equity_lake.portfolio import store as portfolio_store
from equity_lake.portfolio.store import PORTFOLIO_SCHEMA_VERSION, portfolio_db_path  # type: ignore[import-untyped]

runner = CliRunner()

ACCOUNT = "main"
SNAPSHOT = "2024-01-05T21:00:00Z"
AS_OF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)
DB_NAME = "portfolio.sqlite3"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "portfolio"
LEDGER_CSV = FIXTURES / "canonical-ledger.csv"
EXTENSION_CSV = FIXTURES / "canonical-ledger-extension.csv"
MATCHED_STATEMENT = FIXTURES / "statement-matched.csv"
MISMATCHED_STATEMENT = FIXTURES / "statement-mismatched.csv"
LEGACY_V1_SQL = FIXTURES / "legacy-v1-ledger.sql"


# ---------------------------------------------------------------------------
# Rehearsal helpers
# ---------------------------------------------------------------------------


def _text(result: Result) -> str:
    """Everything the command printed, whichever stream a Click version used."""
    return result.output + (getattr(result, "stderr", "") or "")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_snapshot(root: Path) -> dict[str, tuple[str, int]]:
    """Every path under the private root with its content hash and mode (a missing root is empty)."""
    snapshot: dict[str, tuple[str, int]] = {}
    if not root.exists():
        return snapshot
    for path in sorted(root.rglob("*")):
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            snapshot[f"{path.relative_to(root)}/"] = ("dir", mode)
        else:
            snapshot[str(path.relative_to(root))] = (_digest(path), mode)
    return snapshot


def _backups(directory: Path) -> list[str]:
    """Verified-copy names beside a ledger, in stable order."""
    return sorted(path.name for path in directory.glob("*.bak"))


def _sidelines(directory: Path) -> list[str]:
    """Rollback journal / WAL companions: any of these means a write did not finish."""
    return sorted(path.name for path in directory.rglob("*") if path.name.endswith(("-journal", "-wal", "-shm")))


def _use_ledger(monkeypatch: pytest.MonkeyPatch, db_path: Path) -> Path:
    """Point the CLI's lazily-read ledger global at one file (the established store seam)."""
    monkeypatch.setattr(portfolio_store, "PORTFOLIO_DB_PATH", db_path)
    return db_path


def _plant_ledger(monkeypatch: pytest.MonkeyPatch, data_dir: Path, *, csv_path: Path = LEDGER_CSV, expect: str | None = None) -> Path:
    """Create the rehearsal ledger from a committed fixture CSV and return its path."""
    db_path = _use_ledger(monkeypatch, portfolio_db_path(data_dir))
    result = runner.invoke(app, ["portfolio", "import", "--file", str(csv_path)])
    assert result.exit_code == 0, _text(result)
    if expect is not None:
        assert expect in _text(result), _text(result)
    return db_path


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _replay_fingerprint(db_path: Path) -> str:
    """Digest of the replayed book: equal records, not equal files (``recorded_at`` differs)."""
    projection = replay_ledger(LedgerStore(path=db_path).load_events(), account=ACCOUNT, as_of=AS_OF)
    payload = {
        "account": projection.account,
        "as_of": projection.as_of.isoformat(),
        "replayed_event_count": projection.replayed_event_count,
        "cash": _decimal_text(projection.cash),
        "deposits": _decimal_text(projection.deposits),
        "withdrawals": _decimal_text(projection.withdrawals),
        "embedded_fees": _decimal_text(projection.embedded_fees),
        "standalone_fees": _decimal_text(projection.standalone_fees),
        "net_dividend_income": _decimal_text(projection.net_dividend_income),
        "realized_gain": _decimal_text(projection.realized_gain),
        "positions": [
            [
                list(position.instrument.tuple),
                _decimal_text(position.quantity),
                _decimal_text(position.basis),
                _decimal_text(position.realized_gain),
                _decimal_text(position.embedded_fees),
            ]
            for position in projection.positions
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _isolated_copy(source_dir: Path, target_dir: Path) -> Path:
    """A drill target: a byte-identical copy of the private directory, never the original."""
    target_dir.mkdir(parents=True)
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    return target_dir / DB_NAME


@pytest.fixture
def private_root(tmp_path: Path) -> Path:
    """The rehearsal's private data root (``tmp_path/data``), never the repository's ``data/``."""
    return tmp_path / "data"


# ---------------------------------------------------------------------------
# 1. A dry run writes nothing (hashed before and after)
# ---------------------------------------------------------------------------


def test_dry_run_import_creates_no_private_state(tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard that makes ``--dry-run`` a dry run: the tree is untouched and no ledger appears."""
    repo_portfolio_existed = PORTFOLIO_DIR.exists()
    db_path = _use_ledger(monkeypatch, portfolio_db_path(private_root))
    before = _tree_snapshot(tmp_path)

    result = runner.invoke(app, ["portfolio", "import", "--file", str(LEDGER_CSV), "--dry-run"])

    assert result.exit_code == 0, _text(result)
    assert "4 new, 0 already stored identically" in _text(result)
    assert "Dry run wrote nothing" in _text(result)
    assert _tree_snapshot(tmp_path) == before
    assert not private_root.exists()
    assert not db_path.exists()
    assert _backups(private_root / "portfolio") == []
    assert _sidelines(tmp_path) == []
    assert PORTFOLIO_DIR.exists() is repo_portfolio_existed


def test_dry_run_import_on_a_stored_ledger_leaves_the_tree_byte_identical(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Against a populated ledger the dry run still writes nothing at all."""
    db_path = _plant_ledger(monkeypatch, private_root)
    verification_before = verify_ledger_file(db_path)
    before = _tree_snapshot(private_root)

    result = runner.invoke(app, ["portfolio", "import", "--file", str(LEDGER_CSV), "--dry-run"])

    assert result.exit_code == 0, _text(result)
    assert "0 new, 4 already stored identically" in _text(result)
    assert _tree_snapshot(private_root) == before
    assert verify_ledger_file(db_path) == verification_before
    assert _backups(db_path.parent) == []
    assert _sidelines(private_root) == []


def test_report_only_migrate_writes_nothing(tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _plant_ledger(monkeypatch, private_root)
    before = _tree_snapshot(private_root)

    result = runner.invoke(app, ["portfolio", "migrate"])

    assert result.exit_code == 0, _text(result)
    assert "already at schema v2" in _text(result)
    assert _tree_snapshot(private_root) == before
    assert _backups(db_path.parent) == []


def test_report_only_restore_writes_nothing(private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The restore plan is printed, including what it would roll back, and nothing is written."""
    db_path = _plant_ledger(monkeypatch, private_root)
    candidate = db_path.with_name(f"{DB_NAME}.rehearsal.v{PORTFOLIO_SCHEMA_VERSION}.bak")
    shutil.copy2(db_path, candidate)
    assert runner.invoke(app, ["portfolio", "import", "--file", str(EXTENSION_CSV)]).exit_code == 0
    before = _tree_snapshot(private_root)

    result = runner.invoke(app, ["portfolio", "restore", "--from", str(candidate)])

    assert result.exit_code == 0, _text(result)
    assert "1 event(s) it holds would be left out" in _text(result)
    assert "Report only: nothing was written" in _text(result)
    assert _tree_snapshot(private_root) == before
    assert _backups(db_path.parent) == [candidate.name]


def test_report_only_compact_writes_nothing(private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _plant_ledger(monkeypatch, private_root)
    verification_before = verify_ledger_file(db_path)
    before = _tree_snapshot(private_root)

    result = runner.invoke(app, ["portfolio", "compact"])

    assert result.exit_code == 0, _text(result)
    assert "Report only: VACUUM INTO a verified copy" in _text(result)
    assert _tree_snapshot(private_root) == before
    assert verify_ledger_file(db_path) == verification_before
    assert _backups(db_path.parent) == []


# ---------------------------------------------------------------------------
# 2. A second identical import is a no-op
# ---------------------------------------------------------------------------


def test_second_import_of_the_same_csv_adds_nothing_and_keeps_the_record_digest(private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-import reports the stored state honestly instead of double-counting it.

    The idempotency contract is on the *record* (ADR-0024 decision 5), not on the file bytes: a
    committed no-op batch still bumps SQLite's header change counter (offsets 27 and 95), so the
    rehearsal compares the row count, the record digest, the replayed book and the file's other
    bytes instead of claiming a byte-identical database.
    """
    db_path = _plant_ledger(monkeypatch, private_root, expect="inserted 4, unchanged 0, rejected 0")
    first = verify_ledger_file(db_path)
    first_bytes = db_path.read_bytes()
    first_tree = _tree_snapshot(private_root)
    first_fingerprint = _replay_fingerprint(db_path)

    result = runner.invoke(app, ["portfolio", "import", "--file", str(LEDGER_CSV)])

    assert result.exit_code == 0, _text(result)
    assert "inserted 0, unchanged 4, rejected 0" in _text(result)
    second = verify_ledger_file(db_path)
    assert (second.row_count, second.record_digest) == (first.row_count, first.record_digest)
    assert second.row_count == 4
    assert _replay_fingerprint(db_path) == first_fingerprint
    assert len(LedgerStore(path=db_path).load_events()) == 4
    # Nothing new appeared beside the ledger, and the only changed bytes are the two header
    # counters SQLite rewrites for any committed transaction.
    second_tree = _tree_snapshot(private_root)
    assert sorted(second_tree) == sorted(first_tree)
    assert second_tree[f"portfolio/{DB_NAME}"][1] == first_tree[f"portfolio/{DB_NAME}"][1] == 0o600
    assert _backups(db_path.parent) == []
    assert _sidelines(private_root) == []
    second_bytes = db_path.read_bytes()
    assert len(second_bytes) == len(first_bytes)
    changed = {index for index, (old, new) in enumerate(zip(first_bytes, second_bytes, strict=True)) if old != new}
    assert changed <= {27, 95}, sorted(changed)


# ---------------------------------------------------------------------------
# 3. Statement reconciliation
# ---------------------------------------------------------------------------


def test_reconcile_matches_a_synthetic_statement_line_by_line(private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = _plant_ledger(monkeypatch, private_root)
    verification_before = verify_ledger_file(db_path)
    before = _tree_snapshot(private_root)

    result = runner.invoke(app, ["portfolio", "reconcile", "--as-of", SNAPSHOT, "--statement", str(MATCHED_STATEMENT)])

    assert result.exit_code == 0, _text(result)
    assert "cash: statement 924.00, ledger 924.00, delta 0.00, tolerance 0.01, matched" in _text(result)
    assert "quantity XNAS:PRIVATESYM: statement 11, ledger 11, delta 0, tolerance 0.00000001, matched" in _text(result)
    assert "overall: matched" in _text(result)
    assert verify_ledger_file(db_path) == verification_before
    assert _tree_snapshot(private_root) == before


def test_reconcile_reports_a_deliberate_mismatch_instead_of_absorbing_it(private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A disagreed statement line is mismatched and exits 1; the ledger is never balanced to agree."""
    db_path = _plant_ledger(monkeypatch, private_root)
    verification_before = verify_ledger_file(db_path)
    before = _tree_snapshot(private_root)

    result = runner.invoke(app, ["portfolio", "reconcile", "--as-of", SNAPSHOT, "--statement", str(MISMATCHED_STATEMENT)])

    assert result.exit_code == 1, _text(result)
    assert "cash: statement 900.00, ledger 924.00, delta -24.00, tolerance 0.01, mismatched" in _text(result)
    assert "quantity XNAS:PRIVATESYM: statement 10, ledger 11, delta -1, tolerance 0.00000001, mismatched" in _text(result)
    assert "overall: mismatched" in _text(result)
    assert "the ledger was not adjusted and no balancing event was inserted" in _text(result)
    assert verify_ledger_file(db_path) == verification_before
    assert _tree_snapshot(private_root) == before


# ---------------------------------------------------------------------------
# 4. Isolated-copy drills: the production copy is never the target
# ---------------------------------------------------------------------------


def test_compact_runs_on_an_isolated_copy_and_leaves_the_production_copy_unchanged(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _plant_ledger(monkeypatch, private_root)
    production = verify_ledger_file(db_path)
    production_tree = _tree_snapshot(private_root)

    drill_db = _isolated_copy(db_path.parent, tmp_path / "drill-compact" / "portfolio")
    _use_ledger(monkeypatch, drill_db)
    result = runner.invoke(app, ["portfolio", "compact", "--apply"])

    assert result.exit_code == 0, _text(result)
    assert "4 row(s) preserved" in _text(result)
    compacted = verify_ledger_file(drill_db)
    assert (compacted.schema_version, compacted.row_count, compacted.record_digest) == (
        PORTFOLIO_SCHEMA_VERSION,
        production.row_count,
        production.record_digest,
    )
    # The drill kept a verified pre-compaction backup of the drill copy, not of production.
    backups = _backups(drill_db.parent)
    assert len(backups) == 1
    assert verify_ledger_file(drill_db.parent / backups[0]).sha256 == production.sha256
    # And the production copy is exactly as it was: bytes, record, tree, no new backup.
    assert verify_ledger_file(db_path) == production
    assert _tree_snapshot(private_root) == production_tree
    assert _backups(db_path.parent) == []


def test_restore_on_an_isolated_copy_reproduces_the_recorded_bytes_and_replay_fingerprint(
    tmp_path: Path, private_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = _plant_ledger(monkeypatch, private_root)
    production = verify_ledger_file(db_path)
    production_tree = _tree_snapshot(private_root)
    production_fingerprint = _replay_fingerprint(db_path)

    drill_db = _isolated_copy(db_path.parent, tmp_path / "drill-restore" / "portfolio")
    recorded = drill_db.with_name(f"{DB_NAME}.rehearsal.v{PORTFOLIO_SCHEMA_VERSION}.bak")
    shutil.copy2(drill_db, recorded)
    # Diverge the isolated copy so the restore has something real to roll back.
    _use_ledger(monkeypatch, drill_db)
    diverged = runner.invoke(app, ["portfolio", "import", "--file", str(EXTENSION_CSV)])
    assert diverged.exit_code == 0, _text(diverged)
    assert "inserted 1, unchanged 4, rejected 0" in _text(diverged)
    assert verify_ledger_file(drill_db).record_digest != production.record_digest

    result = runner.invoke(app, ["portfolio", "restore", "--from", str(recorded), "--apply"])

    assert result.exit_code == 0, _text(result)
    assert "1 event(s) it holds would be left out" in _text(result)
    restored = verify_ledger_file(drill_db)
    assert restored.sha256 == production.sha256
    assert (restored.row_count, restored.record_digest) == (production.row_count, production.record_digest)
    assert _replay_fingerprint(drill_db) == production_fingerprint
    # The drill kept a verified backup of the diverged copy it replaced.
    assert len(_backups(drill_db.parent)) == 2
    assert _sidelines(drill_db.parent) == []
    # Production is untouched: same bytes, same record, same tree, no backup beside it.
    assert verify_ledger_file(db_path) == production
    assert _tree_snapshot(private_root) == production_tree
    assert _backups(db_path.parent) == []


# ---------------------------------------------------------------------------
# 5. Schema paths: a current ledger needs nothing; a legacy one upgrades on a copy
# ---------------------------------------------------------------------------


def test_fresh_v2_ledger_needs_no_migration_and_no_op_apply_never_makes_a_backup(private_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh ledger is already v2, so neither mode writes — and `--apply` invents no backup."""
    db_path = _plant_ledger(monkeypatch, private_root)
    assert verify_ledger_file(db_path).schema_version == PORTFOLIO_SCHEMA_VERSION
    before = _tree_snapshot(private_root)

    report_only = runner.invoke(app, ["portfolio", "migrate"])
    applied = runner.invoke(app, ["portfolio", "migrate", "--apply"])

    assert report_only.exit_code == 0, _text(report_only)
    assert applied.exit_code == 0, _text(applied)
    assert "already at schema v2" in _text(report_only)
    assert "already at schema v2" in _text(applied)
    assert "nothing to do" in _text(applied)
    # A no-op migration is not a backup source: forcing one to obtain a copy is refused by
    # design (ADR-0031 decision 3); an export is RS-P02's command, not a fake migration.
    assert _tree_snapshot(private_root) == before
    assert _backups(db_path.parent) == []


def test_legacy_v1_upgrades_only_on_a_legacy_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The v1 original stays v1; the copy is reported, upgraded, and its record proven unchanged."""
    source_dir = tmp_path / "legacy-source" / "portfolio"
    source_dir.mkdir(parents=True)
    source_db = source_dir / DB_NAME
    connection = sqlite3.connect(source_db)
    try:
        connection.executescript(LEGACY_V1_SQL.read_text(encoding="utf-8"))
        connection.commit()
    finally:
        connection.close()
    source_db.chmod(0o600)
    legacy = verify_ledger_file(source_db)
    assert legacy.schema_version == 1
    source_digest = _digest(source_db)

    copy_db = _isolated_copy(source_dir, tmp_path / "legacy-copy" / "portfolio")
    copy_before = _tree_snapshot(copy_db.parent)
    _use_ledger(monkeypatch, copy_db)

    report_only = runner.invoke(app, ["portfolio", "migrate"])

    assert report_only.exit_code == 0, _text(report_only)
    assert "Report only" in _text(report_only)
    assert "is schema v1" in _text(report_only)
    assert _tree_snapshot(copy_db.parent) == copy_before
    assert _backups(copy_db.parent) == []

    applied = runner.invoke(app, ["portfolio", "migrate", "--apply"])

    assert applied.exit_code == 0, _text(applied)
    assert "schema v1 -> v2" in _text(applied)
    upgraded = verify_ledger_file(copy_db)
    assert upgraded.schema_version == PORTFOLIO_SCHEMA_VERSION
    assert (upgraded.row_count, upgraded.record_digest) == (legacy.row_count, legacy.record_digest)
    backups = _backups(copy_db.parent)
    assert len(backups) == 1
    assert backups[0].endswith(".v1.bak")
    assert verify_ledger_file(copy_db.parent / backups[0]).sha256 == source_digest
    assert len(LedgerStore(path=copy_db).load_events()) == legacy.row_count

    # Never in place: the v1 original is byte-identical, still v1, with no backup beside it.
    assert _digest(source_db) == source_digest
    assert verify_ledger_file(source_db).schema_version == 1
    assert _backups(source_dir) == []
