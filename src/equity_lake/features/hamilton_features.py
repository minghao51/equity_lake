"""Hamilton feature definitions for equity data."""

from __future__ import annotations

import numpy as np
import pandas as pd

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


def ticker(price_data: pd.DataFrame) -> pd.Series:
    return price_data["ticker"]


def date(price_data: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(price_data["date"])


def open_price(price_data: pd.DataFrame) -> pd.Series:
    return price_data["open"]


def high(price_data: pd.DataFrame) -> pd.Series:
    return price_data["high"]


def low(price_data: pd.DataFrame) -> pd.Series:
    return price_data["low"]


def close(price_data: pd.DataFrame) -> pd.Series:
    return price_data["close"]


def volume(price_data: pd.DataFrame) -> pd.Series:
    return price_data["volume"]


def returns(close: pd.Series) -> pd.Series:
    return close.pct_change()


def rsi_14(close: pd.Series) -> pd.Series:
    return rsi(close, length=14)


def macd_frame(close: pd.Series) -> pd.DataFrame:
    return macd_indicator(close, fast=12, slow=26, signal=9)


def macd(macd_frame: pd.DataFrame) -> pd.Series:
    return macd_frame["macd"]


def macd_signal(macd_frame: pd.DataFrame) -> pd.Series:
    return macd_frame["signal"]


def macd_histogram(macd_frame: pd.DataFrame) -> pd.Series:
    return macd_frame["histogram"]


def bollinger_frame(close: pd.Series) -> pd.DataFrame:
    return bollinger_bands(close, length=20, std=2)


def bb_upper(bollinger_frame: pd.DataFrame) -> pd.Series:
    return bollinger_frame["upper"]


def bb_middle(bollinger_frame: pd.DataFrame) -> pd.Series:
    return bollinger_frame["middle"]


def bb_lower(bollinger_frame: pd.DataFrame) -> pd.Series:
    return bollinger_frame["lower"]


def bb_width(bb_upper: pd.Series, bb_lower: pd.Series, bb_middle: pd.Series) -> pd.Series:
    return (bb_upper - bb_lower) / bb_middle


def bb_pct(close: pd.Series, bb_upper: pd.Series, bb_lower: pd.Series) -> pd.Series:
    return (close - bb_lower) / (bb_upper - bb_lower)


def atr_14(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    return atr(high, low, close, length=14)


def roc_5(close: pd.Series) -> pd.Series:
    return roc(close, length=5)


def roc_10(close: pd.Series) -> pd.Series:
    return roc(close, length=10)


def roc_20(close: pd.Series) -> pd.Series:
    return roc(close, length=20)


def return_1d(close: pd.Series) -> pd.Series:
    return close.pct_change(1)


def return_5d(close: pd.Series) -> pd.Series:
    return close.pct_change(5)


def return_10d(close: pd.Series) -> pd.Series:
    return close.pct_change(10)


def return_20d(close: pd.Series) -> pd.Series:
    return close.pct_change(20)


def overnight_return(open_price: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return (open_price - prev_close) / prev_close


def intraday_return(open_price: pd.Series, close: pd.Series) -> pd.Series:
    return (close - open_price) / open_price


def hl_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    return (high - low) / close


def volume_ma_20(volume: pd.Series) -> pd.Series:
    return volume.rolling(window=20).mean()


def volume_roc_5(volume: pd.Series) -> pd.Series:
    return volume.pct_change(5)


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    return obv_indicator(close, volume)


def volume_ratio(volume: pd.Series, volume_ma_20: pd.Series) -> pd.Series:
    return volume / volume_ma_20


def day_of_week(date: pd.Series) -> pd.Series:
    return date.dt.dayofweek


def day_of_month(date: pd.Series) -> pd.Series:
    return date.dt.day


def month(date: pd.Series) -> pd.Series:
    return date.dt.month


def quarter(date: pd.Series) -> pd.Series:
    return date.dt.quarter


def days_to_month_end(date: pd.Series) -> pd.Series:
    month_end = date + pd.offsets.MonthEnd(0)
    return (month_end - date).dt.days


def trading_day_of_month(date: pd.Series) -> pd.Series:
    return date.groupby([date.dt.year, date.dt.month]).cumcount() + 1


def volatility_20(returns: pd.Series) -> pd.Series:
    return returns.rolling(20).std() * np.sqrt(252)


def next_day_return(close: pd.Series) -> pd.Series:
    return close.shift(-1) / close - 1
