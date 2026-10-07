"""Silver layer: basic transforms and boundary validation on raw OHLCV data.

The ``returns`` function sits at the Silver boundary — it is the first
derived feature that downstream Gold-layer indicators consume.

``validated_ohlcv`` assembles the cleaned OHLCV frame and serves as the
Silver→Gold boundary checkpoint. Since W01 (D01) it is *connected*: every
Gold-layer indicator consumes its output, and it enforces the invalid-row
policy with full-frame vectorized checks — invalid required price input
**fails** the computation rather than silently shrinking scope. Pydantic row
sampling remains as a clearly labeled diagnostic only.
"""

from __future__ import annotations

import polars as pl
import structlog
from hamilton.function_modifiers import tag

from equity_lake.features.dag.schemas import OHLCVCleanModel

logger = structlog.get_logger()

_SAMPLE_SIZE = 100


class OHLCVValidationError(ValueError):
    """Raised when required OHLCV input violates the Silver boundary contract.

    D01 policy: invalid required price input fails the per-ticker computation
    loudly instead of being silently filtered (silent filtering was the F02
    defect — bad rows survived and shrank scope invisibly).
    """


@tag(layer="silver", category="transform", produces="returns")  # type: ignore[untyped-decorator]
def returns(validated_ohlcv: pl.DataFrame) -> pl.Series:
    return validated_ohlcv["close"].pct_change()


def _require(condition: pl.Series, message: str, df: pl.DataFrame) -> None:
    """Raise :class:`OHLCVValidationError` when ``condition`` has any True row."""
    if condition.any():
        bad = df.filter(condition).head(3)
        raise OHLCVValidationError(f"{message}; first offending rows: {bad.rows(named=True)}")


def _validate_ohlcv_boundary(df: pl.DataFrame) -> pl.DataFrame:
    """Diagnostic-only Pydantic row sampling at the Silver→Gold boundary.

    Enforcement lives in the vectorized checks in :func:`validated_ohlcv`;
    this sampling only logs a summary for observability (it cannot reject or
    filter anything).
    """
    if df.is_empty():
        return df

    sample = df.sample(n=min(_SAMPLE_SIZE, df.height), seed=42)
    invalid = 0
    for idx in range(sample.height):
        row = sample.row(idx, named=True)
        try:
            OHLCVCleanModel(**row)
        except Exception as exc:  # noqa: BLE001 - diagnostic only, never fatal
            invalid += 1
            logger.debug("ohlcv_boundary_diagnostic_row_invalid", ticker=row.get("ticker"), error=str(exc))
    if invalid:
        logger.warning(
            "ohlcv_boundary_diagnostic_summary",
            invalid_rows_in_sample=invalid,
            sampled=sample.height,
            note="diagnostic only; enforcement is vectorized in validated_ohlcv",
        )
    return df


@tag(
    layer="silver",
    category="validation",
    produces="validated_ohlcv",
    description="Full-frame vectorized OHLCV validation (D01: invalid required input fails) at Bronze→Silver",
)  # type: ignore[untyped-decorator]
def validated_ohlcv(
    ticker: pl.Series,
    date: pl.Series,
    open_price: pl.Series,
    high: pl.Series,
    low: pl.Series,
    close: pl.Series,
    volume: pl.Series,
) -> pl.DataFrame:
    """Assemble, validate, and order OHLCV at the Silver→Gold boundary.

    D01 invalid-row policy (enforced, not sampled):

    - null ``ticker``/``date`` or null OHLCV values fail;
    - ``open``/``high``/``low``/``close`` must be strictly positive and
      ``volume`` non-negative — violations fail;
    - OHLC relationships (``high >= max(open, close, low)``,
      ``low <= min(open, close)``) must hold — violations fail;
    - rows are sorted by ``(ticker, date)`` (stable) and duplicates on
      ``(ticker, date)`` keep the first input occurrence — deterministic
      ordering before rolling/shift calculations.

    Every Gold-layer indicator consumes this frame, so all derived series are
    row-aligned by construction.
    """
    df = pl.DataFrame(
        {
            "ticker": ticker,
            "date": date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    if df.is_empty():
        return df

    _require(df["ticker"].is_null(), "null ticker in required OHLCV input", df)
    _require(df["date"].is_null(), "null date in required OHLCV input", df)
    for column in ("open", "high", "low", "close", "volume"):
        _require(df[column].is_null(), f"null {column} in required OHLCV input", df)
        # NaN is not null in Polars (and NaN <= 0 is false) — reject it here so
        # the failure names the raw input column instead of surfacing later at
        # the Gold boundary against a derived feature.
        if df[column].dtype in (pl.Float64, pl.Float32):
            _require(df[column].is_nan(), f"NaN {column} in required OHLCV input", df)
    _require((df["open"] <= 0) | (df["high"] <= 0) | (df["low"] <= 0) | (df["close"] <= 0), "OHLC prices must be strictly positive", df)
    _require(df["volume"] < 0, "volume must be non-negative", df)
    _require(
        (df["high"] < df[["high", "low", "open", "close"]].max_horizontal()) | (df["low"] > df[["low", "open", "close"]].min_horizontal()),
        "OHLC relationship violated (high must be >= open/close/low; low must be <= open/close)",
        df,
    )

    duplicate_count = df.height - df.unique(subset=["ticker", "date"]).height
    if duplicate_count:
        logger.warning(
            "ohlcv_duplicate_identity_rows_deduplicated",
            duplicates=duplicate_count,
            policy="keep first input occurrence per (ticker, date)",
        )
    df = df.sort(["ticker", "date"], maintain_order=True).unique(subset=["ticker", "date"], keep="first", maintain_order=True)

    return _validate_ohlcv_boundary(df)
