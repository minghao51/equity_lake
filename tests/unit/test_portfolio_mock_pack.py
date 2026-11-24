"""A realistic synthetic private-ledger pack: import, replay, reconcile.

`equity portfolio import` accepts exactly one format — the frozen canonical CSV (ADR-0024) — so
this pack is the import surface's realistic exercise, not a broker-export mapping. Everything
here is invented: a neutral ``main`` account label, three invented instruments, invented amounts.

The pack under ``tests/fixtures/portfolio/mock-broker/`` covers every approved event kind
(``OPENING`` cash and positions, ``BUY``, ``SELL``, ``DEPOSIT``, ``WITHDRAWAL``, ``FEE``,
``CASH_DIVIDEND`` with withholding netted, and a 4:1 ``SPLIT``), plus three statement snapshots
that must match and one that must not. The arithmetic is hand-checked in that directory's
README, and these tests assert the numbers rather than trusting the file.

The real-instance rehearsal in the residual plan stays `blocked-input`: a synthetic pack cannot
discharge "someone imported their own account". Nothing here reads ``data/``, the lake, or the
network, and the repository's ``data/portfolio`` is never created.
"""

from __future__ import annotations

import stat
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from click.testing import Result
from typer.testing import CliRunner

from equity_lake.cli.__main__ import app
from equity_lake.portfolio import LedgerStore, replay_ledger
from equity_lake.portfolio import store as portfolio_store
from equity_lake.portfolio.store import portfolio_db_path

runner = CliRunner()

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "portfolio" / "mock-broker"
LEDGER_CSV = FIXTURES / "ledger.csv"
STATEMENT_MARCH = FIXTURES / "statement-2024-03-29.csv"
STATEMENT_JUNE = FIXTURES / "statement-2024-06-14.csv"
STATEMENT_JUNE_21 = FIXTURES / "statement-2024-06-21.csv"
STATEMENT_DRIFT = FIXTURES / "statement-2024-06-14-quantity-drift.csv"
MARKS = FIXTURES / "marks-2024-06-14.json"

ACCOUNT = "main"
SNAPSHOT_MARCH = "2024-03-29T21:00:00Z"
SNAPSHOT_JUNE = "2024-06-14T21:00:00Z"
SNAPSHOT_JUNE_21 = "2024-06-21T21:00:00Z"
#: 16:00 America/New_York on a Friday — the exact XNAS close the mark must quote.
SNAPSHOT_VALUED = "2024-06-14T20:00:00Z"
AFTER_SPLIT = datetime(2024, 6, 10, 21, 0, tzinfo=UTC)
EVENT_COUNT = 10


def _text(result: Result) -> str:
    """Everything the command printed, whichever stream a Click version used."""
    return result.output + (getattr(result, "stderr", "") or "")


@pytest.fixture
def private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private root whose ledger path is redirected into ``tmp_path`` (the established seam)."""
    root = tmp_path / "private"
    root.mkdir()
    monkeypatch.setattr(portfolio_store, "PORTFOLIO_DB_PATH", portfolio_db_path(root))
    return root


def _import(private_root: Path) -> str:
    """Import the pack for real and return the command's output."""
    result = runner.invoke(app, ["portfolio", "import", "--file", str(LEDGER_CSV)])
    assert result.exit_code == 0, _text(result)
    return _text(result)


def _book(root: Path, as_of: datetime) -> dict[str, Decimal]:
    """Replay the stored events at one instant and return ``symbol -> quantity`` plus cash."""
    projection = replay_ledger(LedgerStore(path=portfolio_db_path(root)).load_events(), account=ACCOUNT, as_of=as_of)
    book = {position.instrument.symbol: position.quantity for position in projection.positions}
    book["__cash__"] = projection.cash
    return book


def test_the_dry_run_validates_every_event_and_writes_nothing(private_root: Path) -> None:
    """A dry run fully validates the pack and leaves no private state behind."""
    result = runner.invoke(app, ["portfolio", "import", "--file", str(LEDGER_CSV), "--dry-run"])

    assert result.exit_code == 0, _text(result)
    output = _text(result)
    assert f"{EVENT_COUNT} event(s), nothing was written" in output
    for kind in ("BUY 1", "CASH_DIVIDEND 1", "DEPOSIT 1", "FEE 1", "OPENING 3", "SELL 1", "SPLIT 1", "WITHDRAWAL 1"):
        assert kind in output, output
    assert "cash delta: 27474.50" in output
    assert "split events: 1" in output
    assert not portfolio_db_path(private_root).exists()
    ledger_dir = portfolio_db_path(private_root).parent
    assert not ledger_dir.exists() or not any(ledger_dir.iterdir())


def test_every_approved_event_kind_imports(private_root: Path) -> None:
    """All ten rows commit, including the split and the netted dividend."""
    output = _import(private_root)

    assert f"inserted {EVENT_COUNT}" in output, output
    assert portfolio_db_path(private_root).exists()
    assert stat.S_IMODE(portfolio_db_path(private_root).stat().st_mode) == 0o600


def test_a_second_identical_import_is_a_no_op(private_root: Path) -> None:
    """The idempotency key is ``(account, external_id)``: re-importing inserts nothing."""
    _import(private_root)
    output = _import(private_root)

    assert f"inserted 0, unchanged {EVENT_COUNT}" in output, output


def test_the_replayed_book_matches_the_march_statement(private_root: Path) -> None:
    """Opening state plus one buy and one netted dividend: 1019.00 cash, 100/50/40 shares."""
    _import(private_root)

    march = _book(private_root, datetime(2024, 3, 29, 21, 0, tzinfo=UTC))

    assert march["AAPL"] == Decimal(100)
    assert march["MSFT"] == Decimal(50)
    assert march["NVDA"] == Decimal(40)
    assert march["__cash__"] == Decimal("1019.00")


def test_the_split_quadruples_quantity_and_leaves_basis_alone(private_root: Path) -> None:
    """ADR-0029: the ratio adjusts shares, never the book cost."""
    _import(private_root)

    projection = replay_ledger(
        LedgerStore(path=portfolio_db_path(private_root)).load_events(),
        account=ACCOUNT,
        as_of=AFTER_SPLIT,
    )
    aapl = next(position for position in projection.positions if position.instrument.symbol == "AAPL")

    assert aapl.quantity == Decimal(400)
    assert aapl.basis == Decimal("15000.00")


def test_the_sale_realizes_gain_net_of_its_fee(private_root: Path) -> None:
    """Proceeds 18,990.50 against 17,500.00 basis realizes 1,490.50."""
    _import(private_root)

    projection = replay_ledger(
        LedgerStore(path=portfolio_db_path(private_root)).load_events(),
        account=ACCOUNT,
        as_of=datetime(2024, 6, 14, 21, 0, tzinfo=UTC),
    )

    assert projection.realized_gain == Decimal("1490.50")
    assert projection.net_dividend_income == Decimal("24.00")
    assert projection.cash == Decimal("29974.50")


@pytest.mark.parametrize(
    ("statement", "snapshot", "cash"),
    [
        (STATEMENT_MARCH, SNAPSHOT_MARCH, "1019.00"),
        (STATEMENT_JUNE, SNAPSHOT_JUNE, "29974.50"),
        (STATEMENT_JUNE_21, SNAPSHOT_JUNE_21, "27474.50"),
    ],
)
def test_reconcile_matches_each_statement_snapshot(private_root: Path, statement: Path, snapshot: str, cash: str) -> None:
    """Three snapshots agree, including the one after the withdrawal and the one after the split."""
    _import(private_root)

    result = runner.invoke(app, ["portfolio", "reconcile", "--as-of", snapshot, "--statement", str(statement)])

    assert result.exit_code == 0, _text(result)
    output = _text(result)
    assert f"cash: statement {cash}, ledger {cash}, delta 0.00, tolerance 0.01, matched" in output
    assert "overall: matched" in output


def test_a_statement_that_shows_an_unrecorded_drip_is_reported_not_absorbed(private_root: Path) -> None:
    """The broker claims 404 shares after the split; the ledger says 400 and stays 400."""
    _import(private_root)
    ledger_before = portfolio_db_path(private_root).read_bytes()

    result = runner.invoke(app, ["portfolio", "reconcile", "--as-of", SNAPSHOT_JUNE, "--statement", str(STATEMENT_DRIFT)])

    assert result.exit_code == 1, _text(result)
    output = _text(result)
    assert "quantity XNAS:AAPL: statement 404, ledger 400, delta 4, tolerance 0.00000001, mismatched" in output
    assert "overall: mismatched" in output
    assert "the ledger was not adjusted and no balancing event was inserted" in output
    assert portfolio_db_path(private_root).read_bytes() == ledger_before


def test_positions_value_the_book_from_stored_marks(private_root: Path) -> None:
    """A stored mark is the only way a position gets a value; marks are never fetched or guessed."""
    _import(private_root)

    result = runner.invoke(app, ["portfolio", "positions", "--as-of", SNAPSHOT_VALUED, "--marks", str(MARKS)])

    assert result.exit_code == 0, _text(result)
    output = _text(result)
    assert "XNAS:AAPL  quantity 400" in output
    assert "84800.00" in output  # 400 x 212.00
    assert "5240.00" in output  # 40 x 131.00
    assert "NAV 120014.50" in output
    assert "provenance synthetic" in output


def test_positions_refuse_without_a_marks_file(private_root: Path) -> None:
    """No mark producer exists by design: a missing --marks is an error, never a silent zero."""
    _import(private_root)

    result = runner.invoke(app, ["portfolio", "positions", "--as-of", SNAPSHOT_VALUED])

    assert result.exit_code == 1, _text(result)
    assert "--marks is required" in _text(result)
    assert "no files were written" in _text(result)


def test_summary_separates_flows_fees_and_dividend_income(private_root: Path) -> None:
    """External flows are never profit, fees are counted once, and dividend limits stay disclosed."""
    _import(private_root)

    result = runner.invoke(app, ["portfolio", "summary", "--as-of", SNAPSHOT_VALUED, "--marks", str(MARKS)])

    assert result.exit_code == 0, _text(result)
    output = _text(result)
    assert "deposits 10000.00, withdrawals 0.00, net 10000.00" in output
    assert "embedded 14.50, standalone 35.00" in output
    assert "net dividend income 24.00" in output
    assert "realized gain 1490.50" in output
    assert "gross dividend income and withholding unavailable" in output
    assert "NAV 120014.50" in output
