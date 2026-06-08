#!/usr/bin/env python3
"""
DuckDB Query Examples for Equity EOD Data

This module demonstrates how to query the Hive-partitioned Parquet data
using DuckDB's Python API. It includes examples of:

- Creating unified views across markets
- Common analytical queries
- Performance optimization
- Integration with pandas
- Data visualization preparation

Usage:
    uv run equity query
    uv run equity query --query top_gainers
    uv run equity query --date 2024-12-01
"""

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from equity_lake.core.logging import setup_logging
from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    US_EQUITY_DIR,
)

# Logger configuration
logger = logging.getLogger(__name__)


# =============================================================================
# Database Connection and View Creation
# =============================================================================


class EquityDataDB:
    """DuckDB connection manager for equity data queries.

    Supports both Hive-partitioned Parquet and Delta Lake tables.
    Automatically detects which format is present per market directory.
    """

    MARKET_VIEWS = [
        ("us_equity", US_EQUITY_DIR, "us"),
        ("cn_ashare", CN_ASHARE_DIR, "cn"),
        ("hk_sg_equity", HK_SG_EQUITY_DIR, "hk_sg"),
        ("jpx_equity", JPX_EQUITY_DIR, "jpx"),
        ("krx_equity", KRX_EQUITY_DIR, "krx"),
    ]

    def __init__(self, db_path: str | Path | None = ":memory:"):
        self.db_path = db_path if db_path is not None else ":memory:"
        self.con = duckdb.connect(self.db_path)
        self.available_views: list[str] = []
        self._views_initialized = False

    def _ensure_views(self) -> None:
        if self._views_initialized:
            return
        self._views_initialized = True
        logger.info("Setting up unified views...")
        self.con.execute("INSTALL delta; LOAD delta;")

        for view_name, data_dir, market_label in self.MARKET_VIEWS:
            self._create_market_view(view_name, data_dir, market_label)

        self._create_unified_view()
        logger.info("Views created successfully")

    def close(self) -> None:
        if hasattr(self, "con") and self.con is not None:
            self.con.close()

    def __enter__(self) -> "EquityDataDB":
        self._ensure_views()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def _is_delta_table(data_dir: Path) -> bool:
        from deltalake import DeltaTable

        return DeltaTable.is_deltatable(str(data_dir))

    def _create_market_view(self, view_name: str, data_dir: Path, market_label: str) -> None:
        if not data_dir.exists():
            logger.warning(f"Data directory not found: {data_dir}")
            return

        if self._is_delta_table(data_dir):
            scan = f"SELECT *, '{market_label}' as market FROM delta_scan('{data_dir}')"
        else:
            parquet_pattern = str(data_dir / "date=*/*.parquet")
            scan = f"SELECT *, '{market_label}' as market FROM read_parquet('{parquet_pattern}', hive_partitioning=1)"

        sql = f"CREATE OR REPLACE VIEW {view_name} AS {scan}"

        try:
            self.con.execute(sql)
            logger.debug(f"Created view: {view_name}")
            self.available_views.append(view_name)
        except Exception as e:
            logger.error(f"Failed to create view {view_name}: {e}")

    def _create_unified_view(self) -> None:
        """Create unified view across all markets."""
        if not self.available_views:
            self.con.execute("CREATE OR REPLACE VIEW equity_all AS SELECT NULL::VARCHAR AS ticker WHERE FALSE")
            return

        sql = "CREATE OR REPLACE VIEW equity_all AS " + " UNION ALL ".join(f"SELECT * FROM {view_name}" for view_name in self.available_views)

        try:
            self.con.execute(sql)
            logger.debug("Created unified view: equity_all")
        except Exception as e:
            logger.error(f"Failed to create unified view: {e}")

    def query(self, sql: str) -> pd.DataFrame:
        """Execute SQL query and return result as DataFrame."""
        self._ensure_views()
        try:
            return self.con.execute(sql).df()
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return pd.DataFrame()

    def query_arrow(self, sql: str) -> Any:
        """Execute SQL query and return result as PyArrow Table (zero-copy)."""
        import pyarrow as pa

        self._ensure_views()
        try:
            return self.con.execute(sql).fetch_arrow_table()
        except Exception as e:
            logger.error(f"Arrow query failed: {e}")
            return pa.table({})

    def execute(self, sql: str) -> Any:
        """Execute SQL query and return result."""
        self._ensure_views()
        try:
            return self.con.execute(sql)
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            raise


# =============================================================================
# Example Queries
# =============================================================================


class QueryExamples:
    """Collection of example queries for equity data analysis."""

    def __init__(self, db: EquityDataDB):
        self.db = db

    def query_1_latest_data_summary(self) -> pd.DataFrame:
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

    def query_2_top_volume_stocks(self, days: int = 7) -> pd.DataFrame:
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

    def query_3_top_gainers_losers(self, days: int = 7) -> pd.DataFrame:
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

    def query_4_cross_market_comparison(self, ticker: str) -> pd.DataFrame:
        """Query 4: Compare same ticker across markets (if available)."""
        logger.info(f"Running Query 4: Cross-Market Comparison for {ticker}")

        sql = f"""
        SELECT
            date,
            market,
            ticker,
            close,
            volume
        FROM equity_all
        WHERE ticker = '{ticker.upper()}'
        ORDER BY market, date
        LIMIT 100
        """

        return self.db.query(sql)

    def query_5_moving_averages(self, ticker: str, ma_days: int = 20) -> pd.DataFrame:
        """Query 5: Moving averages for a stock."""
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
            WHERE ticker = '{ticker.upper()}'
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

        return self.db.query(sql)

    def query_6_volatility_analysis(self, days: int = 30) -> pd.DataFrame:
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

    def query_7_market_summary_stats(self) -> pd.DataFrame:
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

    def query_8_price_range_analysis(self, days: int = 30) -> pd.DataFrame:
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


# =============================================================================
# Performance Benchmarking
# =============================================================================


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


# =============================================================================
# CLI Interface
# =============================================================================


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="DuckDB query examples for equity EOD data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Available Queries:
  latest_summary      Latest data summary by market
  top_volume          Top stocks by volume (last N days)
  gainers_losers      Top gainers and losers
  volatility          Most volatile stocks
  market_stats        Market summary statistics
  price_range         Price range analysis
  benchmark           Run performance benchmarks
  all                 Run all queries

Examples:
  # Run all queries
  uv run equity query

  # Run specific query
  uv run equity query --query top_volume

  # Query with parameters
  uv run equity query --query gainers_losers --days 14

  # Export results to CSV
  uv run equity query --query top_volume --output results.csv

  # Benchmark performance
  uv run equity query --query benchmark
        """,
    )

    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default="all",
        help="Query name to run (default: all)",
    )

    parser.add_argument(
        "--ticker",
        "-t",
        type=str,
        help="Ticker symbol for ticker-specific queries",
    )

    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=7,
        help="Number of days for rolling calculations (default: 7)",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Output CSV file path",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--db-path",
        type=str,
        default=":memory:",
        help="DuckDB database path (default: :memory:)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = setup_logging(__name__, level=log_level)

    try:
        # Initialize database connection
        logger.info(f"Connecting to DuckDB: {args.db_path}")
        db = EquityDataDB(db_path=args.db_path)

        # Initialize queries
        queries = QueryExamples(db)

        # Map query names to functions
        query_map = {
            "latest_summary": lambda: queries.query_1_latest_data_summary(),
            "top_volume": lambda: queries.query_2_top_volume_stocks(args.days),
            "gainers_losers": lambda: queries.query_3_top_gainers_losers(args.days),
            "cross_market": lambda: queries.query_4_cross_market_comparison(args.ticker) if args.ticker else pd.DataFrame(),
            "moving_avg": lambda: queries.query_5_moving_averages(args.ticker, args.days) if args.ticker else pd.DataFrame(),
            "volatility": lambda: queries.query_6_volatility_analysis(args.days),
            "market_stats": lambda: queries.query_7_market_summary_stats(),
            "price_range": lambda: queries.query_8_price_range_analysis(args.days),
        }

        # Execute query
        if args.query == "benchmark":
            results = benchmark_queries(db)
            print("\n" + "=" * 60)
            print("Performance Benchmarks")
            print("=" * 60)
            for name, elapsed in results.items():
                status = f"{elapsed:.3f}s" if elapsed > 0 else "FAILED"
                print(f"  {name:20s}: {status}")

        elif args.query == "all":
            # Run all queries
            for name, query_func in query_map.items():
                print(f"\n{'=' * 60}")
                print(f"Query: {name}")
                print(f"{'=' * 60}")

                df = query_func()
                if not df.empty:
                    print(df.to_string(index=False))
                else:
                    print("No results or missing parameters")

        else:
            # Run specific query
            if args.query not in query_map:
                logger.error(f"Unknown query: {args.query}")
                logger.error(f"Available queries: {', '.join(query_map.keys())}")
                sys.exit(1)

            df = query_map[args.query]()

            if df.empty:
                logger.warning("No results returned")
                if args.query in ["cross_market", "moving_avg"] and not args.ticker:
                    logger.error("This query requires --ticker parameter")
                    sys.exit(1)
            else:
                print(f"\n{'=' * 60}")
                print(f"Query: {args.query}")
                print(f"{'=' * 60}\n")
                print(df.to_string(index=False))

                # Export to CSV if requested
                if args.output:
                    df.to_csv(args.output, index=False)
                    logger.info(f"✅ Results exported to {args.output}")

    except Exception as e:
        logger.error(f"Query execution failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
