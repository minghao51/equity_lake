"""Lightweight technical indicators used by the beta feature pipeline."""

from __future__ import annotations

import polars as pl


def ema(series: pl.Series, span: int) -> pl.Series:
    return series.cast(pl.Float64).ewm_mean(span=span, adjust=False)


def rsi(series: pl.Series, length: int = 14) -> pl.Series:
    series = series.cast(pl.Float64)
    delta = series.diff()
    gains = delta.clip(lower_bound=0)
    losses = -delta.clip(upper_bound=0)
    avg_gain = gains.ewm_mean(alpha=1 / length, min_samples=length, adjust=False)
    avg_loss = losses.ewm_mean(alpha=1 / length, min_samples=length, adjust=False)
    rs = avg_gain / avg_loss.replace(0.0, None)
    return 100 - (100 / (1 + rs))


def macd(series: pl.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pl.DataFrame:
    fast_ema = ema(series, span=fast)
    slow_ema = ema(series, span=slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, span=signal)
    histogram = macd_line - signal_line
    return pl.DataFrame(
        {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram,
        }
    )


def bollinger_bands(series: pl.Series, length: int = 20, std: float = 2.0) -> pl.DataFrame:
    series = series.cast(pl.Float64)
    middle = series.rolling_mean(window_size=length)
    deviation = series.rolling_std(window_size=length)
    upper = middle + std * deviation
    lower = middle - std * deviation
    return pl.DataFrame({"upper": upper, "middle": middle, "lower": lower})


def true_range(high: pl.Series, low: pl.Series, close: pl.Series) -> pl.Series:
    frame = pl.DataFrame(
        [
            high.cast(pl.Float64) - low.cast(pl.Float64),
            (high.cast(pl.Float64) - close.cast(pl.Float64).shift(1)).abs(),
            (low.cast(pl.Float64) - close.cast(pl.Float64).shift(1)).abs(),
        ],
        schema=["intraday_range", "gap_up_range", "gap_down_range"],
    )
    return frame.max_horizontal()


def atr(high: pl.Series, low: pl.Series, close: pl.Series, length: int = 14) -> pl.Series:
    return true_range(high, low, close).rolling_mean(window_size=length)


def roc(series: pl.Series, length: int) -> pl.Series:
    return series.cast(pl.Float64).pct_change(length) * 100


def obv(close: pl.Series, volume: pl.Series) -> pl.Series:
    direction = close.cast(pl.Float64).diff().sign().fill_null(0.0)
    return (direction * volume.cast(pl.Float64)).cum_sum()
