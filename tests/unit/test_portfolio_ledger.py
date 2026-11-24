"""Synthetic private-ledger contracts: event rules, store atomicity, replay (ADR-0024).

Every value here is invented: one short synthetic account label, one invented
instrument per venue, no broker file and no real account data.
"""

from __future__ import annotations

import inspect
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from equity_lake.core.instruments import InstrumentKey
from equity_lake.portfolio.models import (
    BasisStatus,
    LedgerEvent,
    LedgerEventType,
    LedgerValidationError,
    event_order_key,
    redacted_validation_message,
    validate_batch,
)
from equity_lake.portfolio.store import (
    AppendOutcome,
    LedgerConflictError,
    LedgerStore,
    LedgerStoreError,
    portfolio_db_path,
)

portfolio_store = import_module("equity_lake.portfolio.store")

ACCOUNT = "acct-synth-01"
SYNTHETIC_DIGEST = "0" * 64
SYNT = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNT")
EFFECTIVE = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
RECEIVED = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
# Long amounts that Decimal.normalize() would round into one another at the
# default 28-digit context precision.
LONG_AMOUNT_A = Decimal("1234567890123456789012345678.1")
LONG_AMOUNT_B = Decimal("1234567890123456789012345678.2")
COMPANION_SUFFIXES = ("-journal", "-wal", "-shm")
DECIMAL_LIKE = re.compile(r"\d+\.\d+")


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _event_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "parser_version": "test-parser-v1",
        "source_digest": SYNTHETIC_DIGEST,
        "account": ACCOUNT,
        "external_source": "synthetic-broker",
        "external_id": "evt-0001",
        "event_type": "DEPOSIT",
        "effective_at": EFFECTIVE,
        "received_at": RECEIVED,
        "sequence": 1,
        "instrument": None,
        "currency": "USD",
        "cash_effect": Decimal("100.00"),
        "quantity": None,
        "price": None,
        "fees": Decimal("0"),
        "basis": None,
        "basis_status": "KNOWN",
        "reverses_external_id": None,
    }
    for name, value in overrides.items():
        if name in {"cash_effect", "quantity", "price", "fees", "basis"}:
            kwargs[name] = _optional_decimal(value)
        elif name == "event_type" and isinstance(value, str):
            kwargs[name] = LedgerEventType(value)
        elif name == "basis_status" and isinstance(value, str):
            kwargs[name] = BasisStatus(value)
        else:
            kwargs[name] = value
    kwargs["event_type"] = LedgerEventType(kwargs["event_type"]) if isinstance(kwargs["event_type"], str) else kwargs["event_type"]
    kwargs["basis_status"] = BasisStatus(kwargs["basis_status"]) if isinstance(kwargs["basis_status"], str) else kwargs["basis_status"]
    return kwargs


def _event(**overrides: object) -> LedgerEvent:
    return LedgerEvent(**cast(dict[str, Any], _event_kwargs(**overrides)))


def _opening(
    *, sequence: int = 1, quantity: str = "10", basis: str = "1000.00", cash: str = "1000.00", external_id: str | None = None
) -> LedgerEvent:
    return _event(
        event_type="OPENING",
        external_id=external_id or f"evt-{sequence:04d}",
        sequence=sequence,
        instrument=SYNT,
        cash_effect=cash,
        quantity=quantity,
        price="100.00",
        basis=basis,
    )


def _buy(
    *, sequence: int, quantity: str = "5", price: str = "120.00", fees: str = "5.00", cash: str = "-605.00", instrument: InstrumentKey = SYNT
) -> LedgerEvent:
    return _event(
        event_type="BUY",
        external_id=f"evt-{sequence:04d}",
        sequence=sequence,
        instrument=instrument,
        cash_effect=cash,
        quantity=quantity,
        price=price,
        fees=fees,
    )


def _sell(*, sequence: int, quantity: str, price: str = "130.00", fees: str = "2.00", reverses: str | None = None) -> LedgerEvent:
    return _event(
        event_type="SELL",
        external_id=f"evt-{sequence:04d}",
        sequence=sequence,
        instrument=SYNT,
        cash_effect=str(Decimal(quantity) * Decimal(price) - Decimal(fees)),
        quantity=quantity,
        price=price,
        fees=fees,
        reverses_external_id=reverses,
    )


def _stored_rows(path: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return [tuple(row) for row in connection.execute("SELECT * FROM ledger_events ORDER BY row_id")]
    finally:
        connection.close()


def _companion_files(path: Path) -> list[str]:
    """SQLite journal/checkpoint siblings that a read, dry-run or rollback must not leave."""
    return [suffix for suffix in COMPANION_SUFFIXES if path.with_name(f"{path.name}{suffix}").exists()]


def _install_rejecting_trigger(path: Path, external_id: str) -> None:
    """Synthetic database-level constraint that fires after an earlier row inserted."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.execute(
            "CREATE TRIGGER synth_reject_row BEFORE INSERT ON ledger_events "
            f"WHEN NEW.external_id = '{external_id}' BEGIN SELECT RAISE(ABORT, 'synthetic constraint'); END"
        )
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Event contract (ADR-0024 decisions 4, 6, 7, 9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({"effective_at": datetime(2024, 1, 2, 15, 0)}, "timezone-aware", id="naive-effective-at"),
        pytest.param({"received_at": datetime(2024, 1, 2, 21, 0)}, "timezone-aware", id="naive-received-at"),
        pytest.param({"cash_effect": Decimal("NaN")}, "finite number", id="nan-amount"),
        pytest.param(
            {"quantity": Decimal("Infinity"), "event_type": "BUY", "instrument": SYNT, "price": "1"}, "finite number", id="infinite-quantity"
        ),
        pytest.param({"currency": "EUR"}, "USD-only", id="wrong-currency"),
        pytest.param(
            {
                "event_type": "BUY",
                "instrument": InstrumentKey(market="cn_ashare", mic="XSHG", symbol="600000"),
                "price": "1",
                "quantity": "1",
                "cash_effect": "-1",
            },
            "covers us_equity only",
            id="wrong-market",
        ),
        pytest.param(
            {"event_type": "BUY", "quantity": "1", "price": "1", "cash_effect": "-1"}, "requires a canonical instrument", id="blank-identity"
        ),
        pytest.param(
            {"event_type": "BUY", "instrument": SYNT, "quantity": "-5", "price": "1", "cash_effect": "-1"},
            "positive quantity",
            id="negative-quantity",
        ),
        pytest.param(
            {"event_type": "SELL", "instrument": SYNT, "quantity": "5", "price": "0", "cash_effect": "1"}, "positive price", id="zero-price"
        ),
        pytest.param(
            {"event_type": "SELL", "instrument": SYNT, "quantity": "5", "price": "-1", "cash_effect": "1"}, "positive price", id="negative-price"
        ),
        pytest.param({"event_type": "DEPOSIT", "cash_effect": "-5"}, "DEPOSIT cash_effect must be positive", id="negative-deposit"),
        pytest.param(
            {"event_type": "WITHDRAWAL", "cash_effect": "-5", "instrument": SYNT}, "must not name an instrument", id="withdrawal-with-instrument"
        ),
        pytest.param(
            {"event_type": "SELL", "instrument": SYNT, "quantity": "5", "price": "1", "cash_effect": "-1"},
            "SELL cash_effect is signed",
            id="sell-cash-sign",
        ),
        pytest.param({"event_type": "FEE", "cash_effect": "-1", "quantity": "1"}, "FEE must not carry", id="fee-with-quantity"),
        pytest.param(
            {"event_type": "BUY", "instrument": SYNT, "quantity": "1", "price": "1", "cash_effect": "-1", "basis": "5"},
            "basis is only stated on OPENING",
            id="basis-on-buy",
        ),
        pytest.param({"event_type": "OPENING", "instrument": SYNT, "quantity": "10"}, "must state its total basis", id="opening-without-basis"),
        pytest.param(
            {"event_type": "OPENING", "instrument": SYNT, "quantity": "10", "basis": "1000", "basis_status": "UNKNOWN"},
            "unknown basis must not carry a basis amount",
            id="unknown-basis-with-amount",
        ),
        pytest.param(
            {"event_type": "OPENING", "instrument": SYNT, "cash_effect": "10"},
            "must state the opening quantity",
            id="opening-instrument-without-quantity",
        ),
        pytest.param({"reverses_external_id": "evt-0001"}, "cannot reverse itself", id="self-reversal"),
        pytest.param({"received_at": datetime(2024, 1, 2, 14, 0, tzinfo=UTC)}, "must not precede", id="received-before-effective"),
        pytest.param({"fees": "-1"}, "fees must be non-negative", id="negative-fees"),
        pytest.param({"external_id": "evt 0001"}, "String should match pattern", id="malformed-external-id"),
        pytest.param({"sequence": 0}, "greater than or equal to 1", id="sequence-zero"),
    ],
)
def test_event_contract_rejects_unsupported_values(overrides: dict[str, object], expected: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _event(**overrides)
    assert expected in str(excinfo.value)


def test_money_and_quantity_never_accept_a_binary_float() -> None:
    kwargs = _event_kwargs()
    kwargs["cash_effect"] = 605.1
    with pytest.raises(ValidationError) as excinfo:
        LedgerEvent(**cast(dict[str, Any], kwargs))
    assert "Input should be an instance of Decimal" in str(excinfo.value)


def test_pydantic_backed_failures_never_render_input_values() -> None:
    kwargs = _event_kwargs()
    kwargs["cash_effect"] = 605.1
    with pytest.raises(ValidationError) as decimal_exc:
        LedgerEvent(**cast(dict[str, Any], kwargs))
    # The raw Pydantic string is the leak this helper exists to prevent.
    assert "input_value=" in str(decimal_exc.value)

    decimal_message = redacted_validation_message(decimal_exc.value)
    assert "input_value" not in decimal_message, decimal_message
    assert "605.1" not in decimal_message, decimal_message
    assert "cash_effect" in decimal_message, decimal_message
    assert "Decimal" in decimal_message, decimal_message

    with pytest.raises(ValidationError) as instrument_exc:
        InstrumentKey(market="us_equity", mic="XHKG", symbol="PRIVATE_SYMBOL")
    assert "input_value=" in str(instrument_exc.value)

    instrument_message = redacted_validation_message(instrument_exc.value)
    assert "input_value" not in instrument_message, instrument_message
    assert "XHKG" not in instrument_message, instrument_message
    assert "PRIVATE_SYMBOL" not in instrument_message, instrument_message
    assert "us_equity" not in instrument_message, instrument_message
    assert "MIC" in instrument_message, instrument_message
    assert "<redacted>" in instrument_message, instrument_message


def test_split_is_the_only_quantity_adjusting_kind() -> None:
    """ADR-0029 adds exactly one quantity-adjusting kind; aliases stay absent."""
    assert {kind.value for kind in LedgerEventType} == {
        "OPENING",
        "BUY",
        "SELL",
        "DEPOSIT",
        "WITHDRAWAL",
        "FEE",
        "CASH_DIVIDEND",
        "SPLIT",
    }
    for alias in ("STOCK_SPLIT", "REVERSE_SPLIT", "TRANSFER_IN", "SPIN_OFF"):
        with pytest.raises(ValueError):
            LedgerEventType(alias)


def test_event_content_is_exact_and_normalised_to_utc() -> None:
    event = _event(
        event_type="BUY",
        instrument=SYNT,
        cash_effect="-605.10",
        quantity="5.50",
        price="120.10",
        fees="5.00",
        effective_at=datetime(2024, 1, 2, 11, 0, tzinfo=timezone(timedelta(hours=-5))),
    )
    assert event.cash_effect == Decimal("-605.10")
    assert str(event.quantity) == "5.50"
    # A non-UTC aware timestamp is normalised to UTC, never re-interpreted.
    assert event.effective_at == datetime(2024, 1, 2, 16, 0, tzinfo=UTC)
    assert event.effective_at.utcoffset() == timedelta(0)
    assert event.basis_status is BasisStatus.KNOWN


def test_content_digest_ignores_provenance_but_detects_changed_amount() -> None:
    base = _event(event_type="BUY", instrument=SYNT, cash_effect="-605.00", quantity="5", price="120.00", fees="5.00")
    reprovenanced = _event(
        event_type="BUY",
        instrument=SYNT,
        cash_effect="-605.00",
        quantity="5.00",
        price="120.00",
        fees="5.00",
        source_digest="f" * 64,
        parser_version="test-parser-v2",
    )
    changed = _event(event_type="BUY", instrument=SYNT, cash_effect="-605.01", quantity="5", price="120.00", fees="5.00")
    assert base.content_digest == reprovenanced.content_digest
    assert base.content_digest != changed.content_digest


def test_event_order_uses_the_source_sequence() -> None:
    early = _buy(sequence=2)
    late = _buy(sequence=9)
    assert sorted([late, early], key=event_order_key) == [early, late]


def test_content_digest_never_rounds_distinct_long_decimals_together() -> None:
    first = _event(cash_effect=LONG_AMOUNT_A)
    second = _event(cash_effect=LONG_AMOUNT_B)

    assert first.content_payload()["cash_effect"] == "1234567890123456789012345678.1"
    assert second.content_payload()["cash_effect"] == "1234567890123456789012345678.2"
    assert first.content_digest != second.content_digest


def test_content_digest_treats_equal_decimals_at_different_scales_as_identical() -> None:
    scaled = _event(event_type="BUY", instrument=SYNT, cash_effect="-605.10", quantity="5.50", price="120.10", fees="5.00")
    plain = _event(event_type="BUY", instrument=SYNT, cash_effect="-605.1", quantity="5.5", price="120.1", fees="5")

    payload = scaled.content_payload()
    assert payload["cash_effect"] == "-605.1"
    assert payload["quantity"] == "5.5"
    assert payload["price"] == "120.1"
    assert payload["fees"] == "5"
    assert scaled.content_digest == plain.content_digest


@pytest.mark.parametrize(
    "amount",
    [
        pytest.param(Decimal("1" * 39), id="39-significant-digits"),
        pytest.param(Decimal("1E+60"), id="exponent-above-bound"),
        pytest.param(Decimal("1E-60"), id="exponent-below-bound"),
        pytest.param(Decimal("0." + "1" * 40), id="40-fractional-digits"),
    ],
)
def test_absurd_decimal_magnitudes_are_rejected_before_they_reach_the_digest(amount: Decimal) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _event(cash_effect=amount)
    message = str(excinfo.value)
    assert "exact" in message, message
    assert "never rounds" in message, message
    assert "bounded to" in message, message


# ---------------------------------------------------------------------------
# Idempotency and atomicity (ADR-0024 decision 5)
# ---------------------------------------------------------------------------


def test_append_is_idempotent_for_identical_content(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    batch = [_opening(), _buy(sequence=2)]

    first = store.append(batch)
    second = store.append(batch)

    assert first == AppendOutcome(inserted=2, unchanged=0)
    assert second == AppendOutcome(inserted=0, unchanged=2)
    assert len(store.load_events()) == 2


def test_changed_content_for_the_same_external_id_fails_and_changes_nothing(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    assert store.append([_opening(), _buy(sequence=2)]).inserted == 2
    before = _stored_rows(store.path)

    conflicting = _buy(sequence=2, quantity="6", cash="-725.00")

    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), [conflicting])
    assert "different content" in str(excinfo.value)
    with pytest.raises(LedgerConflictError) as excinfo:
        store.append([conflicting])
    assert "different content" in str(excinfo.value)
    assert _stored_rows(store.path) == before


def test_store_rolls_back_the_whole_batch_when_a_later_row_conflicts(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    assert store.append([_opening(), _buy(sequence=2)]).inserted == 2
    before = _stored_rows(store.path)

    conflict = _opening(quantity="11", cash="1100.00")
    batch = [_sell(sequence=3, quantity="1"), _buy(sequence=4), conflict]

    with pytest.raises(LedgerConflictError):
        store.append(batch)

    assert _stored_rows(store.path) == before
    assert len(store.load_events()) == 2


def test_batch_with_an_invalid_last_row_persists_nothing(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10")])
    before = _stored_rows(store.path)

    batch = [_sell(sequence=2, quantity="1"), _sell(sequence=3, quantity="50")]

    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), batch)
    assert "oversells are rejected" in str(excinfo.value)
    assert _stored_rows(store.path) == before


def test_first_row_conflict_rolls_back_before_anything_is_inserted(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(), _buy(sequence=2)])
    before = _stored_rows(store.path)

    with pytest.raises(LedgerConflictError):
        store.append([_buy(sequence=2, quantity="6", cash="-725.00"), _buy(sequence=3)])

    assert _stored_rows(store.path) == before
    assert len(store.load_events()) == 2
    assert _companion_files(store.path) == []


def test_middle_row_conflict_leaves_no_partial_batch_behind(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(), _buy(sequence=2)])
    before = _stored_rows(store.path)
    conflict = _buy(sequence=2, quantity="6", cash="-725.00")

    with pytest.raises(LedgerConflictError):
        store.append([_sell(sequence=3, quantity="1"), conflict, _buy(sequence=4)])

    assert _stored_rows(store.path) == before
    assert len(store.load_events()) == 2
    assert _companion_files(store.path) == []


def test_database_constraint_failure_after_a_successful_insert_rolls_the_batch_back(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening()])
    before = _stored_rows(store.path)
    _install_rejecting_trigger(store.path, "evt-0003")

    # ADR-0031: a storage-boundary constraint rejection is reported as a schema/contract
    # mismatch (the CLI used to call this "could not be read ... retry"), never as a raw sqlite3
    # error escaping to the caller.
    with pytest.raises(LedgerStoreError, match="schema refused an event that the event contract allows"):
        store.append([_buy(sequence=2), _buy(sequence=3)])

    assert _stored_rows(store.path) == before
    assert len(store.load_events()) == 1
    assert _companion_files(store.path) == []


# ---------------------------------------------------------------------------
# Quantity invariant (ADR-0024 decisions 4, 9)
# ---------------------------------------------------------------------------


def test_oversell_is_rejected_and_selling_to_exactly_zero_is_accepted(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10")])

    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), [_sell(sequence=2, quantity="11")])
    assert "would take the position negative" in str(excinfo.value)

    # Selling the whole position is allowed; a further sell is not.
    store.append([_sell(sequence=2, quantity="10")])
    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), [_sell(sequence=3, quantity="1")])
    assert "would take the position negative" in str(excinfo.value)


def test_reimporting_a_stored_sell_is_unchanged_and_not_an_oversell(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10"), _sell(sequence=2, quantity="9")])
    stored_sell = store.load_events()[-1]
    before = _stored_rows(store.path)

    assert validate_batch(store.load_events(), [stored_sell]) == ()
    assert store.append([stored_sell]) == AppendOutcome(inserted=0, unchanged=1)
    assert _stored_rows(store.path) == before


def test_a_changed_stored_sell_is_a_content_conflict_not_an_oversell(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10"), _sell(sequence=2, quantity="9")])

    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), [_sell(sequence=2, quantity="8")])
    message = str(excinfo.value)
    assert "different content" in message
    assert "negative" not in message
    assert "evt-0002" in message


def test_a_genuinely_new_oversell_against_stored_state_is_rejected(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10"), _sell(sequence=2, quantity="9")])
    before = _stored_rows(store.path)

    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), [_sell(sequence=3, quantity="5")])
    message = str(excinfo.value)
    assert "would take the position negative" in message
    assert "evt-0003" in message
    assert "SYNT" not in message
    assert _stored_rows(store.path) == before


def test_ledger_errors_never_disclose_holdings_or_instrument_symbols(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10"), _sell(sequence=2, quantity="9")])
    messages: list[str] = []

    with pytest.raises(LedgerValidationError) as oversell:
        validate_batch(store.load_events(), [_sell(sequence=3, quantity="5")])
    messages.append(str(oversell.value))

    with pytest.raises(LedgerValidationError) as changed:
        validate_batch(store.load_events(), [_sell(sequence=2, quantity="3")])
    messages.append(str(changed.value))

    with pytest.raises(LedgerValidationError) as unknown_target:
        validate_batch(store.load_events(), [_sell(sequence=3, quantity="1", reverses="evt-9999")])
    messages.append(str(unknown_target.value))

    for message in messages:
        assert DECIMAL_LIKE.search(message) is None, message
        assert "SYNT" not in message, message
        assert "available" not in message.lower(), message
        assert "SELL" in message, message
        assert "evt-" in message, message


def test_sell_after_a_reversal_correction_needs_the_supported_quantity(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10"), _buy(sequence=2, quantity="5")])

    # Explicit reversal of the buy: a separate event that names its target.
    reversal = _sell(sequence=3, quantity="5", reverses="evt-0002")
    store.append([reversal])
    stored = store.load_events()
    assert stored[-1].reverses_external_id == "evt-0002"

    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(stored, [_sell(sequence=4, quantity="11")])
    assert "oversells are rejected" in str(excinfo.value)

    store.append([_sell(sequence=4, quantity="10")])
    assert [event.external_id for event in store.load_events()] == ["evt-0001", "evt-0002", "evt-0003", "evt-0004"]


def test_reversal_must_name_an_existing_event(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10")])
    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), [_sell(sequence=2, quantity="1", reverses="evt-9999")])
    assert "reverses unknown external_id" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Ordering and duplicate rejection
# ---------------------------------------------------------------------------


def test_duplicate_external_ids_inside_one_batch_are_rejected() -> None:
    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch((), [_opening(quantity="10"), _opening(quantity="10", external_id="evt-0001", sequence=2)])
    assert "appears twice in one batch" in str(excinfo.value)


def test_ambiguous_source_sequence_is_rejected(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10")])
    clashing = _event(event_type="DEPOSIT", external_id="evt-0002", sequence=1, cash_effect="5.00")
    with pytest.raises(LedgerValidationError) as excinfo:
        validate_batch(store.load_events(), [clashing])
    assert "ambiguous ordering is rejected" in str(excinfo.value)


def test_replay_order_is_independent_of_file_order(tmp_path: Path) -> None:
    events = [_opening(quantity="10"), _buy(sequence=2, quantity="5"), _sell(sequence=3, quantity="4"), _buy(sequence=4, quantity="2")]
    forward = LedgerStore(data_dir=tmp_path / "forward")
    reversed_store = LedgerStore(data_dir=tmp_path / "reversed")
    forward.append(events)
    reversed_store.append(list(reversed(events)))

    assert [event.content_digest for event in forward.load_events()] == [event.content_digest for event in reversed_store.load_events()]
    assert [event.external_id for event in reversed_store.load_events()] == ["evt-0001", "evt-0002", "evt-0003", "evt-0004"]

    oversell = [_sell(sequence=5, quantity="14")]
    for store in (forward, reversed_store):
        with pytest.raises(LedgerValidationError) as excinfo:
            validate_batch(store.load_events(), oversell)
        assert "would take the position negative" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Append-only correction path, storage privacy, read-only access
# ---------------------------------------------------------------------------


def test_correction_event_appends_without_mutating_earlier_rows(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening(quantity="10"), _buy(sequence=2, quantity="5")])
    before = _stored_rows(store.path)

    store.append([_sell(sequence=3, quantity="5", price="120.00", fees="0.00", reverses="evt-0002")])

    after = _stored_rows(store.path)
    assert after[: len(before)] == before
    assert len(after) == len(before) + 1


def test_store_exposes_no_in_place_update_path() -> None:
    for name in ("update", "update_event", "replace", "delete", "rewrite", "amend"):
        assert not hasattr(LedgerStore, name)
    source = inspect.getsource(portfolio_store)
    assert re.search(r"\bUPDATE\s+\w+\s+SET\b", source, re.IGNORECASE) is None
    assert re.search(r"\bDELETE\s+FROM\b", source, re.IGNORECASE) is None


def test_private_directory_and_database_permissions(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission bits only")
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening()])

    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert sorted(path.name for path in store.path.parent.iterdir()) == ["portfolio.sqlite3"]


def test_read_path_cannot_create_a_missing_ledger(tmp_path: Path) -> None:
    store = LedgerStore(path=tmp_path / "absent" / "portfolio.sqlite3")
    assert store.exists() is False
    with pytest.raises(LedgerStoreError) as excinfo:
        store.load_events()
    assert "never create it" in str(excinfo.value)
    assert not (tmp_path / "absent").exists()


def test_ledger_rejects_an_unknown_schema_version(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening()])
    connection = sqlite3.connect(store.path, isolation_level=None)
    try:
        connection.execute("PRAGMA user_version=99")
    finally:
        connection.close()

    with pytest.raises(LedgerStoreError) as excinfo:
        store.append([_buy(sequence=2)])
    assert "unsupported private portfolio ledger schema version 99" in str(excinfo.value)


def test_read_path_refuses_an_unsupported_schema_version(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening()])
    connection = sqlite3.connect(store.path, isolation_level=None)
    try:
        connection.execute("PRAGMA user_version=7")
    finally:
        connection.close()

    with pytest.raises(LedgerStoreError) as excinfo:
        store.load_events()
    assert "unsupported private portfolio ledger schema version 7" in str(excinfo.value)


def test_an_unexpected_user_version_is_refused_by_both_paths_without_a_rewrite(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening()])
    connection = sqlite3.connect(store.path, isolation_level=None)
    try:
        connection.execute("PRAGMA user_version=42")
    finally:
        connection.close()
    before = store.path.read_bytes()

    with pytest.raises(LedgerStoreError) as read_exc:
        store.load_events()
    assert "unsupported private portfolio ledger schema version 42" in str(read_exc.value)

    with pytest.raises(LedgerStoreError) as write_exc:
        store.append([_buy(sequence=2)])
    assert "unsupported private portfolio ledger schema version 42" in str(write_exc.value)

    assert store.path.read_bytes() == before
    assert _companion_files(store.path) == []


def test_a_file_that_is_not_a_sqlite_database_is_refused_and_never_overwritten(tmp_path: Path) -> None:
    db_path = portfolio_db_path(tmp_path / "data")
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"this is not a sqlite database" * 4)
    before = db_path.read_bytes()

    store = LedgerStore(path=db_path)
    with pytest.raises(LedgerStoreError) as read_exc:
        store.load_events()
    assert "not a usable SQLite database" in str(read_exc.value)
    assert "refusing to read or overwrite it" in str(read_exc.value)

    with pytest.raises(LedgerStoreError) as write_exc:
        store.append([_opening()])
    assert "not a usable SQLite database" in str(write_exc.value)

    assert db_path.read_bytes() == before
    assert _companion_files(db_path) == []


def test_a_stored_event_that_violates_the_contract_is_reported_without_its_values(tmp_path: Path) -> None:
    store = LedgerStore(data_dir=tmp_path)
    store.append([_opening()])
    connection = sqlite3.connect(store.path, isolation_level=None)
    try:
        connection.execute("UPDATE ledger_events SET account = ?", ("acct 12.5 shares",))
    finally:
        connection.close()

    with pytest.raises(LedgerStoreError) as excinfo:
        store.load_events()
    message = str(excinfo.value)
    assert "violates the event contract" in message, message
    assert "refusing to read it" in message, message
    assert "12.5" not in message, message
    assert "shares" not in message, message
    assert "input_value" not in message, message
    assert DECIMAL_LIKE.search(message) is None, message
