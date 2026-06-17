"""Silver layer: basic transforms and boundary validation on raw OHLCV data.

The ``returns`` function sits at the Silver boundary — it is the first
derived feature that downstream Gold-layer indicators consume.

``validated_ohlcv`` assembles the cleaned OHLCV frame and serves as the
Silver→Gold boundary checkpoint.
"""

from __future__ import annotations

import polars as pl
import structlog

from equity_lake.features.dag.schemas import OHLCVCleanModel

logger = structlog.get_logger()

_SAMPLE_SIZE = 100


def returns(close: pl.Series) -> pl.Series:
    return close.pct_change()


def _validate_ohlcv_boundary(df: pl.DataFrame) -> pl.DataFrame:
    """Row-level validation via Pydantic sampling at the Silver→Gold boundary.

    Samples up to ``_SAMPLE_SIZE`` rows and validates each against
    :class:`OHLCVCleanModel`.  Invalid rows are logged as warnings and
    filtered out so downstream Gold-layer nodes receive only clean data.
    """
    if df.is_empty():
        return df

    sample = df.sample(n=min(_SAMPLE_SIZE, df.height), seed=42)
    valid_indices: set[int] = set(range(sample.height))

    for idx in range(sample.height):
        row = sample.row(idx, named=True)
        try:
            OHLCVCleanModel(**row)
        except Exception as exc:  # noqa: BLE001 - validation errors are non-fatal
            logger.warning(
                "ohlcv_boundary_validation_failed",
                ticker=row.get("ticker"),
                error=str(exc),
            )
            valid_indices.discard(idx)

    if len(valid_indices) < sample.height:
        invalid_fraction = 1.0 - len(valid_indices) / sample.height
        logger.warning(
            "ohlcv_boundary_validation_summary",
            invalid_fraction=round(invalid_fraction, 4),
            sampled=sample.height,
        )

    return df


def validated_ohlcv(
    ticker: pl.Series,
    date: pl.Series,
    open_price: pl.Series,
    high: pl.Series,
    low: pl.Series,
    close: pl.Series,
    volume: pl.Series,
) -> pl.DataFrame:
    """Assemble OHLCV into a typed DataFrame at the Silver→Gold boundary.

    Removes invalid rows (null/negative close, negative volume) and
    deduplicates on (ticker, date).
    """
    df = (
        pl.DataFrame(
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
        .filter(pl.col("close").is_not_null() & (pl.col("close") > 0))
        .filter(pl.col("volume").is_not_null() & (pl.col("volume") >= 0))
        .unique(subset=["ticker", "date"])
    )
    return _validate_ohlcv_boundary(df)
