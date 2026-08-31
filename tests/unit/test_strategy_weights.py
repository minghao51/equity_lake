"""Weight-shape tests for hold-until-opposite-signal strategies (handoff 08, B1).

The strategies must emit a *contiguous nonzero weight block* from the entry
event until the opposite exit event — not a one-day spike on the signal bar.
The engine executes weight changes at the *next* bar's close, verified at the
engine level via ``entry_date > entry_sig_date``.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from equity_lake.backtesting.engine import POLARS_BACKTEST_AVAILABLE, VectorBacktestEngine
from equity_lake.backtesting.strategy.mean_reversion import BBMeanReversionStrategy
from equity_lake.backtesting.strategy.trend_following import SMACrossoverStrategy


def _frame(closes: list[float], ticker: str = "TEST", start: date = date(2025, 1, 1)) -> pl.DataFrame:
    """Long OHLCV frame from a close series (one row per business day)."""
    d0 = start
    dates = []
    d = d0
    while len(dates) < len(closes):
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    return pl.DataFrame(
        {
            "ticker": [ticker] * len(closes),
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )


def _blocks(weights: pl.Series) -> list[list[int]]:
    """Indices of contiguous nonzero blocks."""
    blocks: list[list[int]] = []
    current: list[int] = []
    for i, w in enumerate(weights.to_list()):
        if w != 0.0:
            current.append(i)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    return blocks


# ---------------------------------------------------------------------------
# SMA crossover — hold from golden cross until cross-under
# ---------------------------------------------------------------------------


def test_sma_holds_from_golden_cross_until_cross_under() -> None:
    """One trend leg: contiguous 1.0 block from the cross bar to the bar before cross-under."""
    # Flat (MAs warm up in an exactly-neutral regime), then a rising leg (golden
    # cross AFTER warm-up), then a falling leg (cross-under).
    closes = (
        [100.0] * 25  # warm-up, fast == slow
        + [100.0 + 1.0 * i for i in range(1, 35)]  # uptrend -> golden cross
        + [134.0 - 1.5 * i for i in range(1, 35)]  # downtrend -> cross-under
    )
    data = _frame(closes)
    strat = SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20})
    strat.initialize(data)
    weights = strat.generate_weights(data).sort("date")

    assert weights["weight"].dtype == pl.Float64
    blocks = _blocks(weights["weight"])
    assert len(blocks) == 1, "exactly one long leg expected"
    block = blocks[0]
    # Multi-day hold — the whole point of the fix (was a single bar before).
    assert len(block) > 10
    assert all(weights["weight"][i] == 1.0 for i in block)

    # The block starts exactly on the golden-cross bar and ends the bar before
    # the cross-under bar (the cross-under bar itself carries weight 0).
    df = strat._data_with_indicators
    fast = df["fast_ma"].to_list()
    slow = df["slow_ma"].to_list()
    cross_idx = next(i for i in range(20, len(closes)) if fast[i] > slow[i] and fast[i - 1] <= slow[i - 1])
    cross_under_idx = next(i for i in range(cross_idx + 1, len(closes)) if fast[i] <= slow[i] and fast[i - 1] > slow[i - 1])
    assert block[0] == cross_idx
    assert block[-1] == cross_under_idx - 1
    assert weights["weight"][cross_under_idx] == 0.0


def test_sma_no_weight_before_first_cross_or_in_warmup() -> None:
    """Warm-up (null MAs) and pre-cross bars carry zero weight."""
    closes = [100.0 + 0.5 * i for i in range(30)]  # steady rise, never crosses down
    data = _frame(closes)
    strat = SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20})
    strat.initialize(data)
    weights = strat.generate_weights(data).sort("date").with_row_index("i")

    nonzero = weights.filter(pl.col("weight") != 0.0)
    # zero weight during the 20-bar slow-MA warm-up; the first possible long bar
    # is the first valid MA bar (index 19 for slow_period=20)
    assert (nonzero["i"] >= 19).all()
    # single cross -> single block that persists to the end of the sample
    blocks = _blocks(weights["weight"])
    assert len(blocks) == 1
    assert blocks[0][-1] == len(closes) - 1


def test_sma_two_legs_two_blocks() -> None:
    """Up -> down -> up again produces two separate nonzero blocks."""
    closes = (
        [100.0] * 25
        + [100.0 + 1.0 * i for i in range(1, 30)]  # leg 1 up
        + [129.0 - 1.5 * i for i in range(1, 30)]  # leg 1 down
        + [85.0 + 2.0 * i for i in range(1, 30)]  # leg 2 up
    )
    data = _frame(closes)
    strat = SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20})
    strat.initialize(data)
    weights = strat.generate_weights(data).sort("date")

    blocks = _blocks(weights["weight"])
    assert len(blocks) == 2
    assert all(len(b) > 5 for b in blocks)
    assert blocks[1][-1] == len(closes) - 1  # still long at the end


# ---------------------------------------------------------------------------
# Bollinger-band mean reversion — hold from lower-band entry until middle-band touch
# ---------------------------------------------------------------------------


def test_bb_holds_from_lower_band_entry_until_mid_band_touch() -> None:
    """Entry on the first below-band close; hold until close >= middle band."""
    closes = (
        [100.0] * 25  # warm-up, stable prices
        + [100.0 - 2.0 * i for i in range(1, 8)]  # crash below the lower band -> entry
        + [87.0 + 2.5 * i for i in range(1, 8)]  # recovery to the middle band -> exit
        + [106.0] * 10  # calm tail
    )
    data = _frame(closes)
    strat = BBMeanReversionStrategy(params={"period": 20, "num_std": 2.0, "use_trend_filter": False})
    strat.initialize(data)
    weights = strat.generate_weights(data).sort("date")

    blocks = _blocks(weights["weight"])
    assert len(blocks) == 1
    block = blocks[0]
    assert len(block) > 3, "entry must be held for multiple bars, not a one-day trade"

    df = strat._data_with_indicators.with_row_index("i")
    # Entry bar: first close strictly below the lower band.
    entry_idx = next(i for i in range(21, len(closes)) if df["close"][i] < df["lower_band"][i] and df["close"][i - 1] >= df["lower_band"][i - 1])
    # Exit bar: first close at/above the middle band while long.
    exit_idx = next(i for i in range(entry_idx + 1, len(closes)) if df["close"][i] >= df["middle_band"][i])
    assert block[0] == entry_idx
    assert block[-1] == exit_idx - 1
    assert weights["weight"][entry_idx] == pytest.approx(0.95)
    assert weights["weight"][exit_idx] == 0.0


def test_bb_reentry_after_exit_opens_a_new_block() -> None:
    """A second dip below the band re-opens the position after the mid-band exit."""
    closes = (
        [100.0] * 25
        + [100.0 - 2.0 * i for i in range(1, 8)]  # dip 1 -> entry
        + [87.0 + 2.5 * i for i in range(1, 8)]  # recovery -> mid-band exit
        + [104.5] * 15  # calm: band narrows again
        + [104.5 - 2.5 * i for i in range(1, 8)]  # dip 2 -> re-entry
        + [86.0 + 2.5 * i for i in range(1, 8)]  # recovery 2 -> exit
        + [104.0] * 5
    )
    data = _frame(closes)
    strat = BBMeanReversionStrategy(params={"period": 20, "num_std": 2.0, "use_trend_filter": False})
    strat.initialize(data)
    weights = strat.generate_weights(data).sort("date")

    blocks = _blocks(weights["weight"])
    assert len(blocks) == 2
    assert all(w == pytest.approx(0.95) for w in weights["weight"].to_list() if w != 0.0)


def test_bb_trend_filter_blocks_entry_in_downtrend() -> None:
    """With the trend filter on, entries below the 200-day SMA are suppressed."""
    # Persistent -0.5/bar downtrend with two sharp dip+bounce cycles that pierce
    # the lower band (and recover past the mid band) while staying far below SMA200.
    closes = []
    px = 200.0
    for _ in range(240):
        px -= 0.5
        closes.append(px)
    for t in (100, 160):
        closes[t] -= 8.0
        closes[t + 1] += 13.5
    data = _frame(closes)
    strat_no_filter = BBMeanReversionStrategy(params={"period": 20, "num_std": 2.0, "use_trend_filter": False})
    strat_filtered = BBMeanReversionStrategy(params={"period": 20, "num_std": 2.0, "use_trend_filter": True})
    strat_no_filter.initialize(data)
    strat_filtered.initialize(data)
    w_free = strat_no_filter.generate_weights(data).sort("date")["weight"]
    w_filtered = strat_filtered.generate_weights(data).sort("date")["weight"]

    assert (w_free != 0.0).any(), "fixture must produce at least one unfiltered entry"
    assert (w_filtered == 0.0).all(), "trend filter must suppress every entry in a 200-bar downtrend"


# ---------------------------------------------------------------------------
# Next-bar execution semantics (engine level)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not POLARS_BACKTEST_AVAILABLE, reason="polars-backtest not installed")
def test_engine_executes_entry_the_bar_after_the_signal() -> None:
    """The engine must not trade the signal bar itself: entry_date > entry_sig_date."""
    closes = [100.0] * 25 + [100.0 + 1.0 * i for i in range(1, 30)] + [129.0 - 1.5 * i for i in range(1, 30)]
    data = _frame(closes)

    engine = VectorBacktestEngine(
        strategy=SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20}),
        tickers=["TEST"],
        start_date=data["date"].min(),
        end_date=data["date"].max(),
        initial_cash=10_000.0,
        preloaded_data=data,
    )
    result = engine.run()

    assert result.trades, "one long leg must produce at least one round-trip trade"
    strat = SMACrossoverStrategy(params={"fast_period": 5, "slow_period": 20})
    strat.initialize(data)
    weights = strat.generate_weights(data)
    signal_dates = weights.filter(pl.col("weight") != 0.0)["date"].to_list()
    first_signal = min(signal_dates)
    # The engine must not trade the signal bar itself: execution happens at the
    # NEXT bar's close.
    next_bar = data.filter(pl.col("date") > first_signal).sort("date")["date"][0]
    executed = [t for t in result.trades if t["entry_date"] is not None]
    assert executed, "at least one dated trade expected"
    # The very first execution is exactly the bar after the first signal bar.
    assert executed[0]["entry_date"] == next_bar
    for trade in executed:
        assert trade["entry_date"] >= next_bar, "execution must lag the signal by one bar"
