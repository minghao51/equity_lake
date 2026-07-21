#!/usr/bin/env python3
"""
Backfill Historical Data

Delegates to ``run_daily_ingestion`` per trading day in the requested range,
avoiding duplicated fetcher logic. Driven via the ``equity backfill`` Typer
command (see ``cli/commands/data.py``).

Usage:
    uv run equity backfill --start 2023-04-06 --end 2026-04-05
    uv run equity backfill --days-back 1095
    uv run equity backfill --days-back 365 --markets us
"""

from datetime import date, timedelta

import structlog

from equity_lake.core.config import TickerConfig, get_settings

logger = structlog.get_logger()
SETTINGS = get_settings()

DEFAULT_MARKETS = ["us", "cn", "hk_sg", "jpx", "krx"]


def backfill_date_range(
    start_date: date,
    end_date: date,
    markets: list[str] | None = None,
    ticker_config: TickerConfig | None = None,
    dry_run: bool = False,
    explicit_tickers: list[str] | None = None,
) -> int:
    from equity_lake.ingestion.orchestrator import run_daily_ingestion
    from equity_lake.ingestion.types import SourceOutcome, SourceStatus

    if markets is None:
        markets = DEFAULT_MARKETS

    total_dates = 0
    current = start_date
    while current <= end_date:
        for market in markets:
            try:
                result = run_daily_ingestion(
                    trading_date=current,
                    markets=[market],
                    dry_run=dry_run,
                    ticker_config=ticker_config,
                    explicit_tickers=explicit_tickers,
                    skip_existing=True,
                    parallel=False,
                )
                if result.get(market, SourceOutcome(SourceStatus.FAILED)).succeeded:
                    logger.info("backfill_ok", date=str(current), market=market)
                    total_dates += 1
                else:
                    logger.warning("backfill_skip", date=str(current), market=market)
            except Exception as exc:
                logger.error("backfill_error", date=str(current), market=market, error=str(exc))
        current += timedelta(days=1)

    return total_dates
