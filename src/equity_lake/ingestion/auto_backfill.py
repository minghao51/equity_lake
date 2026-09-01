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
from equity_lake.ingestion.types import MARKET_DIR_MAP, REQUIRED_PRICE_MARKETS, SourceOutcome, SourceStatus, normalize_markets

logger = structlog.get_logger(__name__)


def find_and_fill_gaps(
    end_date: date | None = None,
    days_back: int = 90,
    markets: list[str] | None = None,
    ticker_config: TickerConfig | None = None,
    dry_run: bool = False,
    max_gap_days: int = 30,
) -> dict[str, int]:
    """Detect missing dates per market and backfill them.

    Gap-filling applies only to the required price markets (``us_equity``,
    ``cn_ashare``, ``hk_sg_equity``, ``jpx_equity``, ``krx_equity``): a gap is
    a missing ticker/date row measured
    against an exchange trading calendar, and only these markets have one.
    Enrichment markets are excluded — several share ticker-less tables (e.g.
    ``01_bronze/raw_articles``) with no per-ticker-per-trading-day expectation,
    and the rest are event-driven. Explicitly requesting an enrichment market
    is skipped with a warning.

    Returns a dict mapping market name to number of dates filled.
    """
    end_date = end_date or date.today()
    start_date = end_date - timedelta(days=days_back)
    markets = normalize_markets(markets) if markets is not None else None
    target_markets = markets or sorted(REQUIRED_PRICE_MARKETS)
    results: dict[str, int] = {}

    with GapDetector() as detector:
        for market in target_markets:
            if market not in REQUIRED_PRICE_MARKETS:
                logger.warning(
                    "auto_backfill_skip_non_price_market",
                    market=market,
                    reason="gap-filling requires a ticker-scoped price table backed by a trading calendar",
                )
                continue

            market_dir = MARKET_DIR_MAP.get(market, market)

            try:
                missing = detector.find_missing_dates(
                    market_dir,
                    ticker=None,
                    start_date=start_date,
                    end_date=end_date,
                    business_days_only=True,
                )
            except Exception as exc:
                # One broken table (missing dir, ticker-less schema, ...) must
                # not abort the whole run; the detector has already logged the
                # query failure with its traceback.
                logger.error(
                    "auto_backfill_detection_failed",
                    market=market,
                    market_dir=market_dir,
                    error=str(exc),
                )
                continue

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
                results[market] = len(all_missing)
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

            results[market] = filled
            logger.info(
                "auto_backfill_complete",
                market=market,
                filled=filled,
                total_missing=len(all_missing),
            )

    return results
