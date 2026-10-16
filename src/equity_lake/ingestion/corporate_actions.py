"""Corporate-actions ingestion (ADR-0011) — event-driven and incremental.

Unlike the daily price markets, corporate actions have no per-trading-date
fetch contract: new rows appear when a company announces an ex-date. This
module owns the end-to-end route (fetch → bronze → silver) and the
per-ticker ``since`` watermarks derived from each ticker's max stored
``ex_date`` (W03 PR4/F14: a ticker's fetch window depends only on its own
history, never on a recent event on a different ticker).

Outside a ticker's watermarked window (``ex_date <= watermark``) vendor
corrections and late older events are not re-detected — the watermark is
exclusive and no bounded overlap is implemented. The window edges are
logged and returned in the ingest result payload instead of being promised
away.

Not part of ``VALID_MARKETS``: the daily orchestrator never fetches this
dataset; it runs through the ``equity ingest corporate-actions`` command or
direct calls to :func:`ingest_corporate_actions`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, cast

import polars as pl
import structlog

from equity_lake.core.paths import canonical_market

logger = structlog.get_logger()


def corporate_actions_table(market: str, layer: str = "bronze") -> str:
    """Lake-relative table path for a market's corporate actions (ADR-0011).

    Both segments derive from the canonical ``*_CORPORATE_ACTIONS_DIR``
    constants (catalog/paths stay the source of truth) and the lookup goes
    through ``getattr``, so tmp-dir test patches remain effective.
    """
    from equity_lake.core import paths

    root = cast(Path, getattr(paths, f"{layer.upper()}_CORPORATE_ACTIONS_DIR"))
    return f"{root.parent.name}/{root.name}/{canonical_market(market)}"


def max_stored_ex_dates(market: str) -> dict[str, date]:
    """Per-ticker watermarks: max stored ``ex_date`` for each ticker (silver).

    A ticker's incremental fetch window is ``(watermark, ∞)`` — events at or
    below it are not re-fetched (exclusive bound). Tickers absent from the
    mapping have no stored history and fetch in full. ``{}`` when the table
    does not exist yet (first run fetches full history). Bronze is
    intentionally not consulted — silver is the read surface for adjustment.
    """
    from equity_lake.storage.delta import DeltaReadError, read_delta

    try:
        df = read_delta(corporate_actions_table(market, "silver"))
    except DeltaReadError:
        return {}
    if df.is_empty():
        return {}
    grouped = df.group_by("ticker").agg(pl.col("ex_date").max())
    return dict(zip(grouped["ticker"].to_list(), grouped["ex_date"].to_list(), strict=True))


def max_stored_ex_date(market: str) -> date | None:
    """Market-wide max stored ``ex_date`` (silver) — summary/compat value.

    The fetch path no longer bounds fetches by this market-global value
    (W03 PR4/F14); per-ticker windows come from :func:`max_stored_ex_dates`.
    """
    watermarks = max_stored_ex_dates(market)
    return max(watermarks.values()) if watermarks else None


def ingest_corporate_actions(
    market: str = "us_equity",
    *,
    tickers: list[str] | None = None,
    dry_run: bool = False,
    fetcher: Any = None,
) -> dict[str, Any]:
    """Fetch corporate actions for *market* and write bronze + silver.

    Single write pass to both layers: the same quality-gated frame lands in
    ``01_bronze/corporate_actions/<market>`` and
    ``02_silver/corporate_actions/<market>``, partitioned by ``ex_date``,
    upserted on ``(ticker, ex_date, action)`` — idempotent by construction.

    Args:
        market: Price-market identifier (ADR-0010 long key). Only
            yfinance-backed markets are supported in v1.
        tickers: Explicit ticker list; defaults to the fetcher's configured
            tickers for the market.
        dry_run: Fetch (unless ``fetcher`` is injected) but write nothing.
        fetcher: Injectable fetcher exposing
            ``fetch_corporate_actions(since, since_by_ticker)``; overrides
            registry construction (tests, backfills). Per-ticker watermarks
            are scoped to ``fetcher.tickers`` when that attribute exists.

    Returns:
        Outcome dict: ``ok``, ``fetched``, ``since`` (market-wide max stored
        ``ex_date``, display summary), ``since_by_ticker`` (the per-ticker
        exclusive watermarks that bounded the fetch; ``None`` per ticker =
        full history), ``market``.
    """
    from equity_lake.core.config import get_settings
    from equity_lake.ingestion.router import MARKET_REGISTRY
    from equity_lake.ingestion.writers import upsert_dataset

    if market != "us_equity":
        logger.error("corporate_actions_unsupported_market", market=market)
        return {"ok": False, "fetched": 0, "since": None, "market": market}

    if fetcher is None:
        factory = MARKET_REGISTRY.get(market)
        if factory is None:
            logger.error("corporate_actions_unknown_market", market=market)
            return {"ok": False, "fetched": 0, "since": None, "market": market}
        ingestion = get_settings().ingestion
        fetcher = factory(
            retry_attempts=ingestion.retry_attempts,
            retry_delay=ingestion.retry_delay,
            ticker_config=None,
            filters=None,
            explicit_tickers=tickers,
        )

    # W03 PR4/F14: per-ticker scoped watermarks. The fetch bound for each
    # ticker is its own max stored ex_date — a recent event on a different
    # ticker cannot suppress a newly scoped ticker's history, and a late
    # event above a ticker's own watermark is picked up. Events at or below
    # a ticker's watermark are not re-detected (exclusive bound); the edges
    # are logged and returned so ops can see the window.
    watermarks = max_stored_ex_dates(market)
    effective_tickers = list(getattr(fetcher, "tickers", None) or [])
    since_by_ticker: dict[str, date | None] = {t: watermarks.get(t) for t in effective_tickers}
    since = max(watermarks.values()) if watermarks else None
    logger.info(
        "corporate_actions_fetch_start",
        market=market,
        since=since,
        since_by_ticker=since_by_ticker,
        tickers=len(effective_tickers),
    )

    df = fetcher.fetch_corporate_actions(since_by_ticker=since_by_ticker)

    if df.is_empty():
        logger.info("corporate_actions_no_new_events", market=market, since=since)
        return {"ok": True, "fetched": 0, "since": since, "market": market, "since_by_ticker": since_by_ticker}

    trading_date = date.today()
    bronze_ok = upsert_dataset(
        df,
        corporate_actions_table(market, "bronze"),
        trading_date,
        dry_run=dry_run,
        partition_by=["ex_date"],
    )
    silver_ok = upsert_dataset(
        df,
        corporate_actions_table(market, "silver"),
        trading_date,
        dry_run=dry_run,
        partition_by=["ex_date"],
    )

    if dry_run:
        logger.info("corporate_actions_dry_run", market=market, rows=df.height)
        return {"ok": True, "fetched": df.height, "since": since, "market": market, "since_by_ticker": since_by_ticker}

    ok = bool(bronze_ok and silver_ok)
    if not ok:
        logger.error("corporate_actions_write_failed", market=market, bronze=bronze_ok, silver=silver_ok)
    else:
        logger.info("corporate_actions_ingested", market=market, rows=df.height, since=since)
    return {"ok": ok, "fetched": df.height, "since": since, "market": market, "since_by_ticker": since_by_ticker}


__all__ = ["corporate_actions_table", "ingest_corporate_actions", "max_stored_ex_date", "max_stored_ex_dates"]
