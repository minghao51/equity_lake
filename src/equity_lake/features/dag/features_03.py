"""Gold layer: technical indicator computation.

All indicator functions migrate from the former monolithic
``hamilton_features.py``.  The ``@parameterize`` decorator replaces
hand-written ``roc_N`` / ``return_Nd`` variants — Hamilton strips the
``__suffix`` and generates nodes with the same names (``roc_5``, ``return_1d``,
etc.) so downstream consumers are unaffected.
"""

from __future__ import annotations

import numpy as np
import polars as pl
import structlog
from hamilton.function_modifiers import check_output, parameterize, tag, value

from equity_lake.features.dag.polars_validators import PolarsRangeValidator
from equity_lake.features.dag.schemas import FeatureModel
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

logger = structlog.get_logger()
_FEATURE_SAMPLE_SIZE = 100

# ---------------------------------------------------------------------------
# Momentum indicators
# ---------------------------------------------------------------------------


@tag(layer="gold", category="momentum", produces="rsi_14", validators="check_output(range=(0,100))")  # type: ignore[untyped-decorator]
@check_output(  # type: ignore[untyped-decorator]
    range=(0.0, 100.0),
    importance="warn",
    default_validator_candidates=[PolarsRangeValidator],
)
def rsi_14(close: pl.Series) -> pl.Series:
    return rsi(close, length=14)


@tag(layer="gold", category="momentum", produces="macd_frame")  # type: ignore[untyped-decorator]
def macd_frame(close: pl.Series) -> pl.DataFrame:
    return macd_indicator(close, fast=12, slow=26, signal=9)


@tag(layer="gold", category="momentum", produces="macd")  # type: ignore[untyped-decorator]
def macd(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["macd"]


@tag(layer="gold", category="momentum", produces="macd_signal")  # type: ignore[untyped-decorator]
def macd_signal(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["signal"]


@tag(layer="gold", category="momentum", produces="macd_histogram")  # type: ignore[untyped-decorator]
def macd_histogram(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["histogram"]


@tag(layer="gold", category="momentum", produces="roc_5|roc_10|roc_20")  # type: ignore[untyped-decorator]
@parameterize(  # type: ignore[untyped-decorator]
    roc_5={"length": value(5)},
    roc_10={"length": value(10)},
    roc_20={"length": value(20)},
)
def roc_pct(close: pl.Series, length: int) -> pl.Series:
    """Rate of change as percentage."""
    return roc(close, length=length)


@tag(layer="gold", category="momentum", produces="return_1d|return_5d|return_10d|return_20d")  # type: ignore[untyped-decorator]
@parameterize(  # type: ignore[untyped-decorator]
    return_1d={"window": value(1)},
    return_5d={"window": value(5)},
    return_10d={"window": value(10)},
    return_20d={"window": value(20)},
)
def pct_return(close: pl.Series, window: int) -> pl.Series:
    """N-day percentage return."""
    return close.pct_change(window)


# ---------------------------------------------------------------------------
# Volatility / bands
# ---------------------------------------------------------------------------


@tag(layer="gold", category="volatility", produces="bollinger_frame")  # type: ignore[untyped-decorator]
def bollinger_frame(close: pl.Series) -> pl.DataFrame:
    return bollinger_bands(close, length=20, std=2)


@tag(layer="gold", category="volatility", produces="bb_upper")  # type: ignore[untyped-decorator]
def bb_upper(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["upper"]


@tag(layer="gold", category="volatility", produces="bb_middle")  # type: ignore[untyped-decorator]
def bb_middle(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["middle"]


@tag(layer="gold", category="volatility", produces="bb_lower")  # type: ignore[untyped-decorator]
def bb_lower(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["lower"]


@tag(layer="gold", category="volatility", produces="bb_width")  # type: ignore[untyped-decorator]
def bb_width(bb_upper: pl.Series, bb_lower: pl.Series, bb_middle: pl.Series) -> pl.Series:
    return (bb_upper - bb_lower) / bb_middle


@tag(layer="gold", category="volatility", produces="bb_pct")  # type: ignore[untyped-decorator]
def bb_pct(close: pl.Series, bb_upper: pl.Series, bb_lower: pl.Series) -> pl.Series:
    band_width = (bb_upper - bb_lower).replace(0.0, None)
    return (close - bb_lower) / band_width


@tag(layer="gold", category="volatility", produces="atr_14")  # type: ignore[untyped-decorator]
def atr_14(high: pl.Series, low: pl.Series, close: pl.Series) -> pl.Series:
    return atr(high, low, close, length=14)


@tag(layer="gold", category="volatility", produces="volatility_20")  # type: ignore[untyped-decorator]
def volatility_20(returns: pl.Series) -> pl.Series:
    annualization_factor = float(np.sqrt(252))
    return returns.rolling_std(window_size=20) * annualization_factor


# ---------------------------------------------------------------------------
# Price structure
# ---------------------------------------------------------------------------


@tag(layer="gold", category="price_structure", produces="overnight_return")  # type: ignore[untyped-decorator]
def overnight_return(open_price: pl.Series, close: pl.Series) -> pl.Series:
    prev_close = close.shift(1)
    return (open_price - prev_close) / prev_close


@tag(layer="gold", category="price_structure", produces="intraday_return")  # type: ignore[untyped-decorator]
def intraday_return(open_price: pl.Series, close: pl.Series) -> pl.Series:
    return (close - open_price) / open_price


@tag(layer="gold", category="price_structure", produces="hl_range")  # type: ignore[untyped-decorator]
def hl_range(high: pl.Series, low: pl.Series, close: pl.Series) -> pl.Series:
    return (high - low) / close


# ---------------------------------------------------------------------------
# Volume indicators
# ---------------------------------------------------------------------------


@tag(layer="gold", category="volume", produces="volume_ma_20")  # type: ignore[untyped-decorator]
def volume_ma_20(volume: pl.Series) -> pl.Series:
    return volume.rolling_mean(window_size=20)


@tag(layer="gold", category="volume", produces="volume_roc_5")  # type: ignore[untyped-decorator]
def volume_roc_5(volume: pl.Series) -> pl.Series:
    return volume.pct_change(5)


@tag(layer="gold", category="volume", produces="obv")  # type: ignore[untyped-decorator]
def obv(close: pl.Series, volume: pl.Series) -> pl.Series:
    return obv_indicator(close, volume)


@tag(layer="gold", category="volume", produces="volume_ratio")  # type: ignore[untyped-decorator]
def volume_ratio(volume: pl.Series, volume_ma_20: pl.Series) -> pl.Series:
    safe_ma = volume_ma_20.replace(0.0, None)
    return volume / safe_ma


# ---------------------------------------------------------------------------
# Calendar features
# ---------------------------------------------------------------------------


@tag(layer="gold", category="calendar", produces="day_of_week")  # type: ignore[untyped-decorator]
def day_of_week(date: pl.Series) -> pl.Series:
    return date.dt.weekday()


@tag(layer="gold", category="calendar", produces="day_of_month")  # type: ignore[untyped-decorator]
def day_of_month(date: pl.Series) -> pl.Series:
    return date.dt.day()


@tag(layer="gold", category="calendar", produces="month")  # type: ignore[untyped-decorator]
def month(date: pl.Series) -> pl.Series:
    return date.dt.month()


@tag(layer="gold", category="calendar", produces="quarter")  # type: ignore[untyped-decorator]
def quarter(date: pl.Series) -> pl.Series:
    return date.dt.quarter()


@tag(layer="gold", category="calendar", produces="days_to_month_end")  # type: ignore[untyped-decorator]
def days_to_month_end(date: pl.Series) -> pl.Series:
    return (date.dt.month_end() - date).dt.total_days()


@tag(layer="gold", category="calendar", produces="trading_day_of_month")  # type: ignore[untyped-decorator]
def trading_day_of_month(ticker: pl.Series, date: pl.Series) -> pl.Series:
    return (
        pl.DataFrame({"ticker": ticker, "date": date})
        .with_row_index("row_nr")
        .with_columns(
            (pl.int_range(pl.len()).over(["ticker", pl.col("date").dt.year(), pl.col("date").dt.month()]) + 1).alias("trading_day_of_month")
        )
        .sort("row_nr")["trading_day_of_month"]
    )


# ---------------------------------------------------------------------------
# Gold boundary validation
# ---------------------------------------------------------------------------


@tag(layer="gold", category="validation", produces="validated_features", description="Pydantic FeatureModel boundary validation at Silver→Gold")  # type: ignore[untyped-decorator]
def validated_features(
    ticker: pl.Series,
    date: pl.Series,
    close: pl.Series,
    rsi_14: pl.Series,
    macd: pl.Series,
    volume: pl.Series,
) -> pl.DataFrame:
    """Assemble key features into a DataFrame at the Gold→Platinum boundary.

    Samples rows and validates each against :class:`FeatureModel` for
    row-level schema enforcement (complementary to ``@check_output``).
    """
    df = pl.DataFrame(
        {
            "ticker": ticker,
            "date": date,
            "close": close,
            "rsi_14": rsi_14,
            "macd": macd,
            "volume": volume,
        }
    )
    if df.is_empty():
        return df

    sample = df.sample(n=min(_FEATURE_SAMPLE_SIZE, df.height), seed=42)
    valid_count = 0
    for idx in range(sample.height):
        row = sample.row(idx, named=True)
        try:
            FeatureModel(**row)
            valid_count += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "feature_boundary_validation_failed",
                ticker=row.get("ticker"),
                error=str(exc),
            )

    if valid_count < sample.height:
        logger.warning(
            "feature_boundary_validation_summary",
            valid=valid_count,
            sampled=sample.height,
        )

    return df


# ---------------------------------------------------------------------------
# Target variable
# ---------------------------------------------------------------------------


@tag(layer="gold", category="target", produces="next_day_return", description="Target: next-day return (uses future data shift -1)")  # type: ignore[untyped-decorator]
def next_day_return(close: pl.Series) -> pl.Series:
    return close.shift(-1) / close - 1


next_day_return.__doc__ = (
    "Target variable. Uses future data (shift -1). "
    "Must be excluded from inference feature lists - "
    "FeatureEngineer.generate_features(compute_target=False) handles this."
)
