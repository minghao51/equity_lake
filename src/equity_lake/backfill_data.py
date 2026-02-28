#!/usr/bin/env python3
"""
Backfill Historical Data

Fetches historical EOD data for a date range by running the daily ingestion command
for each trading day.

Usage:
    uv run equity-backfill --start 2025-10-01 --end 2026-01-24
    uv run equity-backfill --days-back 90 --parallel
"""

import argparse
import subprocess
import sys
from datetime import date, datetime, timedelta

import structlog

from equity_lake.core.runtime import setup_logging

logger = structlog.get_logger()


def get_trading_days(start_date: date, end_date: date) -> list:
    """
    Generate list of trading days (exclude weekends).

    Args:
        start_date: Start date
        end_date: End date

    Returns:
        List of trading days
    """
    trading_days = []
    current = start_date

    while current <= end_date:
        # Exclude weekends
        if current.weekday() < 5:  # 0=Monday, 4=Friday
            trading_days.append(current)
        current += timedelta(days=1)

    return trading_days


def backfill_date_range(
    start_date: date,
    end_date: date,
    markets: str = "us,cn,hk_sg",
    parallel: bool = True
):
    """
    Backfill data for a date range.

    Args:
        start_date: Start date
        end_date: End date
        markets: Markets to fetch (comma-separated)
        parallel: Use parallel fetching
    """
    logger.info(f"Backfilling data from {start_date} to {end_date}")

    # Get trading days
    trading_days = get_trading_days(start_date, end_date)
    logger.info(f"Found {len(trading_days)} trading days to fetch")

    success_count = 0
    fail_count = 0

    for i, trading_date in enumerate(trading_days, 1):
        date_str = trading_date.strftime('%Y-%m-%d')
        logger.info(f"[{i}/{len(trading_days)}] Fetching {date_str}")

        try:
            # Build command
            cmd = [
                "uv", "run", "equity-daily",
                '--date', date_str,
                '--markets', markets
            ]

            if parallel:
                cmd.append('--parallel')

            # Run command
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout per day
            )

            if result.returncode == 0:
                logger.info(f"  ✅ {date_str} succeeded")
                success_count += 1
            else:
                logger.error(f"  ❌ {date_str} failed: {result.stderr}")
                fail_count += 1

        except subprocess.TimeoutExpired:
            logger.error(f"  ❌ {date_str} timed out")
            fail_count += 1
        except Exception as e:
            logger.error(f"  ❌ {date_str} error: {e}")
            fail_count += 1

    logger.info("Backfill completed:")
    logger.info(f"  Success: {success_count}/{len(trading_days)}")
    logger.info(f"  Failed: {fail_count}/{len(trading_days)}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill historical equity data"
    )

    parser.add_argument(
        '--start',
        type=str,
        help='Start date (YYYY-MM-DD format)'
    )

    parser.add_argument(
        '--end',
        type=str,
        help='End date (YYYY-MM-DD format, default: today)'
    )

    parser.add_argument(
        '--days-back',
        type=int,
        help='Number of trading days back from today'
    )

    parser.add_argument(
        '--markets',
        type=str,
        default='us,cn,hk_sg',
        help='Markets to fetch (default: us,cn,hk_sg)'
    )

    parser.add_argument(
        '--parallel',
        action='store_true',
        help='Enable parallel fetching'
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(name="backfill_data", level="INFO", log_file="backfill_data.log")

    # Determine date range
    if args.days_back:
        # Calculate start date from days_back
        end_date = date.today()
        # Approximate: days_back / 5 * 7 (account for weekends)
        start_date = end_date - timedelta(days=args.days_back * 7 // 5)
    elif args.start and args.end:
        start_date = datetime.strptime(args.start, '%Y-%m-%d').date()
        end_date = datetime.strptime(args.end, '%Y-%m-%d').date()
    else:
        logger.error("Must specify --days-back OR both --start and --end")
        sys.exit(1)

    logger.info(f"Date range: {start_date} to {end_date}")

    # Backfill data
    backfill_date_range(
        start_date=start_date,
        end_date=end_date,
        markets=args.markets,
        parallel=args.parallel
    )


if __name__ == '__main__':
    main()
