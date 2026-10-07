"""Gold layer: technical indicator computation.

All indicator functions migrate from the former monolithic
``hamilton_features.py``.  The ``@parameterize`` decorator replaces
hand-written ``roc_N`` / ``return_Nd`` variants — Hamilton strips the
``__suffix`` and generates nodes with the same names (``roc_5``, ``return_1d``,
etc.) so downstream consumers are unaffected.

Since W01 (F02/D01) every price/calendar/target node consumes the same
``validated_ohlcv`` frame, guaranteeing row alignment across all derived
series, and the emitted output is enforced by :func:`validate_emitted_frame`
at the ``validated_features`` Gold boundary and in
``FeaturePipeline.compute_technical``.
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

#: Emitted feature columns that are structurally defined for every row after
#: the first observation (no windowed warm-up). Nulls here beyond row index 0
#: indicate a computation defect, not insufficient history.
_ROW_DEFINED_FEATURES: tuple[str, ...] = (
    "macd",
    "macd_signal",
    "macd_histogram",
    "obv",
    "hl_range",
    "intraday_return",
    "overnight_return",
    "day_of_week",
    "day_of_month",
    "month",
    "quarter",
    "days_to_month_end",
    "trading_day_of_month",
)


class FeatureOutputValidationError(ValueError):
    """Raised when the emitted technical feature frame violates the Gold contract.

    Covers null/duplicate identity keys, ±inf/NaN pollution in float features,
    and nulls in row-defined feature columns beyond the first row. Warm-up
    nulls in windowed indicators and the unavailable final target are
    legitimate and never rejected.
    """


def validate_emitted_frame(frame: pl.DataFrame, *, target_included: bool) -> None:
    """Full-frame vectorized validation of an emitted technical feature frame.

    Called at both enforcement seams — the ``validated_features`` DAG node and
    ``FeaturePipeline.compute_technical`` — so the persisted-path output is
    always checked (F02). Raises :class:`FeatureOutputValidationError` on:

    - null ``ticker``/``date`` or duplicate ``(ticker, date)``;
    - any ±inf or NaN value in a float feature column (F15);
    - nulls in :data:`_ROW_DEFINED_FEATURES` columns beyond row index 0
      within each ticker.

    Allowed by contract: leading warm-up nulls in windowed indicators
    (``rsi_14``, ``bb_*``, ``atr_14``, ``roc_*``, ``return_*``,
    ``volume_ma_20``, ``volume_roc_5``, ``volatility_20``) and the null final
    ``next_day_return`` (the next session has not been observed yet).
    """
    if frame.is_empty():
        return

    if frame["ticker"].is_null().any() or frame["date"].is_null().any():
        raise FeatureOutputValidationError("null ticker/date in emitted feature frame")
    if frame.select(pl.struct("ticker", "date").is_duplicated().any()).item():
        raise FeatureOutputValidationError("duplicate (ticker, date) identity in emitted feature frame")

    float_columns = [name for name, dtype in frame.schema.items() if dtype in (pl.Float64, pl.Float32)]
    for name in float_columns:
        column = frame[name]
        non_finite = column.is_infinite() | column.is_nan()
        if non_finite.any():
            raise FeatureOutputValidationError(f"non-finite value(s) in emitted feature column {name!r}")

    row_within_ticker = pl.int_range(pl.len()).over("ticker")
    present_row_defined = [c for c in _ROW_DEFINED_FEATURES if c in frame.columns]
    for name in present_row_defined:
        if frame.select((pl.col(name).is_null() & (row_within_ticker > 0)).any()).item():
            raise FeatureOutputValidationError(f"null in row-defined feature column {name!r} beyond each ticker's first row")

    if target_included and "next_day_return" in frame.columns:
        last_within_ticker = pl.len().over("ticker") - 1
        interior_null = pl.col("next_day_return").is_null() & (row_within_ticker < last_within_ticker)
        if frame.select(interior_null.any()).item():
            raise FeatureOutputValidationError("next_day_return null before a ticker's final row (target must be defined on every completed session)")


# ---------------------------------------------------------------------------
# Momentum indicators
# ---------------------------------------------------------------------------


@tag(layer="gold", category="momentum", produces="rsi_14", validators="check_output(range=(0,100))")  # type: ignore[untyped-decorator]
@check_output(  # type: ignore[untyped-decorator]
    range=(0.0, 100.0),
    importance="warn",
    default_validator_candidates=[PolarsRangeValidator],
)
def rsi_14(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return rsi(validated_ohlcv["close"], length=14)


@tag(layer="gold", category="momentum", produces="macd_frame")  # type: ignore[untyped-decorator]
def macd_frame(validated_ohlcv: pl.DataFrame) -> pl.DataFrame:
    return macd_indicator(validated_ohlcv["close"], fast=12, slow=26, signal=9)


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
def roc_pct(validated_ohlcv: pl.DataFrame, length: int) -> pl.Series:
    """Rate of change as percentage (defined: ``close`` is strictly positive)."""
    return roc(validated_ohlcv["close"], length=length)


@tag(layer="gold", category="momentum", produces="return_1d|return_5d|return_10d|return_20d")  # type: ignore[untyped-decorator]
@parameterize(  # type: ignore[untyped-decorator]
    return_1d={"window": value(1)},
    return_5d={"window": value(5)},
    return_10d={"window": value(10)},
    return_20d={"window": value(20)},
)
def pct_return(validated_ohlcv: pl.DataFrame, window: int) -> pl.Series:
    """N-day percentage return (defined: ``close`` is strictly positive)."""
    return validated_ohlcv["close"].pct_change(window)


# ---------------------------------------------------------------------------
# Volatility / bands
# ---------------------------------------------------------------------------


@tag(layer="gold", category="volatility", produces="bollinger_frame")  # type: ignore[untyped-decorator]
def bollinger_frame(validated_ohlcv: pl.DataFrame) -> pl.DataFrame:
    return bollinger_bands(validated_ohlcv["close"], length=20, std=2)


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
def bb_pct(validated_ohlcv: pl.DataFrame, bb_upper: pl.Series, bb_lower: pl.Series) -> pl.Series:
    band_width = (bb_upper - bb_lower).replace(0.0, None)
    return (validated_ohlcv["close"] - bb_lower) / band_width


@tag(layer="gold", category="volatility", produces="atr_14")  # type: ignore[untyped-decorator]
def atr_14(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return atr(validated_ohlcv["high"], validated_ohlcv["low"], validated_ohlcv["close"], length=14)


@tag(layer="gold", category="volatility", produces="volatility_20")  # type: ignore[untyped-decorator]
def volatility_20(returns: pl.Series) -> pl.Series:
    annualization_factor = float(np.sqrt(252))
    return returns.rolling_std(window_size=20) * annualization_factor


# ---------------------------------------------------------------------------
# Price structure (defined: prices strictly positive per the Silver boundary)
# ---------------------------------------------------------------------------


@tag(layer="gold", category="price_structure", produces="overnight_return")  # type: ignore[untyped-decorator]
def overnight_return(validated_ohlcv: pl.DataFrame) -> pl.Series:
    prev_close = validated_ohlcv["close"].shift(1)
    return (validated_ohlcv["open"] - prev_close) / prev_close


@tag(layer="gold", category="price_structure", produces="intraday_return")  # type: ignore[untyped-decorator]
def intraday_return(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return (validated_ohlcv["close"] - validated_ohlcv["open"]) / validated_ohlcv["open"]


@tag(layer="gold", category="price_structure", produces="hl_range")  # type: ignore[untyped-decorator]
def hl_range(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return (validated_ohlcv["high"] - validated_ohlcv["low"]) / validated_ohlcv["close"]


# ---------------------------------------------------------------------------
# Volume indicators
# ---------------------------------------------------------------------------


@tag(layer="gold", category="volume", produces="volume_ma_20")  # type: ignore[untyped-decorator]
def volume_ma_20(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return validated_ohlcv["volume"].rolling_mean(window_size=20)


@tag(layer="gold", category="volume", produces="volume_roc_5")  # type: ignore[untyped-decorator]
def volume_roc_5(validated_ohlcv: pl.DataFrame) -> pl.Series:
    """Five-observation volume rate of change.

    Undefined-ratio semantics (F15/W01): a zero baseline is null, never
    infinity — zero-volume sessions legitimately exist and ``x / 0`` is not a
    finite change measure. Warm-up rows are null by construction.
    """
    volume = validated_ohlcv["volume"].cast(pl.Float64)
    baseline = volume.shift(5).replace(0.0, None)
    return (volume - volume.shift(5)) / baseline


@tag(layer="gold", category="volume", produces="obv")  # type: ignore[untyped-decorator]
def obv(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return obv_indicator(validated_ohlcv["close"], validated_ohlcv["volume"])


@tag(layer="gold", category="volume", produces="volume_ratio")  # type: ignore[untyped-decorator]
def volume_ratio(validated_ohlcv: pl.DataFrame, volume_ma_20: pl.Series) -> pl.Series:
    safe_ma = volume_ma_20.replace(0.0, None)
    return validated_ohlcv["volume"] / safe_ma


# ---------------------------------------------------------------------------
# Calendar features
# ---------------------------------------------------------------------------


@tag(layer="gold", category="calendar", produces="day_of_week")  # type: ignore[untyped-decorator]
def day_of_week(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return validated_ohlcv["date"].dt.weekday()


@tag(layer="gold", category="calendar", produces="day_of_month")  # type: ignore[untyped-decorator]
def day_of_month(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return validated_ohlcv["date"].dt.day()


@tag(layer="gold", category="calendar", produces="month")  # type: ignore[untyped-decorator]
def month(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return validated_ohlcv["date"].dt.month()


@tag(layer="gold", category="calendar", produces="quarter")  # type: ignore[untyped-decorator]
def quarter(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return validated_ohlcv["date"].dt.quarter()


@tag(layer="gold", category="calendar", produces="days_to_month_end")  # type: ignore[untyped-decorator]
def days_to_month_end(validated_ohlcv: pl.DataFrame) -> pl.Series:
    date = validated_ohlcv["date"]
    return (date.dt.month_end() - date).dt.total_days()


@tag(layer="gold", category="calendar", produces="trading_day_of_month")  # type: ignore[untyped-decorator]
def trading_day_of_month(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return (
        validated_ohlcv.select("ticker", "date")
        .with_row_index("row_nr")
        .with_columns(
            (pl.int_range(pl.len()).over(["ticker", pl.col("date").dt.year(), pl.col("date").dt.month()]) + 1).alias("trading_day_of_month")
        )
        .sort("row_nr")["trading_day_of_month"]
    )


# ---------------------------------------------------------------------------
# Gold boundary validation
# ---------------------------------------------------------------------------


@tag(
    layer="gold",
    category="validation",
    produces="validated_features",
    description="Full-frame vectorized emitted-feature validation at the Gold boundary (F02/D01)",
)  # type: ignore[untyped-decorator]
def validated_features(
    validated_ohlcv: pl.DataFrame,
    rsi_14: pl.Series,
    macd: pl.Series,
    macd_signal: pl.Series,
    macd_histogram: pl.Series,
    bb_upper: pl.Series,
    bb_middle: pl.Series,
    bb_lower: pl.Series,
    bb_width: pl.Series,
    bb_pct: pl.Series,
    atr_14: pl.Series,
    roc_5: pl.Series,
    roc_10: pl.Series,
    roc_20: pl.Series,
    return_1d: pl.Series,
    return_5d: pl.Series,
    return_10d: pl.Series,
    return_20d: pl.Series,
    overnight_return: pl.Series,
    intraday_return: pl.Series,
    hl_range: pl.Series,
    volume_ma_20: pl.Series,
    volume_roc_5: pl.Series,
    obv: pl.Series,
    volume_ratio: pl.Series,
    day_of_week: pl.Series,
    day_of_month: pl.Series,
    month: pl.Series,
    quarter: pl.Series,
    days_to_month_end: pl.Series,
    trading_day_of_month: pl.Series,
    volatility_20: pl.Series,
) -> pl.DataFrame:
    """Assemble and enforce the full emitted technical frame at the Gold boundary.

    Runs :func:`validate_emitted_frame` on the assembled output — null/duplicate
    identity, ±inf/NaN, and row-defined nulls reject the frame (F02). Pydantic
    row sampling remains a labeled diagnostic; it cannot pass or fail a batch.
    """
    frame = pl.DataFrame(
        {
            "ticker": validated_ohlcv["ticker"],
            "date": validated_ohlcv["date"],
            "open": validated_ohlcv["open"],
            "high": validated_ohlcv["high"],
            "low": validated_ohlcv["low"],
            "close": validated_ohlcv["close"],
            "volume": validated_ohlcv["volume"],
            "rsi_14": rsi_14,
            "macd": macd,
            "macd_signal": macd_signal,
            "macd_histogram": macd_histogram,
            "bb_upper": bb_upper,
            "bb_middle": bb_middle,
            "bb_lower": bb_lower,
            "bb_width": bb_width,
            "bb_pct": bb_pct,
            "atr_14": atr_14,
            "roc_5": roc_5,
            "roc_10": roc_10,
            "roc_20": roc_20,
            "return_1d": return_1d,
            "return_5d": return_5d,
            "return_10d": return_10d,
            "return_20d": return_20d,
            "overnight_return": overnight_return,
            "intraday_return": intraday_return,
            "hl_range": hl_range,
            "volume_ma_20": volume_ma_20,
            "volume_roc_5": volume_roc_5,
            "obv": obv,
            "volume_ratio": volume_ratio,
            "day_of_week": day_of_week,
            "day_of_month": day_of_month,
            "month": month,
            "quarter": quarter,
            "days_to_month_end": days_to_month_end,
            "trading_day_of_month": trading_day_of_month,
            "volatility_20": volatility_20,
        }
    )
    validate_emitted_frame(frame, target_included=False)
    _feature_boundary_diagnostic(frame)
    return frame


def _feature_boundary_diagnostic(frame: pl.DataFrame) -> None:
    """Diagnostic-only Pydantic row sampling (enforcement is vectorized above)."""
    if frame.is_empty():
        return

    sample = frame.sample(n=min(_FEATURE_SAMPLE_SIZE, frame.height), seed=42)
    invalid = 0
    for idx in range(sample.height):
        row = sample.row(idx, named=True)
        try:
            FeatureModel(**row)
        except Exception as exc:  # noqa: BLE001 - diagnostic only, never fatal
            invalid += 1
            logger.debug("feature_boundary_diagnostic_row_invalid", ticker=row.get("ticker"), error=str(exc))
    if invalid:
        logger.warning(
            "feature_boundary_diagnostic_summary",
            invalid_rows_in_sample=invalid,
            sampled=sample.height,
            note="diagnostic only; enforcement is vectorized in validated_features",
        )


# ---------------------------------------------------------------------------
# Target variable
# ---------------------------------------------------------------------------


@tag(layer="gold", category="target", produces="next_day_return", description="Target: next-day return (uses future data shift -1)")  # type: ignore[untyped-decorator]
def next_day_return(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return validated_ohlcv["close"].shift(-1) / validated_ohlcv["close"] - 1


next_day_return.__doc__ = (
    "Target variable. Uses future data (shift -1). "
    "Must be excluded from inference feature lists - "
    "FeatureEngineer.generate_features(compute_target=False) handles this."
)
