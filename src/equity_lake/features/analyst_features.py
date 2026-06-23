"""Feature engineering integration for analyst rating data.

Reads the ``us_analyst_ratings`` Delta table, aggregates per-ticker
daily features, and left-joins into the price DataFrame.
"""

from __future__ import annotations

import warnings
from datetime import date

import duckdb
import polars as pl
import structlog

from equity_lake.core.paths import ANALYST_RATINGS_DIR
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger()

ANALYST_FEATURE_COLUMNS = [
    "analyst_consensus_score",
    "analyst_consensus_score_ewma_7d",
    "analyst_coverage_count",
    "analyst_price_target_mean",
    "analyst_price_target_upside",
]


def merge_analyst_rating_features(
    conn: duckdb.DuckDBPyConnection,
    features_df: FrameLike,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge analyst rating features into the feature frame.

    .. deprecated::
        Use :meth:`FeaturePipeline.compute_enriched` with ``enable_analyst_ratings=True``.

    Args:
        conn: DuckDB connection (from FeatureEngineer).
        features_df: Price + technical feature DataFrame.
        start_date: Query start date.
        end_date: Query end date.

    Returns:
        DataFrame with analyst rating columns added via left join.
    """
    warnings.warn("Use FeaturePipeline.compute_enriched(enable_analyst_ratings=True) instead", DeprecationWarning, stacklevel=2)
    features_df = ensure_polars(features_df)
    if features_df.is_empty():
        logger.warning("Empty features DataFrame, skipping analyst rating merge")
        return features_df

    ratings_path = ANALYST_RATINGS_DIR
    if not ratings_path.exists():
        logger.info("Analyst ratings directory not found, skipping merge")
        return _add_empty_analyst_columns(features_df)

    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())
    scan = duckdb_scan_for(ratings_path)

    query = f"""
        SELECT
            ticker,
            date,
            consensus_score AS analyst_consensus_score,
            price_target_count AS analyst_coverage_count,
            price_target_mean AS analyst_price_target_mean
        FROM {scan}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
    """

    try:
        ratings_df = conn.execute(query, [tickers, start_date, end_date]).pl()
    except Exception as exc:
        logger.error("analyst_rating_query_failed", error=str(exc))
        return _add_empty_analyst_columns(features_df)

    if ratings_df.is_empty():
        logger.info("No analyst rating data found, adding neutral columns")
        return _add_empty_analyst_columns(features_df)

    logger.info("Loaded analyst rating data points", rows=ratings_df.height)

    ewma_df = ratings_df.sort(["ticker", "date"]).with_columns(
        pl.col("analyst_consensus_score")
        .ewm_mean(half_life=7.0, ignore_nulls=True)
        .over("ticker")
        .fill_null(0.0)
        .alias("analyst_consensus_score_ewma_7d"),
    )

    merged_df = features_df.join(ewma_df, on=["ticker", "date"], how="left").with_columns(
        pl.col("analyst_consensus_score").fill_null(0.0),
        pl.col("analyst_consensus_score_ewma_7d").fill_null(0.0),
        pl.col("analyst_coverage_count").fill_null(0).cast(pl.Int64),
    )

    if "close" in merged_df.columns and "analyst_price_target_mean" in merged_df.columns:
        merged_df = merged_df.with_columns(
            ((pl.col("analyst_price_target_mean") - pl.col("close")) / pl.col("close").clip(lower_bound=1e-8)).alias("analyst_price_target_upside"),
        )
    else:
        merged_df = merged_df.with_columns(pl.lit(0.0).alias("analyst_price_target_upside"))

    merged_df = merged_df.with_columns(
        pl.col("analyst_price_target_mean").fill_null(0.0),
        pl.col("analyst_price_target_upside").fill_null(0.0),
    )

    logger.info(
        "Merged analyst rating features",
        rows_with_data=merged_df.filter(pl.col("analyst_coverage_count") > 0).height,
        rows_without_data=merged_df.filter(pl.col("analyst_coverage_count") == 0).height,
    )
    return merged_df


def _add_empty_analyst_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add zero-filled analyst columns when no data exists."""
    return df.with_columns(
        pl.lit(0.0).alias("analyst_consensus_score"),
        pl.lit(0.0).alias("analyst_consensus_score_ewma_7d"),
        pl.lit(0).alias("analyst_coverage_count"),
        pl.lit(0.0).alias("analyst_price_target_mean"),
        pl.lit(0.0).alias("analyst_price_target_upside"),
    )


__all__ = ["ANALYST_FEATURE_COLUMNS", "merge_analyst_rating_features"]
