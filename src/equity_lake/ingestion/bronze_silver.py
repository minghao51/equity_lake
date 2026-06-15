"""Bronze-to-silver transform for unstructured articles.

Reads raw articles from the bronze Delta table, filters out already-processed
ones, runs LLM batch processing, then explodes to silver article-ticker pairs
and writes to the silver Delta table.

Provides a unified :func:`process_unstructured_to_silver` that both article
and SEC pipelines delegate to, eliminating duplicated orchestration logic.
"""

from __future__ import annotations

from collections.abc import Callable
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


def process_unstructured_to_silver(
    trading_date: date,
    *,
    source_type_filter: str | None,
    process_fn: Callable[[pl.DataFrame], pl.DataFrame],
    silver_path: Path,
    silver_table_name: str,
    silver_key_columns: list[str],
    log_label: str = "article",
) -> bool:
    """Unified bronze→silver pipeline for all unstructured sources.

    Reads bronze articles (optionally filtered by ``source_type``), skips
    already-processed rows, runs the provided ``process_fn``, filters by
    known tickers, and writes to the silver Delta table.

    Args:
        trading_date: The trading date to process.
        source_type_filter: If set, filter bronze to this ``source_type`` (e.g. ``"sec_filing"``).
        process_fn: Function that takes bronze DataFrame → silver DataFrame.
        silver_path: Directory path for the silver Delta table.
        silver_table_name: Delta table name (e.g. ``"silver/processed_articles"``).
        silver_key_columns: Dedup key columns for the silver merge.
        log_label: Label for log messages.

    Returns:
        True if silver write succeeded, False otherwise.
    """
    bronze_df = read_bronze(trading_date)
    if bronze_df.is_empty():
        logger.warning(f"No bronze {log_label}s to process", trading_date=str(trading_date))
        return False

    if source_type_filter and "source_type" in bronze_df.columns:
        bronze_df = bronze_df.filter(pl.col("source_type") == source_type_filter)
        if bronze_df.is_empty():
            logger.info(f"No {log_label} bronze articles for source_type={source_type_filter}")
            return False

    processed_ids = _get_processed_ids(silver_path, trading_date)
    if processed_ids:
        before = bronze_df.height
        bronze_df = bronze_df.filter(~pl.col("article_id").is_in(list(processed_ids)))
        skipped = before - bronze_df.height
        if skipped:
            logger.info(f"Skipping already-processed {log_label}s", skipped=skipped, remaining=bronze_df.height)

    if bronze_df.is_empty():
        logger.info(f"All bronze {log_label}s already processed", trading_date=str(trading_date))
        return True

    logger.info(f"Processing {log_label} bronze to silver", count=bronze_df.height, trading_date=str(trading_date))

    try:
        silver_df = process_fn(bronze_df)
    except Exception as exc:
        logger.error(f"{log_label}_processing_failed", error=str(exc))
        return False

    if silver_df.is_empty():
        logger.warning(f"{log_label} processing produced no silver rows")
        return False

    ticker_filter = _load_known_tickers()
    if ticker_filter and "ticker" in silver_df.columns:
        silver_df = silver_df.filter(pl.col("ticker").is_null() | pl.col("ticker").is_in(ticker_filter))
        logger.info(f"Filtered {log_label} silver by known tickers", remaining=silver_df.height, known_tickers=len(ticker_filter))

    return _write_silver_generic(silver_df, silver_table_name, silver_key_columns)


def _write_silver_generic(df: pl.DataFrame, table_name: str, key_columns: list[str]) -> bool:
    """Write silver DataFrame to Delta table."""
    if df.is_empty():
        logger.warning("Empty DataFrame, skipping silver write")
        return False
    return merge_delta(df, table_name, key_columns=key_columns)


def _get_processed_ids(silver_path: Path, trading_date: date) -> set[str]:
    """Return article_ids already present in the given silver table for the date."""
    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan = duckdb_scan_for(silver_path)
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

    Delegates to :func:`process_unstructured_to_silver` with article-specific
    parameters (all non-SEC source types).

    Args:
        trading_date: The trading date to process articles for.

    Returns:
        True if silver write succeeded, False otherwise.
    """
    from equity_lake.ingestion.llm_processor import run_llm_processing

    return process_unstructured_to_silver(
        trading_date,
        source_type_filter=None,
        process_fn=run_llm_processing,
        silver_path=SILVER_PROCESSED_ARTICLES_DIR,
        silver_table_name="silver/processed_articles",
        silver_key_columns=["article_id", "ticker"],
        log_label="article",
    )


def _load_known_tickers() -> list[str]:
    try:
        from equity_lake.core.config import TickerConfig

        config = TickerConfig()
        return config.get_tickers_for_market("us", active_only=True)
    except Exception:
        return []


__all__ = [
    "process_bronze_to_silver",
    "process_unstructured_to_silver",
    "read_bronze",
    "write_silver",
]
