"""Bronze-to-silver transform for unstructured articles.

Writes raw articles to the bronze Delta table, runs LLM batch processing,
then explodes to silver article-ticker pairs and writes to the silver Delta table.
"""

from __future__ import annotations

from datetime import date

import polars as pl
import structlog

from equity_lake.core.paths import BRONZE_RAW_ARTICLES_DIR, SILVER_PROCESSED_ARTICLES_DIR
from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS, SILVER_ARTICLE_COLUMNS
from equity_lake.storage.delta import merge_delta

logger = structlog.get_logger()


def write_bronze(df: pl.DataFrame) -> bool:
    if df.is_empty():
        logger.warning("Empty DataFrame, skipping bronze write")
        return False

    for col in BRONZE_ARTICLE_COLUMNS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))

    df = df.select(BRONZE_ARTICLE_COLUMNS)
    BRONZE_RAW_ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    return merge_delta(df, "bronze/raw_articles", key_columns=["source_url"])


def write_silver(df: pl.DataFrame) -> bool:
    if df.is_empty():
        logger.warning("Empty DataFrame, skipping silver write")
        return False

    for col in SILVER_ARTICLE_COLUMNS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))

    df = df.select(SILVER_ARTICLE_COLUMNS)
    SILVER_PROCESSED_ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    return merge_delta(df, "silver/processed_articles", key_columns=["article_id", "ticker"])


def read_bronze(trading_date: date | None = None) -> pl.DataFrame:
    """Read bronze articles, optionally filtered by date."""
    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan = duckdb_scan_for(BRONZE_RAW_ARTICLES_DIR)
        con = duckdb.connect(":memory:")
        con.execute("INSTALL delta; LOAD delta;")
        query = f"SELECT * FROM {scan}"
        if trading_date:
            query += f" WHERE date = '{trading_date}'"
        df = con.execute(query).pl()
        con.close()
        return df
    except Exception as exc:
        logger.warning("bronze_read_failed", error=str(exc))
        return pl.DataFrame()


def process_bronze_to_silver(trading_date: date) -> bool:
    """Process unprocessed bronze articles through LLM and write to silver.

    Args:
        trading_date: The trading date to process articles for.

    Returns:
        True if silver write succeeded, False otherwise.
    """
    bronze_df = read_bronze(trading_date)
    if bronze_df.is_empty():
        logger.warning("No bronze articles to process", trading_date=str(trading_date))
        return False

    logger.info("Processing bronze to silver", article_count=bronze_df.height, trading_date=str(trading_date))

    try:
        from equity_lake.ingestion.llm_processor import run_llm_processing

        silver_df = run_llm_processing(bronze_df)
    except Exception as exc:
        logger.error("llm_processing_failed", error=str(exc))
        return False

    if silver_df.is_empty():
        logger.warning("LLM processing produced no silver rows")
        return False

    ticker_filter = _load_known_tickers()
    if ticker_filter:
        silver_df = silver_df.filter(pl.col("ticker").is_null() | pl.col("ticker").is_in(ticker_filter))
        logger.info("Filtered silver by known tickers", remaining=silver_df.height, known_tickers=len(ticker_filter))

    return write_silver(silver_df)


def _load_known_tickers() -> list[str]:
    try:
        from equity_lake.core.config import TickerConfig

        config = TickerConfig()
        return config.get_tickers_for_market("us", active_only=True)
    except Exception:
        return []


__all__ = [
    "process_bronze_to_silver",
    "read_bronze",
    "write_bronze",
    "write_silver",
]
