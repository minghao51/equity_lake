"""Deterministic effective-date replay of the private ledger (ADR-0026 decisions 2-4).

This module turns stored :class:`~equity_lake.portfolio.models.LedgerEvent` rows
into positions, cash, average-cost basis, realised gain and income totals. It is
*only* arithmetic over already-validated events: no database, no file, no clock,
no network, no price and no forecast. Marks and valuation live in
:mod:`equity_lake.portfolio.marks`; reconciliation lives in
:mod:`equity_lake.portfolio.reconcile`.

Three rules deserve attention because they are the ones a convenience
implementation would quietly break:

* **Order is the stored source sequence.** The timeline is
  ``(account, sequence, external_id)``, never file order and never arrival time.
  A temporal inversion in that order is refused, not repaired by re-sorting.
* **Exactness is not negotiable.** Every addition, subtraction and multiplication
  runs in a *local* ``Decimal`` context of 256 significant digits and any
  rounding/overflow signal is turned into a visible refusal. The process-global
  context is never consulted, so an unrelated caller cannot change ledger maths.
* **Unavailable is not zero.** An unknown opening basis leaves the pooled basis,
  the pool's realised gain and every aggregate gain that includes it ``None``
  forever — even across liquidation and reopening. Only the *current* pool may
  start again with a known basis.

The one deliberate rounding is the partial-sale basis removal required by
ADR-0026 decision 3: ``B * q / Q`` rounded **directly** to 38 fractional USD
digits, half-even. It is computed once from the exact rational value using
:mod:`fractions` so a 256-digit intermediate can never double-round a halfway
case; full liquidation removes the exact residual instead.

Money displays (average cost, cents) are derived at render time and never feed
the replay.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import (
    Context,
    Decimal,
    DecimalException,
    Inexact,
    Overflow,
    Rounded,
    Underflow,
    localcontext,
)
from fractions import Fraction
from itertools import pairwise
from typing import Final

from pydantic import BaseModel, ConfigDict

from equity_lake.core.instruments import InstrumentKey
from equity_lake.portfolio.models import (
    BasisStatus,
    LedgerEvent,
    LedgerEventType,
    PortfolioLedgerError,
    event_order_key,
)

# ADR-0026 decision 3: an explicit local context, never the process-global one.
PROJECTION_DECIMAL_PRECISION: Final[int] = 256
# ADR-0026 decision 3: partial-sale basis removal is rounded once, to 38 fractional
# USD digits, half-even. Full liquidation removes the exact residual instead.
PARTIAL_SALE_BASIS_FRACTIONAL_DIGITS: Final[int] = 38
# Display only: money is shown in cents and never re-enters the replay.
MONEY_DISPLAY_FRACTIONAL_DIGITS: Final[int] = 2

# Schema v1 stores net dividend cash. Gross and withholding are not derivable and
# are reported as unavailable rather than guessed (ADR-0026 decision 3).
DIVIDEND_BREAKDOWN_UNAVAILABLE: Final[str] = (
    "unavailable: ledger schema v1 records net dividend cash only, so gross amount and withholding "
    "cannot be established and are never assumed to be zero"
)

_ZERO: Final[Decimal] = Decimal("0")
_EXACTNESS_FLAGS: Final[tuple[type[DecimalException], ...]] = (Inexact, Rounded, Overflow, Underflow)


class ProjectionError(PortfolioLedgerError):
    """The supplied timeline cannot support a valid projection at this instant.

    Messages name the reason category, the event type and the external ID at
    most. They never carry an amount, quantity, price, basis or instrument
    symbol (ADR-0024 decision 11): identities belong in the explicit local
    report, not in an error string.
    """


class _StrictModel(BaseModel):
    """The package's strict/frozen model configuration (ADR-0006 write boundary)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def round_half_even(value: Fraction, places: int) -> Decimal:
    """Round an exact rational to ``places`` decimals, half-even, in one step.

    ``Decimal`` division would round to the active context precision first and
    then quantise, which is exactly the double rounding ADR-0026 decision 3
    forbids. Integer arithmetic on the exact rational rounds once, including
    negatives (``divmod`` floors, so the remainder is always in ``[0, 1)``) and
    exact halfway cases (the tie goes to the even neighbour).
    """
    if places < 0:
        raise ValueError("places must be non-negative")
    scaled = value * (10**places)
    quotient, remainder = divmod(scaled.numerator, scaled.denominator)
    doubled = remainder * 2
    if doubled > scaled.denominator or (doubled == scaled.denominator and quotient % 2 != 0):
        quotient += 1
    # ``scaleb``/``quantize`` are context-sensitive and would round a long
    # quotient to the *global* precision; decimal text is parsed exactly.
    return Decimal(f"{quotient}E-{places}")


def money_display(value: Decimal) -> Decimal:
    """Render money as cents, half-even. A display value: never a replay input."""
    return round_half_even(Fraction(value), MONEY_DISPLAY_FRACTIONAL_DIGITS)


def average_cost_display(basis: Decimal | None, quantity: Decimal) -> Decimal | None:
    """Average cost per share as a derived display value, not an accumulator."""
    if basis is None or quantity == 0:
        return None
    return round_half_even(Fraction(basis) / Fraction(quantity), MONEY_DISPLAY_FRACTIONAL_DIGITS)


def _reset_flags(context: Context) -> None:
    for flag in _EXACTNESS_FLAGS:
        context.flags[flag] = False


def _checked(context: Context, what: str, result: Decimal) -> Decimal:
    """Refuse any implicit rounding or overflow produced by the last operation."""
    if any(context.flags[flag] for flag in _EXACTNESS_FLAGS):
        raise ProjectionError(
            f"{what} would lose exactness at the {PROJECTION_DECIMAL_PRECISION}-digit projection precision; "
            "ledger money and quantity are never rounded to fit (ADR-0026 decision 3)"
        )
    _reset_flags(context)
    return result


def _projection_context() -> Context:
    """A fresh explicit context; arithmetic uses it directly, never the global one."""
    return Context(prec=PROJECTION_DECIMAL_PRECISION)


def exact_add(context: Context, what: str, left: Decimal, right: Decimal) -> Decimal:
    """Exact addition under an explicit context, or a visible refusal.

    The operation goes through ``context.add`` so it cannot silently use the
    thread's current (process-global) context: the flags we inspect and the
    precision that produced the result are then always the same object. Shared
    with the valuation layer so both obey one exactness rule (ADR-0026
    decision 3).
    """
    _reset_flags(context)
    return _checked(context, what, context.add(left, right))


def exact_sub(context: Context, what: str, left: Decimal, right: Decimal) -> Decimal:
    """Exact subtraction under an explicit context, or a visible refusal."""
    _reset_flags(context)
    return _checked(context, what, context.subtract(left, right))


def exact_mul(context: Context, what: str, left: Decimal, right: Decimal) -> Decimal:
    """Exact multiplication under an explicit context, or a visible refusal."""
    _reset_flags(context)
    return _checked(context, what, context.multiply(left, right))


def _aware_utc(value: datetime, *, what: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ProjectionError(
            f"{what} must be a timezone-aware instant; a naive timestamp has no defined valuation time and the current clock is never used"
        )
    return value.astimezone(UTC)


class PositionProjection(_StrictModel):
    """One open instrument at the valuation instant.

    ``basis`` is ``None`` when the pool's average cost is unavailable (an unknown
    opening basis that later purchases do not repair); ``realized_gain`` is
    ``None`` when any realised component of this instrument involved an unknown
    pool, even after liquidation and reopening.
    """

    instrument: InstrumentKey
    quantity: Decimal
    basis: Decimal | None
    realized_gain: Decimal | None
    embedded_fees: Decimal

    @property
    def basis_is_known(self) -> bool:
        return self.basis is not None

    @property
    def average_cost(self) -> Decimal | None:
        """Display cents only; the replay never uses this value."""
        return average_cost_display(self.basis, self.quantity)


class LedgerProjection(_StrictModel):
    """The book at one effective-date instant, reconstructed from stored events.

    This is explicitly an *effective-date reconstruction* from the supplied
    ledger, not proof of what a source had published or what the owner knew at
    that instant (ADR-0026 decision 2). ``realized_gain`` is ``None`` as soon as
    any sale touched an unknown basis pool.
    """

    account: str
    as_of: datetime
    positions: tuple[PositionProjection, ...]
    cash: Decimal
    deposits: Decimal
    withdrawals: Decimal
    embedded_fees: Decimal
    standalone_fees: Decimal
    dividend_fees: Decimal
    net_dividend_income: Decimal
    realized_gain: Decimal | None
    replayed_event_count: int

    @property
    def net_external_flow(self) -> Decimal:
        """Deposits minus withdrawals: an external flow, never investment profit.

        Computed in an explicit projection context so a caller's process-global
        precision cannot silently round the result (ADR-0026 decision 3).
        """
        return exact_sub(_projection_context(), "a net external flow", self.deposits, self.withdrawals)

    @property
    def total_fees(self) -> Decimal:
        """Embedded purchase/sale fees plus standalone fee events, each counted once.

        Dividend ``fees`` are disclosed separately: they are already inside the
        net dividend cash and are never re-deducted or treated as withholding.
        Computed in an explicit projection context, like ``net_external_flow``.
        """
        return exact_add(_projection_context(), "a total fee total", self.embedded_fees, self.standalone_fees)


@dataclass
class _Pool:
    """Mutable replay state for one instrument identity."""

    quantity: Decimal = _ZERO
    # ``None`` means the pooled average cost is unavailable, never zero.
    basis: Decimal | None = _ZERO
    # ``None`` is sticky: an unknown component keeps aggregate gains unavailable.
    realized_gain: Decimal | None = _ZERO
    embedded_fees: Decimal = _ZERO


def _ordered_timeline(events: Sequence[LedgerEvent], account: str) -> tuple[LedgerEvent, ...]:
    """Validate the stored timeline structurally and return it in logical order."""
    labels = {event.account for event in events}
    if len(labels) > 1:
        raise ProjectionError(
            "the supplied ledger holds more than one account label; one private book holds exactly one account (ADR-0024 decision 1)"
        )
    if labels and account not in labels:
        raise ProjectionError("the supplied ledger has no events for the requested account label")

    ordered = tuple(sorted(events, key=event_order_key))
    for attribute, described in (("external_id", "external ID"), ("sequence", "source sequence")):
        seen: set[tuple[str, object]] = set()
        for event in ordered:
            key = (event.account, getattr(event, attribute))
            if key in seen:
                raise ProjectionError(
                    f"the supplied ledger repeats one {described}; ambiguous ordering is refused rather than tie-broken (ADR-0026 decision 2)"
                )
            seen.add(key)
    for previous, current in pairwise(ordered):
        if current.effective_at < previous.effective_at:
            raise ProjectionError(
                "the supplied ledger has a temporal inversion in source-sequence order; it is refused, never repaired by re-sorting or editing events"
            )
    return ordered


def _apply_buy(event: LedgerEvent, pool: _Pool, context: Context) -> None:
    quantity = event.quantity
    price = event.price
    if quantity is None or price is None:  # pragma: no cover - the event contract guarantees these
        raise ProjectionError("BUY is missing its quantity or price")
    cost = exact_add(context, "a BUY cost", exact_mul(context, "a BUY cost", quantity, price), event.fees)
    _reset_flags(context)
    if event.cash_effect != -cost:
        raise ProjectionError(
            f"BUY external_id {event.external_id!r} cash_effect does not equal the negative of quantity*price+fees; "
            "inconsistent stored monetary relationships fail projection instead of being rewritten"
        )
    pool.quantity = exact_add(context, "a BUY quantity addition", pool.quantity, quantity)
    basis = pool.basis
    if basis is not None:
        # Later purchases never repair an unknown pool: basis stays unavailable.
        pool.basis = exact_add(context, "a BUY basis addition", basis, cost)
    pool.embedded_fees = exact_add(context, "a BUY fee total", pool.embedded_fees, event.fees)


def _removed_basis(basis: Decimal, held: Decimal, sold: Decimal) -> Decimal:
    """ADR-0026 decision 3: ``B * q / Q`` rounded once, half-even, to 38 dp."""
    exact = Fraction(basis) * Fraction(sold) / Fraction(held)
    return round_half_even(exact, PARTIAL_SALE_BASIS_FRACTIONAL_DIGITS)


def _apply_sell(event: LedgerEvent, pool: _Pool, context: Context) -> None:
    quantity = event.quantity
    price = event.price
    if quantity is None or price is None:  # pragma: no cover - the event contract guarantees these
        raise ProjectionError("SELL is missing its quantity or price")
    if quantity > pool.quantity:
        raise ProjectionError(
            f"SELL external_id {event.external_id!r} exceeds the replayed quantity; oversells, short positions and margin are refused"
        )
    proceeds = exact_sub(context, "SELL net proceeds", exact_mul(context, "SELL gross proceeds", quantity, price), event.fees)
    _reset_flags(context)
    if event.cash_effect != proceeds:
        raise ProjectionError(
            f"SELL external_id {event.external_id!r} cash_effect does not equal quantity*price-fees; "
            "inconsistent stored monetary relationships fail projection instead of being rewritten"
        )

    held = pool.quantity
    basis = pool.basis
    if quantity == held:
        # Full liquidation consumes the exact residual: no stranded rounding.
        removed = basis
        pool.quantity = _ZERO
        pool.basis = _ZERO
    else:
        removed = None if basis is None else _removed_basis(basis, held, quantity)
        if removed is not None and basis is not None:
            if removed > basis:
                raise ProjectionError(
                    f"SELL external_id {event.external_id!r} would remove more basis than the pool holds after half-even "
                    "rounding; the removed basis must stay within the pool (ADR-0026 decision 3)"
                )
            pool.basis = exact_sub(context, "a partial-sale basis subtraction", basis, removed)
        pool.quantity = exact_sub(context, "a SELL quantity subtraction", held, quantity)

    realized = pool.realized_gain
    if removed is None or realized is None:
        # An unknown pool makes this sale's realised gain — and the instrument's
        # aggregate — unavailable, permanently.
        pool.realized_gain = None
    else:
        gain = exact_sub(context, "a realised gain", proceeds, removed)
        pool.realized_gain = exact_add(context, "a realised gain total", realized, gain)
    pool.embedded_fees = exact_add(context, "a SELL fee total", pool.embedded_fees, event.fees)


def _apply_opening(event: LedgerEvent, pool: _Pool, context: Context) -> None:
    quantity = event.quantity
    if quantity is None:
        return
    pool.quantity = exact_add(context, "an OPENING quantity addition", pool.quantity, quantity)
    basis = pool.basis
    if event.basis_status is BasisStatus.UNKNOWN:
        # Unknown basis is unavailable, never zero, and later buys do not repair it.
        pool.basis = None
    elif basis is not None and event.basis is not None:
        pool.basis = exact_add(context, "an OPENING basis addition", basis, event.basis)


@dataclass
class _Totals:
    cash: Decimal = _ZERO
    deposits: Decimal = _ZERO
    withdrawals: Decimal = _ZERO
    embedded_fees: Decimal = _ZERO
    standalone_fees: Decimal = _ZERO
    dividend_fees: Decimal = _ZERO
    net_dividend_income: Decimal = _ZERO
    replayed_event_count: int = 0


def _add_realized(context: Context, total: Decimal | None, component: Decimal | None) -> Decimal | None:
    """Accumulate realized gain exactly, keeping an unknown component sticky."""
    if component is None or total is None:
        return None
    return exact_add(context, "a realized gain total", total, component)


def replay_ledger(events: Sequence[LedgerEvent], *, account: str, as_of: datetime) -> LedgerProjection:
    """Replay one account's stored events up to and including ``as_of``.

    Events with ``effective_at <= as_of`` are applied once each, in source
    sequence order. Receipt/recording times stay provenance and never become a
    historical-knowledge cutoff. Every replayed step re-validates the invariants
    a projection depends on (oversells, negative cash/margin, monetary
    relationships) and refuses the stored row without modifying it.
    """
    instant = _aware_utc(as_of, what="the valuation instant")
    timeline = _ordered_timeline(events, account)

    pools: dict[tuple[str, str, str], _Pool] = {}
    totals = _Totals()
    # ``exact_add``/``exact_sub`` work on the context they are given, and this block
    # additionally makes that context the thread's current one for the plain
    # decimal comparisons below.
    with localcontext(Context(prec=PROJECTION_DECIMAL_PRECISION)) as context:
        for event in timeline:
            if event.effective_at > instant:
                # Nondecreasing effective_at in sequence order makes the rest ineligible.
                break
            totals.replayed_event_count += 1
            key = event.instrument.tuple if event.instrument is not None else None
            pool = None if key is None else pools.setdefault(key, _Pool())

            if event.event_type is LedgerEventType.BUY and pool is not None:
                _apply_buy(event, pool, context)
                totals.embedded_fees = exact_add(context, "an embedded fee total", totals.embedded_fees, event.fees)
            elif event.event_type is LedgerEventType.SELL and pool is not None:
                _apply_sell(event, pool, context)
                totals.embedded_fees = exact_add(context, "an embedded fee total", totals.embedded_fees, event.fees)
            elif event.event_type is LedgerEventType.OPENING and pool is not None:
                _apply_opening(event, pool, context)
            elif event.event_type is LedgerEventType.FEE:
                if event.fees != _ZERO and event.fees != -event.cash_effect:
                    raise ProjectionError(
                        f"FEE external_id {event.external_id!r} descriptive fees disagree with its cash outflow; "
                        "a standalone fee is counted once and never as two expenses"
                    )
                totals.standalone_fees = exact_sub(context, "a standalone fee total", totals.standalone_fees, event.cash_effect)
            elif event.event_type is LedgerEventType.CASH_DIVIDEND:
                totals.net_dividend_income = exact_add(context, "a net dividend income total", totals.net_dividend_income, event.cash_effect)
                if event.fees != _ZERO:
                    # Not withholding: schema v1 has no separate meaning for it.
                    totals.dividend_fees = exact_add(context, "a dividend fee total", totals.dividend_fees, event.fees)
            elif event.event_type is LedgerEventType.DEPOSIT:
                totals.deposits = exact_add(context, "a deposit total", totals.deposits, event.cash_effect)
            elif event.event_type is LedgerEventType.WITHDRAWAL:
                totals.withdrawals = exact_sub(context, "a withdrawal total", totals.withdrawals, event.cash_effect)

            # Signed cash effect is summed exactly once per event; embedded
            # BUY/SELL fees are already inside it and are never subtracted again.
            totals.cash = exact_add(context, "a cash total", totals.cash, event.cash_effect)
            if totals.cash < _ZERO:
                raise ProjectionError(
                    f"{event.event_type.value} external_id {event.external_id!r} would leave a negative cash balance; "
                    "margin and short states are not supported by this book"
                )

    positions: list[PositionProjection] = []
    realized: Decimal | None = _ZERO
    # Aggregation is arithmetic too: it uses the same explicit-precision context so
    # a process-global precision cannot silently collapse two large gains.
    with localcontext(Context(prec=PROJECTION_DECIMAL_PRECISION)) as context:
        for key in sorted(pools):
            pool = pools[key]
            if pool.quantity == _ZERO:
                # A closed pool keeps its own zero basis; its realised gain history is
                # still aggregated, so an unknown component is not lost.
                realized = _add_realized(context, realized, pool.realized_gain)
                continue
            instrument = InstrumentKey(market=key[0], mic=key[1], symbol=key[2])
            positions.append(
                PositionProjection(
                    instrument=instrument,
                    quantity=pool.quantity,
                    basis=pool.basis,
                    realized_gain=pool.realized_gain,
                    embedded_fees=pool.embedded_fees,
                )
            )
            realized = _add_realized(context, realized, pool.realized_gain)

    return LedgerProjection(
        account=account,
        as_of=instant,
        positions=tuple(positions),
        cash=totals.cash,
        deposits=totals.deposits,
        withdrawals=totals.withdrawals,
        # Fees are reported for every replayed event, so a fully liquidated
        # instrument cannot hide the fees it paid (ADR-0026 decision 3).
        embedded_fees=totals.embedded_fees,
        standalone_fees=totals.standalone_fees,
        dividend_fees=totals.dividend_fees,
        net_dividend_income=totals.net_dividend_income,
        realized_gain=realized,
        replayed_event_count=totals.replayed_event_count,
    )


__all__ = [
    "DIVIDEND_BREAKDOWN_UNAVAILABLE",
    "LedgerProjection",
    "MONEY_DISPLAY_FRACTIONAL_DIGITS",
    "PARTIAL_SALE_BASIS_FRACTIONAL_DIGITS",
    "PROJECTION_DECIMAL_PRECISION",
    "PositionProjection",
    "ProjectionError",
    "average_cost_display",
    "exact_add",
    "exact_mul",
    "exact_sub",
    "money_display",
    "replay_ledger",
    "round_half_even",
]
