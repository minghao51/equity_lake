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
    if not _validate_silver_articles(df, "02_silver/processed_articles"):
        return False
    SILVER_PROCESSED_ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
    return merge_delta(df, "02_silver/processed_articles", key_columns=["article_id", "ticker"])


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
    exclude_source_types: list[str] | None = None,
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
        silver_table_name: Delta table name (e.g. ``"02_silver/processed_articles"``).
        silver_key_columns: Dedup key columns for the silver merge.
        log_label: Label for log messages.

    Returns:
        True if silver write succeeded, False otherwise.
    """
    bronze_df = read_bronze()
    if bronze_df.is_empty():
        logger.warning("no_bronze_to_process", log_label=log_label, trading_date=str(trading_date))
        return False

    # SEC filings have a dedicated processor + silver table (sec_extractions);
    # never route them through the generic article processor. The single-day
    # filter used to hide them, but backfilled historical corpus coexists in bronze.
    if exclude_source_types and "source_type" in bronze_df.columns:
        bronze_df = bronze_df.filter(~pl.col("source_type").is_in(exclude_source_types))

    if source_type_filter and "source_type" in bronze_df.columns:
        bronze_df = bronze_df.filter(pl.col("source_type") == source_type_filter)
        if bronze_df.is_empty():
            logger.info("no_bronze_for_source_type", log_label=log_label, source_type=source_type_filter)
            return False

    processed_ids = _get_processed_ids(silver_path, trading_date)
    if processed_ids:
        before = bronze_df.height
        bronze_df = bronze_df.filter(~pl.col("article_id").is_in(list(processed_ids)))
        skipped = before - bronze_df.height
        if skipped:
            logger.info("skipping_already_processed", log_label=log_label, skipped=skipped, remaining=bronze_df.height)

    if bronze_df.is_empty():
        logger.info("all_bronze_already_processed", log_label=log_label, trading_date=str(trading_date))
        return True

    logger.info("processing_bronze_to_silver", log_label=log_label, count=bronze_df.height, trading_date=str(trading_date))

    try:
        silver_df = process_fn(bronze_df)
    except Exception as exc:
        logger.error("processing_failed", log_label=log_label, error=str(exc))
        return False

    if silver_df.is_empty():
        logger.warning("processing_produced_no_silver", log_label=log_label)
        return False

    ticker_filter = _load_known_tickers()
    if ticker_filter and "ticker" in silver_df.columns:
        silver_df = silver_df.filter(pl.col("ticker").is_null() | pl.col("ticker").is_in(ticker_filter))
        logger.info("filtered_silver_by_known_tickers", log_label=log_label, remaining=silver_df.height, known_tickers=len(ticker_filter))

    return _write_silver_generic(silver_df, silver_table_name, silver_key_columns)


def _validate_silver_articles(df: pl.DataFrame, table_name: str) -> bool:
    """Enforce the pointblank article contract before a silver merge (ADR-0007).

    Silver merges bypass ``upsert_dataset`` entirely, so this is the write
    boundary for ``02_silver/processed_articles`` and
    ``02_silver/sec_extractions``. Profiling stays in memory — validation
    never persists artifacts (ADR-0006 auxiliary paths).
    """
    from equity_lake.validation.pipeline import ValidationPipeline

    vp = ValidationPipeline()
    result = vp.validate(df, data_type="article")
    if not result.success:
        logger.error("Silver quality validation failed", table=table_name, errors=result.errors)
        return False
    if result.warnings:
        for w in result.warnings:
            logger.warning("Silver quality warning", table=table_name, warning=w)
    return True


def _write_silver_generic(df: pl.DataFrame, table_name: str, key_columns: list[str]) -> bool:
    """Write silver DataFrame to Delta table."""
    if df.is_empty():
        logger.warning("Empty DataFrame, skipping silver write")
        return False
    if not _validate_silver_articles(df, table_name):
        return False
    return merge_delta(df, table_name, key_columns=key_columns)


def _get_processed_ids(silver_path: Path, trading_date: date) -> set[str]:
    """Return article_ids already present in the given silver table.

    ``trading_date`` is retained for caller compatibility but no longer filters
    the scan: dedup is global so backfilled historical rows are never reprocessed.
    """
    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan = duckdb_scan_for(silver_path)
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL delta; LOAD delta;")
            rows = con.execute(
                f"SELECT DISTINCT article_id FROM {scan}",
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
        exclude_source_types=["sec_filing"],
        process_fn=run_llm_processing,
        silver_path=SILVER_PROCESSED_ARTICLES_DIR,
        silver_table_name="02_silver/processed_articles",
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
