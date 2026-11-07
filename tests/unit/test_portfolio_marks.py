"""Stored book marks and as-of valuation contracts (P02b, ADR-0026 decisions 5-6).

Expected numbers come from the ADR-0026 acceptance oracle and from hand-computed
session calendars — the scheduled closes below are literals taken from each
venue's published calendar, not values read back from the implementation.

All marks are invented fixtures written to ``tmp_path`` only, with the private
``0700``/``0600`` modes the ADR requires of a fixture writer. Nothing here reads
the lake, a provider, a real account or ``data/``.
"""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.portfolio.marks import (  # type: ignore[import-untyped]
    DEFAULT_MAX_STALE_SESSIONS,
    MAX_SUPPORTED_STALE_SESSIONS,
    STRICT_MAX_STALE_SESSIONS,
    MarkProvenance,
    MarksDocument,
    MarksError,
    MarkStatus,
    MarksValidationError,
    PriceMark,
    completed_sessions_after,
    dump_marks_text,
    load_marks_file,
    parse_marks_text,
    value_positions,
)
from equity_lake.portfolio.models import BasisStatus, LedgerEvent, LedgerEventType  # type: ignore[import-untyped]
from equity_lake.portfolio.projections import LedgerProjection, replay_ledger  # type: ignore[import-untyped]

ACCOUNT = "main"
INSTRUMENT = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAA")
OTHER_INSTRUMENT = InstrumentKey(market="us_equity", mic="XNYS", symbol="BBB")
_DIGEST = "0" * 64
# Friday 2024-01-05 XNAS scheduled close (21:00 UTC), hand-checked.
FRIDAY_CLOSE = "2024-01-05T21:00:00+00:00"
ORACLE_AS_OF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)

_MARK = {
    "market": "us_equity",
    "mic": "XNAS",
    "symbol": "AAA",
    "currency": "USD",
    "price": "125",
    "session": "2024-01-05",
    "as_of": FRIDAY_CLOSE,
    "source": "fixture",
    "price_basis": "unadjusted",
    "provenance": "synthetic",
}


def mark(**overrides: object) -> dict[str, object]:
    return {**_MARK, **overrides}


def marks_text(
    *marks: Mapping[str, object],
    blocks: Sequence[Mapping[str, object]] | None = None,
    version: object = 1,
) -> str:
    return json.dumps({"schema_version": version, "marks": list(marks), "valuation_blocks": list(blocks or [])})


def write_private(path: Path, text: str) -> Path:
    """Fixture writer: validates through ``dump_marks_text`` callers, private modes."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(path, 0o600)
    return path


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


def oracle_projection(as_of: datetime = ORACLE_AS_OF) -> LedgerProjection:
    events = [
        _event(1, "OPENING", "1000", instrument=INSTRUMENT, quantity="10", price="100", basis="1000", effective_at="2024-01-02T15:00:00Z"),
        _event(2, "BUY", "-605", instrument=INSTRUMENT, quantity="5", price="120", fees="5", effective_at="2024-01-03T15:00:00Z"),
        _event(3, "SELL", "518", instrument=INSTRUMENT, quantity="4", price="130", fees="2", effective_at="2024-01-04T15:00:00Z"),
        _event(4, "CASH_DIVIDEND", "11", instrument=INSTRUMENT, effective_at="2024-01-04T16:00:00Z"),
    ]
    return replay_ledger(events, account=ACCOUNT, as_of=as_of)


def unknown_basis_projection() -> LedgerProjection:
    events = [
        _event(1, "OPENING", "500", instrument=INSTRUMENT, quantity="10", basis_status=BasisStatus.UNKNOWN),
        _event(2, "DEPOSIT", "500", effective_at="2024-01-03T15:00:00Z"),
    ]
    return replay_ledger(events, account=ACCOUNT, as_of=ORACLE_AS_OF)


class TestDocumentValidation:
    """Structurally invalid mark files are refused whole; nothing is partly accepted."""

    def test_valid_document_parses(self) -> None:
        document = parse_marks_text(marks_text(mark()))

        assert document.schema_version == 1
        assert len(document.marks) == 1
        assert document.marks[0].instrument == INSTRUMENT
        assert document.marks[0].provenance is MarkProvenance.SYNTHETIC
        assert document.valuation_blocks == ()

    def test_price_must_be_decimal_text_not_a_json_number(self) -> None:
        with pytest.raises(MarksValidationError, match="decimal text"):
            parse_marks_text(marks_text(mark(price=125.0)))

    def test_unknown_schema_version_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="schema_version"):
            parse_marks_text(marks_text(mark(), version=2))

    def test_extra_document_field_is_refused(self) -> None:
        payload = json.loads(marks_text(mark()))
        payload["extra"] = True

        with pytest.raises(MarksValidationError, match="unexpected field"):
            parse_marks_text(json.dumps(payload))

    def test_extra_mark_field_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="unexpected field"):
            parse_marks_text(marks_text(mark(adjusted_price="1")))

    def test_missing_mark_field_is_refused(self) -> None:
        incomplete = mark()
        del incomplete["as_of"]

        with pytest.raises(MarksValidationError, match="missing"):
            parse_marks_text(marks_text(incomplete))

    def test_naive_as_of_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="timezone-aware"):
            parse_marks_text(marks_text(mark(as_of="2024-01-05T21:00:00")))

    def test_as_of_must_be_the_scheduled_close_on_an_early_close_day(self) -> None:
        # 2024-11-29 is a US early close: the scheduled close is 18:00 UTC.
        early = mark(session="2024-11-29", as_of="2024-11-29T18:00:00+00:00")
        assert parse_marks_text(marks_text(early)).marks[0].session.isoformat() == "2024-11-29"

        with pytest.raises(MarksValidationError, match="scheduled close"):
            parse_marks_text(marks_text(mark(session="2024-11-29", as_of="2024-11-29T21:00:00+00:00")))

    def test_non_session_date_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="not a scheduled session"):
            parse_marks_text(marks_text(mark(session="2024-01-06", as_of="2024-01-06T21:00:00+00:00")))

    def test_holiday_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="not a scheduled session"):
            parse_marks_text(marks_text(mark(session="2024-01-01", as_of="2024-01-01T21:00:00+00:00")))

    def test_currency_must_be_usd(self) -> None:
        with pytest.raises(MarksValidationError, match="currency"):
            parse_marks_text(marks_text(mark(currency="EUR")))

    def test_adjusted_price_basis_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="price_basis"):
            parse_marks_text(marks_text(mark(price_basis="adjusted")))

    def test_provenance_is_explicit_and_closed(self) -> None:
        with pytest.raises(MarksValidationError, match="provenance"):
            parse_marks_text(marks_text(mark(provenance="real")))

        assert parse_marks_text(marks_text(mark(provenance="unknown"))).marks[0].provenance is MarkProvenance.UNKNOWN

    @pytest.mark.parametrize("price", ["0", "-1", "0.00"])
    def test_price_must_be_positive(self, price: str) -> None:
        with pytest.raises(MarksValidationError):
            parse_marks_text(marks_text(mark(price=price)))

    def test_duplicate_valuation_block_identity_is_refused(self) -> None:
        block = {"market": "us_equity", "mic": "XNAS", "symbol": "AAA", "reason": "unsupported_quantity_adjustment"}

        with pytest.raises(MarksValidationError, match="valuation_blocks"):
            parse_marks_text(marks_text(mark(), blocks=[block, block]))

    def test_unknown_valuation_block_reason_is_refused(self) -> None:
        block = {"market": "us_equity", "mic": "XNAS", "symbol": "AAA", "reason": "split_discovered"}

        with pytest.raises(MarksValidationError, match="reason"):
            parse_marks_text(marks_text(mark(), blocks=[block]))

    def test_invalid_json_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="not valid JSON"):
            parse_marks_text("{not json")

    def test_document_must_be_an_object(self) -> None:
        with pytest.raises(MarksValidationError, match="JSON object"):
            parse_marks_text("[]")

    def test_marks_must_be_an_array(self) -> None:
        with pytest.raises(MarksValidationError, match="must be a JSON array"):
            parse_marks_text(json.dumps({"schema_version": 1, "marks": {}, "valuation_blocks": []}))


class TestSerialization:
    """Fixture writers validate before serializing; consumers validate on read."""

    def test_round_trip_is_stable_and_prices_are_strings(self) -> None:
        document = parse_marks_text(marks_text(mark(price="125.125"), mark(session="2024-01-04", as_of="2024-01-04T21:00:00+00:00")))
        text = dump_marks_text(document)

        payload = json.loads(text)
        assert payload["marks"][0]["price"] == "125.125"
        assert isinstance(payload["marks"][0]["price"], str)
        assert payload["schema_version"] == 1
        assert parse_marks_text(text) == document

    def test_serialization_validates_before_writing(self) -> None:
        invalid = PriceMark(
            market="us_equity",
            mic="XNAS",
            symbol="AAA",
            currency="USD",
            price=Decimal("125"),
            session=datetime(2024, 11, 29, tzinfo=UTC).date(),
            as_of=datetime(2024, 11, 29, 21, 0, tzinfo=UTC),
            source="fixture",
            provenance=MarkProvenance.SYNTHETIC,
        )

        with pytest.raises(MarksValidationError, match="scheduled close"):
            dump_marks_text(MarksDocument(marks=(invalid,)))

    def test_missing_file_is_refused_and_not_created(self, tmp_path: Path) -> None:
        target = tmp_path / "private" / "marks.json"

        with pytest.raises(MarksValidationError, match="never creates"):
            load_marks_file(target)

        assert not target.exists()
        assert not target.parent.exists()

    def test_file_round_trip_with_private_modes(self, tmp_path: Path) -> None:
        block = {"market": "us_equity", "mic": "XNYS", "symbol": "BBB", "reason": "unsupported_quantity_adjustment"}
        document = parse_marks_text(marks_text(mark(), blocks=[block]))
        target = write_private(tmp_path / "private" / "marks.json", dump_marks_text(document))

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
        assert load_marks_file(target) == document

    def test_non_utf8_file_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "marks.json"
        target.write_bytes(b"\xff\xfe{}")

        with pytest.raises(MarksValidationError, match="UTF-8"):
            load_marks_file(target)


class TestSessionAge:
    """Age counts completed exact-venue sessions, never calendar days."""

    def test_friday_close_through_a_closed_weekend_is_age_zero(self) -> None:
        assert completed_sessions_after(INSTRUMENT, datetime(2024, 1, 5).date(), datetime(2024, 1, 7, 12, 0, tzinfo=UTC)) == 0

    def test_monday_before_the_close_is_still_age_zero(self) -> None:
        assert completed_sessions_after(INSTRUMENT, datetime(2024, 1, 5).date(), datetime(2024, 1, 8, 20, 0, tzinfo=UTC)) == 0

    def test_monday_at_the_close_counts_one_session(self) -> None:
        assert completed_sessions_after(INSTRUMENT, datetime(2024, 1, 5).date(), datetime(2024, 1, 8, 21, 0, tzinfo=UTC)) == 1

    def test_tuesday_after_the_close_counts_two_sessions(self) -> None:
        assert completed_sessions_after(INSTRUMENT, datetime(2024, 1, 5).date(), datetime(2024, 1, 9, 21, 0, tzinfo=UTC)) == 2

    def test_early_close_and_holiday_are_exact_venue_sessions(self) -> None:
        # Wed 2024-11-27 close 21:00Z; Thu 11-28 is Thanksgiving (no session);
        # Fri 2024-11-29 is an early close at 18:00Z.
        assert completed_sessions_after(INSTRUMENT, datetime(2024, 11, 27).date(), datetime(2024, 11, 28, 23, 0, tzinfo=UTC)) == 0
        assert completed_sessions_after(INSTRUMENT, datetime(2024, 11, 27).date(), datetime(2024, 11, 29, 18, 0, tzinfo=UTC)) == 1

    def test_default_bound_is_one_session_and_never_wider(self) -> None:
        assert DEFAULT_MAX_STALE_SESSIONS == 1
        assert MAX_SUPPORTED_STALE_SESSIONS == 1
        assert STRICT_MAX_STALE_SESSIONS == 0


class TestMarkSelection:
    """Latest eligible mark by exact identity; conflicts block, never hide."""

    def test_latest_eligible_mark_is_selected(self) -> None:
        document = parse_marks_text(
            marks_text(
                mark(session="2024-01-04", as_of="2024-01-04T21:00:00+00:00", price="100"),
                mark(price="125"),
            )
        )
        valuation = value_positions(oracle_projection(), document)

        assert valuation.positions[0].mark_price == Decimal("125")

    def test_future_mark_is_ignored_in_favour_of_an_older_valid_mark(self) -> None:
        # T is Friday 2024-01-05; the second mark is Monday's close (future).
        document = parse_marks_text(
            marks_text(
                mark(session="2024-01-04", as_of="2024-01-04T21:00:00+00:00", price="100"),
                mark(session="2024-01-08", as_of="2024-01-08T21:00:00+00:00", price="150"),
            )
        )
        valuation = value_positions(oracle_projection(), document)

        position = valuation.positions[0]
        assert position.mark_price == Decimal("100")
        assert position.mark_session is not None
        assert position.mark_session.isoformat() == "2024-01-04"
        # Thursday's close through Friday's completed session is age one.
        assert position.mark_age_sessions == 1
        assert position.status is MarkStatus.VALUED
        assert valuation.complete is True

    def test_identical_duplicate_marks_collapse(self) -> None:
        document = parse_marks_text(marks_text(mark(), mark()))

        valuation = value_positions(oracle_projection(), document)

        assert valuation.positions[0].status is MarkStatus.VALUED
        assert valuation.complete is True

    def test_same_instant_disagreement_blocks_the_instrument(self) -> None:
        document = parse_marks_text(marks_text(mark(), mark(source="other-fixture")))

        valuation = value_positions(oracle_projection(), document)

        position = valuation.positions[0]
        assert position.status is MarkStatus.CONFLICTING_MARKS
        assert position.value is None
        assert valuation.nav is None
        assert valuation.complete is False
        assert "disagree" in (position.blocker or "")

    def test_conflict_never_falls_back_to_an_older_quote(self) -> None:
        document = parse_marks_text(
            marks_text(
                mark(session="2024-01-04", as_of="2024-01-04T21:00:00+00:00", price="100"),
                mark(price="125"),
                mark(price="130"),
            )
        )

        valuation = value_positions(oracle_projection(), document)

        assert valuation.positions[0].status is MarkStatus.CONFLICTING_MARKS
        assert valuation.positions[0].mark_price is None
        assert valuation.nav is None

    def test_marks_for_unheld_instruments_are_ignored(self) -> None:
        foreign = mark(market="us_equity", mic="XNYS", symbol="BBB", session="2024-01-05", as_of=FRIDAY_CLOSE, price="10")
        document = parse_marks_text(marks_text(mark(), foreign))

        valuation = value_positions(oracle_projection(), document)

        assert valuation.complete is True
        assert len(valuation.positions) == 1


class TestValuation:
    """Complete NAV or an explicit partial report — never a partial total."""

    def test_hand_oracle_nav_unrealized_and_total_gain(self) -> None:
        valuation = value_positions(oracle_projection(), parse_marks_text(marks_text(mark())))

        position = valuation.positions[0]
        assert (position.value, position.unrealized_gain) == (Decimal("1375"), Decimal("198"))
        assert valuation.cash == Decimal("924")
        assert valuation.marked_position_value == Decimal("1375")
        assert valuation.nav == Decimal("2299")
        assert valuation.unrealized_gain == Decimal("198")
        assert valuation.total_gain == Decimal("299")
        assert valuation.complete is True

    def test_allocation_includes_cash_and_is_hand_checkable(self) -> None:
        valuation = value_positions(oracle_projection(), parse_marks_text(marks_text(mark())))

        # 1375/2299 = 0.5980861... and 924/2299 = 0.4019138..., rounded half-even to
        # six places; the two weights sum to exactly 1.000000.
        assert valuation.allocation == {"XNAS:AAA": Decimal("0.598086"), "cash": Decimal("0.401914")}

    def test_missing_mark_withholds_nav_but_keeps_cash_and_quantities(self) -> None:
        valuation = value_positions(oracle_projection(), parse_marks_text(marks_text()))

        position = valuation.positions[0]
        assert position.status is MarkStatus.MISSING_MARK
        assert position.value is None
        assert position.quantity == Decimal("11")
        assert valuation.cash == Decimal("924")
        assert valuation.marked_position_value == Decimal("0")
        assert valuation.nav is None
        assert valuation.unrealized_gain is None
        assert valuation.total_gain is None
        assert valuation.allocation is None
        assert valuation.complete is False
        assert valuation.blockers and "XNAS:AAA" in valuation.blockers[0]

    def test_zero_quantity_holdings_are_not_valued(self) -> None:
        events = [
            _event(1, "OPENING", "1000", instrument=INSTRUMENT, quantity="2", basis="200"),
            _event(2, "SELL", "300", instrument=INSTRUMENT, quantity="2", price="150", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_ledger(events, account=ACCOUNT, as_of=ORACLE_AS_OF)

        valuation = value_positions(projection, parse_marks_text(marks_text()))

        assert valuation.positions == ()
        assert valuation.complete is True
        # cash 1000 + 2 shares sold at 150 = 1300; nothing is held.
        assert valuation.nav == Decimal("1300")

    def test_stale_mark_withholds_value_but_keeps_mark_identity(self) -> None:
        # Friday's mark evaluated at Tuesday's close: two completed sessions.
        tuesday = datetime(2024, 1, 9, 21, 0, tzinfo=UTC)
        valuation = value_positions(oracle_projection(tuesday), parse_marks_text(marks_text(mark())))

        position = valuation.positions[0]
        assert position.status is MarkStatus.STALE_MARK
        assert position.mark_age_sessions == 2
        assert position.mark_price == Decimal("125")  # visible for diagnosis
        assert position.mark_source == "fixture"
        assert position.mark_provenance is MarkProvenance.SYNTHETIC
        assert position.value is None
        assert valuation.nav is None

    def test_stricter_zero_bound_makes_a_one_session_mark_stale(self) -> None:
        monday = datetime(2024, 1, 8, 21, 0, tzinfo=UTC)
        projection = oracle_projection(monday)
        # Friday's close is one completed session old at Monday's close.
        document = parse_marks_text(marks_text(mark()))

        relaxed = value_positions(projection, document)
        strict = value_positions(projection, document, max_stale_sessions=0)

        assert relaxed.positions[0].mark_age_sessions == 1
        assert relaxed.positions[0].status is MarkStatus.VALUED
        assert strict.positions[0].status is MarkStatus.STALE_MARK
        assert strict.nav is None

    def test_bound_above_one_is_refused(self) -> None:
        with pytest.raises(MarksError, match="must be 0 or 1"):
            value_positions(oracle_projection(), parse_marks_text(marks_text(mark())), max_stale_sessions=2)

    def test_quantity_uncertain_block_denies_valuation_but_keeps_the_holding(self) -> None:
        blocked = parse_marks_text(
            marks_text(
                mark(),
                blocks=[{"market": "us_equity", "mic": "XNAS", "symbol": "AAA", "reason": "unsupported_quantity_adjustment"}],
            )
        )

        valuation = value_positions(oracle_projection(), blocked)
        position = valuation.positions[0]

        assert position.status is MarkStatus.QUANTITY_UNCERTAIN
        assert position.quantity_certain is False
        assert position.quantity == Decimal("11")
        assert position.value is None
        assert valuation.nav is None
        assert "quantity" in (position.blocker or "")

    def test_unknown_basis_still_allows_nav_but_not_gains(self) -> None:
        valuation = value_positions(unknown_basis_projection(), parse_marks_text(marks_text(mark())))

        position = valuation.positions[0]
        assert position.basis is None
        assert position.value == Decimal("1250")
        assert position.unrealized_gain is None
        assert valuation.complete is True
        assert valuation.nav == Decimal("2250")
        assert valuation.unrealized_gain is None
        assert valuation.total_gain is None

    def test_partial_coverage_keeps_only_the_valid_subtotal(self) -> None:
        events = [
            _event(1, "OPENING", "1000", instrument=INSTRUMENT, quantity="10", basis="1000", effective_at="2024-01-02T15:00:00Z"),
            _event(2, "OPENING", "0", instrument=OTHER_INSTRUMENT, quantity="5", basis="500", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_ledger(events, account=ACCOUNT, as_of=ORACLE_AS_OF)
        foreign = mark(
            market="us_equity",
            mic="XNYS",
            symbol="BBB",
            session="2024-01-04",
            as_of="2024-01-04T21:00:00+00:00",
            price="200",
            source="fixture",
        )
        # Only BBB has a mark; AAA's is missing, so complete NAV must be withheld.
        document = parse_marks_text(marks_text(foreign))

        valuation = value_positions(projection, document)

        assert valuation.complete is False
        assert valuation.nav is None
        assert valuation.marked_position_value == Decimal("1000")
        assert len(valuation.blockers) == 1
        assert "XNAS:AAA" in valuation.blockers[0]

    def test_zero_nav_reports_allocation_as_unavailable(self) -> None:
        valuation = value_positions(replay_ledger([], account=ACCOUNT, as_of=ORACLE_AS_OF), parse_marks_text(marks_text()))

        assert valuation.complete is True
        assert valuation.nav == Decimal("0")
        assert valuation.allocation is None
        assert valuation.marked_position_value == Decimal("0")

    def test_fractional_mark_value_is_exact(self) -> None:
        valuation = value_positions(oracle_projection(), parse_marks_text(marks_text(mark(price="125.125"))))

        # 11 * 125.125 = 1376.375 exactly; no display rounding feeds the value.
        assert valuation.positions[0].value == Decimal("1376.375")
        assert valuation.nav == Decimal("2300.375")
        assert valuation.unrealized_gain == Decimal("199.375")


class TestJulyEarlyClose:
    """The second published early close is honoured, not just one of them."""

    def test_july_third_early_close_is_accepted_at_17_utc(self) -> None:
        document = parse_marks_text(marks_text(mark(session="2024-07-03", as_of="2024-07-03T17:00:00+00:00")))

        assert document.marks[0].session.isoformat() == "2024-07-03"

    def test_july_third_regular_close_time_is_refused(self) -> None:
        with pytest.raises(MarksValidationError, match="scheduled close"):
            parse_marks_text(marks_text(mark(session="2024-07-03", as_of="2024-07-03T21:00:00+00:00")))


class TestValuationArithmeticExactness:
    """Valuation must share the replay's explicit precision, not the global one.

    Regression: value, unrealized gain, marked subtotal, NAV and total gain are
    Decimal arithmetic too. Under a hostile process-global context these must
    still be exact (or refuse), never silently rounded.
    """

    def test_nav_and_gains_are_exact_under_a_hostile_global_precision(self) -> None:
        from decimal import Context, localcontext

        huge_mark = mark(price="1E+30")

        with localcontext(Context(prec=6)):
            valuation = value_positions(oracle_projection(), parse_marks_text(marks_text(huge_mark)))

        # 11 * 1E+30 needs 32 digits; the oracle book is unchanged otherwise.
        assert valuation.positions[0].value == Decimal("11000000000000000000000000000000")
        assert valuation.positions[0].unrealized_gain == Decimal("10999999999999999999999999998823")
        assert valuation.marked_position_value == Decimal("11000000000000000000000000000000")
        assert valuation.nav == Decimal("11000000000000000000000000000924")
        assert valuation.total_gain == Decimal("10999999999999999999999999998924")
        assert valuation.complete is True

    def test_marked_subtotal_is_exact_across_two_positions(self) -> None:
        from decimal import Context, localcontext

        # Two large holdings: the subtotal needs more digits than the global context.
        events = [
            _event(1, "OPENING", "0", instrument=INSTRUMENT, quantity="1", basis="0", effective_at="2024-01-02T15:00:00Z"),
            _event(2, "OPENING", "0", instrument=OTHER_INSTRUMENT, quantity="1", basis="0", effective_at="2024-01-03T15:00:00Z"),
        ]
        projection = replay_ledger(events, account=ACCOUNT, as_of=ORACLE_AS_OF)
        document = parse_marks_text(
            marks_text(
                mark(price="1E+30"),
                mark(market="us_equity", mic="XNYS", symbol="BBB", session="2024-01-05", as_of=FRIDAY_CLOSE, price="1"),
            )
        )

        with localcontext(Context(prec=6)):
            valuation = value_positions(projection, document)

        assert valuation.marked_position_value == Decimal("1000000000000000000000000000001")
        assert valuation.nav == Decimal("1000000000000000000000000000001")
