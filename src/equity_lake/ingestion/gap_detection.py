"""
Gap Detection Module for Financial Time Series Data

This module provides utilities to detect gaps in time series data stored in
Delta Lake tables using DuckDB for high-performance queries.
"""

from __future__ import annotations

import contextlib
from datetime import date, timedelta
from pathlib import Path

import duckdb
import structlog

from equity_lake.core.calendar import trading_days_between
from equity_lake.core.paths import LAKE_DIR
from equity_lake.ingestion.types import MARKET_DIR_REVERSE

logger = structlog.get_logger(__name__)


def _calendar_key(market: str) -> str:
    """Resolve a medallion table directory (or market key) to a ``core.calendar`` market key.

    Resolution goes through the canonical market map (``MARKET_DIR_REVERSE``)
    instead of substring slicing: ``"01_bronze/market_data/us_equity"`` resolves
    to the ``"us_equity"`` market key, while ``"02_silver/analyst_ratings"`` resolves
    to ``"us_analyst_ratings"`` instead of the bogus calendar key
    ``"analyst_ratings"``. Only the required price markets map to a trading
    calendar; any other key intentionally yields no sessions. Tables shared
    by several markets (e.g. ``01_bronze/raw_articles``) resolve to one of
    their market keys — none of which has a calendar.
    """
    return MARKET_DIR_REVERSE.get(market, market)


def _expected_trading_dates(market: str, start_date: date, end_date: date) -> list[date]:
    """Expected sessions for a market dir; empty (with a warning) when no trading calendar maps to it."""
    trading_dates = trading_days_between(_calendar_key(market), start_date, end_date)
    if not trading_dates:
        logger.warning(
            "gap_detection_no_trading_calendar",
            market=market,
            calendar_key=_calendar_key(market),
            reason="no trading calendar maps to this table; cannot derive expected trading days",
        )
    return trading_dates


class GapDetector:
    """Detect gaps in time series data using DuckDB.

    Uses DuckDB's generate_series to create an "ideal" date range and
    LEFT JOIN with existing Delta Lake data to find missing dates.
    """

    def __init__(self, lake_path: Path | None = None):
        self.lake_path = lake_path or LAKE_DIR
        self.con: duckdb.DuckDBPyConnection | None = duckdb.connect(":memory:")
        with contextlib.suppress(Exception):
            if self.con is not None:
                self.con.execute("INSTALL delta; LOAD delta;")

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        if hasattr(self, "con") and self.con is not None:
            self.con.close()
            self.con = None

    def _connection(self) -> duckdb.DuckDBPyConnection:
        if self.con is None:
            raise RuntimeError("DuckDB connection is closed")
        return self.con

    def __enter__(self) -> GapDetector:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _scan_source(self, market: str) -> str:
        market_path = self.lake_path / market
        return f"delta_scan('{market_path}')"

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

        logger.info("Scanning for gaps in %s from %s to %s", market, start_date, end_date)

        try:
            if ticker:
                rows = self._query_missing_single(market, ticker, start_date, end_date, business_days_only)
            else:
                rows = self._query_missing_all(market, start_date, end_date, business_days_only)
        except duckdb.Error:
            logger.exception("gap_detection_query_failed", market=market, ticker=ticker)
            raise

        missing_dates: dict[str, list[date]] = {}
        for ticker_symbol, missing_date in rows:
            missing_dates.setdefault(ticker_symbol, []).append(missing_date)

        logger.info(
            "Found %d missing data points across %d tickers",
            sum(len(d) for d in missing_dates.values()),
            len(missing_dates),
        )
        return missing_dates

    def _query_missing_single(
        self,
        market: str,
        ticker: str,
        start_date: date,
        end_date: date,
        business_days_only: bool,
    ) -> list[tuple[str, date]]:
        scan = self._scan_source(market)
        if business_days_only:
            trading_dates = _expected_trading_dates(market, start_date, end_date)
            if not trading_dates:
                return []
            placeholders = ", ".join(f"'{d.isoformat()}'" for d in trading_dates)
            # Filter on the outer query (where alias `d` is in scope), NOT inside
            # the date_range CTE — `d` is only introduced by `FROM date_range d`.
            business_day_filter = f"AND d.date IN ({placeholders})"
        else:
            business_day_filter = ""

        query = f"""
        WITH date_range AS (
            SELECT generate_series::DATE AS date
            FROM generate_series($1::DATE, $2::DATE, INTERVAL '1 day')
        ),
        existing_dates AS (
            SELECT DISTINCT date
            FROM {scan}
            WHERE ticker = $3
              AND date BETWEEN $1 AND $2
        )
        SELECT $3::VARCHAR AS ticker, d.date
        FROM date_range d
        LEFT JOIN existing_dates e ON d.date = e.date
        WHERE e.date IS NULL
        {business_day_filter}
        ORDER BY d.date
        """
        return list(self._connection().execute(query, [start_date, end_date, ticker]).fetchall())

    def _query_missing_all(
        self,
        market: str,
        start_date: date,
        end_date: date,
        business_days_only: bool,
    ) -> list[tuple[str, date]]:
        scan = self._scan_source(market)
        if business_days_only:
            trading_dates = _expected_trading_dates(market, start_date, end_date)
            if not trading_dates:
                return []
            placeholders = ", ".join(f"'{d.isoformat()}'" for d in trading_dates)
            # Filter on the final SELECT (alias `dt`), NOT inside the date_range CTE.
            business_day_filter = f"AND dt.date IN ({placeholders})"
        else:
            business_day_filter = ""

        query = f"""
        WITH date_range AS (
            SELECT generate_series::DATE AS date
            FROM generate_series($1::DATE, $2::DATE, INTERVAL '1 day')
        ),
        existing_data AS (
            SELECT DISTINCT ticker, date
            FROM {scan}
        ),
        date_ticker_combos AS (
            SELECT t.ticker, d.date
            FROM (SELECT DISTINCT ticker FROM existing_data) t
            CROSS JOIN date_range d
        )
        SELECT dt.ticker, dt.date
        FROM date_ticker_combos dt
        LEFT JOIN existing_data ed ON dt.ticker = ed.ticker AND dt.date = ed.date
        WHERE ed.date IS NULL
        {business_day_filter}
        ORDER BY dt.ticker, dt.date
        """
        return list(self._connection().execute(query, [start_date, end_date]).fetchall())
