"""
Gap Detection Module for Financial Time Series Data

This module provides utilities to detect gaps in time series data stored in
partitioned Parquet files using DuckDB for high-performance queries.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import duckdb

from equity_lake.core.paths import LAKE_DIR

logger = logging.getLogger(__name__)


class GapDetector:
    """
    Detect gaps in time series data using DuckDB.

    Uses DuckDB's generate_series to create an "ideal" date range and
    LEFT JOIN with existing Parquet data to find missing dates.
    """

    def __init__(self, lake_path: Path | None = None):
        self.lake_path = lake_path or LAKE_DIR
        self.con = duckdb.connect(":memory:")

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None  # type: ignore[assignment]

    def __enter__(self) -> GapDetector:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _parquet_glob(self, market: str) -> str:
        return str(self.lake_path / market / "**" / "*.parquet")

    def find_missing_dates(
        self,
        market: str,
        ticker: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        business_days_only: bool = True,
    ) -> dict[str, list[date]]:
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=90)

        logger.info(f"Scanning for gaps in {market} from {start_date} to {end_date}")

        try:
            if ticker:
                rows = self._query_missing_single(market, ticker, start_date, end_date, business_days_only)
            else:
                rows = self._query_missing_all(market, start_date, end_date, business_days_only)

            missing_dates: dict[str, list[date]] = {}
            for ticker_symbol, missing_date in rows:
                missing_dates.setdefault(ticker_symbol, []).append(missing_date)

            logger.info(f"Found {sum(len(d) for d in missing_dates.values())} missing data points across {len(missing_dates)} tickers")
            return missing_dates

        except Exception as e:
            logger.error(f"Gap detection failed: {e}")
            return {}

    def _query_missing_single(
        self,
        market: str,
        ticker: str,
        start_date: date,
        end_date: date,
        business_days_only: bool,
    ) -> list[tuple]:
        glob = self._parquet_glob(market)
        business_day_filter = "WHERE EXTRACT(ISODOW FROM generate_series) BETWEEN 1 AND 5" if business_days_only else ""

        query = f"""
        WITH date_range AS (
            SELECT generate_series::DATE AS date
            FROM generate_series($1::DATE, $2::DATE, INTERVAL '1 day')
            {business_day_filter}
        ),
        existing_dates AS (
            SELECT DISTINCT date
            FROM read_parquet('{glob}')
            WHERE ticker = $3
              AND date BETWEEN $1 AND $2
        )
        SELECT $3::VARCHAR AS ticker, d.date
        FROM date_range d
        LEFT JOIN existing_dates e ON d.date = e.date
        WHERE e.date IS NULL
        ORDER BY d.date
        """
        return self.con.execute(query, [start_date, end_date, ticker]).fetchall()

    def _query_missing_all(
        self,
        market: str,
        start_date: date,
        end_date: date,
        business_days_only: bool,
    ) -> list[tuple]:
        glob = self._parquet_glob(market)
        business_day_filter = "WHERE EXTRACT(ISODOW FROM generate_series) BETWEEN 1 AND 5" if business_days_only else ""

        query = f"""
        WITH date_range AS (
            SELECT generate_series::DATE AS date
            FROM generate_series($1::DATE, $2::DATE, INTERVAL '1 day')
            {business_day_filter}
        ),
        all_tickers AS (
            SELECT DISTINCT ticker
            FROM read_parquet('{glob}')
        ),
        existing_data AS (
            SELECT DISTINCT ticker, date
            FROM read_parquet('{glob}')
            WHERE date BETWEEN $1 AND $2
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
        return self.con.execute(query, [start_date, end_date]).fetchall()

    def get_latest_date(self, market: str, ticker: str) -> date | None:
        glob = self._parquet_glob(market)
        query = f"""
        SELECT MAX(date) AS latest_date
        FROM read_parquet('{glob}')
        WHERE ticker = $1
        """
        try:
            result = self.con.execute(query, [ticker]).fetchone()
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
        if end_date is None:
            end_date = date.today()
        if start_date is None:
            start_date = end_date - timedelta(days=90)

        expected_days = self._count_business_days(start_date, end_date) if business_days_only else (end_date - start_date).days + 1

        glob = self._parquet_glob(market)
        query = f"""
        SELECT
            ticker,
            COUNT(DISTINCT date) AS actual_days
        FROM read_parquet('{glob}')
        WHERE date BETWEEN $1 AND $2
        GROUP BY ticker
        ORDER BY ticker
        """

        try:
            results = self.con.execute(query, [start_date, end_date]).fetchall()
            stats = {}
            for ticker, actual_days in results:
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
        business_days = 0
        current = start_date
        while current <= end_date:
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
        missing_dates = self.find_missing_dates(market, ticker, start_date, end_date, business_days_only)

        if ticker not in missing_dates or not missing_dates[ticker]:
            return []

        dates = sorted(missing_dates[ticker])
        ranges = []
        start = dates[0]
        prev = dates[0]

        for curr in dates[1:]:
            if (curr - prev).days == 1:
                prev = curr
            else:
                ranges.append((start, prev))
                start = curr
                prev = curr

        ranges.append((start, prev))
        return ranges


def print_gap_report(missing_dates: dict[str, list[date]], verbose: bool = False) -> None:
    if not missing_dates:
        print("No missing data found!")
        return

    total_missing = sum(len(dates) for dates in missing_dates.values())
    total_tickers = len(missing_dates)

    print(f"\n{'=' * 70}")
    print("Gap Detection Report")
    print(f"{'=' * 70}")
    print(f"Total tickers with gaps: {total_tickers}")
    print(f"Total missing data points: {total_missing}")
    print(f"{'=' * 70}\n")

    low_gaps = {}
    medium_gaps = {}
    high_gaps = {}

    for ticker, dates in missing_dates.items():
        gap_count = len(dates)
        if gap_count <= 5:
            low_gaps[ticker] = dates
        elif gap_count <= 20:
            medium_gaps[ticker] = dates
        else:
            high_gaps[ticker] = dates

    if high_gaps:
        print(f"HIGH GAPS (20+ missing days): {len(high_gaps)} tickers")
        for ticker, dates in sorted(high_gaps.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {ticker:10} | {len(dates):3} missing days")
            if verbose:
                print(f"             Missing: {', '.join(str(d) for d in dates[:10])}")
                if len(dates) > 10:
                    print(f"             ... and {len(dates) - 10} more")

    if medium_gaps:
        print(f"\nMEDIUM GAPS (6-20 missing days): {len(medium_gaps)} tickers")
        for ticker, dates in sorted(medium_gaps.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {ticker:10} | {len(dates):3} missing days")

    if low_gaps:
        print(f"\nLOW GAPS (1-5 missing days): {len(low_gaps)} tickers")
        for ticker, dates in sorted(low_gaps.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {ticker:10} | {len(dates):3} missing days")

    print(f"\n{'=' * 70}\n")


def print_coverage_stats(stats: dict[str, dict]) -> None:
    if not stats:
        print("No coverage data available")
        return

    print(f"\n{'Ticker':<10} {'Expected':<10} {'Actual':<10} {'Missing':<10} {'Coverage':<10}")
    print("-" * 60)

    sorted_tickers = sorted(stats.items(), key=lambda x: x[1]["coverage_pct"])

    for ticker, ticker_stats in sorted_tickers:
        print(
            f"{ticker:<10} {ticker_stats['expected']:<10} {ticker_stats['actual']:<10} "
            f"{ticker_stats['missing']:<10} {ticker_stats['coverage_pct']:>6.2f}%"
        )

    print()
