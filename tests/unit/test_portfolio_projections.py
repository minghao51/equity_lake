"""Deterministic replay contracts for the private ledger (P02a, ADR-0026 decisions 2-4).

Every expected number here is derived by hand from the ADR-0024/ADR-0026
acceptance oracle and from exact decimal arithmetic — never by calling the
implementation and freezing its output. Fixtures are invented: a neutral ``main``
account, invented instruments and amounts. No database, file, network or clock is
touched by this module.

The oracle (ADR-0026 decision 8):

| Step | Expected |
| --- | --- |
| Open 10 shares, basis 1000, cash 1000 | quantity 10, cash 1000, basis 1000 |
| Buy 5 at 120, fee 5 | quantity 15, cash 395, basis 1605 |
| Sell 4 at 130, fee 2 | quantity 11, cash 913, basis 1177, realized gain 90 |
| Net dividend 11, mark 125 | cash 924, value 1375, NAV 2299, unrealized 198, total 299 |
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.portfolio.models import BasisStatus, LedgerEvent, LedgerEventType  # type: ignore[import-untyped]
from equity_lake.portfolio.projections import (  # type: ignore[import-untyped]
    PARTIAL_SALE_BASIS_FRACTIONAL_DIGITS,
    LedgerProjection,
    ProjectionError,
    average_cost_display,
    money_display,
    replay_ledger,
    round_half_even,
)

ACCOUNT = "main"
INSTRUMENT = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAA")
OTHER_INSTRUMENT = InstrumentKey(market="us_equity", mic="XNYS", symbol="BBB")
_DIGEST = "0" * 64
# Final valuation instant of the oracle: Friday 2024-01-05 session close.
ORACLE_AS_OF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)


def event(
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
    account: str = ACCOUNT,
    external_id: str | None = None,
) -> LedgerEvent:
    """One invented ledger event; decimal text in, exact ``Decimal`` out."""
    effective = datetime.fromisoformat(effective_at)
    return LedgerEvent(
        parser_version="fixture-v1",
        source_digest=_DIGEST,
        account=account,
        external_source="fixture",
        external_id=external_id or f"evt-{sequence:04d}",
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


def oracle_events() -> list[LedgerEvent]:
    return [
        event(1, "OPENING", "1000", instrument=INSTRUMENT, quantity="10", price="100", basis="1000", effective_at="2024-01-02T15:00:00Z"),
        event(2, "BUY", "-605", instrument=INSTRUMENT, quantity="5", price="120", fees="5", effective_at="2024-01-03T15:00:00Z"),
        event(3, "SELL", "518", instrument=INSTRUMENT, quantity="4", price="130", fees="2", effective_at="2024-01-04T15:00:00Z"),
        event(4, "CASH_DIVIDEND", "11", instrument=INSTRUMENT, effective_at="2024-01-04T16:00:00Z"),
    ]


def replay_at(moment: datetime, events: list[LedgerEvent]) -> LedgerProjection:
    return replay_ledger(events, account=ACCOUNT, as_of=moment)


class TestHandOracle:
    """The ADR-0026 decision 8 oracle, step by step."""

    def test_opening_step(self) -> None:
        projection = replay_at(datetime(2024, 1, 2, 20, 0, tzinfo=UTC), oracle_events())
        position = projection.positions[0]

        assert (position.quantity, projection.cash, position.basis) == (Decimal("10"), Decimal("1000"), Decimal("1000"))

    def test_buy_step_after_fee(self) -> None:
        projection = replay_at(datetime(2024, 1, 3, 20, 0, tzinfo=UTC), oracle_events())
        position = projection.positions[0]

        # 1000 cash - (5 * 120 + 5 fee) = 395; basis 1000 + 605 = 1605.
        assert (position.quantity, projection.cash, position.basis) == (Decimal("15"), Decimal("395"), Decimal("1605"))

    def test_sell_step_removes_proportional_basis(self) -> None:
        projection = replay_at(datetime(2024, 1, 4, 15, 30, tzinfo=UTC), oracle_events())
        position = projection.positions[0]

        # 395 + (4 * 130 - 2 fee) = 913; basis 1605 * 4/15 = 428 removed exactly;
        # realized 518 - 428 = 90.
        assert (position.quantity, projection.cash, position.basis) == (Decimal("11"), Decimal("913"), Decimal("1177"))
        assert position.realized_gain == Decimal("90")
        assert projection.realized_gain == Decimal("90")

    def test_dividend_is_income_not_basis(self) -> None:
        projection = replay_at(datetime(2024, 1, 4, 20, 0, tzinfo=UTC), oracle_events())
        position = projection.positions[0]

        assert projection.cash == Decimal("924")
        assert projection.net_dividend_income == Decimal("11")
        assert position.basis == Decimal("1177")

    def test_final_nav_unrealized_and_total_gain(self) -> None:
        projection = replay_at(ORACLE_AS_OF, oracle_events())
        position = projection.positions[0]

        # Hand oracle: 11 shares marked at 125 -> 1375 value.
        basis = position.basis
        realized = projection.realized_gain
        assert basis is not None and realized is not None
        value = Decimal("11") * Decimal("125")
        unrealized = value - basis
        total_gain = realized + unrealized + projection.net_dividend_income - projection.standalone_fees

        assert value == Decimal("1375")
        assert projection.cash + value == Decimal("2299")
        assert unrealized == Decimal("198")
        assert total_gain == Decimal("299")

    def test_average_cost_is_derived_not_accumulated(self) -> None:
        projection = replay_at(ORACLE_AS_OF, oracle_events())

        # 1177 / 11 = 107 exactly; the display value never feeds the replay.
        assert projection.positions[0].average_cost == Decimal("107.00")


class TestPrecision:
    """Exact arithmetic, single-rounding basis removal and full-close conservation."""

    def test_partial_sale_repeating_cost_rounds_half_even_at_38_digits(self) -> None:
        events = [
            event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="3", basis="100"),
            event(2, "SELL", "50", instrument=INSTRUMENT, quantity="1", price="50", effective_at="2024-01-03T15:00:00Z"),
        ]
        position = replay_at(datetime(2024, 1, 4, 21, 0, tzinfo=UTC), events).positions[0]

        # 100 * 1/3 = 33.333... exactly; rounded once to 38 fractional digits.
        # Literals, not Decimal arithmetic: the process-global context would round
        # this subtraction to 28 digits, which is exactly what the replay module
        # refuses to depend on.
        assert position.basis == Decimal("66.66666666666666666666666666666666666667")
        assert position.realized_gain == Decimal("16.66666666666666666666666666666666666667")

    def test_halfway_case_rounds_to_even_downward(self) -> None:
        # 1 * 1 / (2E38) = 0.5E-38: exactly halfway between 0 and 1E-38.
        events = [
            event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="2E+38", basis="1"),
            event(2, "SELL", "1", instrument=INSTRUMENT, quantity="1", price="1", effective_at="2024-01-03T15:00:00Z"),
        ]
        position = replay_at(datetime(2024, 1, 4, 21, 0, tzinfo=UTC), events).positions[0]

        assert position.realized_gain == Decimal("1")
        assert position.basis == Decimal("1")

    def test_halfway_case_rounds_to_even_upward(self) -> None:
        # 3 * 1 / (2E38) = 1.5E-38: halfway between 1E-38 and 2E-38; 2 is even.
        events = [
            event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="2E+38", basis="3"),
            event(2, "SELL", "1", instrument=INSTRUMENT, quantity="1", price="1", effective_at="2024-01-03T15:00:00Z"),
        ]
        position = replay_at(datetime(2024, 1, 4, 21, 0, tzinfo=UTC), events).positions[0]

        assert position.basis == Decimal("2.99999999999999999999999999999999999998")
        assert position.realized_gain == Decimal("0.99999999999999999999999999999999999998")

    def test_full_liquidation_consumes_exact_residual(self) -> None:
        events = [
            event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="3", basis="100"),
            event(2, "SELL", "90", instrument=INSTRUMENT, quantity="3", price="30", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_at(datetime(2024, 1, 4, 21, 0, tzinfo=UTC), events)

        # The residual basis is consumed exactly, not left as a 1/3 rounding crumb.
        assert projection.positions == ()
        assert projection.realized_gain == Decimal("90") - Decimal("100") == Decimal("-10")

    def test_maximum_precision_inputs_are_retained_exactly(self) -> None:
        quantity = "1.2345678901234567890123456789012345678"
        price = "2"
        # 38 significant digits in, 38 significant digits out: hand-computed literal.
        product = Decimal("2.4691357802469135780246913578024691356")
        events = [
            event(1, "DEPOSIT", "10"),
            event(2, "BUY", "-2.4691357802469135780246913578024691356", instrument=INSTRUMENT, quantity=quantity, price=price),
        ]

        position = replay_at(datetime(2024, 1, 4, 21, 0, tzinfo=UTC), events).positions[0]

        assert position.basis == product
        assert position.quantity == Decimal(quantity)

    def test_a_stored_money_value_above_the_digit_bound_is_refused(self) -> None:
        # The event boundary keeps stored money within 38 significant digits, so
        # the replay's exact-arithmetic guard is defence in depth rather than a
        # licence to round: a 76-digit stored amount never reaches it.
        oversized = "-12.19326311370217952261850327338667885854747751864349946654322511812221002896"

        with pytest.raises(Exception, match="significant digit"):
            event(1, "BUY", oversized, instrument=INSTRUMENT, quantity="1", price="1")

    def test_round_half_even_boundaries(self) -> None:
        assert round_half_even(Fraction("1.25"), 1) == Decimal("1.2")
        assert round_half_even(Fraction("1.35"), 1) == Decimal("1.4")
        assert round_half_even(Fraction("-1.25"), 1) == Decimal("-1.2")
        assert round_half_even(Fraction(1, 3), 38) == Decimal("0.33333333333333333333333333333333333333")
        assert round_half_even(Fraction(2, 3), 38) == Decimal("0.66666666666666666666666666666666666667")
        assert PARTIAL_SALE_BASIS_FRACTIONAL_DIGITS == 38

    def test_display_rounding_is_half_even_and_never_a_replay_input(self) -> None:
        assert money_display(Decimal("2.345")) == Decimal("2.34")
        assert money_display(Decimal("2.355")) == Decimal("2.36")
        assert average_cost_display(Decimal("1177"), Decimal("11")) == Decimal("107.00")
        assert average_cost_display(None, Decimal("11")) is None


class TestUnknownBasis:
    """An unknown pool stays unavailable; it is never zero and never repaired."""

    def unknown_opening(self, *, sequence: int = 1, effective_at: str = "2024-01-02T15:00:00Z") -> LedgerEvent:
        return event(
            sequence,
            "OPENING",
            "1000",
            instrument=INSTRUMENT,
            quantity="10",
            price="100",
            basis_status=BasisStatus.UNKNOWN,
            effective_at=effective_at,
        )

    def test_unknown_basis_keeps_quantity_and_cash_known(self) -> None:
        position = replay_at(ORACLE_AS_OF, [self.unknown_opening()]).positions[0]

        assert position.quantity == Decimal("10")
        assert position.basis is None
        assert position.basis_is_known is False
        assert position.average_cost is None

    def test_later_purchase_does_not_repair_the_pool(self) -> None:
        events = [
            self.unknown_opening(),
            event(2, "BUY", "-605", instrument=INSTRUMENT, quantity="5", price="120", fees="5", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_at(ORACLE_AS_OF, events)

        assert projection.positions[0].quantity == Decimal("15")
        assert projection.positions[0].basis is None
        assert projection.cash == Decimal("395")

    def test_partial_sale_from_unknown_pool_has_no_realized_gain(self) -> None:
        events = [
            self.unknown_opening(),
            event(2, "SELL", "518", instrument=INSTRUMENT, quantity="4", price="130", fees="2", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_at(ORACLE_AS_OF, events)

        assert projection.positions[0].basis is None
        assert projection.positions[0].realized_gain is None
        assert projection.realized_gain is None

    def test_liquidation_and_reopening_leave_history_unavailable(self) -> None:
        events = [
            self.unknown_opening(),
            event(2, "SELL", "1000", instrument=INSTRUMENT, quantity="10", price="100", effective_at="2024-01-03T15:00:00Z"),
            event(3, "BUY", "-400", instrument=INSTRUMENT, quantity="4", price="100", effective_at="2024-01-04T15:00:00Z"),
        ]
        projection = replay_at(ORACLE_AS_OF, events)
        position = projection.positions[0]

        # The closed pool consumed nothing and left no residual; the reopened pool
        # has a known basis, but the instrument's realized history stays unavailable.
        assert position.quantity == Decimal("4")
        assert position.basis == Decimal("400")
        assert position.realized_gain is None
        assert projection.realized_gain is None

    def test_one_unknown_instrument_makes_the_aggregate_unavailable(self) -> None:
        events = [
            event(1, "OPENING", "0", instrument=OTHER_INSTRUMENT, quantity="1", basis="10"),
            event(2, "SELL", "20", instrument=OTHER_INSTRUMENT, quantity="1", price="20", effective_at="2024-01-03T15:00:00Z"),
            self.unknown_opening(sequence=3, effective_at="2024-01-03T16:00:00Z"),
            event(4, "SELL", "518", instrument=INSTRUMENT, quantity="4", price="130", fees="2", effective_at="2024-01-04T15:00:00Z"),
        ]
        projection = replay_at(ORACLE_AS_OF, events)

        assert projection.realized_gain is None
        # The known component is still visible on its own position.
        assert projection.positions[0].instrument == INSTRUMENT
        assert projection.positions[0].realized_gain is None


class TestTemporalReconstruction:
    """Effective-date cutoffs, ordering and refusal of ambiguous timelines."""

    def test_event_at_the_instant_is_included(self) -> None:
        moment = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
        projection = replay_at(moment, oracle_events())

        assert projection.replayed_event_count == 1
        assert projection.cash == Decimal("1000")

    def test_one_second_earlier_excludes_the_event(self) -> None:
        moment = datetime(2024, 1, 2, 14, 59, 59, tzinfo=UTC)
        projection = replay_at(moment, oracle_events())

        assert projection.replayed_event_count == 0
        assert projection.positions == ()
        assert projection.cash == Decimal("0")

    def test_received_at_is_provenance_not_a_cutoff(self) -> None:
        # received_at is six hours after effective_at in every fixture, so a
        # receipt-time ordering would drop both events below.
        moment = datetime(2024, 1, 3, 16, 0, tzinfo=UTC)
        projection = replay_at(moment, oracle_events())

        assert projection.replayed_event_count == 2
        assert projection.cash == Decimal("395")

    def test_offset_aware_instant_is_normalised_to_utc(self) -> None:
        from datetime import timezone

        offset_moment = datetime(2024, 1, 3, 17, 0, tzinfo=timezone(timedelta(hours=1)))

        projection = replay_at(offset_moment, oracle_events())

        assert projection.as_of == datetime(2024, 1, 3, 16, 0, tzinfo=UTC)
        assert projection.replayed_event_count == 2

    def test_naive_instant_is_refused(self) -> None:
        with pytest.raises(ProjectionError, match="timezone-aware"):
            replay_at(datetime(2024, 1, 3, 16, 0), oracle_events())

    def test_temporal_inversion_in_sequence_order_is_refused(self) -> None:
        events = [
            event(1, "OPENING", "1000", instrument=INSTRUMENT, quantity="10", basis="1000", effective_at="2024-01-04T15:00:00Z"),
            event(2, "BUY", "-605", instrument=INSTRUMENT, quantity="5", price="120", fees="5", effective_at="2024-01-03T15:00:00Z"),
        ]

        with pytest.raises(ProjectionError, match="temporal inversion"):
            replay_at(ORACLE_AS_OF, events)

    def test_duplicate_external_id_is_refused(self) -> None:
        events = [event(1, "OPENING", "1000"), event(2, "DEPOSIT", "10", external_id="evt-0001")]

        with pytest.raises(ProjectionError, match="external ID"):
            replay_at(ORACLE_AS_OF, events)

    def test_duplicate_source_sequence_is_refused(self) -> None:
        first = event(1, "OPENING", "1000")
        second = event(1, "DEPOSIT", "10", external_id="evt-9999")

        with pytest.raises(ProjectionError, match="source sequence"):
            replay_at(ORACLE_AS_OF, [first, second])

    def test_multiple_accounts_are_refused(self) -> None:
        events = [event(1, "OPENING", "1000"), event(2, "DEPOSIT", "10", account="other")]

        with pytest.raises(ProjectionError, match="more than one account"):
            replay_at(ORACLE_AS_OF, events)

    def test_unknown_account_label_is_refused(self) -> None:
        with pytest.raises(ProjectionError, match="no events for the requested account"):
            replay_ledger(oracle_events(), account="someone-else", as_of=ORACLE_AS_OF)

    def test_file_order_and_restart_invariance(self) -> None:
        forward = replay_at(ORACLE_AS_OF, oracle_events())
        reversed_order = replay_at(ORACLE_AS_OF, list(reversed(oracle_events())))
        shuffled = replay_at(ORACLE_AS_OF, [oracle_events()[2], oracle_events()[0], oracle_events()[3], oracle_events()[1]])

        assert forward == reversed_order == shuffled

    def test_empty_timeline_projects_an_empty_book(self) -> None:
        projection = replay_at(ORACLE_AS_OF, [])

        assert projection.positions == ()
        assert (projection.cash, projection.replayed_event_count) == (Decimal("0"), 0)
        assert projection.realized_gain == Decimal("0")


class TestRefusals:
    """Stored rows that cannot support a valid projection are refused, never edited."""

    def test_oversell_is_refused(self) -> None:
        events = [
            event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="1", basis="10"),
            event(2, "SELL", "20", instrument=INSTRUMENT, quantity="2", price="10", effective_at="2024-01-03T15:00:00Z"),
        ]

        with pytest.raises(ProjectionError, match="oversell"):
            replay_at(ORACLE_AS_OF, events)

    def test_buy_cash_relationship_mismatch_is_refused(self) -> None:
        events = [event(1, "BUY", "-600", instrument=INSTRUMENT, quantity="5", price="120", fees="5")]

        with pytest.raises(ProjectionError, match="cash_effect does not equal"):
            replay_at(ORACLE_AS_OF, events)

    def test_sell_cash_relationship_mismatch_is_refused(self) -> None:
        events = [
            event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="5", basis="500"),
            event(2, "SELL", "600", instrument=INSTRUMENT, quantity="5", price="120", fees="5", effective_at="2024-01-03T15:00:00Z"),
        ]

        with pytest.raises(ProjectionError, match="cash_effect does not equal"):
            replay_at(ORACLE_AS_OF, events)

    def test_standalone_fee_disagreement_is_refused(self) -> None:
        events = [event(1, "DEPOSIT", "100"), event(2, "FEE", "-5", fees="7", effective_at="2024-01-03T15:00:00Z")]

        with pytest.raises(ProjectionError, match="descriptive fees disagree"):
            replay_at(ORACLE_AS_OF, events)

    def test_negative_cash_state_is_refused(self) -> None:
        events = [event(1, "DEPOSIT", "10"), event(2, "WITHDRAWAL", "-11", effective_at="2024-01-03T15:00:00Z")]

        with pytest.raises(ProjectionError, match="negative cash balance"):
            replay_at(ORACLE_AS_OF, events)

    def test_refused_timeline_leaves_the_events_untouched(self) -> None:
        events = [event(1, "BUY", "-600", instrument=INSTRUMENT, quantity="5", price="120", fees="5")]
        before = [event.content_digest for event in events]

        with pytest.raises(ProjectionError):
            replay_at(ORACLE_AS_OF, events)

        assert [event.content_digest for event in events] == before


class TestFeeAndFlowAccounting:
    """Fees are counted once; external flows are never profit."""

    def test_embedded_fees_are_reported_but_not_deducted_twice(self) -> None:
        projection = replay_at(ORACLE_AS_OF, oracle_events())
        position = projection.positions[0]

        # 5 (buy) + 2 (sell) embedded fees; realized gain 90 already nets the sale fee.
        assert projection.embedded_fees == Decimal("7")
        assert projection.standalone_fees == Decimal("0")
        assert projection.total_fees == Decimal("7")
        assert position.embedded_fees == Decimal("7")
        assert position.realized_gain == Decimal("90")

    def test_standalone_fee_is_one_expense_with_no_second_charge(self) -> None:
        events = [
            event(1, "OPENING", "1000", instrument=INSTRUMENT, quantity="10", basis="1000"),
            event(2, "FEE", "-5", fees="5", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_at(ORACLE_AS_OF, events)

        assert projection.cash == Decimal("995")
        assert projection.standalone_fees == Decimal("5")
        assert projection.total_fees == Decimal("5")

    def test_deposits_and_withdrawals_are_external_flows(self) -> None:
        events = [
            event(1, "DEPOSIT", "500"),
            event(2, "WITHDRAWAL", "-200", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_at(ORACLE_AS_OF, events)

        assert projection.cash == Decimal("300")
        assert (projection.deposits, projection.withdrawals, projection.net_external_flow) == (
            Decimal("500"),
            Decimal("200"),
            Decimal("300"),
        )
        assert projection.net_dividend_income == Decimal("0")
        # No position and no disposal: nothing here is investment profit.
        assert projection.realized_gain == Decimal("0")

    def test_dividend_fees_are_disclosed_not_treated_as_withholding(self) -> None:
        events = [event(1, "CASH_DIVIDEND", "11", instrument=INSTRUMENT, fees="2")]
        projection = replay_at(ORACLE_AS_OF, events)

        assert projection.cash == Decimal("11")
        assert projection.net_dividend_income == Decimal("11")
        assert projection.dividend_fees == Decimal("2")


class TestPrivacy:
    """Error text never discloses holdings, amounts or symbols."""

    def test_refusal_messages_carry_no_amount_or_symbol(self) -> None:
        events = [event(1, "BUY", "-600", instrument=INSTRUMENT, quantity="5", price="120", fees="5")]

        with pytest.raises(ProjectionError) as excinfo:
            replay_at(ORACLE_AS_OF, events)

        message = str(excinfo.value)
        assert "AAA" not in message
        assert "600" not in message
        assert "120" not in message
        assert "evt-0001" in message  # the external ID is the actionable handle


class TestAggregateExactnessAndFeeDisclosure:
    """Aggregation is arithmetic too, and fees belong to the book, not the holdings.

    Both cases below are regressions: they fail if aggregation runs under the
    process-global Decimal context, or if embedded fees are summed from surviving
    positions instead of from every replayed event.
    """

    def test_aggregate_realized_gain_is_exact_beyond_the_global_precision(self) -> None:
        from decimal import Context, localcontext

        events = [
            # Instrument A realizes 1E+30 (basis 0, sold at 1E+30).
            event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="1", basis="0"),
            event(2, "SELL", "1E+30", instrument=INSTRUMENT, quantity="1", price="1E+30", effective_at="2024-01-03T15:00:00Z"),
            # Instrument B realizes 1 (basis 0, sold at 1).
            event(3, "OPENING", "0", instrument=OTHER_INSTRUMENT, quantity="1", basis="0", effective_at="2024-01-04T15:00:00Z"),
            event(4, "SELL", "1", instrument=OTHER_INSTRUMENT, quantity="1", price="1", effective_at="2024-01-04T16:00:00Z"),
        ]

        # A hostile process-global precision must not be able to collapse the sum.
        with localcontext(Context(prec=6)):
            projection = replay_at(ORACLE_AS_OF, events)

        assert projection.realized_gain == Decimal("1000000000000000000000000000001")
        assert projection.cash == Decimal("1000000000000000000000000000001")
        assert projection.positions == ()

    def test_aggregate_realized_gain_stays_unavailable_next_to_a_huge_known_gain(self) -> None:
        """Companion invariant, not a regression of the precision defect.

        ``None``-stickiness already held before the aggregation fix; this test
        pins the *interaction* (an unknown component must not be swallowed by a
        large known one) so the precision fix cannot trade one for the other.
        The precision regression is ``test_aggregate_realized_gain_is_exact_...``.
        """
        from decimal import Context, localcontext

        events = [
            event(
                1,
                "OPENING",
                "0",
                instrument=OTHER_INSTRUMENT,
                quantity="1",
                basis_status=BasisStatus.UNKNOWN,
                effective_at="2024-01-02T14:00:00Z",
            ),
            event(2, "SELL", "1", instrument=OTHER_INSTRUMENT, quantity="1", price="1", effective_at="2024-01-02T15:00:00Z"),
            event(3, "OPENING", "0", instrument=INSTRUMENT, quantity="1", basis="0", effective_at="2024-01-03T15:00:00Z"),
            event(4, "SELL", "1E+30", instrument=INSTRUMENT, quantity="1", price="1E+30", effective_at="2024-01-04T15:00:00Z"),
        ]

        with localcontext(Context(prec=6)):
            projection = replay_at(ORACLE_AS_OF, events)

        # An unknown component must never be swallowed by a known subtotal.
        assert projection.realized_gain is None

    def test_embedded_fees_survive_full_liquidation(self) -> None:
        events = [
            event(1, "OPENING", "20", instrument=None),
            event(2, "BUY", "-11", instrument=INSTRUMENT, quantity="1", price="10", fees="1", effective_at="2024-01-03T15:00:00Z"),
            event(3, "SELL", "9", instrument=INSTRUMENT, quantity="1", price="10", fees="1", effective_at="2024-01-04T15:00:00Z"),
        ]

        projection = replay_at(ORACLE_AS_OF, events)

        # The position is closed, so no per-position fee remains to sum: the book
        # total must still disclose the 1 + 1 embedded fees it paid.
        assert projection.positions == ()
        assert projection.embedded_fees == Decimal("2")
        assert projection.standalone_fees == Decimal("0")
        assert projection.total_fees == Decimal("2")
        # 20 opening cash - 11 (buy with fee) + 9 (sale net of fee) = 18.
        assert projection.cash == Decimal("18")


class TestDerivedTotalsExactness:
    """Derived totals must use one explicit precision, exactly like the replay.

    Regression: `net_external_flow` and `total_fees` are computed properties, so
    they need their own explicit context; plain Decimal arithmetic there would
    follow whatever the process-global context happens to be.
    """

    def test_fee_and_flow_totals_are_exact_under_a_hostile_global_precision(self) -> None:
        from decimal import Context, localcontext

        events = [
            event(1, "DEPOSIT", "1E+31", effective_at="2024-01-02T15:00:00Z"),
            event(2, "WITHDRAWAL", "-1", effective_at="2024-01-02T16:00:00Z"),
            event(3, "BUY", "-2E+30", instrument=INSTRUMENT, quantity="1", price="1E+30", fees="1E+30", effective_at="2024-01-03T15:00:00Z"),
            event(4, "FEE", "-1", fees="1", effective_at="2024-01-04T15:00:00Z"),
        ]

        with localcontext(Context(prec=6)):
            projection = replay_at(ORACLE_AS_OF, events)
            net_flow = projection.net_external_flow
            total_fees = projection.total_fees

        # 1E+31 - 1 and 1E+30 + 1 both need more than 6 (or 28) digits.
        assert net_flow == Decimal("9999999999999999999999999999999")
        assert total_fees == Decimal("1000000000000000000000000000001")
        assert projection.embedded_fees == Decimal("1000000000000000000000000000000")
        assert projection.standalone_fees == Decimal("1")
