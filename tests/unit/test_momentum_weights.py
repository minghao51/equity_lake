"""Momentum weight locality, warmup and deterministic tie-selection regressions (F02).

Contract under test (spec §3.2 / §3.2a):

* the lookback return and the skip lag are computed inside one instrument window,
  so no instrument's lag can come from another symbol, venue or input row order;
* an instrument has no nonzero target before it has ``lookback + skip`` own
  observations, regardless of how much history its peers carry;
* duplicate identity/date rows are rejected instead of resolved by row order;
* candidate order is ``past_return`` descending then canonical identity
  ascending, selected by ordinal rank, so exactly ``top_count`` names are held
  whenever that many eligible instruments exist and the long targets sum to 1.0.

Every assertion names the pre-fix ("old rule") value it would have produced, so a
regression that reintroduces the old behaviour fails here rather than silently
producing a different portfolio.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import polars as pl
import pytest

from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE, VectorBacktestEngine
from equity_lake.backtesting.strategy.momentum import CrossSectionalMomentumStrategy
from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    ActionCoverageProof,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    InstrumentIdentityError,
)

requires_engine = pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")

START = date(2024, 1, 2)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _series_frame(closes: Mapping[str, Sequence[float | None]]) -> pl.DataFrame:
    """Long close-only frame, one row per instrument per consecutive calendar day."""
    rows: list[dict[str, object]] = [
        {"ticker": ticker, "date": START + timedelta(days=index), "close": close}
        for ticker, values in closes.items()
        for index, close in enumerate(values)
    ]
    return pl.DataFrame(rows)


def _venue_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """Long frame carrying canonical composite identity (market, mic, symbol)."""
    return pl.DataFrame(rows)


def _weights(strategy: CrossSectionalMomentumStrategy, data: pl.DataFrame) -> pl.DataFrame:
    """Generate weights and return a canonical key-sorted frame for comparison."""
    strategy.initialize(data)
    weights = cast(pl.DataFrame, strategy.generate_weights(data))
    keys = [column for column in ("date", "market", "mic", "symbol", "ticker") if column in weights.columns]
    return weights.sort(keys)


def _canonical_trades(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise NaN so two structurally identical trade books compare equal (NaN != NaN)."""
    return [{key: ("nan" if isinstance(value, float) and value != value else value) for key, value in trade.items()} for trade in trades]


def _weight_map(weights: pl.DataFrame, identity: str = "ticker") -> dict[tuple[date, str], float]:
    return {(row["date"], row[identity]): row["weight"] for row in weights.select("date", identity, "weight").to_dicts()}


def _past_return(closes: Sequence[float], index: int, lookback: int, skip: int) -> float | None:
    """Hand oracle: `close(t - skip) / close(t - skip - lookback) - 1`.

    Returns ``None`` while the instrument lacks the required ``lookback + skip``
    own observations. This is deliberately written with plain list indexing in the
    instrument's own date order, never with a dataframe window.
    """
    anchor = index - skip
    base = anchor - lookback
    if base < 0:
        return None
    return closes[anchor] / closes[base] - 1.0


# ---------------------------------------------------------------------------
# Locality: lookback and skip live inside one instrument window
# ---------------------------------------------------------------------------

AUDIT_PATHS: dict[str, list[float]] = {
    "A": [100.0, 110.0, 120.0, 130.0, 140.0],
    "B": [100.0, 90.0, 80.0, 70.0, 60.0],
    "C": [100.0, 101.0, 102.0, 103.0, 104.0],
}


def test_momentum_lag_is_instrument_local() -> None:
    """Audit A02 fixture: hand-computed past returns, no cross-instrument lag.

    Oracle (lookback=2, skip=1, i.e. `close(t-1) / close(t-3) - 1`):

    ========  ===========  ===========  ===========
    date      A            B            C
    ========  ===========  ===========  ===========
    01-02     None         None         None
    01-03     None         None         None
    01-04     None         None         None
    01-05     120/100-1   80/100-1     102/100-1    = +0.20 / -0.20 / +0.02
    01-06     130/110-1   70/90-1      103/101-1    = +0.181818.. / -0.222222.. / +0.019802..
    ========  ===========  ===========  ===========

    ``lookback + skip = 3``, so no instrument is a candidate before 01-05. With
    ``top_pct=0.3`` over 3 candidates, ``top_count = max(int(3 * 0.3), 1) = 1``,
    so A holds the only long target on both eligible dates.

    Old rule (the DataFrame-level ``shift`` after ``.over(identity)``): on a
    ticker-major frame B's first row borrows A's last lookback return (+0.20) and
    C's first row borrows B's last (-0.2222), so B would be the single 1.0 target
    on 01-02 and 01-03/01-04 would not be uniformly zero.
    """
    data = _series_frame(AUDIT_PATHS)
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 2, "skip_days": 1, "top_pct": 0.3, "rebalance_days": 1, "min_stocks": 1})
    weights = _weights(strategy, data)

    # Independent oracle: the documented arithmetic above, recomputed from the
    # raw series rather than from the strategy's own window expression.
    oracle = {(START + timedelta(days=i), ticker): _past_return(closes, i, 2, 1) for ticker, closes in AUDIT_PATHS.items() for i in range(5)}
    assert oracle[(START + timedelta(days=3), "A")] == pytest.approx(0.20, abs=1e-12)
    assert oracle[(START + timedelta(days=3), "B")] == pytest.approx(-0.20, abs=1e-12)
    assert oracle[(START + timedelta(days=3), "C")] == pytest.approx(0.02, abs=1e-12)
    assert oracle[(START + timedelta(days=4), "A")] == pytest.approx(130.0 / 110.0 - 1.0, abs=1e-12)
    assert oracle[(START + timedelta(days=4), "B")] == pytest.approx(70.0 / 90.0 - 1.0, abs=1e-12)
    assert oracle[(START + timedelta(days=4), "C")] == pytest.approx(103.0 / 101.0 - 1.0, abs=1e-12)

    got = _weight_map(weights)
    # Warm-up: 3 own observations required, so the first three sessions are flat.
    # Old rule: B carried a leaked +0.20 at 01-02/01-03 (weight 1.0 on 01-02).
    for offset in (0, 1, 2):
        for ticker in AUDIT_PATHS:
            assert got[(START + timedelta(days=offset), ticker)] == 0.0, f"{ticker} is inside its own warm-up on day {offset}"

    # Eligible dates: exactly one long target, the highest own past return (A).
    for offset in (3, 4):
        assert got[(START + timedelta(days=offset), "A")] == pytest.approx(1.0)
        assert got[(START + timedelta(days=offset), "B")] == 0.0
        assert got[(START + timedelta(days=offset), "C")] == 0.0

    # No instrument inherits the preceding instrument's return: B (the only
    # instrument whose own return is negative on 01-05/01-06) is never selected,
    # and the leaked values (-0.2222 on C's first row) never become a target.
    assert all(weight >= 0.0 for weight in weights["weight"].to_list())


def test_momentum_skip_lag_is_applied_within_the_same_instrument() -> None:
    """The skip really is a lag: `close(t-skip)/close(t-skip-lookback)-1`, not `close(t)`.

    lookback=2, skip=1, two instruments:

    * X [100, 100, 100, 200, 100]: correct past return on day 5 = 200/100-1 = +1.0;
      without the skip it would be 100/200-1 = -0.50.
    * Y [100, 100, 100, 100, 130]: correct = 100/100-1 = 0.0; without the skip
      it would be 130/100-1 = +0.30.

    ``min_stocks=1`` and ``top_pct=0.3`` give ``top_count = 1``, so the selected
    symbol discriminates the two rules: X under the contract, Y if the skip were
    dropped (the old-leak family of bugs).
    """
    data = _series_frame({"X": [100.0, 100.0, 100.0, 200.0, 100.0], "Y": [100.0, 100.0, 100.0, 100.0, 130.0]})
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 2, "skip_days": 1, "top_pct": 0.3, "rebalance_days": 1, "min_stocks": 1})
    got = _weight_map(_weights(strategy, data))

    oracle_x = _past_return([100.0, 100.0, 100.0, 200.0, 100.0], 4, 2, 1)
    oracle_y = _past_return([100.0, 100.0, 100.0, 100.0, 130.0], 4, 2, 1)
    assert oracle_x is not None
    assert oracle_y is not None
    assert oracle_x == pytest.approx(1.0, abs=1e-12)
    assert oracle_y == pytest.approx(0.0, abs=1e-12)
    assert oracle_x > oracle_y

    last = START + timedelta(days=4)
    assert got[(last, "X")] == pytest.approx(1.0)
    assert got[(last, "Y")] == 0.0
    # Old/no-skip rule would have picked Y at 1.0 (X = -0.50 < Y = +0.30 = 1.0).


# ---------------------------------------------------------------------------
# Order invariance
# ---------------------------------------------------------------------------


def test_momentum_is_order_invariant() -> None:
    """Ticker-major, date-major and shuffled inputs produce identical sorted weights.

    Old rule: the DataFrame-level ``shift`` read the previous *row*, so the
    date-major frame borrowed other symbols' returns and produced a different
    allocation (audit A02: ``order-invariant allocations: False``).
    """
    closes = {
        "A": [100.0, 110.0, 120.0, 130.0, 140.0],
        "B": [100.0, 90.0, 80.0, 70.0, 60.0],
        "C": [100.0, 101.0, 102.0, 103.0, 104.0],
        "D": [100.0, 105.0, 99.0, 108.0, 90.0],
    }
    base = _series_frame(closes)
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 2, "skip_days": 1, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})

    ticker_major = _weights(strategy, base.sort(["ticker", "date"]))
    date_major = _weights(strategy, base.sort(["date", "ticker"]))
    shuffled = _weights(strategy, base.sample(fraction=1.0, shuffle=True, seed=11))

    assert ticker_major.equals(date_major)
    assert ticker_major.equals(shuffled)
    # Non-vacuous: the comparison is over a portfolio that actually invested.
    assert ticker_major["weight"].max() == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Warm-up and staggered listings
# ---------------------------------------------------------------------------


def test_momentum_warmup_requires_own_lookback_plus_skip() -> None:
    """No nonzero target before ``lookback + skip`` own observations (L=3, S=2 here).

    All four instruments start together: the first five sessions must be flat
    because ``3 + 2 = 5`` prior observations are required. Old rule: the
    DataFrame-level shift consumed one extra row per instrument block, so the
    first row of each block inherited the previous block's return and could be
    selected one session early (e.g. B at 1.0 on the very first date).
    """
    closes = {
        "A": [100.0, 102.0, 101.0, 105.0, 104.0, 110.0, 108.0, 115.0],
        "B": [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0],
        "C": [100.0, 101.0, 100.5, 101.5, 101.0, 102.0, 101.5, 103.0],
        "D": [100.0, 103.0, 106.0, 109.0, 112.0, 115.0, 118.0, 121.0],
    }
    data = _series_frame(closes)
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 3, "skip_days": 2, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})
    got = _weight_map(_weights(strategy, data))

    warmup = 3 + 2
    for index in range(warmup):
        for ticker in closes:
            assert got[(START + timedelta(days=index), ticker)] == 0.0, f"{ticker} cannot trade inside its own warm-up"
            assert _past_return(closes[ticker], index, 3, 2) is None

    # First eligible session: D has the highest own past return (109/100-1) and
    # owns the whole book at top_count = max(int(4 * 0.5), 1) = 2 -> 0.5 each.
    first = START + timedelta(days=warmup)
    assert _past_return(closes["D"], warmup, 3, 2) == pytest.approx(109.0 / 100.0 - 1.0, abs=1e-12)
    assert _past_return(closes["A"], warmup, 3, 2) == pytest.approx(105.0 / 100.0 - 1.0, abs=1e-12)
    assert got[(first, "D")] == pytest.approx(0.5)
    assert got[(first, "A")] == pytest.approx(0.5)
    assert got[(first, "B")] == 0.0
    assert got[(first, "C")] == 0.0


def test_momentum_staggered_listing_stays_flat_until_own_history() -> None:
    """A late listing gets zero weight until it owns ``lookback + skip`` observations.

    B finishes with a +100% two-day move (200/100-1) which is the largest past
    return in the universe. Old rule: on its first session C's row inherited
    exactly that value from the end of B's block (the DataFrame-level shift), so C
    would rank first and be handed the single 1.0 target on its listing date.
    """
    closes = {
        "A": [100.0 + index for index in range(10)],
        "B": [100.0] * 9 + [200.0],
        "C": [100.0, 130.0, 140.0, 150.0, 160.0],
    }
    rows = (
        [{"ticker": "A", "date": START + timedelta(days=index), "close": close} for index, close in enumerate(closes["A"])]
        + [{"ticker": "B", "date": START + timedelta(days=index), "close": close} for index, close in enumerate(closes["B"])]
        + [{"ticker": "C", "date": START + timedelta(days=5 + index), "close": close} for index, close in enumerate(closes["C"])]
    )
    data = pl.DataFrame(rows)
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 2, "skip_days": 1, "top_pct": 0.3, "rebalance_days": 1, "min_stocks": 1})
    got = _weight_map(_weights(strategy, data))

    # C requires 2 + 1 = 3 own observations: its listing date plus the next two
    # sessions stay flat even though A and B already carry ten sessions each.
    for offset in (0, 1, 2):
        assert got[(START + timedelta(days=5 + offset), "C")] == 0.0
    assert _past_return(closes["C"], 2, 2, 1) is None
    # B's own lagged return on its last row is 0.0 (close(8)/close(6) - 1), yet the
    # old DataFrame-level shift moved B's last-row *unsifted* lookback return
    # (200/100 - 1 = +1.0) into C's first row, where it ranked first and took the
    # single 1.0 target on the listing date.
    assert _past_return(closes["B"], 9, 2, 1) == pytest.approx(0.0, abs=1e-12)
    assert 200.0 / closes["B"][7] - 1.0 == pytest.approx(1.0, abs=1e-12)

    # First eligible C session: 140/100-1 = +0.40 beats A (+0.019) and B (0.0), so
    # C takes the target; the +100% move B posts on its own last row is invisible
    # to C's window either way.
    first_c = START + timedelta(days=8)
    assert _past_return(closes["C"], 3, 2, 1) == pytest.approx(0.40, abs=1e-12)
    assert got[(first_c, "C")] == pytest.approx(1.0)
    assert got[(first_c, "A")] == 0.0
    assert got[(first_c, "B")] == 0.0


def test_momentum_insufficient_universe_keeps_previous_weights() -> None:
    """A rebalance date below ``min_stocks`` makes no new selection (hold semantics).

    D's close gap on day 4 makes its past return unknown on days 4-5, so day 5
    has only three eligible candidates against ``min_stocks=4``. The strategy must
    keep day 4's book instead of re-ranking the three survivors; a hypothetical
    re-rank would hand the target to C (day-5 past returns: A=+0.05, B=-0.04,
    C=+0.20), so the assertion distinguishes hold from re-selection.
    """
    closes: dict[str, list[float | None]] = {
        "A": [100.0, 100.0, 100.0, 100.0, 105.0, 110.25],
        "B": [100.0, 100.0, 100.0, 104.0, 100.0, 96.0],
        "C": [100.0, 100.0, 100.0, 100.0, 100.0, 120.0],
        "D": [100.0, 100.0, 100.0, 100.0, 100.0, None],
    }
    data = _series_frame(closes)
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.25, "rebalance_days": 1, "min_stocks": 4})
    got = _weight_map(_weights(strategy, data))

    # Day 3 (index 3): four eligible names, B = 104/100-1 = +0.04 is best,
    # top_count = max(int(4 * 0.25), 1) = 1.
    day3 = START + timedelta(days=3)
    assert got[(day3, "B")] == pytest.approx(1.0)
    assert got[(day3, "A")] == 0.0

    # Day 4 (index 4): four eligible names again, A = 105/104-1 = +0.0096 is best,
    # so the book rotates from B to A.
    day4 = START + timedelta(days=4)
    assert got[(day4, "A")] == pytest.approx(1.0)
    assert got[(day4, "B")] == 0.0

    # Day 5 (index 5): D's close gap makes it ineligible, so only A/B/C remain
    # against min_stocks=4 and no new selection happens.
    day5 = START + timedelta(days=5)
    assert got[(day5, "A")] == pytest.approx(1.0), "no new selection on an insufficient-universe date (hold A)"
    assert got[(day5, "C")] == 0.0, "C would win a re-rank (120/100-1 = +0.20); the hold must keep A"


# ---------------------------------------------------------------------------
# Identity integrity
# ---------------------------------------------------------------------------


def test_momentum_rejects_duplicate_identity_date() -> None:
    """Duplicate identity/date rows raise instead of being resolved by row order."""
    data = pl.concat(
        [
            _series_frame({"A": [100.0, 110.0], "B": [100.0, 90.0]}),
            pl.DataFrame([{"ticker": "A", "date": START + timedelta(days=1), "close": 111.0}]),
        ]
    )
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})
    strategy.initialize(data)
    with pytest.raises(InstrumentIdentityError) as excinfo:
        strategy.generate_weights(data)
    message = str(excinfo.value)
    assert "ticker='A'" in message
    assert str(START + timedelta(days=1)) in message
    assert isinstance(excinfo.value, ValueError)  # documented exception family


def test_momentum_rejects_duplicate_composite_identity_date() -> None:
    """The duplicate key is the full canonical identity plus date, not the ticker."""
    base = _venue_frame(
        [
            {"market": "us_equity", "mic": "XNAS", "symbol": "DUP", "ticker": "DUP", "date": START, "close": 50.0},
            {"market": "us_equity", "mic": "XNYS", "symbol": "DUP", "ticker": "DUP", "date": START, "close": 100.0},
        ]
    )
    data = pl.concat([base, base.filter(pl.col("mic") == "XNYS")])
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})
    strategy.initialize(data)
    with pytest.raises(InstrumentIdentityError, match=r"market='us_equity'.*mic='XNYS'.*symbol='DUP'"):
        strategy.generate_weights(data)


def test_momentum_keeps_same_symbol_on_two_mics_distinct() -> None:
    """Identical symbols on two MICs are separate instruments; their returns never mix.

    XNYS ``DUP`` [100, 105, 102.9] -> +5.0% then -2.0%.
    XNAS ``DUP`` [50, 60, 63]     -> +20.0% then +5.0%.
    ``top_pct=0.5`` over 2 candidates gives ``top_count=1``: XNAS wins both
    sessions. If the two venues shared one ticker window (or a shift crossed the
    block boundary) the day-2 XNYS return would be computed against the XNAS
    close and the allocation would flip.
    """
    rows: list[dict[str, object]] = []
    for mic, closes in {"XNYS": [100.0, 105.0, 102.9], "XNAS": [50.0, 60.0, 63.0]}.items():
        for index, close in enumerate(closes):
            rows.append({"market": "us_equity", "mic": mic, "symbol": "DUP", "ticker": "DUP", "date": START + timedelta(days=index), "close": close})
    data = _venue_frame(rows)
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})
    weights = _weights(strategy, data)

    assert weights.select("market", "mic", "symbol").unique().height == 2
    got = {(row["date"], row["mic"]): row["weight"] for row in weights.select("date", "mic", "weight").to_dicts()}
    day1 = START + timedelta(days=1)
    day2 = START + timedelta(days=2)
    assert got[(day1, "XNAS")] == pytest.approx(1.0)  # +20% vs +5%
    assert got[(day1, "XNYS")] == 0.0
    assert got[(day2, "XNAS")] == pytest.approx(1.0)  # +5% vs -2%
    assert got[(day2, "XNYS")] == 0.0
    # Merged-window counterfactual: a single shared window would see day-2 return
    # 63/105-1 = -0.40 for XNAS, i.e. XNYS would keep the target instead.
    assert 63.0 / 105.0 - 1.0 < 102.9 / 100.0 * 0 + (-0.02)


# ---------------------------------------------------------------------------
# Truncation / future append
# ---------------------------------------------------------------------------


def test_momentum_future_append_does_not_change_past_weights() -> None:
    """Appending later dates leaves every weight on/before the cutoff unchanged."""
    closes = {
        "A": [100.0, 104.0, 103.0, 107.0, 106.0],
        "B": [100.0, 98.0, 101.0, 99.0, 102.0],
        "C": [100.0, 100.5, 99.5, 100.2, 100.1],
    }
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 2, "skip_days": 1, "top_pct": 0.6, "rebalance_days": 1, "min_stocks": 1})
    history = _series_frame(closes)
    earlier = _weights(strategy, history)

    cutoff = START + timedelta(days=4)
    future_rows = pl.DataFrame(
        [
            {"ticker": ticker, "date": cutoff + timedelta(days=offset), "close": close}
            for ticker, tail in {"A": [130.0, 70.0], "B": [60.0, 140.0], "C": [100.0, 100.0]}.items()
            for offset, close in enumerate(tail, start=1)
        ]
    )
    extended = _weights(strategy, pl.concat([history, future_rows]))

    keys = ["date", "ticker"]
    earlier_window = earlier.filter(pl.col("date") <= cutoff).sort(keys)
    extended_window = extended.filter(pl.col("date") <= cutoff).sort(keys)
    assert earlier_window.height == extended_window.height == 15
    assert earlier_window.equals(extended_window)
    # Non-vacuous: the cutoff window is invested, and the appended tail is not flat.
    assert cast(float, earlier_window["weight"].max()) > 0.0
    assert cast(float, extended.filter(pl.col("date") > cutoff)["weight"].max()) > 0.0
    # Old rule check: day-5 past returns (2,1) are 106/103-1=+0.0291 for A,
    # 102/101-1=+0.0099 for B and 100.1/99.5-1=+0.0060 for C, so both the
    # truncated and the extended frame must hold only A at 1.0 there.
    assert earlier_window.filter(pl.col("date") == cutoff)["weight"].to_list() == [1.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# §3.2a deterministic tie policy
# ---------------------------------------------------------------------------


def test_momentum_tie_at_cut_keeps_full_investment() -> None:
    """A tie straddling the cut selects exactly ``top_count`` names summing to 1.0.

    Probe from spec §3.2a: four instruments, ``lookback=1``, ``skip=0``, A and B
    tied best (+10%). With ``top_pct=0.5``, ``top_count = max(int(4 * 0.5), 1) = 2``,
    so A and B each hold 0.5. Old rule (average ``rank()``): both tied names rank
    1.5, nothing satisfies ``rank <= 2``... the cut keeps zero names while the
    target size stays 1/2, so every weight was 0.0 — an unreported full-cash book.

    The spec's written probe uses ``top_pct=0.2``, where
    ``top_count = max(int(4 * 0.2), 1) = 1`` — that selects exactly one name at
    1.0, not two at 0.5. Both readings are asserted: full investment at the real
    ``top_count``, with the identity tie-break picking A first.
    """
    closes = {"A": [100.0, 110.0], "B": [100.0, 110.0], "C": [100.0, 101.0], "D": [100.0, 102.0]}
    data = _series_frame(closes)

    half = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})
    weights = _weights(half, data)
    eligible = weights.filter(pl.col("date") == START + timedelta(days=1))
    assert eligible.height == 4
    selected = eligible.filter(pl.col("weight") != 0.0).sort("ticker")
    assert selected["ticker"].to_list() == ["A", "B"], "both tied best names must be selected (old rule: none)"
    assert selected["weight"].to_list() == [pytest.approx(0.5), pytest.approx(0.5)]
    assert eligible["weight"].sum() == pytest.approx(1.0), "old rule summed to 0.0"

    fifth = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.2, "rebalance_days": 1, "min_stocks": 1})
    weights_02 = _weights(fifth, data)
    eligible_02 = weights_02.filter(pl.col("date") == START + timedelta(days=1))
    selected_02 = eligible_02.filter(pl.col("weight") != 0.0).sort("ticker")
    assert selected_02["ticker"].to_list() == ["A"], "top_count=1 with the identity tie-break (old rule: none)"
    assert selected_02["weight"].to_list() == [pytest.approx(1.0)]
    assert eligible_02["weight"].sum() == pytest.approx(1.0), "old rule summed to 0.0"


def test_momentum_tie_break_is_identity_deterministic() -> None:
    """Equal returns select the identity-ascending names, independent of input order.

    Five instruments with identical paths, ``top_pct=0.4`` -> ``top_count=2``:
    the two identity-ascending names (A, B) are selected. Old rule: every name
    ranked 3.0, so nothing cleared ``rank <= 2`` and the whole book stayed at 0.0.
    """
    closes = {ticker: [100.0, 110.0] for ticker in ("A", "B", "C", "D", "E")}
    data = _series_frame(closes)
    strategy = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.4, "rebalance_days": 1, "min_stocks": 1})

    first = _weights(strategy, data.sort(["ticker", "date"]))
    second = _weights(strategy, data.sort(["ticker", "date"], descending=True))
    shuffled = _weights(strategy, data.sample(fraction=1.0, shuffle=True, seed=3))

    assert first.equals(second)
    assert first.equals(shuffled)
    selected = first.filter((pl.col("date") == START + timedelta(days=1)) & (pl.col("weight") != 0.0))
    assert selected["ticker"].to_list() == ["A", "B"], "identity-ascending tie-break (old rule: 0.0 everywhere)"
    assert selected["weight"].to_list() == [pytest.approx(0.5), pytest.approx(0.5)]

    # Composite identity uses (market, mic, symbol) ascending: XNAS sorts before XNYS.
    venues = _venue_frame(
        [
            {"market": "us_equity", "mic": mic, "symbol": "DUP", "ticker": "DUP", "date": START + timedelta(days=index), "close": close}
            for mic in ("XNYS", "XNAS")
            for index, close in enumerate([50.0, 60.0])
        ]
    )
    venue_strategy = CrossSectionalMomentumStrategy({"lookback_days": 1, "skip_days": 0, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1})
    venue_weights = _weights(venue_strategy, venues)
    venue_weights_reversed = _weights(venue_strategy, venues.sort(["mic", "date"], descending=True))
    assert venue_weights.equals(venue_weights_reversed)
    day1 = venue_weights.filter(pl.col("date") == START + timedelta(days=1))
    assert day1.filter(pl.col("weight") != 0.0)["mic"].to_list() == ["XNAS"], "identity-ascending tie-break across venues (old rule: 0.0)"


def test_momentum_long_short_overlap_keeps_long_precedence() -> None:
    """When the long and short sets overlap, the long side keeps the name.

    ``long_only=False``, ``top_pct = bottom_pct = 0.75`` over four candidates:
    ``top_count = bottom_count = 3``. Ranks 1-3 are long at +1/3; the short rule
    ("rank > 4 - 3" i.e. ranks 2-4) only applies to rank 4, so D carries -1/3 and
    the book sums to +2/3. A naive short-first implementation would instead give
    B and C -1/3 and sum to -1/3.
    """
    closes = {"A": [100.0, 130.0], "B": [100.0, 120.0], "C": [100.0, 110.0], "D": [100.0, 90.0]}
    data = _series_frame(closes)
    strategy = CrossSectionalMomentumStrategy(
        {"lookback_days": 1, "skip_days": 0, "top_pct": 0.75, "bottom_pct": 0.75, "rebalance_days": 1, "min_stocks": 1, "long_only": False}
    )
    got = _weight_map(_weights(strategy, data))
    day1 = START + timedelta(days=1)
    assert got[(day1, "A")] == pytest.approx(1.0 / 3.0)
    assert got[(day1, "B")] == pytest.approx(1.0 / 3.0)
    assert got[(day1, "C")] == pytest.approx(1.0 / 3.0)
    assert got[(day1, "D")] == pytest.approx(-1.0 / 3.0)
    assert sum(weight for (day, _ticker), weight in got.items() if day == day1) == pytest.approx(1.0 / 3.0 * 3 - 1.0 / 3.0)


# ---------------------------------------------------------------------------
# Engine-level as-of integration
# ---------------------------------------------------------------------------

# Shared by both engine regressions: 12 daily bars per instrument with the as-of
# cutoff on bar 8, so four bars of post-cutoff content exist to leak from.
_ENGINE_SERIES: dict[str, list[float]] = {
    "T00": [100.0, 101.0, 102.5, 101.0, 104.0, 106.0, 105.0, 108.0, 110.0, 109.0, 112.0, 115.0],
    "T01": [100.0, 99.0, 98.5, 100.0, 99.0, 97.0, 98.0, 96.0, 95.0, 97.0, 96.0, 94.0],
    "T02": [100.0, 103.0, 101.0, 105.0, 107.0, 104.0, 109.0, 111.0, 108.0, 112.0, 114.0, 113.0],
}
_ENGINE_PARAMS: dict[str, Any] = {"lookback_days": 2, "skip_days": 1, "top_pct": 0.5, "rebalance_days": 1, "min_stocks": 1}
_ENGINE_CUTOFF = START + timedelta(days=7)

# Distinct per-instrument post-cutoff price scales: they invert the cross-sectional
# order at the last bar, so a future-leaker that only reads ranks is caught too.
_POST_CUTOFF_PRICE_FACTORS: dict[str, float] = {"T00": 1.0, "T01": 8.0, "T02": 4.0}
_VOLUME_PERTURBATION = 11.0
_PERTURBED_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    "__pit_raw_open",
    "__pit_raw_high",
    "__pit_raw_low",
    "__pit_raw_close",
    "__pit_raw_volume",
)


def _engine_frame(series: dict[str, list[float]]) -> pl.DataFrame:
    """Synthetic OHLCV frame with verified, source-issued action-coverage context.

    Mirrors ``tests/unit/test_arena.py::_synthetic_data``: canonical identity per
    row, a hidden raw-price copy for each OHLCV column and one verified
    point-in-time coverage proof per instrument (no actions, so the adjusted mark
    chain is the raw close ratio).
    """
    dates = [START + timedelta(days=index) for index in range(max(len(values) for values in series.values()))]
    rows: list[dict[str, object]] = []
    for ticker, closes in series.items():
        for index, close in enumerate(closes):
            rows.append(
                {
                    "ticker": ticker,
                    "date": dates[index],
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 1_000_000.0,
                }
            )
    data = pl.DataFrame(rows).with_columns(
        pl.lit("us_equity").alias("market"),
        pl.lit("XNYS").alias("mic"),
        pl.col("ticker").alias("symbol"),
    )
    context_by_ticker: dict[str, str] = {}
    for ticker in series:
        proof = ActionCoverageProof(
            market="us_equity",
            mic="XNYS",
            symbol=ticker,
            source="synthetic-f02-engine",
            coverage_start=min(dates),
            coverage_end=max(dates),
            source_available_at=datetime.combine(min(dates) - timedelta(days=1), datetime.min.time(), tzinfo=UTC),
            source_valid_until=datetime.combine(max(dates), datetime.max.time(), tzinfo=UTC),
        )
        context_by_ticker[ticker] = json.dumps({"actions": [], "coverage": [proof.model_dump(mode="json")]})
    return data.with_columns(
        *[pl.col(column).alias(f"__pit_raw_{column}") for column in ("open", "high", "low", "close", "volume")],
        pl.col("ticker").replace_strict(context_by_ticker, return_dtype=pl.String).alias("__pit_action_context"),
    )


def _perturb_post_cutoff(frame: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """Rewrite every post-cutoff row so a future leak cannot stay invisible.

    Rows after ``cutoff`` are path-reversed per instrument, prices -- both the
    visible OHLC and the hidden ``__pit_raw_*`` twins the engine actually reads --
    are scaled by their ``_POST_CUTOFF_PRICE_FACTORS`` entry and volume by
    ``_VOLUME_PERTURBATION``. The scales differ per instrument so the
    cross-sectional order at the last bar inverts, catching a leaker that only
    consumes rankings. The action-coverage payload is per instrument (the engine
    rejects divergent payloads for one identity), so its post-cutoff content is
    perturbed the only way the contract allows: the covered window is extended
    past the cutoff and a 4:1 split dated the first post-cutoff bar is added.
    Rows on/before the cutoff keep their prices, volume and coverage start
    untouched.
    """
    late = pl.col("date") > cutoff
    factor = pl.col("ticker").replace_strict(_POST_CUTOFF_PRICE_FACTORS, return_dtype=pl.Float64)
    tail = (
        frame.filter(late)
        .sort(["ticker", "date"])
        .with_columns(*[pl.col(column).reverse().over("ticker").alias(column) for column in _PERTURBED_COLUMNS])
    )
    tail = tail.with_columns(
        *[(pl.col(column) * (_VOLUME_PERTURBATION if column.endswith("volume") else factor)).alias(column) for column in _PERTURBED_COLUMNS]
    )
    # The reversal swaps high/low; repair the envelope so the bars stay plausible.
    tail = tail.with_columns(
        pl.max_horizontal("open", "high", "low", "close").alias("high"),
        pl.min_horizontal("open", "high", "low", "close").alias("low"),
        pl.max_horizontal("__pit_raw_open", "__pit_raw_high", "__pit_raw_low", "__pit_raw_close").alias("__pit_raw_high"),
        pl.min_horizontal("__pit_raw_open", "__pit_raw_high", "__pit_raw_low", "__pit_raw_close").alias("__pit_raw_low"),
    )
    reversed_frame = pl.concat([frame.filter(~late), tail], how="vertical")

    split_date = cutoff + timedelta(days=1)
    perturbed_contexts: dict[str, str] = {}
    for row in reversed_frame.select("ticker", "__pit_action_context").unique().to_dicts():
        payload = json.loads(cast(str, row["__pit_action_context"]))
        proof = payload["coverage"][0]
        proof["coverage_end"] = str(split_date + timedelta(days=9))
        payload["actions"].append(
            {
                "market": proof["market"],
                "mic": proof["mic"],
                "symbol": proof["symbol"],
                "ticker": row["ticker"],
                "ex_date": str(split_date),
                "action": "split",
                "value": 4.0,
                "source": proof["source"],
                "source_available_at": datetime.combine(split_date - timedelta(days=1), datetime.min.time(), tzinfo=UTC).isoformat(),
            }
        )
        perturbed_contexts[cast(str, row["ticker"])] = json.dumps(payload)
    return reversed_frame.with_columns(pl.col("ticker").replace_strict(perturbed_contexts, return_dtype=pl.String).alias("__pit_action_context"))


@requires_engine
@pytest.mark.slow
def test_engine_as_of_cutoff_matches_truncated_history() -> None:
    """(a) An engine run bounded at a cutoff equals a run on history truncated there.

    The full frame is preloaded and the engine is given ``end_date=cutoff``; the
    same engine is then fed only rows on/before the cutoff. Equity curves and
    trades must be identical for the window, i.e. later bars cannot influence
    earlier decisions or execution marks. This half only proves that the rows
    *beyond* the cutoff are not needed; it cannot see a component that reads them
    while they are present -- that is what
    ``test_engine_ignores_post_cutoff_row_content_as_of_cutoff`` falsifies.
    """
    frame = _engine_frame(_ENGINE_SERIES)
    cutoff = _ENGINE_CUTOFF

    def run_as_of(data: pl.DataFrame) -> Any:
        return VectorBacktestEngine(
            strategy=CrossSectionalMomentumStrategy(dict(_ENGINE_PARAMS)),
            tickers=list(_ENGINE_SERIES),
            start_date=START,
            end_date=cutoff,
            initial_cash=100_000.0,
            preloaded_data=data,
        ).run()

    full = run_as_of(frame)
    truncated = run_as_of(frame.filter(pl.col("date") <= cutoff))

    assert full.equity_curve.len() > 1
    assert full.equity_curve.to_list() == truncated.equity_curve.to_list()
    assert _canonical_trades(full.trades) == _canonical_trades(truncated.trades)
    # Non-vacuous: the window actually traded, so this compares real execution,
    # not two empty books (old leak: the first block row borrowed a peer's return,
    # which would change the earlier entries in one of the two runs).
    assert full.trades, "the as-of window must contain at least one trade"


@requires_engine
@pytest.mark.slow
def test_engine_ignores_post_cutoff_row_content_as_of_cutoff() -> None:
    """(b) Post-cutoff row *content* must not reach an as-of-cutoff engine run.

    Falsifies the leak the truncation-equivalence test structurally cannot see: a
    strategy or engine path that reads rows dated after ``end_date`` while the
    frame still carries them. Two frames share identical rows on/before the
    cutoff and differ materially after it (reversed price path, per-instrument
    price scales of 1x/8x/4x that also invert the post-cutoff cross-sectional
    order, and 11x volume on both the visible OHLC and the hidden ``__pit_raw_*``
    columns the engine reads, plus an extended coverage window and a post-cutoff
    4:1 split in the action context). With ``end_date=cutoff`` the engine must
    produce an identical equity curve and trade book from both frames. The
    perturbation is provably material: it changes the result as soon as those same
    rows fall inside a later window, so the equality cannot be explained by an
    engine that ignores prices.

    Concretely, a component that peeks at future rows would have to pass: (i)
    keep trades and marks on/before the cutoff byte-identical while every
    post-cutoff close is path-reversed and rescaled per instrument (so the last
    bar's cross-section reorders), every post-cutoff volume is 11x, and each
    instrument's coverage window reaches further forward with a new future split;
    and (ii) still change its output when that same tail is pulled inside the
    window. A strategy that read the frame tail (last close, full-frame return or
    any future row) instead of its as-of window breaks the first half, as does a
    component that derives its signal from the post-cutoff coverage window or the
    future action set. The price path/rank inversion is the load-bearing part: a
    split factor applied uniformly is scale-cancelling in this engine's
    split-neutral mark chain, so the action-context change alone would be weak.
    """
    frame = _engine_frame(_ENGINE_SERIES)
    cutoff = _ENGINE_CUTOFF
    perturbed = _perturb_post_cutoff(frame, cutoff)
    last_date = cast(date, frame["date"].max())

    def run_window(data: pl.DataFrame, end_date: date) -> Any:
        return VectorBacktestEngine(
            strategy=CrossSectionalMomentumStrategy(dict(_ENGINE_PARAMS)),
            tickers=list(_ENGINE_SERIES),
            start_date=START,
            end_date=end_date,
            initial_cash=100_000.0,
            preloaded_data=data,
        ).run()

    as_of = run_window(frame, cutoff)
    as_of_perturbed = run_window(perturbed, cutoff)

    assert as_of.equity_curve.len() > 1
    assert as_of.trades, "the as-of window must contain at least one trade"

    # The load-bearing comparison: identical post-cutoff rows, different content.
    assert as_of.equity_curve.to_list() == as_of_perturbed.equity_curve.to_list()
    assert _canonical_trades(as_of.trades) == _canonical_trades(as_of_perturbed.trades)

    # The perturbation is real and confined to the post-cutoff rows.
    late = pl.col("date") > cutoff
    assert frame.filter(~late).select(_PERTURBED_COLUMNS).equals(perturbed.filter(~late).select(_PERTURBED_COLUMNS))
    before = frame.filter(late).sort(["ticker", "date"])
    after = perturbed.filter(late).sort(["ticker", "date"])
    assert before.height == 4 * len(_ENGINE_SERIES), "4 post-cutoff bars per instrument"
    for column in ("close", "__pit_raw_close", "high", "low", "volume", "__pit_raw_volume"):
        assert before[column].to_list() != after[column].to_list(), f"post-cutoff {column} was not perturbed"

    def cross_section_at_last_bar(data: pl.DataFrame) -> list[str]:
        return data.filter(pl.col("date") == last_date).sort("close", descending=True)["ticker"].to_list()

    # Distinct per-instrument scales invert the post-cutoff order, so the tail is
    # materially different for magnitude *and* rank based readers.
    assert cross_section_at_last_bar(frame) != cross_section_at_last_bar(perturbed)
    original_payload = json.loads(cast(str, frame["__pit_action_context"][0]))
    perturbed_payload = json.loads(cast(str, perturbed["__pit_action_context"][0]))
    assert perturbed_payload["coverage"][0]["coverage_start"] == original_payload["coverage"][0]["coverage_start"]
    assert perturbed_payload["coverage"][0]["coverage_end"] > original_payload["coverage"][0]["coverage_end"]
    assert original_payload["actions"] == []
    assert [row["ex_date"] for row in perturbed_payload["actions"]] == [str(cutoff + timedelta(days=1))]

    # ...and it is material: inside a later window the same tail changes the run,
    # so the equalities above are an as-of invariance, not price-blindness.
    extended = run_window(frame, last_date)
    extended_perturbed = run_window(perturbed, last_date)
    assert extended.equity_curve.len() > as_of.equity_curve.len()
    assert extended.equity_curve.to_list() != extended_perturbed.equity_curve.to_list()


def test_engine_frame_fixture_bounds_are_as_of_safe() -> None:
    """Guard the engine fixture itself: no raw row sits beyond the declared proof."""
    frame = _engine_frame({"T00": [100.0, 101.0, 102.0]})
    contexts = frame["__pit_action_context"].unique().to_list()
    assert len(contexts) == 1
    proof = json.loads(cast(str, contexts[0]))["coverage"][0]
    assert proof["coverage_start"] == str(START)
    assert proof["coverage_end"] == str(START + timedelta(days=2))
    assert cast(date, frame["date"].max()) == START + timedelta(days=2)
