"""Bronze-to-silver transform for unstructured articles.

Reads raw articles from the bronze Delta table, filters out already-processed
ones, runs LLM batch processing, then explodes to silver article-ticker pairs
and writes to the silver Delta table.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import structlog

from equity_lake.core.paths import BRONZE_RAW_ARTICLES_DIR, SILVER_PROCESSED_ARTICLES_DIR
from equity_lake.core.schemas import SILVER_ARTICLE_COLUMNS
from equity_lake.storage.delta import merge_delta

logger = structlog.get_logger()


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


def read_bronze(trading_date: date | None = None, table_path: Path | None = None) -> pl.DataFrame:
    """Read bronze articles, optionally filtered by date.

    Args:
        trading_date: If provided, filter to this date only.
        table_path: Override the bronze directory path.
    """
    path = table_path or BRONZE_RAW_ARTICLES_DIR
    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan = duckdb_scan_for(path)
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL delta; LOAD delta;")
            query = f"SELECT * FROM {scan}"
            if trading_date:
                query = query + " WHERE date = ?"
                df = con.execute(query, [trading_date]).pl()
            else:
                df = con.execute(query).pl()
        finally:
            con.close()
        return df
    except Exception as exc:
        logger.warning("bronze_read_failed", error=str(exc))
        return pl.DataFrame()


def _get_processed_article_ids(trading_date: date) -> set[str]:
    """Return article_ids already present in silver for the given date."""
    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan = duckdb_scan_for(SILVER_PROCESSED_ARTICLES_DIR)
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL delta; LOAD delta;")
            rows = con.execute(
                f"SELECT DISTINCT article_id FROM {scan} WHERE date = ?",
                [trading_date],
            ).fetchall()
        finally:
            con.close()
        return {r[0] for r in rows}
    except Exception as exc:
        logger.debug("silver_read_skipped", error=str(exc))
        return set()


def process_bronze_to_silver(trading_date: date) -> bool:
    """Process unprocessed bronze articles through LLM and write to silver.

    Skips articles already present in the silver table to avoid redundant
    DeepSeek API calls on reruns.

    Args:
        trading_date: The trading date to process articles for.

    Returns:
        True if silver write succeeded, False otherwise.
    """
    bronze_df = read_bronze(trading_date)
    if bronze_df.is_empty():
        logger.warning("No bronze articles to process", trading_date=str(trading_date))
        return False

    processed_ids = _get_processed_article_ids(trading_date)
    if processed_ids:
        before = bronze_df.height
        bronze_df = bronze_df.filter(~pl.col("article_id").is_in(list(processed_ids)))
        skipped = before - bronze_df.height
        if skipped:
            logger.info("Skipping already-processed articles", skipped=skipped, remaining=bronze_df.height)

    if bronze_df.is_empty():
        logger.info("All bronze articles already processed", trading_date=str(trading_date))
        return True

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
    "write_silver",
]
