"""Canonical statement reconciliation contracts (P02b, ADR-0026 decision 6).

Numbers come from the ADR-0026 oracle and from the frozen tolerances
(0.00000001 share, 0.01 USD, inclusive, applied to unrounded differences).
Fixtures are invented CSV text held in memory; nothing reads a broker export, a
real account, the lake or ``data/``, and no price mark is involved.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.portfolio.models import BasisStatus, LedgerEvent, LedgerEventType  # type: ignore[import-untyped]
from equity_lake.portfolio.projections import LedgerProjection, replay_ledger  # type: ignore[import-untyped]
from equity_lake.portfolio.reconcile import (  # type: ignore[import-untyped]
    CASH_TOLERANCE,
    QUANTITY_TOLERANCE,
    REASON_SNAPSHOT,
    ReconcileLineType,
    ReconcileState,
    StatementError,
    parse_statement_file,
    parse_statement_text,
    reconcile,
)

ACCOUNT = "main"
INSTRUMENT = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAA")
OTHER_INSTRUMENT = InstrumentKey(market="us_equity", mic="XNYS", symbol="BBB")
_DIGEST = "0" * 64
ORACLE_AS_OF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)
SNAPSHOT = "2024-01-05T21:00:00Z"
HEADER = "account,as_of,line_type,market,mic,symbol,currency,quantity,cash\n"


def cash_row(amount: str, *, as_of: str = SNAPSHOT, account: str = ACCOUNT, currency: str = "USD") -> str:
    return f"{account},{as_of},CASH,,,,{currency},,{amount}\n"


def position_row(
    quantity: str,
    *,
    mic: str = "XNAS",
    symbol: str = "AAA",
    market: str = "us_equity",
    as_of: str = SNAPSHOT,
    account: str = ACCOUNT,
) -> str:
    return f"{account},{as_of},POSITION,{market},{mic},{symbol},USD,{quantity},\n"


def statement_text(*rows: str) -> str:
    return HEADER + "".join(rows)


def _event(
    sequence: int,
    kind: str,
    cash_effect: str,
    *,
    instrument: InstrumentKey | None = None,
    quantity: str | None = None,
    price: str | None = None,
    fees: str = "0",
    basis: str | None = None,
    basis_status: BasisStatus = BasisStatus.KNOWN,
    effective_at: str = "2024-01-02T15:00:00Z",
) -> LedgerEvent:
    effective = datetime.fromisoformat(effective_at)
    return LedgerEvent(
        parser_version="fixture-v1",
        source_digest=_DIGEST,
        account=ACCOUNT,
        external_source="fixture",
        external_id=f"evt-{sequence:04d}",
        event_type=LedgerEventType(kind),
        effective_at=effective,
        received_at=effective + timedelta(hours=6),
        sequence=sequence,
        instrument=instrument,
        cash_effect=Decimal(cash_effect),
        quantity=None if quantity is None else Decimal(quantity),
        price=None if price is None else Decimal(price),
        fees=Decimal(fees),
        basis=None if basis is None else Decimal(basis),
        basis_status=basis_status,
    )


def oracle_projection() -> LedgerProjection:
    """Cash 924, 11 shares of XNAS:AAA and 90 realized gain."""
    events = [
        _event(1, "OPENING", "1000", instrument=INSTRUMENT, quantity="10", price="100", basis="1000", effective_at="2024-01-02T15:00:00Z"),
        _event(2, "BUY", "-605", instrument=INSTRUMENT, quantity="5", price="120", fees="5", effective_at="2024-01-03T15:00:00Z"),
        _event(3, "SELL", "518", instrument=INSTRUMENT, quantity="4", price="130", fees="2", effective_at="2024-01-04T15:00:00Z"),
        _event(4, "CASH_DIVIDEND", "11", instrument=INSTRUMENT, effective_at="2024-01-04T16:00:00Z"),
    ]
    return replay_ledger(events, account=ACCOUNT, as_of=ORACLE_AS_OF)


class TestStatementParsing:
    """Structural problems reject the whole statement instead of being combined."""

    def test_valid_statement_parses(self) -> None:
        statement = parse_statement_text(statement_text(cash_row("924"), position_row("11")))

        assert statement.account == ACCOUNT
        assert statement.as_of == ORACLE_AS_OF
        assert statement.cash == Decimal("924")
        assert [(item.instrument, item.quantity) for item in statement.positions] == [(INSTRUMENT, Decimal("11"))]

    def test_zero_quantities_are_explicit_not_blank(self) -> None:
        statement = parse_statement_text(statement_text(cash_row("0"), position_row("0")))

        assert statement.positions[0].quantity == Decimal("0")

    def test_header_must_be_the_canonical_schema(self) -> None:
        with pytest.raises(StatementError, match="canonical schema"):
            parse_statement_text("account,as_of,line_type\n" + cash_row("924"))

    def test_extra_column_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="canonical schema"):
            parse_statement_text("account,as_of,line_type,market,mic,symbol,currency,quantity,cash,fees\n" + cash_row("924"))

    def test_row_field_count_mismatch_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="extra or missing fields"):
            parse_statement_text(statement_text("main,2024-01-05T21:00:00Z,CASH,,,USD,924\n"))

    def test_blank_account_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="account is blank"):
            parse_statement_text(statement_text(cash_row("924", account="")))

    def test_cash_row_must_not_carry_identity(self) -> None:
        with pytest.raises(StatementError, match="CASH row must leave market"):
            parse_statement_text(statement_text("main,2024-01-05T21:00:00Z,CASH,us_equity,XNAS,AAA,USD,,924\n"))

    def test_cash_row_requires_a_cash_amount(self) -> None:
        with pytest.raises(StatementError, match="requires its cash amount"):
            parse_statement_text(statement_text("main,2024-01-05T21:00:00Z,CASH,,,,USD,,\n"))

    def test_position_row_must_not_carry_cash(self) -> None:
        with pytest.raises(StatementError, match="POSITION row must leave cash blank"):
            parse_statement_text(statement_text(cash_row("924"), "main,2024-01-05T21:00:00Z,POSITION,us_equity,XNAS,AAA,USD,11,5\n"))

    def test_position_row_requires_a_quantity(self) -> None:
        with pytest.raises(StatementError, match="requires its quantity"):
            parse_statement_text(statement_text(cash_row("924"), "main,2024-01-05T21:00:00Z,POSITION,us_equity,XNAS,AAA,USD,,\n"))

    def test_incomplete_identity_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="identity is incomplete"):
            parse_statement_text(statement_text(cash_row("924"), "main,2024-01-05T21:00:00Z,POSITION,us_equity,,AAA,USD,11,\n"))

    def test_unsupported_market_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="market is not supported"):
            parse_statement_text(statement_text(cash_row("924"), position_row("11", market="cn_ashare")))

    @pytest.mark.parametrize("amount", ["-1", "-0.01"])
    def test_negative_values_are_rejected(self, amount: str) -> None:
        with pytest.raises(StatementError, match="non-negative"):
            parse_statement_text(statement_text(cash_row(amount)))

    def test_non_numeric_value_is_rejected_without_echoing_it(self) -> None:
        with pytest.raises(StatementError) as excinfo:
            parse_statement_text(statement_text(cash_row("nine hundred")))

        assert "nine hundred" not in str(excinfo.value)

    def test_unsupported_currency_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="currency is not supported"):
            parse_statement_text(statement_text(cash_row("924", currency="EUR")))

    def test_unknown_line_type_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="line_type is unknown"):
            parse_statement_text(statement_text("main,2024-01-05T21:00:00Z,TRADE,,,,USD,,1\n"))

    def test_duplicate_position_identity_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="repeats this position identity"):
            parse_statement_text(statement_text(cash_row("924"), position_row("11"), position_row("12")))

    def test_multiple_cash_rows_are_rejected(self) -> None:
        with pytest.raises(StatementError, match="more than one CASH row"):
            parse_statement_text(statement_text(cash_row("924"), cash_row("925")))

    def test_mixed_account_labels_are_rejected(self) -> None:
        with pytest.raises(StatementError, match="more than one account label"):
            parse_statement_text(statement_text(cash_row("924"), position_row("11", account="other")))

    def test_mixed_snapshot_instants_are_rejected(self) -> None:
        with pytest.raises(StatementError, match="disagree about the snapshot instant"):
            parse_statement_text(statement_text(cash_row("924"), position_row("11", as_of="2024-01-04T21:00:00Z")))

    def test_naive_snapshot_instant_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="timezone-aware"):
            parse_statement_text(statement_text(cash_row("924", as_of="2024-01-05T21:00:00")))

    def test_statement_without_a_cash_row_parses_as_unavailable(self) -> None:
        statement = parse_statement_text(statement_text(position_row("11")))

        assert statement.cash is None

    def test_missing_file_is_refused_and_not_created(self, tmp_path: Path) -> None:
        target = tmp_path / "private" / "statement.csv"

        with pytest.raises(StatementError, match="never creates"):
            parse_statement_file(target)

        assert not target.exists()
        assert not target.parent.exists()

    def test_non_utf8_file_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "statement.csv"
        target.write_bytes(b"\xff\xfeaccount\n")

        with pytest.raises(StatementError, match="UTF-8"):
            parse_statement_file(target)


class TestReconciliation:
    """Matched / mismatched / unavailable, with no ledger mutation and no balancing."""

    def test_exact_match(self) -> None:
        report = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("924"), position_row("11"))))

        assert report.state is ReconcileState.MATCHED
        assert report.matched is True
        assert [line.state for line in report.lines] == [ReconcileState.MATCHED, ReconcileState.MATCHED]

    def test_tolerances_are_inclusive_at_the_boundary(self) -> None:
        matched = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("924.01"), position_row("11.00000001"))))
        just_over = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("924.0100001"), position_row("11.00000002"))))

        assert matched.state is ReconcileState.MATCHED
        assert just_over.state is ReconcileState.MISMATCHED
        assert Decimal("0.00000001") == QUANTITY_TOLERANCE
        assert Decimal("0.01") == CASH_TOLERANCE

    def test_delta_is_signed_statement_minus_ledger(self) -> None:
        report = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("900"), position_row("9"))))

        quantity_line, cash_line = report.lines[1], report.lines[0]
        assert quantity_line.delta == Decimal("-2")
        assert cash_line.delta == Decimal("-24")
        assert quantity_line.statement_value == Decimal("9")
        assert quantity_line.ledger_value == Decimal("11")
        assert quantity_line.tolerance == QUANTITY_TOLERANCE
        assert cash_line.tolerance == CASH_TOLERANCE

    def test_reconciliation_reports_every_line_when_outcomes_differ(self) -> None:
        report = reconcile(
            oracle_projection(),
            parse_statement_text(statement_text(cash_row("924"), position_row("11"), position_row("3", mic="XNYS", symbol="BBB"))),
        )

        states = {line.label: line.state for line in report.lines}
        assert states == {"cash": ReconcileState.MATCHED, "XNAS:AAA": ReconcileState.MATCHED, "XNYS:BBB": ReconcileState.MISMATCHED}
        assert report.state is ReconcileState.MISMATCHED

    def test_statement_only_position_compares_with_ledger_zero(self) -> None:
        rows = statement_text(cash_row("924"), position_row("11"), position_row("2", mic="XNYS", symbol="BBB"))
        report = reconcile(oracle_projection(), parse_statement_text(rows))

        line = next(line for line in report.lines if line.label == "XNYS:BBB")
        assert line.ledger_value == Decimal("0")
        assert line.delta == Decimal("2")
        assert line.state is ReconcileState.MISMATCHED

    def test_explicit_zero_row_for_a_held_instrument_is_a_mismatch(self) -> None:
        report = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("924"), position_row("0"))))

        line = next(line for line in report.lines if line.instrument is not None)
        assert line.statement_value == Decimal("0")
        assert line.state is ReconcileState.MISMATCHED

    def test_omitted_ledger_holding_is_unavailable_not_zero(self) -> None:
        report = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("924"))))

        line = next(line for line in report.lines if line.instrument is not None)
        assert line.state is ReconcileState.UNAVAILABLE
        assert line.statement_value is None
        assert line.delta is None
        assert "omits an open ledger holding" in line.reason
        # Cash still matched, but overall availability cannot be claimed.
        assert report.lines[0].state is ReconcileState.MATCHED
        assert report.state is ReconcileState.UNAVAILABLE

    def test_missing_cash_line_makes_the_report_unavailable(self) -> None:
        report = reconcile(oracle_projection(), parse_statement_text(statement_text(position_row("11"))))

        cash_line = report.lines[0]
        assert cash_line.line_type is ReconcileLineType.CASH
        assert cash_line.state is ReconcileState.UNAVAILABLE
        assert cash_line.statement_value is None
        assert cash_line.ledger_value == Decimal("924")
        assert report.state is ReconcileState.UNAVAILABLE

    def test_snapshot_disagreement_is_unavailable_and_has_no_delta(self) -> None:
        earlier = "2024-01-04T21:00:00Z"
        rows = statement_text(cash_row("924", as_of=earlier), position_row("11", as_of=earlier))
        statement = parse_statement_text(rows)

        report = reconcile(oracle_projection(), statement)

        assert report.state is ReconcileState.UNAVAILABLE
        assert all(line.reason == REASON_SNAPSHOT for line in report.lines)
        assert all(line.delta is None for line in report.lines)

    def test_unavailable_takes_precedence_over_mismatched(self) -> None:
        report = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("1"), position_row("99"))))

        assert report.state is ReconcileState.MISMATCHED

        with_omission = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("1"))))

        assert with_omission.state is ReconcileState.UNAVAILABLE

    def test_account_label_mismatch_is_refused(self) -> None:
        statement = parse_statement_text(statement_text(cash_row("924", account="other"), position_row("11", account="other")))

        with pytest.raises(StatementError, match="account label differs"):
            reconcile(oracle_projection(), statement)

    def test_reconciliation_does_not_mutate_the_ledger(self) -> None:
        projection = oracle_projection()
        before = projection.model_dump()
        statement = parse_statement_text(statement_text(cash_row("1"), position_row("0")))

        report = reconcile(projection, statement)

        assert report.state is ReconcileState.MISMATCHED
        assert projection.model_dump() == before
        # No balancing event, mark adjustment or tolerance widening happened.
        assert report.quantity_tolerance == QUANTITY_TOLERANCE
        assert report.cash_tolerance == CASH_TOLERANCE

    def test_both_values_and_tolerance_are_always_reported(self) -> None:
        report = reconcile(oracle_projection(), parse_statement_text(statement_text(cash_row("1"), position_row("1"))))

        for line in report.lines:
            assert line.ledger_value is not None
            assert line.tolerance > Decimal("0")
            assert line.reason

    def test_empty_ledger_and_empty_book_agree(self) -> None:
        empty = replay_ledger([], account=ACCOUNT, as_of=ORACLE_AS_OF)

        report = reconcile(empty, parse_statement_text(statement_text(cash_row("0"))))

        assert report.state is ReconcileState.MATCHED
        assert len(report.lines) == 1

    def test_duplicate_header_column_is_rejected(self) -> None:
        """A repeated canonical column would be silently reinterpreted by ``dict(zip(...))``."""
        duplicated = "account,account,as_of,line_type,market,mic,symbol,currency,quantity,cash\n"
        row = f"{ACCOUNT},{ACCOUNT},{SNAPSHOT},CASH,,,,USD,,924\n"

        with pytest.raises(StatementError, match="repeats 2 column name"):
            parse_statement_text(duplicated + row)

    def test_duplicate_unexpected_header_column_is_rejected(self) -> None:
        duplicated = f"{HEADER.rstrip()},broker_note,broker_note\n"

        with pytest.raises(StatementError, match="canonical schema"):
            parse_statement_text(duplicated)

    def test_header_only_statement_is_rejected_not_crashed(self) -> None:
        """A statement with no data rows has no snapshot instant: reject, never raise KeyError."""
        with pytest.raises(StatementError, match="no data rows"):
            parse_statement_text(HEADER)

    def test_all_blank_statement_is_rejected(self) -> None:
        with pytest.raises(StatementError, match="no data rows"):
            parse_statement_text(HEADER + "\n,\n")
