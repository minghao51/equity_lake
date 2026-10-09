#!/usr/bin/env python3
"""
Backfill Historical Data

Delegates to ``run_daily_ingestion`` per trading day in the requested range,
avoiding duplicated fetcher logic. Driven via the ``equity backfill`` Typer
command (see ``cli/commands/data.py``).

Usage:
    uv run equity backfill --start 2023-04-06 --end 2026-04-05
    uv run equity backfill --days-back 1095
    uv run equity backfill --days-back 365 --markets us_equity
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import structlog

from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.core.paths import PRICE_MARKETS

logger = structlog.get_logger()
SETTINGS = get_settings()

# Registry-derived (ADR-0010): all five price markets, canonical long keys.
DEFAULT_MARKETS = sorted(PRICE_MARKETS)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """Frozen scope plus outcome counts for one backfill invocation (W03/F25).

    The scope fields are captured before the date loop so logs and callers report
    what was actually authorized/attempted rather than re-deriving it afterwards.
    ``total_dates`` preserves the historical return contract: the number of
    successful (market, date) work items. ``trading_dates`` counts the calendar
    days iterated in the range (pre-existing loop semantics, deliberately
    unchanged) — it is NOT an exchange-session count; session iteration stays
    future work (wave-page limitation note).
    """

    start_date: date
    end_date: date
    markets: tuple[str, ...]
    instruments: tuple[str, ...] | None
    authorized: bool
    trading_dates: int
    market_date_attempts: int
    succeeded: int
    failed: int
    dates_recovered: int
    per_market: dict[str, int]

    @property
    def total_dates(self) -> int:
        """Successful market-date count (the historical ``backfill_date_range`` return)."""
        return self.succeeded

    def to_payload(self) -> dict[str, Any]:
        """JSON-friendly summary for pipeline stage results and logs."""
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "markets": list(self.markets),
            "instruments": list(self.instruments) if self.instruments is not None else None,
            "authorized": self.authorized,
            "trading_dates": self.trading_dates,
            "market_date_attempts": self.market_date_attempts,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dates_recovered": self.dates_recovered,
            "per_market": dict(self.per_market),
        }


def backfill_date_range(
    start_date: date,
    end_date: date,
    markets: list[str] | None = None,
    ticker_config: TickerConfig | None = None,
    dry_run: bool = False,
    explicit_tickers: list[str] | None = None,
    authorized: bool = False,
) -> BackfillResult:
    from equity_lake.ingestion.orchestrator import run_daily_ingestion
    from equity_lake.ingestion.types import SourceOutcome, SourceStatus

    if markets is None:
        markets = DEFAULT_MARKETS
    market_list = list(markets)
    instruments = tuple(explicit_tickers) if explicit_tickers is not None else None

    per_market: dict[str, int] = {market: 0 for market in market_list}
    trading_dates = 0
    market_date_attempts = 0
    succeeded = 0
    failed = 0
    dates_recovered = 0

    current = start_date
    while current <= end_date:
        trading_dates += 1
        day_success = 0
        for market in market_list:
            market_date_attempts += 1
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
                outcome = result.get(market, SourceOutcome(SourceStatus.FAILED))
                if outcome.status is SourceStatus.NOT_SCHEDULED:
                    # D04/F11: a closed exchange is a planned skip, not a
                    # recovered date and not a failure. It stays counted in
                    # market_date_attempts but is excluded from succeeded/failed
                    # so recovery counts are not inflated by weekends/holidays.
                    logger.info("backfill_not_scheduled", date=str(current), market=market)
                elif outcome.succeeded:
                    logger.info("backfill_ok", date=str(current), market=market)
                    succeeded += 1
                    day_success += 1
                    per_market[market] += 1
                else:
                    failed += 1
                    logger.warning("backfill_skip", date=str(current), market=market)
            except Exception as exc:
                failed += 1
                logger.error("backfill_error", date=str(current), market=market, error=str(exc))
        if market_list and day_success == len(market_list):
            dates_recovered += 1
        current += timedelta(days=1)

    return BackfillResult(
        start_date=start_date,
        end_date=end_date,
        markets=tuple(market_list),
        instruments=instruments,
        authorized=authorized,
        trading_dates=trading_dates,
        market_date_attempts=market_date_attempts,
        succeeded=succeeded,
        failed=failed,
        dates_recovered=dates_recovered,
        per_market=per_market,
    )
