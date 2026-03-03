"""Parquet writer helpers for ingestion."""

from datetime import date
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import structlog

from equity_lake.core.runtime import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    NEWS_COLUMNS,
    SOCIAL_COLUMNS,
    US_EQUITY_DIR,
    US_NEWS_DIR,
    US_SOCIAL_SENTIMENT_DIR,
)

logger = structlog.get_logger()


def write_to_partitioned_parquet(
    df: pd.DataFrame,
    market: str,
    trading_date: date,
    dry_run: bool = False,
) -> bool:
    """Write a DataFrame to the date-partitioned parquet layout."""
    if df.empty:
        logger.warning(
            "Empty DataFrame for %s on %s, skipping write",
            market,
            trading_date,
        )
        return False

    # Map market to directory
    market_dirs = {
        "us_equity": US_EQUITY_DIR,
        "cn_ashare": CN_ASHARE_DIR,
        "hk_sg_equity": HK_SG_EQUITY_DIR,
        "us_news": US_NEWS_DIR,
        "us_social_sentiment": US_SOCIAL_SENTIMENT_DIR,
    }

    output_dir = market_dirs.get(market)
    if output_dir is None:
        logger.error("Unknown market: %s", market)
        return False

    partition_dir = output_dir / f"date={trading_date}"
    output_file = partition_dir / f"{trading_date}.parquet"

    # Check for duplicates using appropriate key columns
    if output_file.exists():
        logger.info("File exists: %s. Checking for duplicates...", output_file)
        try:
            existing_df = pd.read_parquet(output_file)

            # Use different keys for news vs OHLCV data
            if market == "us_news":
                # Deduplicate by URL for news
                existing_keys = set(existing_df["url"].tolist())
                duplicate_mask = df["url"].isin(existing_keys)
            elif market == "us_social_sentiment":
                # Deduplicate by ticker + datetime + source
                existing_keys = set(
                    existing_df.apply(
                        lambda row: (
                            row["ticker"],
                            row["datetime"],
                            row["source"],
                        ),
                        axis=1,
                    ).tolist()
                )
                duplicate_mask = df.apply(
                    lambda row: (
                        row["ticker"],
                        row["datetime"],
                        row["source"],
                    )
                    in existing_keys,
                    axis=1,
                )
            else:
                # OHLCV data: deduplicate by ticker + date
                existing_keys = set(
                    existing_df.apply(
                        lambda row: (row["ticker"], row["date"]),
                        axis=1,
                    ).tolist()
                )
                duplicate_mask = df.apply(
                    lambda row: (row["ticker"], row["date"]) in existing_keys,
                    axis=1,
                )

            duplicate_count = int(duplicate_mask.sum())
            total_count = len(df)

            if duplicate_count > 0:
                logger.warning(
                    "Found %s duplicate records (%s/%s = %.1f%%)",
                    duplicate_count,
                    duplicate_count,
                    total_count,
                    duplicate_count / total_count * 100,
                )
                filtered_df = df[~duplicate_mask]
                if not isinstance(filtered_df, pd.DataFrame):
                    filtered_df = pd.DataFrame()
                df = filtered_df
                if df.empty:
                    logger.warning("All records are duplicates. Skipping write.")
                    return True
                logger.info(
                    "Writing %s new records (skipped %s duplicates)",
                    len(df),
                    duplicate_count,
                )
        except Exception as exc:
            logger.error(
                "Failed to check for duplicates: %s. Continuing with write...",
                exc,
            )

    if dry_run:
        logger.info("[DRY RUN] Would write %s rows to %s", len(df), output_file)
        return True

    try:
        partition_dir.mkdir(parents=True, exist_ok=True)
        df_write = df.copy()
        if "date" in df_write.columns:
            df_write["date"] = pd.to_datetime(df_write["date"])
        if "datetime" in df_write.columns:
            df_write["datetime"] = pd.to_datetime(df_write["datetime"])
        df_write.to_parquet(output_file, index=False, compression="snappy")
        file_size_kb = output_file.stat().st_size / 1024
        logger.info(
            "Wrote %s rows to %s (%.1f KB)",
            len(df_write),
            output_file,
            file_size_kb,
        )
        return True
    except Exception as exc:
        logger.error("Failed to write Parquet file: %s", exc)
        return False


def validate_schema(df: pd.DataFrame, market: str) -> bool:
    """Validate the schema based on market type."""
    # Determine required columns based on market
    if market == "us_news":
        required_cols = NEWS_COLUMNS
    elif market == "us_social_sentiment":
        required_cols = SOCIAL_COLUMNS
    else:
        # Default to OHLCV schema
        required_cols = ["ticker", "date", "open", "high", "low", "close", "volume"]

    missing_cols = set(required_cols) - set(df.columns)
    if missing_cols:
        logger.error("%s: Missing required columns: %s", market, missing_cols)
        return False

    for column in required_cols:
        if column in df.columns and bool(df[column].isnull().all()):  # type: ignore[call-overload]
            logger.warning("%s: Column '%s' is all null", market, column)

    return True


def validate_news_data_quality(df: pd.DataFrame) -> dict[str, Any]:
    """
    Validate news data quality and return quality metrics.

    Args:
        df: News DataFrame to validate

    Returns:
        Dictionary with quality metrics:
            - total_rows: Total number of rows
            - missing_headlines: Count of missing headlines
            - missing_urls: Count of missing URLs
            - invalid_dates: Count of invalid dates
            - duplicate_urls: Count of duplicate URLs
            - sentiment_distribution: Distribution of sentiment labels
            - date_range: Min and max dates
    """
    metrics: dict[str, Any] = {
        "total_rows": len(df),
        "missing_headlines": 0,
        "missing_urls": 0,
        "invalid_dates": 0,
        "duplicate_urls": 0,
        "sentiment_distribution": {},
        "date_range": None,
    }

    if df.empty:
        logger.warning("Empty DataFrame provided for quality validation")
        return metrics

    # Check for missing headlines
    if "headline" in df.columns:
        metrics["missing_headlines"] = int(df["headline"].isna().sum())

    # Check for missing URLs
    if "url" in df.columns:
        metrics["missing_urls"] = int(df["url"].isna().sum())

        # Check for duplicate URLs
        metrics["duplicate_urls"] = int(df["url"].duplicated().sum())

    # Check for invalid dates
    if "date" in df.columns:
        try:
            pd.to_datetime(df["date"])
        except Exception:
            metrics["invalid_dates"] = len(df)

        # Get date range
        metrics["date_range"] = {
            "min": str(df["date"].min()),
            "max": str(df["date"].max()),
        }

    # Sentiment distribution
    if "sentiment_label" in df.columns:
        metrics["sentiment_distribution"] = (
            df["sentiment_label"].value_counts().to_dict()
        )

    # Log quality metrics
    logger.info(
        "News data quality: %s rows, %s missing headlines, "
        "%s missing URLs, %s duplicate URLs",
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
