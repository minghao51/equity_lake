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
from equity_lake.ingestion.types import (
    EXPLICIT_TICKER_MARKETS,  # noqa: F401 -- re-exported for existing importers (single definition lives in types.py)
    MARKET_DIR_MAP,
    OPTIONAL_ENRICHMENT_MARKETS,
    PRICE_MARKET_CONFIG_KEYS,
    REQUIRED_PRICE_MARKETS,
    SourceOutcome,
    SourceStatus,
    normalize_markets,
    resolve_explicit_scope,
)
from equity_lake.ingestion.writers import upsert_dataset
from equity_lake.storage.lake_reader import duckdb_scan_for, ensure_delta_extension

__all__ = [
    "fetch_market_data",
    "fetch_market_data_with_config",
    "run_daily_ingestion",
]

logger = structlog.get_logger()


def _finalize_market(market: str, trading_date: date, dry_run: bool, df: Any) -> SourceOutcome:
    """Map one fetched frame to its truthful terminal outcome.

    Shared by the serial and parallel branches so both get identical isolation
    (F09) and identical status semantics (F08/F25): a genuine empty fetch is
    ``NO_DATA``, a simulated write is ``DRY_RUN`` (never ``written``), and any
    conversion/validation/write exception is isolated to this market as a
    ``FAILED`` outcome that retains the root cause.
    """
    if df is None:
        # Defensive: never read a missing frame as no-data. The router raises for
        # unknown markets; this guards direct/legacy callers.
        logger.error("market_no_frame", market=market)
        return SourceOutcome(SourceStatus.FAILED, error="no_frame_returned")
    try:
        if frame_is_empty(df):
            logger.warning("no_data_fetched", market=market)
            return SourceOutcome(SourceStatus.NO_DATA)
        if _write_market(df, market, trading_date, dry_run):
            return SourceOutcome(SourceStatus.DRY_RUN) if dry_run else SourceOutcome(SourceStatus.WRITTEN)
        return SourceOutcome(SourceStatus.FAILED, error="write_returned_false")
    except Exception as exc:
        logger.error("market_write_failed", market=market, error=str(exc))
        return SourceOutcome(SourceStatus.FAILED, error=str(exc))


def _configured_tickers_for_market(market: str, ticker_config: TickerConfig | None) -> list[str]:
    """All configured active tickers for a price market (no implicit cap).

    ``hk_sg_equity`` spans the ``hk`` and ``sg`` config sections; single-market
    price markets use their short alias. Used as the *requested* scope when the
    caller did not pass an explicit list, so presence checks cover the whole
    configured scope rather than a single sampled row.
    """
    if ticker_config is None:
        return []
    tickers: list[str] = []
    for key in PRICE_MARKET_CONFIG_KEYS.get(market, ()):
        for ticker in ticker_config.get_tickers_for_market(key, active_only=True):
            if ticker not in tickers:
                tickers.append(ticker)
    return tickers


def _read_partition_coverage(
    market: str,
    market_dir: str,
    trading_date: date,
    con: duckdb.DuckDBPyConnection,
) -> tuple[bool, set[str]]:
    """One bounded scoped read of the date partition: ``(schema_valid, present_tickers)``.

    Replaces the separate row-count and sampled-schema checks (W03/F05): a single
    ``QUALIFY ROW_NUMBER()`` read returns one row per distinct ticker, so ticker
    presence and schema validity come from the same bounded scan. Schema validity
    reuses the write-boundary validator, so "valid" means the same thing that
    gated the write. A missing/empty/corrupt partition yields ``(False, set())``
    so the caller re-fetches rather than trusting it.
    """
    from equity_lake.ingestion.writers import validate_schema

    market_path = LAKE_DIR / market_dir
    if not market_path.exists():
        return False, set()
    scan_expr = duckdb_scan_for(market_path)
    try:
        df = con.execute(
            f"SELECT * FROM {scan_expr} WHERE date = ? QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY ticker) = 1",
            [trading_date],
        ).pl()
    except Exception as exc:  # duckdb/delta scan failures mean we cannot trust the partition
        logger.warning("partition_read_failed", market=market, date=str(trading_date), error=str(exc))
        return False, set()
    if df.is_empty():
        return False, set()
    valid = validate_schema(df, market)
    present = set(df["ticker"].drop_nulls().to_list()) if "ticker" in df.columns else set()
    return valid, present


def _filter_markets_with_gaps(
    markets: list[str],
    trading_date: date,
    scope: dict[str, list[str] | None],
    ticker_config: TickerConfig | None,
) -> tuple[dict[str, list[str] | None], set[str]]:
    """Split *markets* into fetch work and already-complete markets.

    Returns ``(to_fetch, already_present)`` where ``to_fetch`` maps a market to
    the explicit ticker list it should fetch (``None`` = the provider's own
    configured default, ``[]`` = an explicit empty scope that must not broaden).

    Enrichment markets are never checked for existing partitions and keep their
    per-market scope. A price market is ``already_present`` only when its date
    partition is schema-valid *and* every requested ticker is present. An
    *explicit* scope refetches only the missing tickers (full scope when the
    partition is corrupt); an unspecified (``None``) request refetches with
    ``None`` so the fetcher applies its own filtered/fallback default — the
    coverage list above is used to decide *whether* to fetch, never to widen
    what the fetcher would have pulled on its own (W03 review: filters/fallback
    interplay must not over-broaden). An explicit ``[]`` scope stays ``[]``:
    presence cannot be proven for it, and it must never broaden.
    """
    to_fetch: dict[str, list[str] | None] = {}
    already_present: set[str] = set()
    shared_con: duckdb.DuckDBPyConnection | None = None
    try:
        for market in markets:
            if market in OPTIONAL_ENRICHMENT_MARKETS:
                to_fetch[market] = scope.get(market)
                continue
            market_dir = MARKET_DIR_MAP.get(market, market)
            if shared_con is None:
                shared_con = duckdb.connect(":memory:")
                ensure_delta_extension(shared_con)
            explicit = scope.get(market)
            requested = explicit
            if requested is None:
                requested = _configured_tickers_for_market(market, ticker_config)
            if not requested:
                # Presence cannot be verified without a scope. An explicit []
                # stays [] (never broadens); an unspecified request with an empty
                # configured universe defers to the fetcher's own default so its
                # filters/fallback behavior is unchanged from before W03.
                to_fetch[market] = list(requested) if explicit is not None else None
                continue
            valid, present = _read_partition_coverage(market, market_dir, trading_date, shared_con)
            missing = [ticker for ticker in requested if ticker not in present]
            if valid and not missing:
                logger.debug("market_data_exists", market=market, date=str(trading_date))
                already_present.add(market)
            elif valid:
                to_fetch[market] = missing if explicit is not None else None
            else:
                to_fetch[market] = list(requested) if explicit is not None else None
    finally:
        if shared_con is not None:
            shared_con.close()
    return to_fetch, already_present


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

    # ADR-0010: canonicalize short price-market aliases to long keys once, at
    # the orchestrator boundary. Unknown keys raise here (loud failure).
    markets = normalize_markets(markets)

    # W03/F04: resolve the explicit request to a per-market scope exactly once,
    # before any fetch. Every fetch in both branches consumes ``fetch_scope``,
    # so a ticker can never fan out to every provider or silently broaden.
    scope = resolve_explicit_scope(markets, explicit_ticker_list, ticker_config)
    logger.info("resolved_explicit_scope", trading_date=str(trading_date), scope={market: scope.get(market) for market in markets})

    fetch_scope: dict[str, list[str] | None] = {market: scope.get(market) for market in markets}
    if skip_existing:
        # A coverage check needs the configured active scope when the caller did
        # not pass an explicit list; load the shared config lazily so the direct
        # CLI default (no ticker_config, no explicit tickers) keeps its full
        # configured scope instead of degrading to an empty fetch. The fetchers
        # still receive the original ``ticker_config`` unchanged.
        coverage_config = ticker_config
        if coverage_config is None and any(market in REQUIRED_PRICE_MARKETS and scope.get(market) is None for market in markets):
            coverage_config = TickerConfig()
        fetch_scope, already_present = _filter_markets_with_gaps(markets, trading_date, scope, coverage_config)
        if already_present:
            # Idempotent reruns must surface already-written partitions as success
            # so downstream feature/ML stages are not blocked by a missing result key.
            # SKIPPED_EXISTING (vs WRITTEN) keeps the distinction observable for ops.
            logger.info("skip_existing_data", trading_date=str(trading_date), skipped=sorted(already_present))
            for market in already_present:
                results[market] = SourceOutcome(SourceStatus.SKIPPED_EXISTING)
        markets = list(fetch_scope.keys())

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
                            explicit_tickers=explicit_list,
                        )

                    return fetch_func

                fetch_func_map[market] = (
                    make_fetch_func(market, fetch_scope.get(market), ticker_config, filters),
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
                logger.info("market_processing_start", market=market.upper())

                if not fetch_result.success:
                    # F08: an optional market's fetch failure is a FAILED outcome
                    # with its cause — optionality is applied by the pipeline when
                    # deciding blocking/exit status, never here.
                    logger.error("market_fetch_failed", market=market, error=str(fetch_result.error), duration_seconds=fetch_result.duration_seconds)
                    results[market] = SourceOutcome(SourceStatus.FAILED, error=str(fetch_result.error))
                    continue

                # F09: conversion/validation/write exceptions are isolated per
                # market inside _finalize_market — the same contract as the
                # serial branch; other markets' outcomes always survive.
                results[market] = _finalize_market(market, trading_date, dry_run, fetch_result.data)

            summary = summarize_results(fetch_results)
            logger.info("parallel_ingestion_summary", **summary)

    else:
        for market in markets:
            logger.info("market_processing_start", market=market.upper())

            try:
                with timer(f"fetch_{market}_data", market=market):
                    df = fetch_market_data(
                        market,
                        trading_date,
                        ticker_config=ticker_config,
                        filters=filters,
                        explicit_tickers=fetch_scope.get(market),
                    )
            except Exception as e:
                # F10: the router no longer swallows fetch/schema errors into
                # None; they arrive here with their root cause intact.
                logger.error("market_processing_error", market=market, error=str(e))
                results[market] = SourceOutcome(SourceStatus.FAILED, error=str(e))
                continue

            results[market] = _finalize_market(market, trading_date, dry_run, df)

    return results
