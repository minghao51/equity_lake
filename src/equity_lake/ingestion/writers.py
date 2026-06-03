"""Parquet writer helpers for ingestion."""

import os
import tempfile
from datetime import date
from typing import Any

import pandas as pd
import structlog
from filelock import FileLock

from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    US_EQUITY_DIR,
    US_NEWS_DIR,
    US_SOCIAL_SENTIMENT_DIR,
)
from equity_lake.core.schemas import NEWS_COLUMNS, SOCIAL_COLUMNS

logger = structlog.get_logger()


def _dedupe_key_columns(market: str) -> list[str]:
    if market == "us_news":
        return ["url"]
    if market == "us_social_sentiment":
        return ["ticker", "datetime", "source"]
    return ["ticker", "date"]


def _merge_partition_frames(existing_df: pd.DataFrame, incoming_df: pd.DataFrame, market: str) -> pd.DataFrame:
    """Merge an incoming partition update onto existing rows."""
    if existing_df.empty:
        return incoming_df.copy()
    if incoming_df.empty:
        return existing_df.copy()

    key_columns = _dedupe_key_columns(market)
    combined = pd.concat([existing_df, incoming_df], ignore_index=True)
    merged = combined.drop_duplicates(subset=key_columns, keep="last")
    sort_columns = [column for column in ["date", "datetime", "ticker"] if column in merged.columns]
    if sort_columns:
        merged = merged.sort_values(sort_columns).reset_index(drop=True)
    return merged


def _atomic_write_parquet(df: pd.DataFrame, output_file: os.PathLike[str] | str) -> None:
    """Write parquet to a temp file and atomically replace the target."""
    output_path = os.fspath(output_file)
    output_dir = os.path.dirname(output_path)
    fd, temp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".parquet", dir=output_dir)
    os.close(fd)
    try:
        df.to_parquet(temp_path, index=False, compression="snappy")
        os.replace(temp_path, output_path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise


def write_to_partitioned_parquet(
    df: pd.DataFrame,
    market: str,
    trading_date: date,
    dry_run: bool = False,
    validate_quality: bool = False,
) -> bool:
    """Write a DataFrame to the date-partitioned parquet layout."""
    if df.empty:
        logger.warning(
            "Empty DataFrame for %s on %s, skipping write",
            market,
            trading_date,
        )
        return False

    # Optional quality validation via Pandera
    if validate_quality:
        from equity_lake.validation.pipeline import ValidationPipeline

        data_type = "news" if market in ("us_news", "us_social_sentiment") else "price"
        vp = ValidationPipeline()
        result = vp.validate(df, data_type=data_type, name=f"{market}_{trading_date}")
        if not result.success:
            logger.error("Quality validation failed", market=market, errors=result.errors)
            return False
        if result.warnings:
            for w in result.warnings:
                logger.warning("Quality warning", market=market, warning=w)

    # Map market to directory
    market_dirs = {
        "us_equity": US_EQUITY_DIR,
        "cn_ashare": CN_ASHARE_DIR,
        "hk_sg_equity": HK_SG_EQUITY_DIR,
        "jpx_equity": JPX_EQUITY_DIR,
        "krx_equity": KRX_EQUITY_DIR,
        "us_news": US_NEWS_DIR,
        "us_social_sentiment": US_SOCIAL_SENTIMENT_DIR,
    }

    output_dir = market_dirs.get(market)
    if output_dir is None:
        logger.error("Unknown market: %s", market)
        return False

    partition_dir = output_dir / f"date={trading_date}"
    output_file = partition_dir / f"{trading_date}.parquet"

    if dry_run:
        logger.info("[DRY RUN] Would write %s rows to %s", len(df), output_file)
        return True

    try:
        partition_dir.mkdir(parents=True, exist_ok=True)
        lock_path = partition_dir / ".write.lock"
        with FileLock(str(lock_path)):
            df_write = df.copy()
            if "date" in df_write.columns:
                df_write["date"] = pd.to_datetime(df_write["date"])
            if "datetime" in df_write.columns:
                df_write["datetime"] = pd.to_datetime(df_write["datetime"])

            existing_df = pd.read_parquet(output_file) if output_file.exists() else pd.DataFrame()
            merged_df = _merge_partition_frames(existing_df, df_write, market)
            records_added = max(len(merged_df) - len(existing_df), 0)
            duplicate_count = len(existing_df) + len(df_write) - len(merged_df)

            if duplicate_count > 0:
                logger.warning(
                    "Skipped %s duplicate records while merging %s",
                    duplicate_count,
                    output_file,
                )

            _atomic_write_parquet(merged_df, output_file)

        file_size_kb = output_file.stat().st_size / 1024
        logger.info(
            "Wrote %s total rows to %s (added %s new rows, %.1f KB)",
            len(merged_df),
            output_file,
            records_added,
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
        if column in df.columns and bool(df[column].isnull().all()):
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
        metrics["sentiment_distribution"] = df["sentiment_label"].value_counts().to_dict()

    # Log quality metrics
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
