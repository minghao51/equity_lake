"""
Gap Detection Module for Financial Time Series Data

This module provides utilities to detect gaps in time series data stored in
partitioned Parquet files using DuckDB for high-performance queries.
"""

import logging
from datetime import date, timedelta
from pathlib import Path

import duckdb

from equity_lake.core.runtime import LAKE_DIR

logger = logging.getLogger(__name__)


class GapDetector:
    """
    Detect gaps in time series data using DuckDB.

    Uses DuckDB's generate_series to create an "ideal" date range and
    LEFT JOIN with existing Parquet data to find missing dates.
    """

    def __init__(self, lake_path: Path | None = None):
        """
        Initialize GapDetector.

        Args:
            lake_path: Path to data lake directory (default: from equity_lake.LAKE_DIR)
        """
        self.lake_path = lake_path or LAKE_DIR
        self.con = duckdb.connect(":memory:")

    def find_missing_dates(
        self,
        market: str,
        ticker: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        business_days_only: bool = True,
    ) -> dict[str, list[date]]:
        """
        Find missing dates for tickers in a market.

        Args:
            market: Market directory name ('us_equity', 'cn_ashare', 'hk_sg_equity')
            ticker: Specific ticker to check (None = all tickers)
            start_date: Start of date range (default: 90 days ago)
            end_date: End of date range (default: today)
            business_days_only: Only check Mon-Fri (default: True)

        Returns:
            Dictionary mapping ticker -> list of missing dates
        """
        # Set default date range
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=90)

        logger.info(f"Scanning for gaps in {market} from {start_date} to {end_date}")

        # Build query based on parameters
        if ticker:
            query = self._build_single_ticker_query(market, ticker, start_date, end_date, business_days_only)
        else:
            query = self._build_all_tickers_query(market, start_date, end_date, business_days_only)

        try:
            result = self.con.execute(query).fetchall()

            # Parse results
            missing_dates: dict[str, list[date]] = {}
            for row in result:
                ticker_symbol, missing_date = row[0], row[1]
                if ticker_symbol not in missing_dates:
                    missing_dates[ticker_symbol] = []
                missing_dates[ticker_symbol].append(missing_date)

            logger.info(f"Found {sum(len(d) for d in missing_dates.values())} missing data points across {len(missing_dates)} tickers")

            return missing_dates

        except Exception as e:
            logger.error(f"Gap detection failed: {e}")
            return {}

    def _build_single_ticker_query(
        self,
        market: str,
        ticker: str,
        start_date: date,
        end_date: date,
        business_days_only: bool,
    ) -> str:
        """Build SQL to find missing dates for a single ticker."""
        market_path = self.lake_path / market

        business_day_filter = ""
        if business_days_only:
            # DuckDB EXTRACT(DOW) returns 0=Monday, 6=Sunday
            business_day_filter = "WHERE EXTRACT(DOW FROM generate_series) BETWEEN 0 AND 4"

        return f"""
        WITH date_range AS (
            SELECT generate_series::DATE AS date
            FROM generate_series('{start_date}'::DATE, '{end_date}'::DATE, INTERVAL '1 day')
            {business_day_filter}
        ),
        existing_dates AS (
            SELECT DISTINCT date
            FROM read_parquet('{market_path}/**/*.parquet')
            WHERE ticker = '{ticker}'
              AND date BETWEEN '{start_date}' AND '{end_date}'
        )
        SELECT '{ticker}' AS ticker, d.date
        FROM date_range d
        LEFT JOIN existing_dates e ON d.date = e.date
        WHERE e.date IS NULL
        ORDER BY d.date
        """

    def _build_all_tickers_query(self, market: str, start_date: date, end_date: date, business_days_only: bool) -> str:
        """Build SQL to find missing dates for all tickers."""
        market_path = self.lake_path / market

        business_day_filter = ""
        if business_days_only:
            business_day_filter = "WHERE EXTRACT(DOW FROM generate_series) BETWEEN 0 AND 4"

        return f"""
        WITH date_range AS (
            SELECT generate_series::DATE AS date
            FROM generate_series('{start_date}'::DATE, '{end_date}'::DATE, INTERVAL '1 day')
            {business_day_filter}
        ),
        all_tickers AS (
            SELECT DISTINCT ticker
            FROM read_parquet('{market_path}/**/*.parquet')
        ),
        existing_data AS (
            SELECT DISTINCT ticker, date
            FROM read_parquet('{market_path}/**/*.parquet')
            WHERE date BETWEEN '{start_date}' AND '{end_date}'
        ),
        date_ticker_combos AS (
            SELECT t.ticker, d.date
            FROM all_tickers t
            CROSS JOIN date_range d
        )
        SELECT dt.ticker, dt.date
        FROM date_ticker_combos dt
        LEFT JOIN existing_data ed ON dt.ticker = ed.ticker AND dt.date = ed.date
        WHERE ed.date IS NULL
        ORDER BY dt.ticker, dt.date
        """

    def get_latest_date(self, market: str, ticker: str) -> date | None:
        """
        Get the most recent date for a ticker.

        Args:
            market: Market directory name
            ticker: Ticker symbol

        Returns:
            Latest date or None if no data found
        """
        market_path = self.lake_path / market

        query = f"""
        SELECT MAX(date) AS latest_date
        FROM read_parquet('{market_path}/**/*.parquet')
        WHERE ticker = '{ticker}'
        """

        try:
            result = self.con.execute(query).fetchone()
            return result[0] if result and result[0] else None
        except Exception as e:
            logger.warning(f"Failed to get latest date for {ticker}: {e}")
            return None

    def get_coverage_stats(
        self,
        market: str,
        start_date: date | None = None,
        end_date: date | None = None,
        business_days_only: bool = True,
    ) -> dict[str, dict]:
        """
        Get coverage statistics for all tickers in a market.

        Args:
            market: Market directory name
            start_date: Start of date range (default: 90 days ago)
            end_date: End of date range (default: today)
            business_days_only: Only count business days

        Returns:
            Dictionary mapping ticker -> {
                'expected': int,
                'actual': int,
                'missing': int,
                'coverage_pct': float
            }
        """
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=90)

        # Calculate expected business days
        expected_days = self._count_business_days(start_date, end_date) if business_days_only else (end_date - start_date).days + 1

        market_path = self.lake_path / market

        query = f"""
        SELECT
            ticker,
            COUNT(DISTINCT date) AS actual_days
        FROM read_parquet('{market_path}/**/*.parquet')
        WHERE date BETWEEN '{start_date}' AND '{end_date}'
        GROUP BY ticker
        ORDER BY ticker
        """

        try:
            results = self.con.execute(query).fetchall()

            stats = {}
            for row in results:
                ticker, actual_days = row[0], row[1]
                missing_days = max(0, expected_days - actual_days)
                coverage_pct = (actual_days / expected_days * 100) if expected_days > 0 else 0

                stats[ticker] = {
                    "expected": expected_days,
                    "actual": actual_days,
                    "missing": missing_days,
                    "coverage_pct": round(coverage_pct, 2),
                }

            return stats

        except Exception as e:
            logger.error(f"Failed to get coverage stats: {e}")
            return {}

    def _count_business_days(self, start_date: date, end_date: date) -> int:
        """Count business days between two dates (exclusive of weekends)."""
        business_days = 0
        current = start_date

        while current <= end_date:
            # Monday=0, Friday=4 in Python weekday()
            if current.weekday() < 5:
                business_days += 1
            current += timedelta(days=1)

        return business_days

    def get_missing_date_ranges(
        self,
        market: str,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
        business_days_only: bool = True,
    ) -> list[tuple[date, date]]:
        """
        Get missing dates grouped into contiguous ranges.

        Args:
            market: Market directory name
            ticker: Ticker symbol
            start_date: Start of date range
            end_date: End of date range
            business_days_only: Only check business days

        Returns:
            List of (start_date, end_date) tuples representing gaps
        """
        missing_dates = self.find_missing_dates(market, ticker, start_date, end_date, business_days_only)

        if ticker not in missing_dates or not missing_dates[ticker]:
            return []

        # Group consecutive dates into ranges
        dates = sorted(missing_dates[ticker])
        ranges = []
        start = dates[0]
        prev = dates[0]

        for curr in dates[1:]:
            if (curr - prev).days == 1:
                # Consecutive
                prev = curr
            else:
                # Gap: end current range
                ranges.append((start, prev))
                start = curr
                prev = curr

        # Add final range
        ranges.append((start, prev))

        return ranges


def print_gap_report(missing_dates: dict[str, list[date]], verbose: bool = False) -> None:
    """
    Print a human-readable gap report.

    Args:
        missing_dates: Dictionary from find_missing_dates()
        verbose: If True, show all missing dates
    """
    if not missing_dates:
        print("✅ No missing data found!")
        return

    total_missing = sum(len(dates) for dates in missing_dates.values())
    total_tickers = len(missing_dates)

    print(f"\n{'=' * 70}")
    print("Gap Detection Report")
    print(f"{'=' * 70}")
    print(f"Total tickers with gaps: {total_tickers}")
    print(f"Total missing data points: {total_missing}")
    print(f"{'=' * 70}\n")

    # Group by gap severity
    low_gaps = {}  # 1-5 missing days
    medium_gaps = {}  # 6-20 missing days
    high_gaps = {}  # 20+ missing days

    for ticker, dates in missing_dates.items():
        gap_count = len(dates)
        if gap_count <= 5:
            low_gaps[ticker] = dates
        elif gap_count <= 20:
            medium_gaps[ticker] = dates
        else:
            high_gaps[ticker] = dates

    # Print high gaps first
    if high_gaps:
        print(f"🔴 HIGH GAPS (20+ missing days): {len(high_gaps)} tickers")
        for ticker, dates in sorted(high_gaps.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {ticker:10} | {len(dates):3} missing days")
            if verbose:
                print(f"             Missing: {', '.join(str(d) for d in dates[:10])}")
                if len(dates) > 10:
                    print(f"             ... and {len(dates) - 10} more")

    if medium_gaps:
        print(f"\n🟡 MEDIUM GAPS (6-20 missing days): {len(medium_gaps)} tickers")
        for ticker, dates in sorted(medium_gaps.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {ticker:10} | {len(dates):3} missing days")

    if low_gaps:
        print(f"\n🟢 LOW GAPS (1-5 missing days): {len(low_gaps)} tickers")
        for ticker, dates in sorted(low_gaps.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {ticker:10} | {len(dates):3} missing days")

    print(f"\n{'=' * 70}\n")


def print_coverage_stats(stats: dict[str, dict]) -> None:
    """
    Print coverage statistics for all tickers.

    Args:
        stats: Dictionary from get_coverage_stats()
    """
    if not stats:
        print("No coverage data available")
        return

    print(f"\n{'Ticker':<10} {'Expected':<10} {'Actual':<10} {'Missing':<10} {'Coverage':<10}")
    print("-" * 60)

    # Sort by coverage percentage
    sorted_tickers = sorted(stats.items(), key=lambda x: x[1]["coverage_pct"])

    for ticker, ticker_stats in sorted_tickers:
        coverage_color = "✅" if ticker_stats["coverage_pct"] >= 95 else "⚠️" if ticker_stats["coverage_pct"] >= 80 else "❌"

        print(
            f"{ticker:<10} {ticker_stats['expected']:<10} {ticker_stats['actual']:<10} "
            f"{ticker_stats['missing']:<10} {ticker_stats['coverage_pct']:>6.2f}% {coverage_color}"
        )

    print()
