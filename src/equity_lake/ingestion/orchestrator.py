"""Daily EOD data ingestion orchestrator.

Provides :func:`run_daily_ingestion` which fetches end-of-day equity data
from multiple markets and writes to Delta Lake storage.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

import duckdb
import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.core.logging import correlation_context, timer
from equity_lake.core.paths import LAKE_DIR
from equity_lake.core.polars_utils import frame_is_empty
from equity_lake.ingestion.parallel import (
    fetch_markets_parallel,
    summarize_results,
)
from equity_lake.ingestion.router import (
    fetch_market_data,
    fetch_market_data_with_config,
)
from equity_lake.ingestion.types import MARKET_DIR_MAP, OPTIONAL_ENRICHMENT_MARKETS, SourceOutcome, SourceStatus
from equity_lake.ingestion.writers import upsert_dataset

__all__ = [
    "fetch_market_data",
    "fetch_market_data_with_config",
    "run_daily_ingestion",
]

logger = structlog.get_logger()


def _market_has_date(market_dir: str, trading_date: date, con: duckdb.DuckDBPyConnection | None = None) -> bool:
    """Cheap row-count existence check (does NOT validate schema).

    Use :func:`_partition_is_valid` when you need to confirm a partition is
    usable, not merely present.
    """
    market_path = LAKE_DIR / market_dir
    if not market_path.exists():
        return False

    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(market_path)):
            own_connection = con is None
            active_con = con if con is not None else duckdb.connect(":memory:")
            try:
                if own_connection:
                    active_con.execute("INSTALL delta; LOAD delta;")
                row = active_con.execute(
                    f"SELECT COUNT(*) FROM delta_scan('{market_path}') WHERE date = ?",
                    [trading_date],
                ).fetchone()
                return row is not None and row[0] > 0
            finally:
                if own_connection:
                    active_con.close()
    except ImportError:
        pass

    return False


def _partition_is_valid(market: str, market_dir: str, trading_date: date, con: duckdb.DuckDBPyConnection) -> bool:
    """Confirm a partition is usable: non-empty AND schema columns present and not all-null.

    Reuses the write-boundary validator so the definition of "valid" is the same
    one that gated the write in the first place. ``LIMIT 100`` keeps the read-back
    cheap — column presence and all-null detection don't need a full scan.
    """
    from equity_lake.ingestion.writers import validate_schema
    from equity_lake.storage.lake_reader import duckdb_scan_for

    market_path = LAKE_DIR / market_dir
    if not market_path.exists():
        return False
    scan_expr = duckdb_scan_for(market_path)
    try:
        df = con.execute(f"SELECT * FROM {scan_expr} WHERE date = ? LIMIT 100", [trading_date]).pl()
    except Exception as exc:  # duckdb/delta scan failures mean we cannot trust the partition
        logger.warning("partition_read_failed", market=market, date=str(trading_date), error=str(exc))
        return False
    if df.is_empty():
        return False
    return validate_schema(df, market)


def _filter_markets_with_gaps(markets: list[str], trading_date: date) -> tuple[list[str], set[str]]:
    """Split *markets* into those needing a fetch and those already present.

    Returns ``(markets_needing_fetch, already_present)``. Enrichment markets that
    are not checked for existing partitions are always placed in
    ``markets_needing_fetch``. Price markets are only treated as present when
    their partition passes :func:`_partition_is_valid` — a corrupt or
    all-null partition is re-fetched rather than trusted.
    """
    markets_needing_fetch: list[str] = []
    already_present: set[str] = set()
    shared_con: duckdb.DuckDBPyConnection | None = None
    try:
        for market in markets:
            if market in OPTIONAL_ENRICHMENT_MARKETS:
                markets_needing_fetch.append(market)
                continue
            market_dir = MARKET_DIR_MAP.get(market, market)
            if shared_con is None:
                shared_con = duckdb.connect(":memory:")
                shared_con.execute("INSTALL delta; LOAD delta;")
            if _market_has_date(market_dir, trading_date, con=shared_con) and _partition_is_valid(market, market_dir, trading_date, con=shared_con):
                logger.debug("market_data_exists", market=market, date=str(trading_date))
                already_present.add(market)
            else:
                markets_needing_fetch.append(market)
    finally:
        if shared_con is not None:
            shared_con.close()
    return markets_needing_fetch, already_present


def _write_market(df: Any, market: str, trading_date: date, dry_run: bool) -> bool:
    market_dir = MARKET_DIR_MAP.get(market, market)
    with timer(f"write_{market}_parquet", market=market):
        return upsert_dataset(df, market_dir, trading_date, dry_run=dry_run)


def run_daily_ingestion(
    trading_date: date,
    markets: list[str],
    dry_run: bool = False,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: str | list[str] | None = None,
    parallel: bool = False,
    max_workers: int | None = None,
    skip_existing: bool = True,
) -> dict[str, SourceOutcome]:
    results: dict[str, SourceOutcome] = {}

    explicit_ticker_list = explicit_tickers if isinstance(explicit_tickers, list) else None
    if explicit_tickers and isinstance(explicit_tickers, str):
        explicit_ticker_list = [t.strip() for t in explicit_tickers.split(",")]

    if skip_existing:
        markets_to_fetch, already_present = _filter_markets_with_gaps(markets, trading_date)
        if already_present:
            # Idempotent reruns must surface already-written partitions as success
            # so downstream feature/ML stages are not blocked by a missing result key.
            # SKIPPED_EXISTING (vs WRITTEN) keeps the distinction observable for ops.
            logger.info("skip_existing_data", trading_date=str(trading_date), skipped=sorted(already_present))
            for market in already_present:
                results[market] = SourceOutcome(SourceStatus.SKIPPED_EXISTING)
        markets = markets_to_fetch

    if not markets:
        logger.info("all_markets_up_to_date", trading_date=str(trading_date))
        return results

    if parallel:
        with correlation_context():
            logger.info("parallel_ingestion_mode", markets=markets, max_workers=max_workers or len(markets))

            fetch_func_map: dict[str, tuple[Callable[[Any], Any], dict[Any, Any]]] = {}
            for market in markets:

                def make_fetch_func(mkt: str, explicit_list: Any, config: Any, fltrs: Any) -> Callable[[Any], Any]:
                    def fetch_func(date: Any) -> Any:
                        return fetch_market_data_with_config(
                            mkt,
                            date,
                            ticker_config=config,
                            filters=fltrs,
                            explicit_tickers=(
                                explicit_list
                                if mkt
                                in (
                                    "us",
                                    "stocktwits_messages",
                                    "us_earnings_transcripts",
                                    "us_analyst_ratings",
                                    "sec_filings_fulltext",
                                    "us_sec_financials",
                                )
                                else None
                            ),
                        )

                    return fetch_func

                fetch_func_map[market] = (
                    make_fetch_func(market, explicit_ticker_list, ticker_config, filters),
                    {},
                )

            with timer("fetch_all_markets_parallel", mode="parallel"):
                fetch_results = fetch_markets_parallel(
                    markets=markets,
                    trading_date=trading_date,
                    fetch_func_map=fetch_func_map,
                    max_workers=max_workers,
                )

            for market, fetch_result in fetch_results.items():
                logger.info(f"\n{'=' * 60}")
                logger.info(f"Processing market: {market.upper()}")
                logger.info(f"{'=' * 60}")

                if not fetch_result.success:
                    logger.error(f"{market} fetch failed: {fetch_result.error}", duration_seconds=fetch_result.duration_seconds)
                    results[market] = SourceOutcome(SourceStatus.FAILED, error=str(fetch_result.error))
                    continue

                df = fetch_result.data
                if frame_is_empty(df):
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = SourceOutcome(SourceStatus.FAILED, error="empty_frame")
                    continue

                results[market] = (
                    SourceOutcome(SourceStatus.WRITTEN)
                    if _write_market(df, market, trading_date, dry_run)
                    else SourceOutcome(SourceStatus.FAILED, error="write_returned_false")
                )

            summary = summarize_results(fetch_results)
            logger.info("parallel_ingestion_summary", **summary)

    else:
        for market in markets:
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Processing market: {market.upper()}")
            logger.info(f"{'=' * 60}")

            try:
                with timer(f"fetch_{market}_data", market=market):
                    df = fetch_market_data(
                        market,
                        trading_date,
                        ticker_config=ticker_config,
                        filters=filters,
                        explicit_tickers=(
                            explicit_ticker_list
                            if market
                            in (
                                "us",
                                "stocktwits_messages",
                                "us_earnings_transcripts",
                                "us_analyst_ratings",
                                "sec_filings_fulltext",
                                "us_sec_financials",
                            )
                            else None
                        ),
                    )

                if frame_is_empty(df):
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = SourceOutcome(SourceStatus.FAILED, error="empty_frame")
                    continue

                results[market] = (
                    SourceOutcome(SourceStatus.WRITTEN)
                    if _write_market(df, market, trading_date, dry_run)
                    else SourceOutcome(SourceStatus.FAILED, error="write_returned_false")
                )

            except Exception as e:
                logger.error(f"Error processing {market}: {e}")
                results[market] = SourceOutcome(SourceStatus.FAILED, error=str(e))

    return results
