#!/usr/bin/env python3
"""
Backfill Historical Data

Delegates to ``run_daily_ingestion`` per trading day in the requested range,
avoiding duplicated fetcher logic.

Usage:
    uv run equity backfill --start 2023-04-06 --end 2026-04-05
    uv run equity backfill --days-back 1095
    uv run equity backfill --days-back 365 --markets us
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import structlog

from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.core.logging import setup_logging

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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill historical equity data")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD, default: yesterday)")
    parser.add_argument("--days-back", type=int, help="Calendar days back from today")
    parser.add_argument("--markets", type=str, default="us,cn,hk_sg", help="Comma-separated markets")
    parser.add_argument("--dry-run", action="store_true", help="No writes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug logging")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging(level=log_level, log_file=Path("backfill.log"))

    yesterday = date.today() - timedelta(days=1)
    end_date = date.fromisoformat(args.end) if args.end else yesterday
    if args.days_back:
        start_date = end_date - timedelta(days=args.days_back)
    elif args.start:
        start_date = date.fromisoformat(args.start)
    else:
        logger.error("Must specify --days-back or --start")
        sys.exit(1)

    logger.info("backfill_range", start=str(start_date), end=str(end_date))

    config_path = Path(SETTINGS.ingestion.ticker_config_path)
    ticker_config = TickerConfig(config_path=config_path)
    market_list = [m.strip() for m in args.markets.split(",")]

    total = backfill_date_range(
        start_date=start_date,
        end_date=end_date,
        markets=market_list,
        ticker_config=ticker_config,
        dry_run=args.dry_run,
    )

    logger.info("backfill_complete", total_dates=total, markets=market_list)


if __name__ == "__main__":
    main()
