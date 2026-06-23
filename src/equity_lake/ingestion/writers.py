"""Writer helpers for ingestion.

All writes go through the Delta Lake storage layer (ACID transactions,
merge/upsert, time-travel).
"""

from datetime import date
from typing import Any

import polars as pl
import structlog

from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.core.schemas import MACRO_COLUMNS, NEWS_COLUMNS, SOCIAL_COLUMNS

logger = structlog.get_logger()


def _dedupe_key_columns(market: str) -> list[str]:
    # Medallion paths + legacy market strings both supported
    if market in ("01_bronze/macro", "macro", "macro_indicators"):
        return ["indicator", "date"]
    if market in ("02_silver/news_sentiment", "us_news"):
        return ["url"]
    if market in ("02_silver/social_sentiment", "us_social_sentiment"):
        return ["ticker", "datetime", "source"]
    if market in (
        "01_bronze/raw_articles",
        "bronze/raw_articles",
        "rss_news",
        "reddit_posts",
        "stocktwits_messages",
        "us_earnings_transcripts",
        "sec_filings_fulltext",
    ):
        return ["source_type", "source_url"]
    if market in ("02_silver/processed_articles", "silver/processed_articles"):
        return ["article_id", "ticker"]
    if market in ("02_silver/analyst_ratings", "us_analyst_ratings"):
        return ["ticker", "date"]
    if market in ("02_silver/sec_financials", "us_sec_financials"):
        return ["ticker", "date", "filing_type"]
    if market in ("04_platinum/predictions", "predictions"):
        return ["ticker", "date"]
    return ["ticker", "date"]


def write_to_partitioned_parquet(
    df: FrameLike,
    market: str,
    trading_date: date,
    dry_run: bool = False,
    validate_quality: bool = False,
    skip_schema_validation: bool = False,
) -> bool:
    """Write a DataFrame to the Delta Lake storage layer.

    Args:
        df: Data to write.
        market: Market name (e.g. ``"us_equity"``, ``"us_news"``).
        trading_date: Trading date for the partition.
        dry_run: If True, skip the actual write.
        validate_quality: If True, run pointblank validation before writing.
        skip_schema_validation: If True, bypass column-level schema checks.
    """
    df_polars = ensure_polars(df)

    if df_polars.is_empty():
        logger.warning(
            "Empty DataFrame for %s on %s, skipping write",
            market,
            trading_date,
        )
        return False

    if not skip_schema_validation and not validate_schema(df_polars, market):
        logger.error("Schema validation failed, refusing to write", market=market)
        return False

    if validate_quality:
        from equity_lake.validation.pipeline import ValidationPipeline

        data_type = "news" if market in ("us_news", "us_social_sentiment", "02_silver/news_sentiment", "02_silver/social_sentiment") else "price"
        vp = ValidationPipeline()
        result = vp.validate(df_polars, data_type=data_type, name=f"{market}_{trading_date}")
        if not result.success:
            logger.error("Quality validation failed", market=market, errors=result.errors)
            return False
        if result.warnings:
            for w in result.warnings:
                logger.warning("Quality warning", market=market, warning=w)

    if dry_run:
        logger.info("[DRY RUN] Would write %s rows to Delta table %s", len(df_polars), market)
        return True

    from equity_lake.storage.delta import merge_delta

    key_columns = _dedupe_key_columns(market)
    return merge_delta(df_polars, market, key_columns=key_columns)


def validate_schema(df: FrameLike, market: str) -> bool:
    df_pl = ensure_polars(df)
    if market in ("macro", "macro_indicators", "01_bronze/macro"):
        required_cols = MACRO_COLUMNS
    elif market in ("us_news", "02_silver/news_sentiment"):
        required_cols = NEWS_COLUMNS
    elif market in ("us_social_sentiment", "02_silver/social_sentiment"):
        required_cols = SOCIAL_COLUMNS
    elif market in ("rss_news", "reddit_posts", "stocktwits_messages", "us_earnings_transcripts", "sec_filings_fulltext"):
        required_cols = ["article_id", "source_type", "source_url", "title", "date"]
    elif market in ("us_analyst_ratings", "02_silver/analyst_ratings"):
        required_cols = ["ticker", "date"]
    elif market in ("us_sec_financials", "02_silver/sec_financials"):
        required_cols = ["ticker", "date", "filing_type"]
    elif market in ("bronze/raw_articles", "silver/processed_articles", "01_bronze/raw_articles", "02_silver/processed_articles"):
        required_cols = ["article_id", "date"]
    elif market in ("predictions", "04_platinum/predictions"):
        required_cols = ["ticker", "date", "direction", "probability"]
    else:
        required_cols = ["ticker", "date", "open", "high", "low", "close", "volume"]

    missing_cols = set(required_cols) - set(df_pl.columns)
    if missing_cols:
        logger.error("%s: Missing required columns: %s", market, missing_cols)
        return False

    null_counts = df_pl.null_count().row(0, named=True)
    for col in required_cols:
        if col in df_pl.columns and col in null_counts and null_counts[col] == df_pl.height:
            logger.error("%s: Required column '%s' is all null", market, col)
            return False

    return True


def validate_news_data_quality(df: FrameLike) -> dict[str, Any]:
    """
    Validate news data quality and return quality metrics.

    Args:
        df: News DataFrame to validate

    Returns:
        Dictionary with quality metrics.
    """
    df_pl = ensure_polars(df)
    metrics: dict[str, Any] = {
        "total_rows": df_pl.height,
        "missing_headlines": 0,
        "missing_urls": 0,
        "invalid_dates": 0,
        "duplicate_urls": 0,
        "sentiment_distribution": {},
        "date_range": None,
    }

    if df_pl.is_empty():
        logger.warning("Empty DataFrame provided for quality validation")
        return metrics

    if "headline" in df_pl.columns:
        metrics["missing_headlines"] = df_pl.select(pl.col("headline").null_count()).item()

    if "url" in df_pl.columns:
        metrics["missing_urls"] = df_pl.select(pl.col("url").null_count()).item()
        metrics["duplicate_urls"] = df_pl.select(pl.col("url").is_duplicated().sum()).item()

    if "date" in df_pl.columns:
        metrics["date_range"] = {
            "min": str(df_pl.select(pl.col("date").min()).item()),
            "max": str(df_pl.select(pl.col("date").max()).item()),
        }

    if "sentiment_label" in df_pl.columns:
        counts = df_pl.group_by("sentiment_label").agg(pl.len().alias("count")).rows(named=True)
        metrics["sentiment_distribution"] = {row["sentiment_label"]: row["count"] for row in counts}

    logger.info(
        "News data quality: %s rows, %s missing headlines, %s missing URLs, %s duplicate URLs",
        metrics["total_rows"],
        metrics["missing_headlines"],
        metrics["missing_urls"],
        metrics["duplicate_urls"],
    )

    return metrics


__all__ = [
    "validate_schema",
    "validate_news_data_quality",
    "write_to_partitioned_parquet",
]
