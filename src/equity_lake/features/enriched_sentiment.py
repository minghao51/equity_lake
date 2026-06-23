"""Feature engineering integration for LLM-enriched silver-layer sentiment data.

Reads the silver Delta table, aggregates per-ticker daily features, and
left-joins into the price DataFrame. Follows the existing
``merge_sentiment_features()`` pattern in ``features/engineering.py``.
"""

from __future__ import annotations

import warnings
from datetime import date

import duckdb
import polars as pl
import structlog

from equity_lake.core.paths import SILVER_PROCESSED_ARTICLES_DIR
from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger()

ENRICHED_FEATURE_COLUMNS = [
    "enriched_article_count",
    "enriched_sentiment_mean",
    "enriched_sentiment_ewma_3d",
    "enriched_sentiment_ewma_7d",
    "enriched_confidence_mean",
    "enriched_relevance_mean",
    "bullish_ratio",
    "social_volume",
    "social_sentiment_mean",
    "breaking_news_flag",
]


def merge_enriched_sentiment_features(
    conn: duckdb.DuckDBPyConnection,
    features_df: FrameLike,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Merge aggregated LLM-enriched sentiment features into the feature frame.

    .. deprecated::
        Use :meth:`FeaturePipeline.compute_enriched` with ``enable_enriched_sentiment=True``.

    Args:
        conn: DuckDB connection (from FeatureEngineer).
        features_df: Price + technical feature DataFrame.
        start_date: Query start date.
        end_date: Query end date.

    Returns:
        DataFrame with enriched sentiment columns added via left join.
    """
    warnings.warn("Use FeaturePipeline.compute_enriched(enable_enriched_sentiment=True) instead", DeprecationWarning, stacklevel=2)
    features_df = ensure_polars(features_df)
    if features_df.is_empty():
        logger.warning("Empty features DataFrame, skipping enriched sentiment merge")
        return features_df

    silver_path = SILVER_PROCESSED_ARTICLES_DIR
    if not silver_path.exists():
        logger.info("Silver processed articles directory not found, skipping enriched sentiment merge")
        return _add_empty_enriched_columns(features_df)

    tickers = sorted(str(v) for v in features_df["ticker"].unique().to_list())
    scan = duckdb_scan_for(silver_path)

    query = f"""
        SELECT
            ticker,
            date,
            COUNT(*) as enriched_article_count,
            AVG(sentiment_score) as enriched_sentiment_mean,
            AVG(confidence) as enriched_confidence_mean,
            AVG(market_relevance) as enriched_relevance_mean,
            SUM(CASE WHEN sentiment_label = 'bullish' THEN 1.0 ELSE 0.0 END)
                / NULLIF(COUNT(*), 0) as bullish_ratio,
            SUM(CASE WHEN source_type IN ('reddit', 'stocktwits') THEN 1 ELSE 0 END) as social_volume,
            AVG(CASE WHEN source_type IN ('reddit', 'stocktwits') THEN sentiment_score ELSE NULL END) as social_sentiment_mean,
            MAX(CASE WHEN market_relevance > 0.8 AND impact_horizon = 'short' THEN 1 ELSE 0 END) as breaking_news_flag
        FROM {scan}
        WHERE ticker IN (SELECT unnest(?::VARCHAR[]))
        AND date BETWEEN ? AND ?
        GROUP BY ticker, date
    """

    try:
        sentiment_df = conn.execute(query, [tickers, start_date, end_date]).pl()
    except Exception as exc:
        logger.error("enriched_sentiment_query_failed", error=str(exc))
        return _add_empty_enriched_columns(features_df)

    if sentiment_df.is_empty():
        logger.warning("No enriched sentiment data found, adding neutral columns")
        return _add_empty_enriched_columns(features_df)

    logger.info("Loaded enriched sentiment data points", rows=sentiment_df.height)

    ewma_df = sentiment_df.sort(["ticker", "date"]).with_columns(
        pl.col("enriched_sentiment_mean")
        .ewm_mean(half_life=3.0, ignore_nulls=True)
        .over("ticker")
        .fill_null(0.0)
        .alias("enriched_sentiment_ewma_3d"),
        pl.col("enriched_sentiment_mean")
        .ewm_mean(half_life=7.0, ignore_nulls=True)
        .over("ticker")
        .fill_null(0.0)
        .alias("enriched_sentiment_ewma_7d"),
    )

    merged_df = features_df.join(ewma_df, on=["ticker", "date"], how="left").with_columns(
        pl.col("enriched_article_count").fill_null(0).cast(pl.Int64),
        pl.col("enriched_sentiment_mean").fill_null(0.0),
        pl.col("enriched_sentiment_ewma_3d").fill_null(0.0),
        pl.col("enriched_sentiment_ewma_7d").fill_null(0.0),
        pl.col("enriched_confidence_mean").fill_null(0.0),
        pl.col("enriched_relevance_mean").fill_null(0.0),
        pl.col("bullish_ratio").fill_null(0.0),
        pl.col("social_volume").fill_null(0).cast(pl.Int64),
        pl.col("social_sentiment_mean").fill_null(0.0),
        pl.col("breaking_news_flag").fill_null(0).cast(pl.Int64),
    )

    logger.info(
        "Merged enriched sentiment features",
        rows_with_data=merged_df.filter(pl.col("enriched_article_count") > 0).height,
        rows_without_data=merged_df.filter(pl.col("enriched_article_count") == 0).height,
    )
    return merged_df


def _add_empty_enriched_columns(df: pl.DataFrame) -> pl.DataFrame:
    """Add zero-filled enriched sentiment columns when no silver data exists."""
    return df.with_columns(
        pl.lit(0).alias("enriched_article_count"),
        pl.lit(0.0).alias("enriched_sentiment_mean"),
        pl.lit(0.0).alias("enriched_sentiment_ewma_3d"),
        pl.lit(0.0).alias("enriched_sentiment_ewma_7d"),
        pl.lit(0.0).alias("enriched_confidence_mean"),
        pl.lit(0.0).alias("enriched_relevance_mean"),
        pl.lit(0.0).alias("bullish_ratio"),
        pl.lit(0).alias("social_volume"),
        pl.lit(0.0).alias("social_sentiment_mean"),
        pl.lit(0).alias("breaking_news_flag"),
    )


__all__ = ["ENRICHED_FEATURE_COLUMNS", "merge_enriched_sentiment_features"]
