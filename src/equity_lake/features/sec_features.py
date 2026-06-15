"""Feature engineering integration for SEC filing extraction data.

Reads the ``silver/sec_extractions`` Delta table and merges into the price
DataFrame using ASOF (point-in-time) join: each price row receives the
most recent SEC filing **on or before** that date, preventing look-ahead bias.
"""

from __future__ import annotations

from datetime import date

import duckdb
import polars as pl
import structlog

from equity_lake.core.paths import SEC_EXTRACTIONS_DIR
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger()

SEC_FEATURE_COLUMNS = [
    "sec_risk_sentiment",
    "sec_management_tone",
    "sec_guidance_positive",
    "sec_risk_change_flag",
]


def merge_sec_features(
    conn: duckdb.DuckDBPyConnection,
    features_df: FrameLike,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge SEC filing extraction features via point-in-time ASOF join.

    Each price row receives the most recent SEC filing dated **on or before**
    the price date. This prevents look-ahead bias where a future filing
    retroactively influences earlier features.

    Args:
        conn: DuckDB connection (from FeatureEngineer).
        features_df: Price + technical feature DataFrame.
        start_date: Query start date.
        end_date: Query end date.

    Returns:
        DataFrame with SEC filing columns added via ASOF join.
    """
    features_df = ensure_polars(features_df)
    if features_df.is_empty():
        logger.warning("Empty features DataFrame, skipping SEC merge")
        return features_df

    sec_path = SEC_EXTRACTIONS_DIR
    if not sec_path.exists():
        logger.info("SEC extractions directory not found, skipping merge")
        return _add_empty_sec_columns(features_df)

    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())
    scan = duckdb_scan_for(sec_path)

    query = f"""
        SELECT
            ticker,
            filing_date,
            risk_sentiment,
            management_tone,
            guidance_direction,
            new_vs_repeated
        FROM {scan}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
    """

    try:
        sec_df = conn.execute(query, [tickers, start_date, end_date]).pl()
    except Exception as exc:
        logger.error("sec_query_failed", error=str(exc))
        return _add_empty_sec_columns(features_df)

    if sec_df.is_empty():
        logger.info("No SEC extraction data found, adding neutral columns")
        return _add_empty_sec_columns(features_df)

    logger.info("Loaded SEC extraction data points", rows=sec_df.height)

    sec_df = sec_df.with_columns(
        pl.when(pl.col("guidance_direction") == "positive").then(1).otherwise(0).alias("sec_guidance_positive"),
        pl.when(pl.col("new_vs_repeated").is_in(["new", "modified"])).then(1).otherwise(0).alias("sec_risk_change_flag"),
    )

    sec_aggregated = (
        sec_df.sort("filing_date")
        .group_by("ticker", "filing_date")
        .agg(
            pl.col("risk_sentiment").mean().alias("risk_sentiment"),
            pl.col("management_tone").mean().alias("management_tone"),
            pl.col("sec_guidance_positive").max().alias("sec_guidance_positive"),
            pl.col("sec_risk_change_flag").max().alias("sec_risk_change_flag"),
        )
        .sort(["ticker", "filing_date"])
    )

    merged_df = (
        features_df.sort(["ticker", "date"])
        .join_asof(
            sec_aggregated,
            left_on="date",
            right_on="filing_date",
            by="ticker",
            strategy="backward",
        )
        .with_columns(
            pl.col("risk_sentiment").fill_null(0.0).alias("sec_risk_sentiment"),
            pl.col("management_tone").fill_null(0.0).alias("sec_management_tone"),
            pl.col("sec_guidance_positive").fill_null(0).cast(pl.Int64),
            pl.col("sec_risk_change_flag").fill_null(0).cast(pl.Int64),
        )
        .drop(["filing_date", "risk_sentiment", "management_tone"])
    )

    logger.info(
        "Merged SEC features",
        rows_with_data=merged_df.filter(pl.col("sec_risk_change_flag") > 0).height,
        rows_without_data=merged_df.filter(pl.col("sec_risk_change_flag") == 0).height,
    )
    return merged_df


def _add_empty_sec_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add zero-filled SEC columns when no data exists."""
    return df.with_columns(
        pl.lit(0.0).alias("sec_risk_sentiment"),
        pl.lit(0.0).alias("sec_management_tone"),
        pl.lit(0).alias("sec_guidance_positive"),
        pl.lit(0).alias("sec_risk_change_flag"),
    )


__all__ = ["SEC_FEATURE_COLUMNS", "merge_sec_features"]
