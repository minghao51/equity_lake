"""Tests for Gold layer: technical indicator computation."""

from __future__ import annotations

import numpy as np
import polars as pl

from equity_lake.features.dag.features_03 import (
    atr_14,
    bb_lower,
    bb_middle,
    bb_upper,
    bb_width,
    day_of_month,
    day_of_week,
    days_to_month_end,
    hl_range,
    intraday_return,
    macd,
    macd_frame,
    macd_histogram,
    macd_signal,
    month,
    next_day_return,
    obv,
    overnight_return,
    quarter,
    rsi_14,
    trading_day_of_month,
    volatility_20,
    volume_ma_20,
    volume_ratio,
    volume_roc_5,
)


def _close_series(n: int = 80, base: float = 100.0) -> pl.Series:
    rng = np.random.default_rng(42)
    return pl.Series("close", base + rng.standard_normal(n).cumsum())


def test_rsi_14_returns_series() -> None:
    result = rsi_14(_close_series())
    assert isinstance(result, pl.Series)
    valid = result.drop_nulls()
    assert valid.min() >= 0.0
    assert valid.max() <= 100.0


def test_macd_frame_and_components() -> None:
    close = _close_series()
    frame = macd_frame(close)
    assert isinstance(frame, pl.DataFrame)
    assert set(frame.columns) == {"macd", "signal", "histogram"}

    assert isinstance(macd(frame), pl.Series)
    assert isinstance(macd_signal(frame), pl.Series)
    assert isinstance(macd_histogram(frame), pl.Series)


def test_bollinger_bands() -> None:
    close = _close_series()

    from equity_lake.features.dag.features_03 import bollinger_frame

    bf = bollinger_frame(close)
    assert isinstance(bb_upper(bf), pl.Series)
    assert isinstance(bb_middle(bf), pl.Series)
    assert isinstance(bb_lower(bf), pl.Series)
    assert isinstance(bb_width(bb_upper(bf), bb_lower(bf), bb_middle(bf)), pl.Series)


def test_atr_14() -> None:
    n = 80
    rng = np.random.default_rng(42)
    high = pl.Series("high", 100 + rng.standard_normal(n).cumsum() + 2)
    low = pl.Series("low", 100 + rng.standard_normal(n).cumsum() - 2)
    close = pl.Series("close", 100 + rng.standard_normal(n).cumsum())
    result = atr_14(high, low, close)
    assert isinstance(result, pl.Series)
    assert result.drop_nulls().min() > 0


def test_overnight_and_intraday_return() -> None:
    open_price = pl.Series("open", [100.0, 110.0, 105.0])
    close = pl.Series("close", [110.0, 105.0, 115.0])

    overnight = overnight_return(open_price, close)
    assert isinstance(overnight, pl.Series)

    intraday = intraday_return(open_price, close)
    assert isinstance(intraday, pl.Series)


def test_hl_range() -> None:
    high = pl.Series("high", [105.0, 110.0])
    low = pl.Series("low", [95.0, 100.0])
    close = pl.Series("close", [100.0, 105.0])
    result = hl_range(high, low, close)
    assert isinstance(result, pl.Series)
    assert len(result) == 2


def test_volume_indicators() -> None:
    vol = pl.Series("volume", [1e6] * 80, dtype=pl.Float64)
    assert isinstance(volume_ma_20(vol), pl.Series)
    assert isinstance(volume_roc_5(vol), pl.Series)
    assert isinstance(volume_ratio(vol, volume_ma_20(vol)), pl.Series)


def test_obv() -> None:
    close = _close_series()
    vol = pl.Series("volume", [1e6] * 80, dtype=pl.Float64)
    result = obv(close, vol)
    assert isinstance(result, pl.Series)


def test_volatility_20() -> None:
    close = _close_series()
    from equity_lake.features.dag.clean_02 import returns

    ret = returns(close)
    result = volatility_20(ret)
    assert isinstance(result, pl.Series)


def test_calendar_features() -> None:
    dates = pl.Series(
        "date",
        pl.datetime_range(
            start=pl.datetime(2024, 1, 1),
            end=pl.datetime(2024, 3, 31),
            interval="1d",
            eager=True,
        ),
    )
    assert isinstance(day_of_week(dates), pl.Series)
    assert isinstance(day_of_month(dates), pl.Series)
    assert isinstance(month(dates), pl.Series)
    assert isinstance(quarter(dates), pl.Series)
    assert isinstance(days_to_month_end(dates), pl.Series)


def test_trading_day_of_month() -> None:
    ticker = pl.Series("ticker", ["AAPL"] * 5)
    dates = pl.Series(
        "date",
        pl.datetime_range(pl.datetime(2024, 1, 1), pl.datetime(2024, 1, 5), "1d", eager=True),
    )
    result = trading_day_of_month(ticker, dates)
    assert isinstance(result, pl.Series)
    assert result.to_list() == [1, 2, 3, 4, 5]


def test_next_day_return() -> None:
    close = pl.Series("close", [100.0, 110.0, 105.0])
    result = next_day_return(close)
    assert isinstance(result, pl.Series)
    assert abs(result[0] - 0.1) < 1e-9
    assert result[2] is None
