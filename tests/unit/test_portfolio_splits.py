"""Split treatment in the private ledger (ADR-0029, amending ADR-0024 decision 9).

`SPLIT` is the only quantity-adjusting kind. Its `quantity` is the **share ratio**
(new shares per old share), it never moves cash, total basis is unchanged, a
reversal must invert it exactly, and a mark that predates a split is refused rather
than applied to the post-split quantity.

Every test names the rule it guards, so removing that rule makes the test fail.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from equity_lake.portfolio import (  # type: ignore[import-untyped]
    BasisStatus,
    LedgerEvent,
    LedgerEventType,
    LedgerValidationError,
    MarkProvenance,
    MarksDocument,
    MarkStatus,
    PriceBasis,
    PriceMark,
    ProjectionError,
    parse_csv_text,
    parse_marks_text,
    replay_ledger,
    validate_batch,
    value_positions,
)

HEADER = (
    "account,external_source,external_id,event_type,effective_at,received_at,sequence,market,mic,symbol,"
    "currency,cash_effect,quantity,price,fees,basis,basis_status,reverses_external_id\n"
)
AS_OF = datetime(2024, 1, 5, 21, 0, tzinfo=UTC)


def _rows(*rows: str) -> str:
    return HEADER + "".join(row if row.endswith("\n") else f"{row}\n" for row in rows)


def _opening(*, basis: str = "1000.00", basis_status: str = "KNOWN", sequence: int = 1, account: str = "main") -> str:
    return (
        f"{account},fixture,evt-{sequence:04d},OPENING,2024-01-02T15:00:00Z,2024-01-02T21:00:00Z,{sequence},"
        f"us_equity,XNAS,AAA,USD,1000.00,10,100.00,0,{basis},{basis_status},"
    )


def _split(*, ratio: str = "10", sequence: int = 2, reverses: str = "", account: str = "main") -> str:
    return (
        f"{account},fixture,evt-{sequence:04d},SPLIT,2024-01-05T14:30:00Z,2024-01-05T14:30:00Z,{sequence},"
        f"us_equity,XNAS,AAA,USD,0,{ratio},,0,,KNOWN,{reverses}"
    )


def _sell(*, quantity: str = "50", price: str = "12", cash: str = "600.00", sequence: int = 3) -> str:
    return (
        f"main,fixture,evt-{sequence:04d},SELL,2024-01-05T15:00:00Z,2024-01-05T21:00:00Z,{sequence},"
        f"us_equity,XNAS,AAA,USD,{cash},{quantity},{price},0,,KNOWN,"
    )


def _mark(session: str) -> PriceMark:
    return PriceMark(
        market="us_equity",
        mic="XNAS",
        symbol="AAA",
        currency="USD",
        price=Decimal("125"),
        session=datetime.fromisoformat(session).date(),
        as_of=datetime.fromisoformat(f"{session}T21:00:00+00:00"),
        source="fixture",
        price_basis=PriceBasis.UNADJUSTED,
        provenance=MarkProvenance.SYNTHETIC,
    )


def _document(*marks: PriceMark) -> MarksDocument:
    return parse_marks_text(json.dumps({"schema_version": 1, "marks": [m.model_dump(mode="json") for m in marks], "valuation_blocks": []}))


def _event(**overrides: object) -> LedgerEvent:
    kwargs: dict[str, Any] = {
        "parser_version": "fixture-v1",
        "source_digest": "0" * 64,
        "account": "main",
        "external_source": "fixture",
        "external_id": "evt-0001",
        "event_type": LedgerEventType.SPLIT,
        "effective_at": datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
        "received_at": datetime(2024, 1, 5, 14, 30, tzinfo=UTC),
        "sequence": 1,
        "instrument": {"market": "us_equity", "mic": "XNAS", "symbol": "AAA"},
        "currency": "USD",
        "cash_effect": Decimal("0"),
        "quantity": Decimal("10"),
        "price": None,
        "fees": Decimal("0"),
        "basis": None,
        "basis_status": BasisStatus.KNOWN,
        "reverses_external_id": None,
    }
    kwargs.update(overrides)
    return LedgerEvent(**kwargs)


# ---------------------------------------------------------------------------
# Event contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"quantity": Decimal("1")}, "must change the quantity"),
        ({"quantity": Decimal("0")}, "positive share ratio"),
        ({"quantity": None}, "positive share ratio"),
        ({"cash_effect": Decimal("5")}, "must not move cash"),
        ({"price": Decimal("10")}, "must not carry a price"),
        ({"fees": Decimal("1")}, "must not carry a price"),
    ],
)
def test_split_event_contract_rejects_unsupported_values(overrides: dict[str, object], message: str) -> None:
    """SPLIT carries only a ratio: no cash, price, fee or basis (ADR-0029 decision 9)."""
    with pytest.raises(ValidationError, match=message):
        _event(**overrides)


def test_canonical_csv_accepts_the_bare_split_kind() -> None:
    """The import boundary accepts ``SPLIT`` and validates the batch (ADR-0029 decision 10)."""
    events = parse_csv_text(_rows(_opening(), _split()))
    assert [event.event_type for event in events] == [LedgerEventType.OPENING, LedgerEventType.SPLIT]
    assert events[1].quantity == Decimal("10")
    validate_batch((), events)


def test_a_split_alias_is_still_rejected() -> None:
    """A broker alias is never silently mapped onto SPLIT (ADR-0029 decision 10).

    Falsified by: broadening ``QUANTITY_ADJUSTING_EVENT_TYPES`` handling back to accept
    ``STOCK_SPLIT``, whose ratio convention is not the one this ledger documents.
    """
    row = _split().replace("SPLIT", "STOCK_SPLIT")
    with pytest.raises(LedgerValidationError, match="alias"):
        parse_csv_text(_rows(_opening(), row))


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def test_replay_multiplies_quantity_and_keeps_total_basis() -> None:
    """Quantity scales by the ratio; total basis is unchanged, so average cost divides.

    Falsified by: leaving ``pool.basis`` untouched while forgetting to scale quantity,
    or by scaling basis with the quantity (which would double-count the split).
    """
    projection = replay_ledger(parse_csv_text(_rows(_opening(), _split())), account="main", as_of=AS_OF)
    position = projection.positions[0]

    assert position.quantity == Decimal("100")
    assert position.basis == Decimal("1000")
    assert position.average_cost == Decimal("10")
    assert projection.cash == Decimal("1000")
    assert projection.splits[0].ratio == Decimal("10")


def test_a_post_split_sale_is_not_an_oversell() -> None:
    """A sale of post-split shares is legitimate; the quantity check applies the ratio.

    Falsified by: counting a SPLIT as a zero delta in ``oversell_issues`` without
    applying its ratio, which makes a valid 50-share sale look like an oversell of 10.
    """
    events = parse_csv_text(_rows(_opening(), _split(), _sell()))
    validate_batch((), events)  # must not raise

    projection = replay_ledger(events, account="main", as_of=AS_OF)
    position = projection.positions[0]
    assert position.quantity == Decimal("50")
    assert position.realized_gain == Decimal("100")  # 600 proceeds - 500 removed basis
    assert projection.cash == Decimal("1600")


def test_a_split_reversal_must_exactly_invert_the_split_it_reverses() -> None:
    """Replacement = reversal (reciprocal) + corrected split; products must be 1.

    Falsified by: accepting a reversal whose product is not exactly one, which would
    silently restate quantities instead of restoring them.
    """
    good = parse_csv_text(_rows(_opening(), _split(), _split(ratio="0.1", sequence=3, reverses="evt-0002")))
    validate_batch((), good)
    assert replay_ledger(good, account="main", as_of=AS_OF).positions[0].quantity == Decimal("10")

    bad = parse_csv_text(_rows(_opening(), _split(), _split(ratio="0.2", sequence=3, reverses="evt-0002")))
    with pytest.raises(LedgerValidationError, match="invert"):
        validate_batch((), bad)
    with pytest.raises(ProjectionError, match="invert"):
        replay_ledger(bad, account="main", as_of=AS_OF)


def test_a_reversal_before_the_split_it_names_is_rejected() -> None:
    """A reversal must follow the split it reverses, not merely multiply to one.

    Falsified by: checking only the reciprocal product, which accepts a reversal that
    arrives first and therefore distorts every event between it and the original.
    """
    early = _split(ratio="0.1", sequence=2, reverses="evt-0003").replace("2024-01-05T14:30:00Z", "2024-01-03T14:30:00Z")
    late_original = _split(ratio="10", sequence=3)
    events = parse_csv_text(_rows(_opening(), early, late_original))

    with pytest.raises(LedgerValidationError, match="does not precede|must come after|later"):
        validate_batch((), events)


def test_a_reversal_is_resolved_inside_its_own_account() -> None:
    """External IDs are unique per account, so a shared ID must not decide the verdict.

    Falsified by: looking the referenced split up by external ID alone, which resolves to
    whichever account's event came last in the file; because the ordering key starts with
    the account, the same ledger is then accepted in one row order and rejected in the
    other — a verdict that depends on file order is the one thing the ledger refuses.
    """
    alpha_first = _rows(
        _opening(),
        _split(),
        _split(ratio="0.1", sequence=3, reverses="evt-0002"),
        _opening(account="beta"),
        _split(account="beta", ratio="4"),
    )
    beta_first = _rows(
        _opening(account="beta"),
        _split(account="beta", ratio="4"),
        _opening(),
        _split(),
        _split(ratio="0.1", sequence=3, reverses="evt-0002"),
    )

    for text in (alpha_first, beta_first):
        validate_batch((), parse_csv_text(text))  # alpha's own reversal is valid in either row order

    alpha_only = _rows(_opening(), _split(), _split(ratio="0.1", sequence=3, reverses="evt-0002"))
    projection = replay_ledger(parse_csv_text(alpha_only), account="main", as_of=AS_OF)
    assert projection.positions[0].quantity == Decimal("10")  # 10 -> x10 -> x0.1, net unchanged

    wrong_reciprocal = _rows(
        _opening(account="beta"),
        _split(account="beta", ratio="4"),
        _opening(),
        _split(),
        _split(ratio="0.2", sequence=3, reverses="evt-0002"),
    )
    with pytest.raises(LedgerValidationError, match="invert"):
        validate_batch((), parse_csv_text(wrong_reciprocal))  # a non-reciprocal reversal is still refused


def test_a_pre_split_mark_keeps_the_split_refusal_even_when_stale() -> None:
    """Staleness cannot mask a split refusal; the split reason is the stated blocker."""
    projection = replay_ledger(parse_csv_text(_rows(_opening(), _split())), account="main", as_of=AS_OF)
    valuation = value_positions(projection, _document(_mark("2023-12-01")), max_stale_sessions=0)
    line = valuation.positions[0]

    assert line.status is MarkStatus.MISSING_MARK
    assert "predates a split" in (line.blocker or "")
    assert line.mark_session is not None  # identity kept for diagnosis


def test_the_preview_counts_split_events_separately_from_quantity_deltas() -> None:
    """A split ratio is never summed into the preview's share-count delta."""
    from equity_lake.portfolio import preview_events

    preview = preview_events(parse_csv_text(_rows(_opening(), _split())))
    assert preview.split_event_count == 1
    assert preview.quantity_deltas == {"XNAS:AAA": Decimal("10")}  # opening only, no ratio added


def test_a_split_with_no_open_position_changes_nothing() -> None:
    """A broker export may carry splits for instruments with no position; inert, not fatal."""
    projection = replay_ledger(parse_csv_text(_rows(_split())), account="main", as_of=AS_OF)
    assert projection.positions == ()
    assert projection.cash == Decimal("0")


def test_an_unknown_basis_stays_unknown_across_a_split() -> None:
    """A split never fabricates a basis: unknown stays unknown after scaling."""
    projection = replay_ledger(parse_csv_text(_rows(_opening(basis="", basis_status="UNKNOWN"), _split())), account="main", as_of=AS_OF)
    position = projection.positions[0]
    assert position.quantity == Decimal("100")
    assert position.basis is None
    assert position.average_cost is None


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


def test_a_pre_split_mark_is_refused_for_a_post_split_quantity() -> None:
    """A mark from before the split is not comparable to the adjusted quantity.

    Falsified by: valuing the post-split quantity at the pre-split price, which would
    overstate the position by the split factor.
    """
    projection = replay_ledger(parse_csv_text(_rows(_opening(), _split())), account="main", as_of=AS_OF)
    valuation = value_positions(projection, _document(_mark("2024-01-04")))
    line = valuation.positions[0]

    assert line.status is MarkStatus.MISSING_MARK
    assert line.value is None
    assert "predates a split" in (line.blocker or "")
    assert valuation.complete is False


def test_a_mark_on_or_after_the_split_session_values_the_adjusted_quantity() -> None:
    """The ex-date session close is a post-split price and values the new quantity."""
    projection = replay_ledger(parse_csv_text(_rows(_opening(), _split())), account="main", as_of=AS_OF)
    valuation = value_positions(projection, _document(_mark("2024-01-05")))

    assert valuation.complete is True
    assert valuation.positions[0].value == Decimal("12500")  # 100 shares x 125
    assert valuation.nav == Decimal("13500")


def test_a_split_before_the_as_of_is_invisible_before_it_happens() -> None:
    """Replaying before the ex-date returns the pre-split quantity and no split record."""
    projection = replay_ledger(parse_csv_text(_rows(_opening(), _split())), account="main", as_of=datetime(2024, 1, 4, 21, 0, tzinfo=UTC))

    assert projection.positions[0].quantity == Decimal("10")
    assert projection.splits == ()
