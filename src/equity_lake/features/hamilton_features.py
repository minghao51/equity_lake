"""Hamilton feature definitions for equity data."""

from __future__ import annotations

import numpy as np
import polars as pl

from equity_lake.features.indicators import (
    atr,
    bollinger_bands,
    roc,
    rsi,
)
from equity_lake.features.indicators import (
    macd as macd_indicator,
)
from equity_lake.features.indicators import (
    obv as obv_indicator,
)


def ticker(price_data: pl.DataFrame) -> pl.Series:
    return price_data["ticker"]


def date(price_data: pl.DataFrame) -> pl.Series:
    date_column = price_data["date"]
    if date_column.dtype == pl.Utf8:
        return date_column.str.to_datetime(strict=False)
    if date_column.dtype == pl.Date:
        return date_column.cast(pl.Datetime)
    return date_column


def open_price(price_data: pl.DataFrame) -> pl.Series:
    return price_data["open"]


def high(price_data: pl.DataFrame) -> pl.Series:
    return price_data["high"]


def low(price_data: pl.DataFrame) -> pl.Series:
    return price_data["low"]


def close(price_data: pl.DataFrame) -> pl.Series:
    return price_data["close"].cast(pl.Float64)


def volume(price_data: pl.DataFrame) -> pl.Series:
    return price_data["volume"].cast(pl.Float64)


def returns(close: pl.Series) -> pl.Series:
    return close.pct_change()


def rsi_14(close: pl.Series) -> pl.Series:
    return rsi(close, length=14)


def macd_frame(close: pl.Series) -> pl.DataFrame:
    return macd_indicator(close, fast=12, slow=26, signal=9)


def macd(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["macd"]


def macd_signal(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["signal"]


def macd_histogram(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["histogram"]


def bollinger_frame(close: pl.Series) -> pl.DataFrame:
    return bollinger_bands(close, length=20, std=2)


def bb_upper(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["upper"]


def bb_middle(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["middle"]


def bb_lower(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["lower"]


def bb_width(bb_upper: pl.Series, bb_lower: pl.Series, bb_middle: pl.Series) -> pl.Series:
    return (bb_upper - bb_lower) / bb_middle


def bb_pct(close: pl.Series, bb_upper: pl.Series, bb_lower: pl.Series) -> pl.Series:
    return (close - bb_lower) / (bb_upper - bb_lower)


def atr_14(high: pl.Series, low: pl.Series, close: pl.Series) -> pl.Series:
    return atr(high, low, close, length=14)


def roc_5(close: pl.Series) -> pl.Series:
    return roc(close, length=5)


def roc_10(close: pl.Series) -> pl.Series:
    return roc(close, length=10)


def roc_20(close: pl.Series) -> pl.Series:
    return roc(close, length=20)


def return_1d(close: pl.Series) -> pl.Series:
    return close.pct_change(1)


def return_5d(close: pl.Series) -> pl.Series:
    return close.pct_change(5)


def return_10d(close: pl.Series) -> pl.Series:
    return close.pct_change(10)


def return_20d(close: pl.Series) -> pl.Series:
    return close.pct_change(20)


def overnight_return(open_price: pl.Series, close: pl.Series) -> pl.Series:
    prev_close = close.shift(1)
    return (open_price - prev_close) / prev_close


def intraday_return(open_price: pl.Series, close: pl.Series) -> pl.Series:
    return (close - open_price) / open_price


def hl_range(high: pl.Series, low: pl.Series, close: pl.Series) -> pl.Series:
    return (high - low) / close


def volume_ma_20(volume: pl.Series) -> pl.Series:
    return volume.rolling_mean(window_size=20)


def volume_roc_5(volume: pl.Series) -> pl.Series:
    return volume.pct_change(5)


def obv(close: pl.Series, volume: pl.Series) -> pl.Series:
    return obv_indicator(close, volume)


def volume_ratio(volume: pl.Series, volume_ma_20: pl.Series) -> pl.Series:
    return volume / volume_ma_20


def day_of_week(date: pl.Series) -> pl.Series:
    return date.dt.weekday()


def day_of_month(date: pl.Series) -> pl.Series:
    return date.dt.day()


def month(date: pl.Series) -> pl.Series:
    return date.dt.month()


def quarter(date: pl.Series) -> pl.Series:
    return date.dt.quarter()


def days_to_month_end(date: pl.Series) -> pl.Series:
    return (date.dt.month_end() - date).dt.total_days()


def trading_day_of_month(ticker: pl.Series, date: pl.Series) -> pl.Series:
    return (
        pl.DataFrame({"ticker": ticker, "date": date})
        .with_row_index("row_nr")
        .with_columns(
            (pl.int_range(pl.len()).over(["ticker", pl.col("date").dt.year(), pl.col("date").dt.month()]) + 1).alias("trading_day_of_month")
        )
        .sort("row_nr")["trading_day_of_month"]
    )


def volatility_20(returns: pl.Series) -> pl.Series:
    annualization_factor = float(np.sqrt(252))
    return returns.rolling_std(window_size=20) * annualization_factor


def next_day_return(close: pl.Series) -> pl.Series:
    return close.shift(-1) / close - 1


next_day_return.__doc__ = (
    "Target variable. Uses future data (shift -1). "
    "Must be excluded from inference feature lists - "
    "FeatureEngineer.generate_features(compute_target=False) handles this."
)
