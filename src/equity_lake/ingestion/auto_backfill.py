"""Idempotent gap-detection backfill coordinator.

Wires ``GapDetector`` output into the existing ingestion pipeline so that
``equity ingest`` (or the EOD pipeline) automatically fills missing dates
without requiring a separate ``equity backfill`` invocation.
"""

from __future__ import annotations

from datetime import date, timedelta

import structlog

from equity_lake.core.config import TickerConfig
from equity_lake.ingestion.gap_detection import GapDetector
from equity_lake.ingestion.orchestrator import run_daily_ingestion
from equity_lake.ingestion.types import MARKET_DIR_MAP, VALID_MARKETS, SourceOutcome, SourceStatus

logger = structlog.get_logger(__name__)

_DELTA_MAP: dict[str, str] = {
    "us_equity": "us",
    "cn_ashare": "cn",
    "hk_sg_equity": "hk_sg",
    "jpx_equity": "jpx",
    "krx_equity": "krx",
}


def _market_dir_to_short(market_dir: str) -> str | None:
    return _DELTA_MAP.get(market_dir)


def find_and_fill_gaps(
    end_date: date | None = None,
    days_back: int = 90,
    markets: list[str] | None = None,
    ticker_config: TickerConfig | None = None,
    dry_run: bool = False,
    max_gap_days: int = 30,
) -> dict[str, int]:
    """Detect missing dates per market and backfill them.

    Returns a dict mapping market name to number of dates filled.
    """
    end_date = end_date or date.today()
    start_date = end_date - timedelta(days=days_back)
    target_markets = markets or list(VALID_MARKETS)
    results: dict[str, int] = {}

    with GapDetector() as detector:
        for market in target_markets:
            market_dir = MARKET_DIR_MAP.get(market, market)
            market_short = _market_dir_to_short(market_dir) or market
            if market in ("macro", "us_news", "us_social_sentiment"):
                continue

            missing = detector.find_missing_dates(
                market_dir,
                ticker=None,
                start_date=start_date,
                end_date=end_date,
                business_days_only=True,
            )
            if not missing:
                logger.info("auto_backfill_no_gaps", market=market)
                continue

            all_missing: list[date] = sorted({d for dates in missing.values() for d in dates})

            if not all_missing:
                continue

            gap_span = (all_missing[-1] - all_missing[0]).days
            if gap_span > max_gap_days * len(all_missing):
                logger.warning(
                    "auto_backfill_gap_too_large",
                    market=market,
                    span_days=gap_span,
                    missing_count=len(all_missing),
                    hint="Use manual backfill for large gaps",
                )
                continue

            logger.info(
                "auto_backfill_filling",
                market=market,
                missing_dates=len(all_missing),
                range=f"{all_missing[0]}..{all_missing[-1]}",
                dry_run=dry_run,
            )

            if dry_run:
                results[market_short] = len(all_missing)
                continue

            filled = 0
            for gap_date in all_missing:
                try:
                    day_results = run_daily_ingestion(
                        trading_date=gap_date,
                        markets=[market],
                        dry_run=False,
                        ticker_config=ticker_config,
                        skip_existing=False,
                        parallel=False,
                    )
                    if day_results.get(market, SourceOutcome(SourceStatus.FAILED)).succeeded:
                        filled += 1
                    else:
                        logger.warning(
                            "auto_backfill_date_failed",
                            market=market,
                            date=str(gap_date),
                        )
                except Exception as exc:
                    logger.error(
                        "auto_backfill_date_error",
                        market=market,
                        date=str(gap_date),
                        error=str(exc),
                    )

            results[market_short] = filled
            logger.info(
                "auto_backfill_complete",
                market=market,
                filled=filled,
                total_missing=len(all_missing),
            )

    return results
