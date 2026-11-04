"""CLI contracts for ``equity portfolio import`` (P01b, ADR-0024 decision 13).

Fixtures are invented: a neutral ``main`` account label, an invented instrument and
invented amounts. No broker file, no real account data, no network and no lake write.

Every test runs against a private ledger redirected into the test temp directory
(``monkeypatch`` on :data:`equity_lake.portfolio.store.PORTFOLIO_DB_PATH`, read at
call time by ``portfolio_db_path``), so a test can never touch the repository's
``data/portfolio`` or real account data.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import pytest
import typer
from click.testing import Result
from structlog.testing import capture_logs
from typer.testing import CliRunner

from equity_lake.cli import _app as cli_app  # type: ignore[import-untyped]
from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]
from equity_lake.cli._app import portfolio_app  # type: ignore[import-untyped]
from equity_lake.core import paths as core_paths  # type: ignore[import-untyped]
from equity_lake.core.paths import PORTFOLIO_DIR  # type: ignore[import-untyped]
from equity_lake.portfolio import store as portfolio_store  # type: ignore[import-untyped]
from equity_lake.portfolio.csv_import import CANONICAL_CSV_COLUMNS  # type: ignore[import-untyped]
from equity_lake.portfolio.store import LedgerStore, portfolio_db_path  # type: ignore[import-untyped]

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
    assert f"quantity delta by instrument: XNAS:{SYMBOL} 11" in result.stdout
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


def test_only_import_is_registered() -> None:
    """The frozen surface is not faked: no P02 command is registered, stubbed or hinted."""
    registered = {command.name for command in portfolio_app.registered_commands}
    assert registered == {"import"}
    assert portfolio_app.registered_groups == []

    help_result = runner.invoke(app, ["portfolio", "--help"], env={"COLUMNS": "180"})
    assert help_result.exit_code == 0, help_result.output
    assert "import" in help_result.stdout
    for absent in ("positions", "summary", "reconcile"):
        assert absent not in help_result.stdout
        invocation = runner.invoke(app, ["portfolio", absent])
        assert invocation.exit_code != 0, (absent, invocation.output)


def test_portfolio_group_help_is_listed_on_the_unified_cli() -> None:
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "180"})

    assert result.exit_code == 0, result.output
    assert "portfolio" in result.stdout
