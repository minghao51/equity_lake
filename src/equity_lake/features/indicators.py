"""Lightweight technical indicators used by the beta feature pipeline."""

from __future__ import annotations

import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    fast_ema = ema(series, span=fast)
    slow_ema = ema(series, span=slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, span=signal)
    histogram = macd_line - signal_line
    return pd.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }
    )


def bollinger_bands(series: pd.Series, length: int = 20, std: float = 2.0) -> pd.DataFrame:
    middle = series.rolling(length).mean()
    deviation = series.rolling(length).std()
    upper = middle + std * deviation
    lower = middle - std * deviation
    return pd.DataFrame({"upper": upper, "middle": middle, "lower": lower})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    frame = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    )
    return frame.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    return true_range(high, low, close).rolling(length).mean()


def roc(series: pd.Series, length: int) -> pd.Series:
    return series.pct_change(length) * 100


def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = close.diff().fillna(0).apply(lambda value: 1 if value > 0 else (-1 if value < 0 else 0))
    return (direction * volume).cumsum()
