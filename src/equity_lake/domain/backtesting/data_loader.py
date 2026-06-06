"""
Data loader for backtesting strategies.

This module provides efficient data loading from DuckDB and Parquet files
for backtesting, with support for caching and multi-market queries.

Usage:
    from equity_lake.domain.backtesting import BacktestDataLoader
    from datetime import date

    loader = BacktestDataLoader()
    data = loader.load(
        tickers=["AAPL", "MSFT", "GOOGL"],
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
        markets=["us"]
    )
"""

from __future__ import annotations

from datetime import date
from typing import Any, Self, cast

import duckdb
import pandas as pd
import structlog
from joblib import Memory

from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    LOGS_DIR,
    US_EQUITY_DIR,
)

logger = structlog.get_logger(__name__)

# Cache directory for joblib
CACHE_DIR = LOGS_DIR / "backtest_cache"
memory = Memory(CACHE_DIR, verbose=0)


class BacktestDataLoader:
    """
    Load and prepare data for backtesting strategies.

    This class provides efficient loading of OHLCV data from Hive-partitioned
    Parquet files, with support for:
    - Multi-market queries (US, CN, HK/SG)
    - Data caching for performance
    - Wide-format pivot for VectorBT compatibility
    - Missing data handling

    Attributes:
        conn: DuckDB connection
        cache_enabled: Whether to use caching

    Example:
        >>> loader = BacktestDataLoader()
        >>> data = loader.load(
        ...     tickers=["AAPL", "MSFT"],
        ...     start_date=date(2020, 1, 1),
        ...     end_date=date(2024, 12, 31)
        ... )
        >>> print(data.head())
    """

    # Market directory mappings
    MARKET_DIRS = {
        "us": US_EQUITY_DIR,
        "cn": CN_ASHARE_DIR,
        "hk_sg": HK_SG_EQUITY_DIR,
        "jpx": JPX_EQUITY_DIR,
        "krx": KRX_EQUITY_DIR,
    }

    def __init__(
        self,
        db_path: str = ":memory:",
        cache_enabled: bool = True,
    ):
        """
        Initialize the data loader.

        Args:
            db_path: DuckDB database path (default: :memory:)
            cache_enabled: Enable joblib caching (default: True)
        """
        self.db_path = db_path
        self.cache_enabled = cache_enabled
        self.conn = duckdb.connect(db_path)
        self._setup_views()

        logger.info(
            "BacktestDataLoader initialized",
            cache_enabled=cache_enabled,
            cache_dir=str(CACHE_DIR) if cache_enabled else None,
        )

    def _setup_views(self) -> None:
        """Create DuckDB views for each market."""
        logger.debug("Setting up market views...")

        for market_label, data_dir in self.MARKET_DIRS.items():
            if not data_dir.exists():
                logger.warning(
                    "Data directory not found, skipping view",
                    market=market_label,
                    path=str(data_dir),
                )
                continue

            parquet_pattern = str(data_dir / "date=*/*.parquet")
            view_name = f"backtest_{market_label}"

            sql = f"""
            CREATE OR REPLACE VIEW {view_name} AS
            SELECT
                ticker,
                date,
                open,
                high,
                low,
                close,
                volume,
                adj_close,
                '{market_label}' as market
            FROM read_parquet('{parquet_pattern}', hive_partitioning=1)
            """

            try:
                self.conn.execute(sql)
                logger.debug("Created market view", market=market_label)
            except Exception as e:
                logger.error(
                    "Failed to create view",
                    market=market_label,
                    error=str(e),
                )

    def load(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        markets: list[str] | None = None,
        columns: list[str] | None = None,
        wide_format: bool = True,
        fill_method: str = "ffill",
    ) -> pd.DataFrame:
        """
        Load OHLCV data for backtesting.

        Args:
            tickers: List of ticker symbols to load
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            markets: List of markets to query (default: all available)
            columns: Columns to load (default: all OHLCV columns)
            wide_format: Return wide format (tickers as columns)
            fill_method: Method to fill missing data ('ffill', 'bfill', or None)

        Returns:
            DataFrame with price data. If wide_format=True, returns
            wide-format DataFrame with tickers as columns (for VectorBT).
            If wide_format=False, returns long-format DataFrame.

        Example:
            >>> data = loader.load(
            ...     tickers=["AAPL", "MSFT"],
            ...     start_date=date(2020, 1, 1),
            ...     end_date=date(2024, 12, 31)
            ... )
            >>> print(data.shape)
            (1258, 2)  # 1258 trading days, 2 tickers
        """
        if markets is None:
            markets = list(self.MARKET_DIRS.keys())

        # Default columns to load
        if columns is None:
            columns = [
                "ticker",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "adj_close",
            ]

        logger.info(
            "Loading backtest data",
            tickers=len(tickers),
            start_date=str(start_date),
            end_date=str(end_date),
            markets=markets,
        )

        # Build query
        data = self._query_data(tickers, start_date, end_date, markets, columns)

        if data.empty:
            logger.warning("No data found for query", tickers=tickers)
            return pd.DataFrame()

        # Data cleaning
        data = self._clean_data(data, fill_method)

        # Convert to wide format if requested
        if wide_format:
            data = self._to_wide_format(data)
            logger.debug(
                "Converted to wide format",
                shape=data.shape,
                date_range=f"{data.index.min()} to {data.index.max()}",
            )
        else:
            logger.debug(
                "Returned long format",
                shape=data.shape,
                tickers=data["ticker"].nunique(),
            )

        return data

    def _query_data(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        markets: list[str],
        columns: list[str],
    ) -> pd.DataFrame:
        """Execute DuckDB query to load data."""

        # Build UNION ALL query for selected markets
        union_queries = []
        for market in markets:
            view_name = f"backtest_{market}"
            data_dir = self.MARKET_DIRS.get(market)

            # Validate directory exists for JPX/KRX markets
            if market in ["jpx", "krx"] and (not data_dir or not data_dir.exists()):
                logger.warning(
                    "Market data directory not found",
                    market=market,
                    path=str(data_dir) if data_dir else "None",
                    hint="Run equity ingest command first to fetch market data",
                )
                continue

            union_queries.append(f"SELECT {', '.join(columns)} FROM {view_name}")

        if not union_queries:
            logger.error(
                "No valid markets with data found",
                requested_markets=markets,
                available_markets=[m for m, d in self.MARKET_DIRS.items() if d.exists()],
            )
            return pd.DataFrame()

        self.conn.register("selected_tickers", pd.DataFrame({"ticker": tickers}))
        sql = f"""
        WITH unioned AS (
            {" UNION ALL ".join(union_queries)}
        )
        SELECT unioned.*
        FROM unioned
        JOIN selected_tickers USING (ticker)
        WHERE date >= ?
          AND date <= ?
        ORDER BY ticker, date
        """

        logger.debug("Executing query", sql_preview=sql[:200] + "...")

        try:
            data = self.conn.execute(sql, [start_date, end_date]).df()
            logger.info(
                "Query executed successfully",
                rows=len(data),
                tickers_found=data["ticker"].nunique(),
            )
            return data
        except Exception as e:
            logger.error("Query failed", error=str(e))
            return pd.DataFrame()

    def _clean_data(
        self,
        data: pd.DataFrame,
        fill_method: str | None = "ffill",
    ) -> pd.DataFrame:
        """
        Clean and prepare data.

        Args:
            data: Raw data from query
            fill_method: Method to fill missing values

        Returns:
            Cleaned DataFrame
        """
        # Ensure date column is datetime
        data["date"] = pd.to_datetime(data["date"])

        # Remove duplicates (keep last)
        duplicates = data.duplicated(subset=["ticker", "date"], keep="last")
        if duplicates.sum() > 0:
            logger.warning("Found duplicate entries, removing", count=duplicates.sum())
            data = data[~duplicates]

        # Sort by ticker and date
        data = data.sort_values(["ticker", "date"])

        # Fill missing values if requested
        if fill_method:
            value_columns = [column for column in data.columns if column not in {"ticker", "date"}]
            if fill_method == "ffill":
                data[value_columns] = data.groupby("ticker", group_keys=False)[value_columns].ffill()
            elif fill_method == "bfill":
                data[value_columns] = data.groupby("ticker", group_keys=False)[value_columns].bfill()
            if fill_method == "ffill":
                data[value_columns] = data.groupby("ticker", group_keys=False)[value_columns].bfill()

        # Drop rows with all NaN prices
        price_cols = ["open", "high", "low", "close", "adj_close"]
        data = data.dropna(subset=price_cols, how="all")

        return data

    def _to_wide_format(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Convert long-format data to wide format.

        Transforms data from:
            ticker | date       | open  | close | volume
            AAPL   | 2020-01-01 | 75.0  | 76.0  | 1000000
            MSFT   | 2020-01-01 | 150.0 | 151.0 | 900000

        To:
            date       | (AAPL, close) | (AAPL, volume) | (MSFT, close) | ...
            2020-01-01 | 76.0          | 1000000        | 151.0         | ...

        Args:
            data: Long-format DataFrame

        Returns:
            Wide-format DataFrame with MultiIndex columns
        """
        value_columns = [column for column in ["open", "high", "low", "close", "volume", "adj_close"] if column in data.columns]
        wide_df = data.set_index(["date", "ticker"])[value_columns].unstack("ticker")
        wide_df = wide_df.swaplevel(0, 1, axis=1).sort_index(axis=1)
        wide_df.columns.names = ["ticker", "field"]
        return wide_df

    @memory.cache  # type: ignore[untyped-decorator]
    def load_cached(
        self,
        tickers: tuple[str, ...],
        start_date: str,
        end_date: str,
        markets: tuple[str, ...],
        columns: tuple[str, ...],
    ) -> pd.DataFrame:
        """
        Load data with joblib caching.

        This method caches the query results to disk, making subsequent
        calls with the same parameters much faster.

        Args:
            tickers: Tuple of ticker symbols
            start_date: Start date string (YYYY-MM-DD)
            end_date: End date string (YYYY-MM-DD)
            markets: Tuple of markets
            columns: Tuple of columns to load

        Returns:
            Wide-format DataFrame

        Example:
            >>> data = loader.load_cached(
            ...     tickers=("AAPL", "MSFT"),
            ...     start_date="2020-01-01",
            ...     end_date="2024-12-31",
            ...     markets=("us",),
            ...     columns=("ticker", "date", "close", "volume")
            ... )
        """
        return self.load(
            tickers=list(tickers),
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            markets=list(markets),
            columns=list(columns),
            wide_format=True,
        )

    def get_available_tickers(
        self,
        market: str,
        as_of_date: date | None = None,
    ) -> list[str]:
        """
        Get list of available tickers for a market.

        Args:
            market: Market label ('us', 'cn', or 'hk_sg')
            as_of_date: Date to check availability (default: latest)

        Returns:
            List of ticker symbols

        Example:
            >>> tickers = loader.get_available_tickers("us")
            >>> print(f"Available US tickers: {len(tickers)}")
        """
        view_name = f"backtest_{market}"

        if as_of_date:
            sql = f"""
            SELECT DISTINCT ticker
            FROM {view_name}
            WHERE date = '{as_of_date}'
            ORDER BY ticker
            """
        else:
            sql = f"""
            SELECT DISTINCT ticker
            FROM {view_name}
            ORDER BY ticker
            """

        try:
            result = self.conn.execute(sql).df()
            return cast(list[str], result["ticker"].tolist())
        except Exception as e:
            logger.error("Failed to get tickers", market=market, error=str(e))
            return []

    def get_date_range(
        self,
        market: str,
        ticker: str | None = None,
    ) -> tuple[date | None, date | None]:
        """
        Get available date range for a market or ticker.

        Args:
            market: Market label ('us', 'cn', or 'hk_sg')
            ticker: Optional ticker to check (default: all tickers in market)

        Returns:
            Tuple of (min_date, max_date) - either can be None if not found

        Example:
            >>> min_date, max_date = loader.get_date_range("us", "AAPL")
            >>> print(f"AAPL data: {min_date} to {max_date}")
        """
        view_name = f"backtest_{market}"

        if ticker:
            sql = f"""
            SELECT
                MIN(date) as min_date,
                MAX(date) as max_date
            FROM {view_name}
            WHERE ticker = '{ticker}'
            """
        else:
            sql = f"""
            SELECT
                MIN(date) as min_date,
                MAX(date) as max_date
            FROM {view_name}
            """

        try:
            result = self.conn.execute(sql).df()
            if not result.empty:
                min_date = result["min_date"].iloc[0]
                max_date = result["max_date"].iloc[0]
                return (
                    date.fromisoformat(str(min_date)) if pd.notna(min_date) else None,
                    date.fromisoformat(str(max_date)) if pd.notna(max_date) else None,
                )
        except Exception as e:
            logger.error("Failed to get date range", market=market, error=str(e))

        return (None, None)

    def clear_cache(self) -> None:
        """Clear the joblib cache."""
        memory.clear()
        logger.info("Cache cleared")

    def close(self) -> None:
        """Close the DuckDB connection."""
        self.conn.close()
        logger.debug("DuckDB connection closed")

    def __enter__(self) -> Self:
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()


__all__ = [
    "BacktestDataLoader",
    "CACHE_DIR",
]
