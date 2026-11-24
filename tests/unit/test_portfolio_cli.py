"""CLI contracts for the private portfolio ledger (P01b import, P02 read reports).

``import`` is the only write path; ``positions``, ``summary`` and ``reconcile``
are the frozen P02 read reports (ADR-0024 decision 13, ADR-0026 decision 7).
Fixtures are invented: a neutral ``main`` account label, an invented instrument and
invented amounts. No broker file, no real account data, no network and no lake write.

Every test runs against a private ledger redirected into the test temp directory
(``monkeypatch`` on :data:`equity_lake.portfolio.store.PORTFOLIO_DB_PATH`, read at
call time by ``portfolio_db_path``), so a test can never touch the repository's
``data/portfolio`` or real account data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import stat
from pathlib import Path

import pytest
import typer
from click.testing import Result
from structlog.testing import capture_logs
from typer.testing import CliRunner

from equity_lake.cli import _app as cli_app
from equity_lake.cli.__main__ import app
from equity_lake.cli._app import portfolio_app
from equity_lake.core import paths as core_paths
from equity_lake.core.paths import PORTFOLIO_DIR
from equity_lake.portfolio import store as portfolio_store
from equity_lake.portfolio.csv_import import CANONICAL_CSV_COLUMNS
from equity_lake.portfolio.store import LedgerStore, portfolio_db_path

runner = CliRunner()

ACCOUNT = "main"
# Invented identifiers/amounts: the private tokens an error message must not echo.
SYMBOL = "PRIVATESYM"
PRIVATE_ACCOUNT = "acct-synth-01"
PRIVATE_AMOUNT = "987654.32"
COMPANION_SUFFIXES = ("-journal", "-wal", "-shm")

_DEPOSIT = {
    "account": ACCOUNT,
    "external_source": "synthetic-broker",
    "external_id": "evt-0001",
    "event_type": "DEPOSIT",
    "effective_at": "2024-01-02T15:00:00Z",
    "received_at": "2024-01-02T21:00:00Z",
    "sequence": "1",
    "market": "",
    "mic": "",
    "symbol": "",
    "currency": "USD",
    "cash_effect": "500.00",
    "quantity": "",
    "price": "",
    "fees": "",
    "basis": "",
    "basis_status": "",
    "reverses_external_id": "",
}

_OPENING = {
    **_DEPOSIT,
    "event_type": "OPENING",
    "market": "us_equity",
    "mic": "XNAS",
    "symbol": SYMBOL,
    "cash_effect": "1000.00",
    "quantity": "10",
    "price": "100.00",
    "basis": "1000.00",
}

_BUY = {
    **_DEPOSIT,
    "external_id": "evt-0002",
    "event_type": "BUY",
    "sequence": "2",
    "market": "us_equity",
    "mic": "XNAS",
    "symbol": SYMBOL,
    "cash_effect": "-605.00",
    "quantity": "5",
    "price": "120.00",
    "fees": "5.00",
}


def _row(base: dict[str, str] | None = None, **overrides: str) -> dict[str, str]:
    row = dict(_DEPOSIT if base is None else base)
    row.update(overrides)
    return row


def _hand_oracle_rows() -> list[dict[str, str]]:
    """Opening 10 at 100 + cash 1000; buy 5 at 120 fee 5; sell 4 at 130 fee 2; dividend 11."""
    return [
        _row(_OPENING),
        _row(_BUY),
        _row(_BUY, external_id="evt-0003", event_type="SELL", sequence="3", cash_effect="518.00", quantity="4", price="130.00", fees="2.00"),
        _row(_BUY, external_id="evt-0004", event_type="CASH_DIVIDEND", sequence="4", cash_effect="11.00", quantity="", price="", fees=""),
    ]


def _csv(rows: list[dict[str, str]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CANONICAL_CSV_COLUMNS)
    for row in rows:
        writer.writerow([row[column] for column in CANONICAL_CSV_COLUMNS])
    return buffer.getvalue()


def _write_csv(directory: Path, rows: list[dict[str, str]], *, name: str = "portfolio-ledger.csv") -> Path:
    path = directory / name
    path.write_text(_csv(rows), encoding="utf-8")
    return path


def _import(*args: str) -> Result:
    return runner.invoke(app, ["portfolio", "import", *args])


def _control_context(subcommand: str) -> typer.Context:
    """A real root ``typer.Context`` with ``invoked_subcommand`` set, for callback probes."""
    context = typer.Context(typer.main.get_command(app))
    context.invoked_subcommand = subcommand
    return context


@pytest.fixture(autouse=True)
def isolated_ledger(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the canonical private ledger into the temp dir: the repo is never written."""
    data_dir = tmp_path / "data"
    monkeypatch.setattr(portfolio_store, "PORTFOLIO_DB_PATH", portfolio_db_path(data_dir))
    return data_dir


def _assert_no_ledger_written(root: Path, data_dir: Path) -> None:
    """No ledger directory, database or journal/checkpoint file exists after a no-write path."""
    assert not data_dir.exists()
    assert not (data_dir / "portfolio").exists()
    assert list(root.rglob("*.sqlite3*")) == []
    for suffix in COMPANION_SUFFIXES:
        assert list(root.rglob(f"*{suffix}")) == []
    assert LedgerStore().exists() is False


def _stored_digests() -> list[tuple[str, str]]:
    return [(event.account, event.content_digest) for event in LedgerStore().load_events()]


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------


def test_import_reports_inserted_and_unchanged_counts(tmp_path: Path, isolated_ledger: Path) -> None:
    path = _write_csv(tmp_path, _hand_oracle_rows())

    result = _import("--file", str(path))

    assert result.exit_code == 0, result.output
    assert "inserted 4, unchanged 0, rejected 0" in result.stdout
    assert len(LedgerStore().load_events()) == 4
    assert portfolio_db_path(isolated_ledger).exists()


def test_successful_commit_logs_only_the_non_sensitive_append_counts(tmp_path: Path, isolated_ledger: Path) -> None:
    """ADR-0024 decision 11: the append record carries counts and nothing else."""
    path = _write_csv(tmp_path, _hand_oracle_rows())

    with capture_logs() as captured:
        result = _import("--file", str(path))

    assert result.exit_code == 0, result.output
    records = [entry for entry in captured if entry["event"] == "portfolio_ledger_append"]
    assert len(records) == 1
    record = records[0]
    assert set(record) == {"event", "log_level", "inserted", "unchanged"}
    assert (record["inserted"], record["unchanged"]) == (4, 0)
    assert isinstance(record["inserted"], int)
    assert isinstance(record["unchanged"], int)
    serialized = repr(record)
    for token in (SYMBOL, PRIVATE_ACCOUNT, PRIVATE_AMOUNT, ACCOUNT, "1000.00", "120.00", "605.00", "2024-01-02"):
        assert token not in serialized, (token, serialized)


def test_reimport_is_idempotent_and_reports_zero_inserted(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, _hand_oracle_rows())

    first = _import("--file", str(path))
    after_first = _stored_digests()
    second = _import("--file", str(path))

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert "inserted 0, unchanged 4, rejected 0" in second.stdout
    assert _stored_digests() == after_first
    assert len(LedgerStore().load_events()) == 4


def test_import_records_the_given_account_label(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, [_row(_OPENING, account=PRIVATE_ACCOUNT)])

    default_label = _import("--file", str(path))
    explicit_label = _import("--file", str(path), "--account", PRIVATE_ACCOUNT)

    assert default_label.exit_code == 1
    assert "account column differs from --account" in default_label.output
    assert explicit_label.exit_code == 0, explicit_label.output
    assert "inserted 1, unchanged 0, rejected 0" in explicit_label.stdout
    assert [event.account for event in LedgerStore().load_events()] == [PRIVATE_ACCOUNT]


def test_import_never_echoes_the_account_label_from_the_file(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, [_row(_OPENING, account=PRIVATE_ACCOUNT)])

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert PRIVATE_ACCOUNT not in result.output


# ---------------------------------------------------------------------------
# Dry run: full validation, proposed effects, zero writes
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing_and_reports_proposed_effects(tmp_path: Path, isolated_ledger: Path) -> None:
    path = _write_csv(tmp_path, _hand_oracle_rows())
    repo_portfolio_before = PORTFOLIO_DIR.exists()

    result = _import("--file", str(path), "--dry-run")

    assert result.exit_code == 0, result.output
    assert "nothing was written" in result.stdout
    assert "4 new, 0 already stored identically" in result.stdout
    assert "events by type: BUY 1, CASH_DIVIDEND 1, OPENING 1, SELL 1" in result.stdout
    assert f"quantity delta by instrument (share-count events only): XNAS:{SYMBOL} 11" in result.stdout
    assert "cash delta: 924.00" in result.stdout
    _assert_no_ledger_written(tmp_path, isolated_ledger)
    assert PORTFOLIO_DIR.exists() is repo_portfolio_before


def test_dry_run_against_a_stored_ledger_reports_unchanged_and_creates_nothing(tmp_path: Path, isolated_ledger: Path) -> None:
    path = _write_csv(tmp_path, _hand_oracle_rows())
    assert _import("--file", str(path)).exit_code == 0
    db_path = portfolio_db_path(isolated_ledger)
    stored_before = _stored_digests()
    files_before = sorted(item.name for item in db_path.parent.iterdir())

    result = _import("--file", str(path), "--dry-run")

    assert result.exit_code == 0, result.output
    assert "0 new, 4 already stored identically" in result.stdout
    assert _stored_digests() == stored_before
    assert sorted(item.name for item in db_path.parent.iterdir()) == files_before == ["portfolio.sqlite3"]
    assert [suffix for suffix in COMPANION_SUFFIXES if db_path.with_name(f"{db_path.name}{suffix}").exists()] == []


def test_dry_run_reports_an_invalid_file_without_writing(tmp_path: Path, isolated_ledger: Path) -> None:
    path = _write_csv(tmp_path, [_row(_BUY, quantity="-1")])

    result = _import("--file", str(path), "--dry-run")

    assert result.exit_code == 1
    assert "row 2" in result.output
    assert "positive quantity" in result.output
    assert "nothing was written" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


# ---------------------------------------------------------------------------
# Rejection matrix
# ---------------------------------------------------------------------------


def test_invalid_row_exits_one_and_writes_nothing(tmp_path: Path, isolated_ledger: Path) -> None:
    path = _write_csv(tmp_path, [_row(_OPENING), _row(_BUY, price="120O.00")])

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert "row 3" in result.output
    assert "not a decimal number" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_conflict_on_changed_content_exits_one_and_leaves_the_store_unchanged(tmp_path: Path) -> None:
    path = _write_csv(tmp_path, [_row(_OPENING), _row(_BUY)])
    assert _import("--file", str(path)).exit_code == 0
    stored_before = _stored_digests()

    changed = _write_csv(tmp_path, [_row(_OPENING, cash_effect="999.00")], name="portfolio-ledger-changed.csv")
    result = _import("--file", str(changed))

    assert result.exit_code == 1
    assert "different content" in result.output
    assert "row 2" in result.output
    assert _stored_digests() == stored_before
    assert len(LedgerStore().load_events()) == 2


def test_missing_file_exits_one(tmp_path: Path, isolated_ledger: Path) -> None:
    result = _import("--file", str(tmp_path / "absent.csv"))

    assert result.exit_code == 1
    assert "no such file" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_directory_instead_of_a_file_exits_one(tmp_path: Path, isolated_ledger: Path) -> None:
    result = _import("--file", str(tmp_path))

    assert result.exit_code == 1
    assert "not a regular file" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_unreadable_utf8_exits_one(tmp_path: Path, isolated_ledger: Path) -> None:
    path = tmp_path / "portfolio-ledger.csv"
    path.write_bytes(b"\xff\xfe\x00not-utf8")

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert "UTF-8" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_header_drift_exits_one(tmp_path: Path, isolated_ledger: Path) -> None:
    path = tmp_path / "portfolio-ledger.csv"
    drift = ",".join(column for column in CANONICAL_CSV_COLUMNS if column != "basis")
    path.write_text(f"{drift}\n", encoding="utf-8")

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert "missing ['basis']" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_private_value_in_the_timestamp_column_exits_one_without_echo(tmp_path: Path, isolated_ledger: Path) -> None:
    """A private/amount-bearing cell pasted into ``effective_at`` is named by row, never repeated."""
    pasted = f"{PRIVATE_ACCOUNT} {PRIVATE_AMOUNT} private note"
    path = _write_csv(tmp_path, [_row(_OPENING), _row(_BUY, effective_at=pasted)])

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert "row 3" in result.output
    assert "effective_at is not an ISO-8601 timestamp" in result.output
    assert pasted not in result.output
    scrubbed = result.output.replace(str(tmp_path), "<tmp>")
    assert re.search(r"\d+\.\d+", scrubbed) is None, scrubbed
    for token in (PRIVATE_ACCOUNT, PRIVATE_AMOUNT, "private note"):
        assert token not in scrubbed, (token, scrubbed)
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_long_private_note_like_value_in_a_text_column_is_never_echoed(tmp_path: Path, isolated_ledger: Path) -> None:
    """The frozen canonical CSV has no ``note`` column, so a note is pasted into a text column.

    ``external_source``/``external_id`` accept at most 64 label characters, so the
    accepted case carries a note-like label and the rejected case carries an
    over-long one. Neither may surface in CLI output, and the rejection must
    leave the stored ledger untouched.
    """
    note = "private-note-broker-statement-987654.32-acct-synth-01"
    oversized = note + "-" + "x" * 200
    accepted = _write_csv(tmp_path, [_row(_OPENING, external_source=note)], name="accepted.csv")
    rejected = _write_csv(tmp_path, [_row(_BUY, external_source=oversized)], name="rejected.csv")

    commit = _import("--file", str(accepted))
    stored_before = _stored_digests()
    failure = _import("--file", str(rejected))

    assert commit.exit_code == 0, commit.output
    assert failure.exit_code == 1
    assert "row 2" in failure.output
    assert "external_source: String should have at most 64 characters" in failure.output
    for result in (commit, failure):
        assert note not in result.output
        assert oversized not in result.output
        assert "input_value" not in result.output
    assert _stored_digests() == stored_before
    assert len(LedgerStore().load_events()) == 1


@pytest.mark.parametrize("extra_column", ["broker_note", "note"])
def test_header_drift_with_a_private_extra_column_leaks_neither_name_nor_content(tmp_path: Path, isolated_ledger: Path, extra_column: str) -> None:
    """An unexpected private column is reported by position only (ADR-0024 decision 11)."""
    private_content = f"{PRIVATE_ACCOUNT} paid {PRIVATE_AMOUNT}"
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow([*CANONICAL_CSV_COLUMNS, extra_column])
    writer.writerow([*[_row(_OPENING)[column] for column in CANONICAL_CSV_COLUMNS], private_content])
    path = tmp_path / "portfolio-ledger.csv"
    path.write_text(buffer.getvalue(), encoding="utf-8")

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert "unexpected column(s) at position(s) [19]" in result.output
    assert "unexpected names are not echoed" in result.output
    assert extra_column not in result.output
    for token in (private_content, PRIVATE_ACCOUNT, PRIVATE_AMOUNT):
        assert token not in result.output, (token, result.output)
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_import_never_creates_non_portfolio_runtime_dirs(tmp_path: Path, isolated_ledger: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ADR-0024 decision 13: only ``data/portfolio`` may appear; ``ensure_dirs()`` is skipped."""
    real_dirs = {name: Path(getattr(core_paths, name)) for name in ("LAKE_DIR", "LOGS_DIR", "MODELS_DIR", "PORTFOLIO_DIR")}
    existence_before = {name: directory.exists() for name, directory in real_dirs.items()}
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(core_paths, "LAKE_DIR", runtime / "lake")
    monkeypatch.setattr(core_paths, "LOGS_DIR", runtime / "logs")
    monkeypatch.setattr(core_paths, "MODELS_DIR", runtime / "models")
    path = _write_csv(tmp_path, _hand_oracle_rows())
    before = sorted(item.relative_to(tmp_path).as_posix() for item in tmp_path.rglob("*"))

    result = _import("--file", str(path))

    assert result.exit_code == 0, result.output
    after = sorted(item.relative_to(tmp_path).as_posix() for item in tmp_path.rglob("*"))
    assert after == sorted([*before, "data", "data/portfolio", "data/portfolio/portfolio.sqlite3"])
    assert not runtime.exists()
    assert {name: directory.exists() for name, directory in real_dirs.items()} == existence_before

    # Control: the same callback creates all three for another sub-command, so their
    # absence above comes from the portfolio skip, not from an unwritable environment.
    cli_app._main_callback(_control_context("pipeline"))
    assert (runtime / "lake").is_dir()
    assert (runtime / "logs").is_dir()
    assert (runtime / "models").is_dir()


def test_oversell_exits_one_and_names_the_row(tmp_path: Path, isolated_ledger: Path) -> None:
    sell = _row(_BUY, external_id="evt-0002", event_type="SELL", sequence="2", quantity="11", price="130.00", cash_effect="1428.00", fees="2.00")
    path = _write_csv(tmp_path, [_row(_OPENING), sell])

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert "row 3" in result.output
    assert "oversells are rejected" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_corrupt_ledger_exits_one_without_a_traceback(tmp_path: Path, isolated_ledger: Path) -> None:
    db_path = portfolio_db_path(isolated_ledger)
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not-a-ledger-private-label")
    path = _write_csv(tmp_path, _hand_oracle_rows())

    result = _import("--file", str(path))

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "not a usable SQLite database" in result.output
    assert "not-a-ledger-private-label" not in result.output


def test_failures_never_echo_amounts_quantities_symbols_or_accounts(tmp_path: Path, isolated_ledger: Path) -> None:
    """ADR-0024 decision 11: the CLI repeats value-free issues and formats no private value."""
    cases = [
        [_row(_BUY, cash_effect=f"{PRIVATE_AMOUNT}.00")],
        [_row(_BUY, mic="XHKG", symbol="HIDDENSYM")],
        [_row(_OPENING, account=PRIVATE_ACCOUNT)],
        [_row(_BUY, quantity="0")],
    ]

    for rows in cases:
        path = _write_csv(tmp_path, rows, name="portfolio-ledger.csv")
        result = _import("--file", str(path))
        assert result.exit_code == 1, result.output
        # The temp path itself carries digits; it is the only number allowed through.
        scrubbed = result.output.replace(str(tmp_path), "<tmp>")
        assert re.search(r"\d+\.\d+", scrubbed) is None, scrubbed
        for token in (SYMBOL, "HIDDENSYM", PRIVATE_ACCOUNT, PRIVATE_AMOUNT, "input_value", "XHKG"):
            assert token not in scrubbed, (token, scrubbed)
    _assert_no_ledger_written(tmp_path, isolated_ledger)


# ---------------------------------------------------------------------------
# Help text and the frozen (unfaked) surface
# ---------------------------------------------------------------------------


def test_import_help_documents_canonical_csv_only_and_no_write_dry_run() -> None:
    result = runner.invoke(app, ["portfolio", "import", "--help"], env={"COLUMNS": "180"})

    assert result.exit_code == 0, result.output
    assert "--file" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--account" in result.stdout
    assert "canonical CSV" in result.stdout
    assert "writes nothing" in result.stdout
    assert "never a real account number" in result.stdout


def test_import_help_states_the_frozen_exit_contract() -> None:
    result = runner.invoke(app, ["portfolio", "import", "--help"], env={"COLUMNS": "180"})

    assert result.exit_code == 0, result.output
    assert "Exit codes" in result.stdout
    assert "nothing is written" in result.stdout.lower()


def test_only_import_the_read_reports_and_the_explicit_ledger_mechanisms_are_registered() -> None:
    """The frozen surface: import, the three P02 read reports and the four explicit
    ledger mechanisms (migrate, restore, compact — ADR-0031 decisions 2, 4 and 5 — and
    backup/backups — ADR-0031 decision 3, authorized by the residual plan's RS-P02).

    The two backup commands are added here deliberately, not by convenience: ADR-0031
    decision 3 requires a manual, locally verified backup export with manual retention and
    explicitly introduces no scheduled, remote or cloud backup; RS-P02 §2.5 delivers only
    that in-contract posture (a report-only inventory plus the manual export) and forbids
    backup-file pruning and off-host/encrypted export, which need their own new ADR.

    Falsified by: adding a portfolio command that is not one of these nine; mechanisms are
    added by a decision, never by convenience.
    """
    registered = {command.name for command in portfolio_app.registered_commands}
    expected = {"import", "positions", "summary", "reconcile", "migrate", "restore", "compact", "backup", "backups"}
    assert registered == expected
    assert portfolio_app.registered_groups == []

    help_result = runner.invoke(app, ["portfolio", "--help"], env={"COLUMNS": "180"})
    assert help_result.exit_code == 0, help_result.output
    for present in expected:
        assert present in help_result.stdout, present


def test_portfolio_group_help_is_listed_on_the_unified_cli() -> None:
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "180"})

    assert result.exit_code == 0, result.output
    assert "portfolio" in result.stdout


# ---------------------------------------------------------------------------
# P02 read commands: positions, summary, reconcile (ADR-0026 decision 7)
# ---------------------------------------------------------------------------

SNAPSHOT = "2024-01-05T21:00:00Z"
PRIVATE_PRICE = "987654.32"


def _positions(*args: str) -> Result:
    return runner.invoke(app, ["portfolio", "positions", *args])


def _summary(*args: str) -> Result:
    return runner.invoke(app, ["portfolio", "summary", *args])


def _reconcile(*args: str) -> Result:
    return runner.invoke(app, ["portfolio", "reconcile", *args])


def _marks_file(
    directory: Path,
    *,
    price: object = "125",
    provenance: str = "synthetic",
    session: str = "2024-01-05",
    as_of: str = "2024-01-05T21:00:00+00:00",
    blocks: list[dict[str, str]] | None = None,
    name: str = "marks.json",
) -> Path:
    document = {
        "schema_version": 1,
        "marks": [
            {
                "market": "us_equity",
                "mic": "XNAS",
                "symbol": SYMBOL,
                "currency": "USD",
                "price": price,
                "session": session,
                "as_of": as_of,
                "source": "fixture",
                "price_basis": "unadjusted",
                "provenance": provenance,
            }
        ],
        "valuation_blocks": blocks or [],
    }
    path = directory / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _statement_file(directory: Path, *, quantity: str = "11", cash: str = "924", name: str = "statement.csv") -> Path:
    text = (
        "account,as_of,line_type,market,mic,symbol,currency,quantity,cash\n"
        f"main,{SNAPSHOT},CASH,,,,USD,,{cash}\n"
        f"main,{SNAPSHOT},POSITION,us_equity,XNAS,{SYMBOL},USD,{quantity},\n"
    )
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _seeded_ledger(tmp_path: Path) -> None:
    """Import the hand-oracle timeline: cash 924, 11 shares, realized gain 90."""
    path = _write_csv(tmp_path, _hand_oracle_rows())
    result = _import("--file", str(path))
    assert result.exit_code == 0, result.output


def _ledger_snapshot(root: Path) -> dict[str, tuple[str, int]]:
    """Every path under the private data root with its content hash and mode."""
    snapshot: dict[str, tuple[str, int]] = {}
    for path in sorted(root.rglob("*")):
        mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            snapshot[f"{path.relative_to(root)}/"] = ("dir", mode)
        else:
            snapshot[str(path.relative_to(root))] = (hashlib.sha256(path.read_bytes()).hexdigest(), mode)
    return snapshot


@pytest.mark.parametrize("command", ["positions", "summary", "reconcile"])
def test_read_command_help_states_required_inputs_and_the_exit_contract(command: str) -> None:
    result = runner.invoke(app, ["portfolio", command, "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "--as-of" in result.stdout
    assert "--account" in result.stdout
    assert "Exit codes" in result.stdout
    assert "read paths" in result.stdout.lower()


def test_valuation_help_states_the_staleness_bound_and_marks_requirement() -> None:
    for command in ("positions", "summary"):
        result = runner.invoke(app, ["portfolio", command, "--help"], env={"COLUMNS": "200"})

        assert result.exit_code == 0, result.output
        assert "--marks" in result.stdout
        assert "--max-stale-sessions" in result.stdout
        assert "never above 1" in result.stdout


def test_reconcile_help_states_the_canonical_statement_only() -> None:
    result = runner.invoke(app, ["portfolio", "reconcile", "--help"], env={"COLUMNS": "200"})

    assert result.exit_code == 0, result.output
    assert "--statement" in result.stdout
    assert "canonical" in result.stdout
    assert "never created or mapped from a broker export" in result.stdout


def test_positions_reports_the_hand_oracle(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    marks = _marks_file(tmp_path)

    result = _positions("--as-of", SNAPSHOT, "--marks", str(marks))

    assert result.exit_code == 0, result.output
    stdout = result.stdout
    assert "quantity 11" in stdout
    assert "basis 1177.00" in stdout
    assert "average cost 107.00" in stdout
    assert "realized gain 90.00" in stdout
    assert "mark 125 (fixture, provenance synthetic)" in stdout
    assert "session 2024-01-05" in stdout
    assert "age 0 session(s), bound 1" in stdout
    assert "value 1375.00" in stdout
    assert "unrealized gain 198.00" in stdout
    assert "cash 924.00" in stdout
    assert "marked position value 1375.00" in stdout
    assert "NAV 2299.00" in stdout
    assert "total gain 299.00" in stdout
    assert "effective-date reconstruction" in stdout


def test_summary_reports_the_hand_oracle_and_an_unavailable_breakdown(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    marks = _marks_file(tmp_path)

    result = _summary("--as-of", SNAPSHOT, "--marks", str(marks))

    assert result.exit_code == 0, result.output
    stdout = result.stdout
    assert "cash 924.00" in stdout
    assert "fees counted once: embedded 7.00, standalone 0.00" in stdout
    assert "net dividend income 11.00" in stdout
    assert "gross dividend income and withholding unavailable" in stdout
    assert "realized gain 90.00" in stdout
    assert "NAV 2299.00" in stdout
    assert "unrealized gain 198.00" in stdout
    assert "total gain 299.00" in stdout
    assert "allocation of NAV:" in stdout
    assert "0.598086" in stdout and "0.401914" in stdout


def test_summary_reports_external_flows_as_not_profit(tmp_path: Path, isolated_ledger: Path) -> None:
    rows = [
        _row(_DEPOSIT, cash_effect="500.00"),
        _row(_DEPOSIT, external_id="evt-0002", event_type="WITHDRAWAL", sequence="2", cash_effect="-200.00"),
    ]
    path = _write_csv(tmp_path, rows)
    assert _import("--file", str(path)).exit_code == 0

    result = _summary("--as-of", SNAPSHOT, "--marks", str(_marks_file(tmp_path)))

    assert result.exit_code == 0, result.output
    assert "deposits 500.00, withdrawals 200.00, net 300.00 (external flows, never profit)" in result.stdout
    assert "NAV 300.00" in result.stdout


def test_positions_names_the_blocker_and_exits_one_when_a_mark_is_missing(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    empty_marks = tmp_path / "empty-marks.json"
    empty_marks.write_text(json.dumps({"schema_version": 1, "marks": [], "valuation_blocks": []}), encoding="utf-8")

    result = _positions("--as-of", SNAPSHOT, "--marks", str(empty_marks))

    assert result.exit_code == 1
    assert "cash 924.00" in result.stdout
    assert "marked position value 0.00" in result.stdout
    assert "NAV unavailable" in result.stdout
    assert f"XNAS:{SYMBOL}: no stored mark" in result.output
    assert "cannot be valued" in result.output


@pytest.mark.parametrize("command", ["positions", "summary"])
def test_stale_mark_prints_a_partial_report_and_exits_one(command: str, tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    marks = _marks_file(tmp_path)

    result = runner.invoke(app, ["portfolio", command, "--as-of", "2024-01-09T21:00:00Z", "--marks", str(marks)])

    assert result.exit_code == 1
    assert "924.00" in result.stdout
    assert "NAV unavailable" in result.stdout
    assert "older than the staleness bound" in result.output


def test_quantity_uncertain_block_withholds_valuation_and_names_the_holding(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    marks = _marks_file(
        tmp_path,
        blocks=[{"market": "us_equity", "mic": "XNAS", "symbol": SYMBOL, "reason": "unsupported_quantity_adjustment"}],
    )

    result = _positions("--as-of", SNAPSHOT, "--marks", str(marks))

    assert result.exit_code == 1
    assert "quantity uncertain" in result.stdout
    assert "recorded quantity is uncertain" in result.output


@pytest.mark.parametrize("command", ["positions", "summary", "reconcile"])
def test_missing_as_of_exits_one_with_the_required_option_message(command: str, tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = runner.invoke(app, ["portfolio", command])

    assert result.exit_code == 1
    assert "--as-of is required" in result.output


@pytest.mark.parametrize("value", ["2024-01-05T21:00:00", "2024-01-05 21:00", "not-a-time"])
@pytest.mark.parametrize("command", ["positions", "summary"])
def test_naive_or_invalid_as_of_exits_one(command: str, value: str, tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = runner.invoke(app, ["portfolio", command, "--as-of", value, "--marks", str(_marks_file(tmp_path))])

    assert result.exit_code == 1
    assert "timezone-aware" in result.output or "not an ISO-8601 timestamp" in result.output


def test_missing_ledger_is_unavailable_and_creates_nothing(tmp_path: Path, isolated_ledger: Path) -> None:
    marks = _marks_file(tmp_path)

    result = _positions("--as-of", SNAPSHOT, "--marks", str(marks))

    assert result.exit_code == 1
    assert "does not exist" in result.output
    assert "never create" in result.output
    _assert_no_ledger_written(tmp_path, isolated_ledger)


def test_missing_marks_file_exits_one_and_creates_nothing(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    absent = tmp_path / "absent-marks.json"
    before = _ledger_snapshot(isolated_ledger)

    result = _positions("--as-of", SNAPSHOT, "--marks", str(absent))

    assert result.exit_code == 1
    assert "names a file that does not exist" in result.output
    assert not absent.exists()
    assert _ledger_snapshot(isolated_ledger) == before


def test_missing_statement_exits_one_and_creates_nothing(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    absent = tmp_path / "absent-statement.csv"
    before = _ledger_snapshot(isolated_ledger)

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(absent))

    assert result.exit_code == 1
    assert "names a file that does not exist" in result.output
    assert not absent.exists()
    assert _ledger_snapshot(isolated_ledger) == before


@pytest.mark.parametrize("value", ["2", "-1", "3"])
def test_max_stale_sessions_above_one_is_refused(value: str, tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = _positions("--as-of", SNAPSHOT, "--marks", str(_marks_file(tmp_path)), "--max-stale-sessions", value)

    assert result.exit_code == 1
    assert "must be 0 or 1" in result.output


def test_zero_session_bound_is_accepted_for_a_same_session_mark(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = _positions("--as-of", SNAPSHOT, "--marks", str(_marks_file(tmp_path)), "--max-stale-sessions", "0")

    assert result.exit_code == 0, result.output
    assert "NAV 2299.00" in result.stdout
    assert "bound 0" in result.stdout


def test_reconcile_reports_matched_lines_and_exits_zero(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(_statement_file(tmp_path)))

    assert result.exit_code == 0, result.output
    assert "cash: statement 924.00, ledger 924.00, delta 0.00, tolerance 0.01, matched" in result.stdout
    assert "quantity XNAS:" in result.stdout
    assert "tolerance 0.00000001, matched" in result.stdout
    assert "overall: matched" in result.stdout
    assert "not tax certification" in result.stdout


def test_reconcile_mismatch_exits_one_and_never_adjusts_the_ledger(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    before = _ledger_snapshot(isolated_ledger)

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(_statement_file(tmp_path, quantity="9", cash="900")))

    assert result.exit_code == 1
    assert "delta -2" in result.stdout
    assert "delta -24.00" in result.stdout
    assert "overall: mismatched" in result.stdout
    assert "the ledger was not adjusted and no balancing event was inserted" in result.output
    assert _ledger_snapshot(isolated_ledger) == before


def test_reconcile_unavailable_exits_one(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    partial = tmp_path / "cash-only.csv"
    partial.write_text(f"account,as_of,line_type,market,mic,symbol,currency,quantity,cash\nmain,{SNAPSHOT},CASH,,,,USD,,924\n", encoding="utf-8")

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(partial))

    assert result.exit_code == 1
    assert "overall: unavailable" in result.stdout
    assert "omits an open ledger holding" in result.stdout


def test_reconcile_rejects_a_malformed_statement_without_echoing_values(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    broken = tmp_path / "broken.csv"
    broken.write_text(
        f"account,as_of,line_type,market,mic,symbol,currency,quantity,cash\nmain,{SNAPSHOT},CASH,,,,EUR,,{PRIVATE_PRICE}\n",
        encoding="utf-8",
    )

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(broken))

    assert result.exit_code == 1
    assert "rejected" in result.output
    assert PRIVATE_PRICE not in result.output
    assert SYMBOL not in result.output


def test_invalid_marks_file_is_refused_without_echoing_private_values(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    # A JSON number instead of decimal text: the rejected value must not be echoed.
    marks = _marks_file(tmp_path, price=float(PRIVATE_PRICE))

    result = _positions("--as-of", SNAPSHOT, "--marks", str(marks))

    assert result.exit_code == 1
    assert "decimal text" in result.output
    assert PRIVATE_PRICE not in result.output
    assert SYMBOL not in result.output


def test_read_paths_leave_the_ledger_bytes_and_permissions_unchanged(tmp_path: Path, isolated_ledger: Path) -> None:
    """Success and rejection paths both write nothing: no DB, journal, cache or mark file."""
    _seeded_ledger(tmp_path)
    marks = _marks_file(tmp_path)
    statement = _statement_file(tmp_path)
    before = _ledger_snapshot(isolated_ledger)

    invocations = [
        ["portfolio", "positions", "--as-of", SNAPSHOT, "--marks", str(marks)],
        ["portfolio", "summary", "--as-of", SNAPSHOT, "--marks", str(marks)],
        ["portfolio", "reconcile", "--as-of", SNAPSHOT, "--statement", str(statement)],
        # Rejection paths.
        ["portfolio", "positions", "--as-of", "2024-01-09T21:00:00Z", "--marks", str(marks)],
        ["portfolio", "summary", "--as-of", SNAPSHOT, "--marks", str(tmp_path / "absent.json")],
        ["portfolio", "reconcile", "--as-of", SNAPSHOT, "--statement", str(_statement_file(tmp_path, quantity="9", name="mismatch.csv"))],
        ["portfolio", "positions", "--as-of", "2024-01-05T21:00:00Z", "--max-stale-sessions", "5"],
    ]
    results = [runner.invoke(app, args) for args in invocations]

    assert [result.exit_code for result in results] == [0, 0, 0, 1, 1, 1, 1], [result.output for result in results]
    assert _ledger_snapshot(isolated_ledger) == before
    for suffix in COMPANION_SUFFIXES:
        assert list(tmp_path.rglob(f"*{suffix}")) == []


@pytest.mark.parametrize("command", ["positions", "summary"])
def test_incomplete_valuation_keeps_identities_out_of_stderr(command: str, tmp_path: Path, isolated_ledger: Path) -> None:
    """ADR-0026 decision 7: identities belong in the report, not in a value-free error message."""
    _seeded_ledger(tmp_path)
    empty_marks = tmp_path / "empty-marks.json"
    empty_marks.write_text(json.dumps({"schema_version": 1, "marks": [], "valuation_blocks": []}), encoding="utf-8")

    result = runner.invoke(app, ["portfolio", command, "--as-of", SNAPSHOT, "--marks", str(empty_marks)])

    assert result.exit_code == 1
    # The requested local report (stdout) names the affected instrument ...
    assert SYMBOL in result.stdout
    assert f"XNAS:{SYMBOL}" in result.stdout
    # ... while stderr carries only a value-free category tally.
    assert SYMBOL not in result.stderr
    assert "XNAS" not in result.stderr
    assert "cannot be valued" in result.stderr
    assert "missing_mark 1" in result.stderr
    assert "no private value is written to this message" in result.stderr


@pytest.mark.parametrize("command", ["positions", "summary"])
def test_missing_marks_option_exits_one(command: str, tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = runner.invoke(app, ["portfolio", command, "--as-of", SNAPSHOT])

    assert result.exit_code == 1
    assert "--marks is required" in result.output


def test_missing_statement_option_exits_one(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = runner.invoke(app, ["portfolio", "reconcile", "--as-of", SNAPSHOT])

    assert result.exit_code == 1
    assert "--statement is required" in result.output


def test_stale_valuation_stderr_is_value_free(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)

    result = runner.invoke(app, ["portfolio", "positions", "--as-of", "2024-01-09T21:00:00Z", "--marks", str(_marks_file(tmp_path))])

    assert result.exit_code == 1
    assert "stale_mark 1" in result.stderr
    assert SYMBOL not in result.stderr


def test_duplicate_statement_header_exits_one_without_a_traceback(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    duplicated = tmp_path / "duplicated.csv"
    duplicated.write_text(
        f"account,account,as_of,line_type,market,mic,symbol,currency,quantity,cash\nmain,main,{SNAPSHOT},CASH,,,,USD,,924\n",
        encoding="utf-8",
    )

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(duplicated))

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "repeats 2 column name" in result.output


def test_header_only_statement_exits_one_without_a_traceback(tmp_path: Path, isolated_ledger: Path) -> None:
    _seeded_ledger(tmp_path)
    empty = tmp_path / "header-only.csv"
    empty.write_text("account,as_of,line_type,market,mic,symbol,currency,quantity,cash\n", encoding="utf-8")

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(empty))

    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "no data rows" in result.output


def test_private_filename_is_never_echoed_in_an_error(tmp_path: Path, isolated_ledger: Path) -> None:
    """A user-supplied filename can embed a symbol or an account label: errors stay value-free."""
    _seeded_ledger(tmp_path)
    leaky = _marks_file(tmp_path, name=f"{SYMBOL}-{PRIVATE_ACCOUNT}-marks.json")

    marked = _positions("--as-of", SNAPSHOT, "--marks", str(leaky))
    missing = _positions("--as-of", SNAPSHOT, "--marks", str(tmp_path / f"{SYMBOL}-absent.json"))
    statement = _reconcile("--as-of", SNAPSHOT, "--statement", str(tmp_path / f"{SYMBOL}-absent.csv"))

    assert (marked.exit_code, missing.exit_code, statement.exit_code) == (0, 1, 1)
    for result in (missing, statement):
        assert SYMBOL not in result.output
        assert PRIVATE_ACCOUNT not in result.output
        assert "does not exist" in result.output


@pytest.mark.parametrize("command", ["positions", "summary"])
def test_unprojectable_stored_event_is_reported_without_ledger_text(command: str, tmp_path: Path, isolated_ledger: Path) -> None:
    """A stored row can pass import yet fail projection; its text must not reach stderr.

    The event contract does not check that ``cash_effect`` equals
    ``quantity*price+fees`` — the projection does. Such a row is stored with a
    user-supplied external ID, which a read surface must never repeat
    (ADR-0026 decision 7).
    """
    leaky = _row(_OPENING, external_id="PRIVATESYM")
    inconsistent = _row(_BUY, cash_effect="-600.00")  # 5 * 120 + 5 = 605
    path = _write_csv(tmp_path, [leaky, inconsistent])
    assert _import("--file", str(path)).exit_code == 0
    before = _ledger_snapshot(isolated_ledger)
    marks = _marks_file(tmp_path)

    result = runner.invoke(app, ["portfolio", command, "--as-of", SNAPSHOT, "--marks", str(marks)])

    assert result.exit_code == 1
    assert "cannot support this projection" in result.stderr
    assert "re-validate the source canonical CSV" in result.stderr
    assert "PRIVATESYM" not in result.stderr
    assert "PRIVATESYM" not in result.stdout
    assert "-600" not in result.stderr
    assert _ledger_snapshot(isolated_ledger) == before


def test_unprojectable_stored_event_is_reported_without_ledger_text_in_reconcile(tmp_path: Path, isolated_ledger: Path) -> None:
    leaky = _row(_OPENING, external_id="PRIVATESYM")
    inconsistent = _row(_BUY, cash_effect="-600.00")
    path = _write_csv(tmp_path, [leaky, inconsistent])
    assert _import("--file", str(path)).exit_code == 0

    result = _reconcile("--as-of", SNAPSHOT, "--statement", str(_statement_file(tmp_path)))

    assert result.exit_code == 1
    assert "cannot support this projection" in result.stderr
    assert "PRIVATESYM" not in result.stderr


def test_unsupported_ledger_version_is_reported_without_forwarding_metadata(tmp_path: Path, isolated_ledger: Path) -> None:
    """Store metadata comes from the private file: the read reports a fixed refusal."""
    import sqlite3

    _seeded_ledger(tmp_path)
    db_path = portfolio_db_path(isolated_ledger)
    before = _ledger_snapshot(isolated_ledger)
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("PRAGMA user_version=987654")
    finally:
        connection.close()

    result = _positions("--as-of", SNAPSHOT, "--marks", str(_marks_file(tmp_path)))

    assert result.exit_code == 1
    assert "refused this read" in result.stderr
    assert "LedgerStoreError" in result.stderr
    assert "987654" not in result.stderr
    assert "987654" not in result.stdout
    # The read neither recreated nor migrated the file: only the version changed.
    assert sorted(_ledger_snapshot(isolated_ledger)) == sorted(before)
