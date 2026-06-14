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

from equity_lake.core.config import TickerConfig, get_project_config
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
from equity_lake.ingestion.types import MARKET_DIR_MAP
from equity_lake.ingestion.writers import write_to_partitioned_parquet

__all__ = [
    "fetch_market_data",
    "fetch_market_data_with_config",
    "run_daily_ingestion",
]

logger = structlog.get_logger()


def _market_has_date(market_dir: str, trading_date: date) -> bool:
    market_path = LAKE_DIR / market_dir
    if not market_path.exists():
        return False

    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(market_path)):
            con = duckdb.connect(":memory:")
            try:
                con.execute("INSTALL delta; LOAD delta;")
                row = con.execute(f"SELECT COUNT(*) FROM delta_scan('{market_path}') WHERE date = '{trading_date}'").fetchone()
                return row is not None and row[0] > 0
            finally:
                con.close()
    except ImportError:
        pass

    return False


def _filter_markets_with_gaps(markets: list[str], trading_date: date) -> list[str]:
    markets_needing_fetch: list[str] = []
    for market in markets:
        if market in ("macro", "us_news", "us_social_sentiment", "rss_news", "reddit_posts", "stocktwits_messages"):
            markets_needing_fetch.append(market)
            continue
        market_dir = MARKET_DIR_MAP.get(market, market)
        if _market_has_date(market_dir, trading_date):
            logger.debug("market_data_exists", market=market, date=str(trading_date))
        else:
            markets_needing_fetch.append(market)
    return markets_needing_fetch


def _write_market(df: Any, market: str, trading_date: date, dry_run: bool) -> bool:
    market_dir = MARKET_DIR_MAP.get(market, market)
    with timer(f"write_{market}_parquet", market=market):
        return write_to_partitioned_parquet(df, market_dir, trading_date, dry_run=dry_run)


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
) -> dict[str, bool]:
    results: dict[str, bool] = {}

    explicit_ticker_list = explicit_tickers if isinstance(explicit_tickers, list) else None
    if explicit_tickers and isinstance(explicit_tickers, str):
        explicit_ticker_list = [t.strip() for t in explicit_tickers.split(",")]

    if skip_existing:
        markets_to_fetch = _filter_markets_with_gaps(markets, trading_date)
        skipped = set(markets) - set(markets_to_fetch)
        if skipped:
            logger.info("skip_existing_data", trading_date=str(trading_date), skipped=sorted(skipped))
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
                            explicit_tickers=explicit_list if mkt in ("us", "stocktwits_messages") else None,
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
                    results[market] = False
                    continue

                df = fetch_result.data
                if frame_is_empty(df):
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = False
                    continue

                results[market] = _write_market(df, market, trading_date, dry_run)

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
                        config=get_project_config(),
                        ticker_config=ticker_config,
                        filters=filters,
                        explicit_tickers=explicit_ticker_list if market in ("us", "stocktwits_messages") else None,
                    )

                if frame_is_empty(df):
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = False
                    continue

                results[market] = _write_market(df, market, trading_date, dry_run)

            except Exception as e:
                logger.error(f"Error processing {market}: {e}")
                results[market] = False

    return results
