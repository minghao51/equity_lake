"""Example DuckDB analytical queries and benchmarks.

Separated from ``storage.duckdb`` (which holds the ``EquityDataDB`` connection
manager) so that the demo/analysis SQL lives alongside the benchmark that
exercises it. ``QueryExamples`` is instantiated by ``EquityDataDB.run_named_query``
/ ``run_all_queries`` and by the ``equity query`` CLI command.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from equity_lake.storage.duckdb import EquityDataDB

logger = logging.getLogger(__name__)


class QueryExamples:
    """Collection of example queries for equity data analysis."""

    def __init__(self, db: EquityDataDB):
        self.db = db

    def query_1_latest_data_summary(self) -> pl.DataFrame:
        """Query 1: Summary of latest data by market."""
        logger.info("Running Query 1: Latest Data Summary")

        sql = """
        WITH market_latest AS (
            SELECT market, MAX(date) AS latest_date
            FROM equity_all
            GROUP BY market
        )
        SELECT
            equity_all.market,
            equity_all.date as latest_date,
            COUNT(DISTINCT ticker) as num_tickers,
            COUNT(*) as total_records,
            SUM(volume) as total_volume
        FROM equity_all
        JOIN market_latest
            ON equity_all.market = market_latest.market
           AND equity_all.date = market_latest.latest_date
        GROUP BY equity_all.market, equity_all.date
        ORDER BY equity_all.market
        """

        return self.db.query(sql)

    def query_2_top_volume_stocks(self, days: int = 7) -> pl.DataFrame:
        """Query 2: Top stocks by trading volume."""
        logger.info(f"Running Query 2: Top {days}-Day Volume Leaders")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        latest_volume AS (
            SELECT
                ticker,
                market,
                SUM(volume) as total_volume,
                AVG(volume) as avg_daily_volume,
                COUNT(DISTINCT date) as trading_days
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
            GROUP BY ticker, market
        )
        SELECT
            ticker,
            market,
            total_volume,
            avg_daily_volume,
            trading_days
        FROM latest_volume
        ORDER BY total_volume DESC
        LIMIT 20
        """

        return self.db.query(sql)

    def query_3_top_gainers_losers(self, days: int = 7) -> pl.DataFrame:
        """Query 3: Top gainers and losers."""
        logger.info(f"Running Query 3: Top {days}-Day Gainers & Losers")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        price_change AS (
            SELECT
                ticker,
                market,
                FIRST(close ORDER BY date) as start_price,
                LAST(close ORDER BY date) as end_price,
                (LAST(close ORDER BY date) - FIRST(close ORDER BY date)) / FIRST(close ORDER BY date) * 100 as pct_change
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
            GROUP BY ticker, market
            HAVING COUNT(DISTINCT date) >= {max(1, days - 2)}
        )
        SELECT
            ticker,
            market,
            start_price,
            end_price,
            pct_change,
            CASE
                WHEN pct_change >= 0 THEN 'GAINER'
                ELSE 'LOSER'
            END as category
        FROM price_change
        ORDER BY pct_change DESC
        LIMIT 30
        """

        return self.db.query(sql)

    def query_4_cross_market_comparison(self, ticker: str) -> pl.DataFrame:
        """Query 4: Compare same ticker across markets (if available)."""
        logger.info(f"Running Query 4: Cross-Market Comparison for {ticker}")

        sql = """
        SELECT
            date,
            market,
            ticker,
            close,
            volume
        FROM equity_all
        WHERE ticker = ?
        ORDER BY market, date
        LIMIT 100
        """

        try:
            return self.db.con.execute(sql, [ticker.upper()]).pl()
        except Exception as e:
            logger.error(f"Query 4 failed: {e}")
            return pl.DataFrame()

    def query_5_moving_averages(self, ticker: str, ma_days: int = 20) -> pl.DataFrame:
        """Query 5: Moving averages for a stock."""
        ma_days = int(ma_days)
        logger.info(f"Running Query 5: {ma_days}-Day Moving Average for {ticker}")

        sql = f"""
        WITH stock_data AS (
            SELECT
                date,
                ticker,
                close,
                AVG(close) OVER (
                    PARTITION BY ticker
                    ORDER BY date
                    ROWS BETWEEN {ma_days - 1} PRECEDING AND CURRENT ROW
                ) as ma_{ma_days}
            FROM equity_all
            WHERE ticker = ?
            ORDER BY date DESC
            LIMIT {ma_days * 2}
        )
        SELECT
            date,
            ticker,
            close,
            ma_{ma_days},
            (close - ma_{ma_days}) / ma_{ma_days} * 100 as pct_diff_from_ma
        FROM stock_data
        WHERE ma_{ma_days} IS NOT NULL
        ORDER BY date DESC
        """

        try:
            return self.db.con.execute(sql, [ticker.upper()]).pl()
        except Exception as e:
            logger.error(f"Query 5 failed: {e}")
            return pl.DataFrame()

    def query_6_volatility_analysis(self, days: int = 30) -> pl.DataFrame:
        """Query 6: Most volatile stocks."""
        logger.info(f"Running Query 6: {days}-Day Volatility Analysis")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        daily_returns AS (
            SELECT
                ticker,
                market,
                date,
                close,
                LAG(close) OVER (PARTITION BY ticker ORDER BY date) as prev_close,
                (close - LAG(close) OVER (PARTITION BY ticker ORDER BY date)) / LAG(close) OVER (PARTITION BY ticker ORDER BY date) as daily_return
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
        ),
        volatility_stats AS (
            SELECT
                ticker,
                market,
                AVG(ABS(daily_return)) * 100 as avg_daily_move_pct,
                STDDEV(daily_return) * 100 as volatility_pct,
                COUNT(DISTINCT date) as trading_days
            FROM daily_returns
            WHERE daily_return IS NOT NULL
            GROUP BY ticker, market
            HAVING COUNT(DISTINCT date) >= {max(5, days // 2)}
        )
        SELECT
            ticker,
            market,
            avg_daily_move_pct,
            volatility_pct,
            trading_days
        FROM volatility_stats
        ORDER BY volatility_pct DESC
        LIMIT 20
        """

        return self.db.query(sql)

    def query_7_market_summary_stats(self) -> pl.DataFrame:
        """Query 7: Summary statistics by market."""
        logger.info("Running Query 7: Market Summary Statistics")

        sql = """
        WITH market_stats AS (
            SELECT
                market,
                date,
                COUNT(DISTINCT ticker) as num_tickers,
                SUM(volume) as daily_volume,
                AVG(CASE WHEN volume > 0 THEN close END) as avg_price
            FROM equity_all
            WHERE volume > 0
            GROUP BY market, date
        )
        SELECT
            market,
            COUNT(DISTINCT date) as trading_days_in_data,
            AVG(num_tickers) as avg_tickers_per_day,
            AVG(daily_volume) as avg_daily_volume,
            AVG(avg_price) as avg_stock_price
        FROM market_stats
        GROUP BY market
        ORDER BY market
        """

        return self.db.query(sql)

    def query_8_price_range_analysis(self, days: int = 30) -> pl.DataFrame:
        """Query 8: Price range analysis (52-week high/low)."""
        logger.info(f"Running Query 8: {days}-Day Price Range Analysis")

        sql = f"""
        WITH data_latest AS (
            SELECT MAX(date) AS latest_date FROM equity_all
        ),
        price_ranges AS (
            SELECT
                ticker,
                market,
                MIN(close) as period_low,
                MAX(close) as period_high,
                LAST(close ORDER BY date) as current_price,
                (LAST(close ORDER BY date) - MIN(close)) / MIN(close) * 100 as pct_from_low,
                (MAX(close) - LAST(close ORDER BY date)) / MAX(close) * 100 as pct_from_high
            FROM equity_all
            WHERE date >= (SELECT latest_date FROM data_latest) - INTERVAL '{days} days'
            GROUP BY ticker, market
            HAVING COUNT(DISTINCT date) >= {max(5, days // 2)}
        )
        SELECT
            ticker,
            market,
            period_low,
            period_high,
            current_price,
            pct_from_low,
            pct_from_high
        FROM price_ranges
        ORDER BY pct_from_low DESC
        LIMIT 30
        """

        return self.db.query(sql)


def benchmark_queries(db: EquityDataDB) -> dict[str, float]:
    """Benchmark query execution times."""
    import time

    logger.info("Running query performance benchmarks...")

    queries = QueryExamples(db)
    benchmarks = {}

    benchmark_queries = [
        ("latest_summary", lambda: queries.query_1_latest_data_summary()),
        ("top_volume", lambda: queries.query_2_top_volume_stocks(7)),
        ("gainers_losers", lambda: queries.query_3_top_gainers_losers(7)),
        ("volatility", lambda: queries.query_6_volatility_analysis(30)),
        ("market_stats", lambda: queries.query_7_market_summary_stats()),
    ]

    for name, query_func in benchmark_queries:
        start = time.time()
        try:
            result = query_func()
            elapsed = time.time() - start
            benchmarks[name] = elapsed
            logger.info(f"  {name}: {elapsed:.3f}s ({len(result)} rows)")
        except Exception as e:
            logger.error(f"  {name}: FAILED - {e}")
            benchmarks[name] = -1

    return benchmarks


__all__ = ["QueryExamples", "benchmark_queries"]
