"""Derive a research-grade total-return series from stored distributions (ADR-0037).

The lake already stores raw prices and the vendor's own dividend/split rows, so total
return is *derived* locally instead of bought or baked into stored prices. Nothing here
touches a vendor-adjusted series: storing one would break the raw-at-write-boundary rule
(ADR-0027), double-adjust real-root prices reconstructed under ADR-0029 Part I, and
double-count the dividends already in ``02_silver/corporate_actions``.

Semantics, fixed:

* a cash dividend ``d`` with ex-date ``t`` is reinvested at that session's own close, so
  the step is ``1 + d / close_t`` and the product of every step with ``t <= u`` multiplies
  the price at session ``u``;
* a split whose stored lake ratio is ``r`` (the ADR-0011/ADR-0029 ``old/new`` convention —
  ``0.1`` for a 10-for-1, ``0.25`` for a 4-for-1) multiplies every session **strictly
  before** the split session by ``r``; equivalently by ``1 / (new shares per old share)``.
  The split's own session is already post-split at the vendor and is never scaled;
* a split and a dividend on the same session resolve as **split first, then dividend**: the
  split acts only on earlier sessions while the dividend step applies at and after ``t``
  and uses that session's post-split close, because the distribution is quoted per
  post-split share. The two act on disjoint session sets, so the product is
  order-independent by construction — applying the dividend to a pre-split close, or
  scaling the split's own session, is the defect this rule forbids;
* money is Float64 throughout, matching ``CORPORATE_ACTION_SCHEMA`` and the reader's
  arithmetic: factors multiply in ascending session order and **nothing is rounded**;
* the result is a pure function of ``(prices, actions)`` — no clock, no wall time, no
  dependence on input row order (actions are processed in ``(ticker, ex_date, action)``
  order, and the first refusal found in that order wins).

**Research-grade only (ADR-0037 decision 2).** Every stored action row has a null
``source_available_at``, so the series inherits the ``vendor_events_no_clock`` weakness of
ADR-0032: it asserts the stored rows are the vendor's own event list and nothing about
when an event became visible. It may never feed a strict/point-in-time read, a live or
paper signal, a feature/label/model path, a ledger posting or a published claim. The entry
point therefore requires an explicit ``basis`` argument whose only admitted value is
``vendor_events_no_clock``; a caller asking for the strict ``source_clock`` basis (or
anything else) is refused instead of silently downgraded, the output frame stamps
``total_return_basis`` on every row, and the returned metadata declares the clock absent.

Refusals are named (``TotalReturnError.reason``), never silent skips: ``ex_date_without_price``,
``non_positive_close``, ``invalid_split_ratio``, ``invalid_dividend_value``,
``unknown_action``, ``duplicate_dividend``, ``unknown_instrument``, ``ambiguous_identity``
and ``strict_basis_required``.

Out of scope and deliberately absent (each needs its own decision): tax/withholding beyond
the net cash stored, non-USD currency, payment-date rather than ex-date reinvestment, and
index-level (SPY/QQQ) distribution treatment beyond the same per-share rule.

This module is never wired into ``with_price_adjustment``, ``AdjustmentMode`` or the
backtest loader: RS-B01's uniform ``total_return`` refusal stands unchanged.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import polars as pl

from equity_lake.core.polars_utils import FrameLike, ensure_polars

__all__ = [
    "DIVIDEND_ACTION",
    "NO_SOURCE_CLOCK",
    "SPLIT_ACTION",
    "TOTAL_RETURN_BASIS",
    "TotalReturnError",
    "TotalReturnSeries",
    "derive_total_return",
]

DIVIDEND_ACTION = "dividend"
SPLIT_ACTION = "split"
#: The only admitted basis (ADR-0032 decision 1, ADR-0037 decision 2): vendor-event
#: completeness with no source clock at all.
TOTAL_RETURN_BASIS = "vendor_events_no_clock"
#: Declared clock basis of a derived series: there is none, and none is synthesized.
NO_SOURCE_CLOCK = "none_declared"
_INDEX_BASE = 100.0


class TotalReturnError(ValueError):
    """A total-return series cannot be derived honestly from these inputs (ADR-0037).

    ``reason`` is a stable token (see the module docstring) so callers, tests and
    operator-facing messages can distinguish an unpriced ex-date from an unusable split
    ratio without parsing prose.
    """

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True, slots=True)
class TotalReturnSeries:
    """A derived series plus the provenance facts a caller must not have to guess.

    Attributes:
        frame: The input rows, in input order, with ``total_return_close``,
            ``total_return_index`` (base 100 at the instrument's first session in the
            frame) and ``total_return_basis`` appended.
        dividend_reinvestments: Number of cash dividends reinvested.
        split_adjustments: Number of split events applied.
        basis: Always :data:`TOTAL_RETURN_BASIS` — research-grade, never strict.
        clock_basis: Always :data:`NO_SOURCE_CLOCK`; the absence is declared, not null
            by accident.
    """

    frame: pl.DataFrame
    dividend_reinvestments: int
    split_adjustments: int
    basis: str
    clock_basis: str


def _refuse_strict(basis: str) -> None:
    """Refuse every basis except the one research basis (ADR-0037 decisions 2 and 6)."""
    if basis == TOTAL_RETURN_BASIS:
        return
    raise TotalReturnError(
        "strict_basis_required",
        f"a derived total-return series is research-grade only and cannot satisfy {basis!r}: every stored action "
        f"row carries no source clock, so no strict/point-in-time read may use it (ADR-0037). Use basis="
        f"{TOTAL_RETURN_BASIS!r} for research, or keep the split-only adjusted read for strict consumers.",
    )


def _require_columns(frame: pl.DataFrame, columns: tuple[str, ...], kind: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise TotalReturnError("frame_incomplete", f"{kind} frame missing required column(s): {', '.join(missing)}")


def _refuse_ambiguous_identity(prices: pl.DataFrame) -> None:
    """One ticker spanning more than one market cannot be priced as one instrument."""
    if "market" not in prices.columns:
        return
    spans = prices.group_by("ticker").agg(pl.col("market").n_unique().alias("markets"))
    ambiguous = sorted(row["ticker"] for row in spans.to_dicts() if row["markets"] > 1)
    if ambiguous:
        raise TotalReturnError(
            "ambiguous_identity",
            f"ticker(s) {', '.join(ambiguous)} appear in more than one market in one price frame; partition the frame "
            "by canonical (market, mic, symbol) identity before deriving total return",
        )


def _session_factors(
    sessions: list[date],
    split_products: dict[date, float],
    dividend_steps: dict[date, float],
) -> dict[date, tuple[float, float]]:
    """Return ``(split_factor, dividend_factor)`` per session, in one ascending pass.

    ``split_factor`` is the product of the split ratios whose ex-date is strictly after
    the session (so a split's own session is never scaled) and ``dividend_factor`` is the
    product of the reinvestment steps whose ex-date is at or before it. Walking the
    sessions with running products avoids dividing one product by another, so the result
    carries no avoidable float error.
    """
    descending = sorted(sessions, reverse=True)
    split_dates = sorted(split_products, reverse=True)
    split_factor: dict[date, float] = {}
    running = 1.0
    index = 0
    for session in descending:
        while index < len(split_dates) and split_dates[index] > session:
            running *= split_products[split_dates[index]]
            index += 1
        split_factor[session] = running
    dividend_factor: dict[date, float] = {}
    running = 1.0
    index = 0
    dividend_dates = sorted(dividend_steps)
    for session in sorted(sessions):
        while index < len(dividend_dates) and dividend_dates[index] <= session:
            running *= dividend_steps[dividend_dates[index]]
            index += 1
        dividend_factor[session] = running
    return {session: (split_factor[session], dividend_factor[session]) for session in sessions}


def _usable_price(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0.0 else None


def _dividend_amount(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TotalReturnError("invalid_dividend_value", f"dividend amount {value!r} is not numeric")
    amount = float(value)
    if not math.isfinite(amount) or amount < 0.0:
        raise TotalReturnError(
            "invalid_dividend_value",
            f"dividend amount {value!r} is unusable (expected a finite non-negative cash amount per share)",
        )
    return amount


def _split_ratio(value: object, ticker: str, ex_date: date) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TotalReturnError("invalid_split_ratio", f"{ticker} on {ex_date}: split ratio {value!r} is not numeric")
    ratio = float(value)
    if not math.isfinite(ratio) or ratio <= 0.0 or ratio == 1.0:
        raise TotalReturnError(
            "invalid_split_ratio",
            f"{ticker} on {ex_date}: stored split ratio {value!r} is unusable (expected a finite old/new ratio greater than 0 and not exactly 1)",
        )
    return ratio


def _empty_series(prices: pl.DataFrame) -> TotalReturnSeries:
    frame = prices.with_columns(
        pl.Series("total_return_close", [], dtype=pl.Float64),
        pl.Series("total_return_index", [], dtype=pl.Float64),
        pl.Series("total_return_basis", [], dtype=pl.String),
    )
    return TotalReturnSeries(
        frame=frame,
        dividend_reinvestments=0,
        split_adjustments=0,
        basis=TOTAL_RETURN_BASIS,
        clock_basis=NO_SOURCE_CLOCK,
    )


def derive_total_return(prices: FrameLike, actions: FrameLike, *, basis: str) -> TotalReturnSeries:
    """Return a research-grade total-return series derived from stored distributions.

    Args:
        prices: Raw price rows with ``ticker``, ``date`` and ``close`` (plus any other
            columns, which are carried through untouched). Raw basis is assumed: prices
            already adjusted by a vendor would be adjusted a second time.
        actions: Stored corporate-action rows with ``ticker``, ``ex_date``, ``action``
            and ``value`` (``CORPORATE_ACTION_COLUMNS``). Empty is allowed and yields a
            factor of 1 — this module asserts nothing about action coverage.
        basis: Required and keyword-only. The only admitted value is
            ``"vendor_events_no_clock"``; every other value, including the strict
            ``"source_clock"``, raises ``strict_basis_required``.

    Returns:
        A :class:`TotalReturnSeries`: the input frame plus ``total_return_close``,
        ``total_return_index`` and ``total_return_basis``, the reinvestment and split
        counts, and the declared (absent) clock basis.

    Raises:
        TotalReturnError: Any named refusal in the module docstring.
    """
    _refuse_strict(basis)
    price_frame = ensure_polars(prices)
    action_frame = ensure_polars(actions)
    _require_columns(price_frame, ("ticker", "date", "close"), "prices")
    _require_columns(action_frame, ("ticker", "ex_date", "action", "value"), "actions")
    if price_frame.is_empty():
        if not action_frame.is_empty():
            first = action_frame.sort("ticker", "ex_date").row(0, named=True)
            raise TotalReturnError(
                "unknown_instrument",
                f"{first['ticker']!r} has corporate actions but the price frame is empty; its factors would be unpriced",
            )
        return _empty_series(price_frame)
    _refuse_ambiguous_identity(price_frame)

    closes: dict[str, dict[date, float | None]] = defaultdict(dict)
    for row in price_frame.select("ticker", "date", "close").to_dicts():
        session = row["date"]
        if isinstance(session, date):
            value = row["close"]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                closes[str(row["ticker"])][session] = float(value)
            else:
                # Null in, null out: a missing close is never filled or dropped.
                closes[str(row["ticker"])][session] = None

    split_products: dict[str, dict[date, float]] = defaultdict(dict)
    dividend_steps: dict[str, dict[date, float]] = defaultdict(dict)
    dividend_sessions: set[tuple[str, date]] = set()
    split_adjustments = 0
    for row in action_frame.sort("ticker", "ex_date", "action").select("ticker", "ex_date", "action", "value").to_dicts():
        ticker = row["ticker"]
        ex_date = row["ex_date"]
        action = row["action"]
        if not isinstance(ticker, str) or not ticker:
            raise TotalReturnError("frame_incomplete", "corporate-action row has no ticker")
        if not isinstance(ex_date, date):
            raise TotalReturnError("frame_incomplete", f"{ticker}: corporate-action row has no ex_date")
        if ticker not in closes:
            raise TotalReturnError(
                "unknown_instrument",
                f"{ticker} on {ex_date}: actions for an instrument absent from the price frame; its factors would be unpriced",
            )
        if action == SPLIT_ACTION:
            ratio = _split_ratio(row["value"], ticker, ex_date)
            split_products[ticker][ex_date] = split_products[ticker].get(ex_date, 1.0) * ratio
            split_adjustments += 1
        elif action == DIVIDEND_ACTION:
            amount = _dividend_amount(row["value"])
            session_key = (ticker, ex_date)
            if session_key in dividend_sessions:
                raise TotalReturnError(
                    "duplicate_dividend",
                    f"{ticker} on {ex_date}: a second dividend row for the same session; one session reinvests once",
                )
            close = closes[ticker].get(ex_date)
            if ex_date not in closes[ticker]:
                raise TotalReturnError(
                    "ex_date_without_price",
                    f"{ticker} on {ex_date}: no price row on the dividend ex-date, so there is no close to reinvest at",
                )
            usable = _usable_price(close)
            if usable is None:
                raise TotalReturnError(
                    "non_positive_close",
                    f"{ticker} on {ex_date}: close {close!r} is not a usable positive price for a reinvestment",
                )
            dividend_sessions.add(session_key)
            dividend_steps[ticker][ex_date] = 1.0 + amount / usable
        else:
            raise TotalReturnError(
                "unknown_action",
                f"{ticker} on {ex_date}: unknown corporate action {action!r} (expected 'dividend' or 'split')",
            )

    first_session: dict[str, date] = {ticker: min(sessions) for ticker, sessions in closes.items() if sessions}
    for ticker, session in first_session.items():
        if _usable_price(closes[ticker][session]) is None:
            raise TotalReturnError(
                "non_positive_close",
                f"{ticker} on {session}: close {closes[ticker][session]!r} is unusable as the first-session index base",
            )

    factors: dict[str, dict[date, tuple[float, float]]] = {}
    for ticker, sessions in closes.items():
        factors[ticker] = _session_factors(sorted(sessions), split_products[ticker], dividend_steps[ticker])

    bases: dict[str, float] = {}
    for ticker, session in first_session.items():
        split_factor, dividend_factor = factors[ticker][session]
        bases[ticker] = float(closes[ticker][session] or 0.0) * split_factor * dividend_factor

    levels: list[float | None] = []
    indices: list[float | None] = []
    for row in price_frame.select("ticker", "date").to_dicts():
        ticker = str(row["ticker"])
        session = row["date"]
        if not isinstance(session, date) or session not in factors[ticker]:
            levels.append(None)
            indices.append(None)
            continue
        close = closes[ticker][session]
        if close is None:
            # Null in, null out: a missing close is never filled or dropped.
            levels.append(None)
            indices.append(None)
            continue
        split_factor, dividend_factor = factors[ticker][session]
        level = close * split_factor * dividend_factor
        levels.append(level)
        indices.append(_INDEX_BASE * level / bases[ticker])

    frame = price_frame.with_columns(
        pl.Series("total_return_close", levels, dtype=pl.Float64),
        pl.Series("total_return_index", indices, dtype=pl.Float64),
        pl.Series("total_return_basis", [TOTAL_RETURN_BASIS] * price_frame.height, dtype=pl.String),
    )
    return TotalReturnSeries(
        frame=frame,
        dividend_reinvestments=len(dividend_sessions),
        split_adjustments=split_adjustments,
        basis=TOTAL_RETURN_BASIS,
        clock_basis=NO_SOURCE_CLOCK,
    )
