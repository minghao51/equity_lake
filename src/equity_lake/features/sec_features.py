"""Feature engineering integration for SEC filing extraction data.

Reads the ``silver/sec_extractions`` Delta table, aggregates per-ticker
quarterly features (forward-filled), and left-joins into the price DataFrame.
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
    """Merge SEC filing extraction features into the feature frame.

    Args:
        conn: DuckDB connection (from FeatureEngineer).
        features_df: Price + technical feature DataFrame.
        start_date: Query start date.
        end_date: Query end date.

    Returns:
        DataFrame with SEC filing columns added via left join.
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
        .group_by("ticker")
        .agg(
            pl.col("risk_sentiment").last().alias("sec_risk_sentiment"),
            pl.col("management_tone").last().alias("sec_management_tone"),
            pl.col("sec_guidance_positive").max().alias("sec_guidance_positive"),
            pl.col("sec_risk_change_flag").max().alias("sec_risk_change_flag"),
        )
    )

    merged_df = features_df.join(sec_aggregated, on="ticker", how="left").with_columns(
        pl.col("sec_risk_sentiment").fill_null(0.0),
        pl.col("sec_management_tone").fill_null(0.0),
        pl.col("sec_guidance_positive").fill_null(0).cast(pl.Int64),
        pl.col("sec_risk_change_flag").fill_null(0).cast(pl.Int64),
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
