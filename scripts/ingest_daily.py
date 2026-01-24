#!/usr/bin/env python3
"""
Daily EOD Data Ingestion Script

Fetches end-of-day equity data from multiple markets and writes to Hive-partitioned Parquet.

Markets supported:
- US Equities (NYSE, NASDAQ) via yfinance
- China A-shares (SSE, SZSE) via akshare
- Hong Kong (HKEX) via yfinance
- Singapore (SGX) via yfinance

Usage:
    python -m scripts.ingest_daily
    python -m scripts.ingest_daily --date 2024-12-01
    python -m scripts.ingest_daily --markets us,cn --dry-run
"""

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yfinance as yf
import akshare as ak
import pyarrow as pa
import pyarrow.parquet as pq

from scripts import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    LAKE_DIR,
    LOGS_DIR,
    STANDARD_COLUMNS,
    US_EQUITY_DIR,
    get_project_config,
    setup_logging,
)
from scripts.config import TickerConfig
from scripts.gap_detector import GapDetector, print_gap_report, print_coverage_stats
from scripts.parallel_ingest import (
    MarketFetchResult,
    fetch_markets_parallel,
    fetch_markets_sequential,
    summarize_results,
)
from scripts.logging_utils import timer, correlation_context, setup_structured_logging
import structlog

# Logger configuration - use structlog for structured logging
logger = structlog.get_logger()


# =============================================================================
# Market Data Fetchers
# =============================================================================

class MarketDataFetcher:
    """Base class for market data fetchers."""

    def __init__(self, retry_attempts: int = 3, retry_delay: float = 1.0):
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch data for a specific date. To be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement fetch()")

    def _retry_on_failure(self, func, *args, **kwargs) -> pd.DataFrame:
        """Retry logic for API calls."""
        last_error = None
        for attempt in range(self.retry_attempts):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.retry_attempts - 1:
                    wait_time = self.retry_delay * (2**attempt)  # Exponential backoff
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. Retrying in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.error(f"All {self.retry_attempts} attempts failed: {e}")
                    raise last_error


class USEquityFetcher(MarketDataFetcher):
    """Fetch US equity EOD data using yfinance."""

    def __init__(
        self,
        tickers: Optional[List[str]] = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: Optional[TickerConfig] = None,
        filters: Optional[Dict] = None,
    ):
        """
        Initialize US equity fetcher.

        Args:
            tickers: Explicit list of tickers (overrides config)
            retry_attempts: Number of retry attempts for API calls
            retry_delay: Delay between retries in seconds
            ticker_config: TickerConfig instance (uses default if None)
            filters: Optional filters for selecting tickers from config
                - tags: List[str] - Filter by tags
                - sectors: List[str] - Filter by sectors
                - groups: List[str] - Filter by groups
                - min_priority: int - Minimum priority level
        """
        super().__init__(retry_attempts, retry_delay)

        # Load tickers from config or use explicit list
        if tickers is not None:
            self.tickers = tickers
            logger.info(f"Using explicit ticker list: {len(tickers)} tickers")
        else:
            self.tickers = self._load_tickers_from_config(ticker_config, filters)

    def _load_tickers_from_config(
        self,
        ticker_config: Optional[TickerConfig],
        filters: Optional[Dict]
    ) -> List[str]:
        """
        Load tickers from configuration with optional filtering.

        Args:
            ticker_config: TickerConfig instance
            filters: Filter criteria

        Returns:
            List of ticker symbols
        """
        try:
            config = ticker_config or TickerConfig()
        except Exception as e:
            logger.warning(f"Failed to load ticker config: {e}. Using fallback list.")
            return self._get_fallback_tickers()

        # Apply filters if provided
        if filters:
            return self._apply_filters(config, filters)

        # Get all active US tickers
        tickers = config.get_tickers_for_market('us', active_only=True)

        if not tickers:
            logger.warning("No active tickers found in config for US market")
            return self._get_fallback_tickers()

        logger.info(f"Loaded {len(tickers)} tickers from config for US market")
        return tickers

    def _apply_filters(self, config: TickerConfig, filters: Dict) -> List[str]:
        """Apply filters to select tickers from config."""
        # Filter by tags
        if 'tags' in filters:
            tags = filters['tags']
            match_all = filters.get('match_all_tags', False)
            tickers = config.get_tickers_by_tags(tags, match_all=match_all, market='us')
            logger.info(f"Filtered by tags {tags}: {len(tickers)} tickers")
            return tickers

        # Filter by sectors
        if 'sectors' in filters:
            sectors = filters['sectors']
            tickers = []
            for sector in sectors:
                sector_tickers = config.get_tickers_by_sector(sector, market='us')
                tickers.extend(sector_tickers)
            tickers = list(set(tickers))  # Remove duplicates
            logger.info(f"Filtered by sectors {sectors}: {len(tickers)} tickers")
            return tickers

        # Filter by groups
        if 'groups' in filters:
            groups = filters['groups']
            tickers = []
            for group in groups:
                group_tickers = config.get_tickers_by_group(group)
                tickers.extend(group_tickers)
            tickers = list(set(tickers))  # Remove duplicates
            logger.info(f"Filtered by groups {groups}: {len(tickers)} tickers")
            return tickers

        # Filter by priority
        if 'min_priority' in filters:
            min_priority = filters['min_priority']
            tickers = config.get_tickers_for_market(
                'us',
                active_only=True,
                min_priority=min_priority
            )
            logger.info(f"Filtered by min_priority {min_priority}: {len(tickers)} tickers")
            return tickers

        # No filters applied, get all active
        return config.get_tickers_for_market('us', active_only=True)

    def _get_fallback_tickers(self) -> List[str]:
        """
        Fallback ticker list if config loading fails.

        This maintains backward compatibility with hardcoded lists.
        """
        logger.warning("Using fallback ticker list (config-based approach recommended)")
        return [
            "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "BRK-B",
            "LLY", "AVGO", "JPM", "V", "JNJ", "WMT", "MA", "PG", "COST", "UNH",
            "XOM", "HD", "CVX", "MRK", "ABBV", "BAC", "KO", "PEP", "CRM", "NFLX",
            "AMD", "TMO", "LIN", "ABT", "ORCL", "ADBE", "CMCSA", "WFC", "COP",
            "QCOM", "INTC", "DHR", "VZ", "IBM", "GE", "DIS", "BA", "NKE", "CAT"
        ]

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch US equity data for given date."""
        logger.info(f"Fetching US equity data for {trading_date}")

        def _fetch():
            # Download data for all tickers
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            data = yf.download(
                self.tickers,
                start=start_date,
                end=end_date,
                group_by='ticker',
                progress=False
            )

            if data.empty:
                logger.warning(f"No data returned for US equities on {trading_date}")
                return pd.DataFrame()

            # Handle single ticker case (returns different structure)
            if len(self.tickers) == 1:
                data = pd.DataFrame({self.tickers[0]: data})

            # Normalize to long format
            df_list = []
            for ticker in self.tickers:
                if ticker in data.columns:
                    ticker_data = data[ticker].reset_index()
                    ticker_data['ticker'] = ticker
                    df_list.append(ticker_data)

            if not df_list:
                return pd.DataFrame()

            df = pd.concat(df_list, ignore_index=True)
            df.columns = [col.lower() for col in df.columns]

            # Rename columns to match standard schema
            df = df.rename(columns={'adj close': 'adj_close'})

            # Ensure date column is date type
            df['date'] = pd.to_datetime(df['date']).dt.date

            # Select and order standard columns
            available_cols = [col for col in STANDARD_COLUMNS if col in df.columns]
            df = df[available_cols]

            # Drop rows with all NaN values
            df = df.dropna(how='all')

            logger.info(f"Fetched {len(df)} rows for US equities")
            return df

        return self._retry_on_failure(_fetch)


class CNAshareFetcher(MarketDataFetcher):
    """Fetch China A-share EOD data using akshare with parallel stock fetching."""

    def __init__(
        self,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: Optional[TickerConfig] = None,
        filters: Optional[Dict] = None,
        max_workers: int = 10,
        stock_limit: int = 100,
    ):
        """
        Initialize China A-share fetcher.

        Args:
            retry_attempts: Number of retry attempts for API calls
            retry_delay: Delay between retries in seconds
            ticker_config: TickerConfig instance (uses default if None)
            filters: Optional filters for selecting tickers from config
            max_workers: Maximum number of parallel stock fetches (default: 10)
            stock_limit: Maximum number of stocks to fetch (default: 100)
        """
        super().__init__(retry_attempts, retry_delay)
        self.ticker_config = ticker_config or TickerConfig()
        self.filters = filters or {}
        self.max_workers = max_workers
        self.stock_limit = stock_limit

    def _fetch_single_stock(
        self,
        stock_code: str,
        date_str: str,
        trading_date: date
    ) -> Optional[pd.DataFrame]:
        """
        Fetch data for a single stock.

        Args:
            stock_code: Stock symbol (6-digit code)
            date_str: Date string in YYYYMMDD format
            trading_date: Trading date object

        Returns:
            DataFrame with stock data or None if failed
        """
        try:
            stock_data = ak.stock_zh_a_hist(
                symbol=stock_code,
                period="daily",
                start_date=date_str,
                end_date=date_str,
                adjust=""
            )

            if not stock_data.empty:
                stock_data['ticker'] = stock_code
                stock_data['date'] = trading_date
                return stock_data

            return None

        except Exception as e:
            logger.debug(f"Failed to fetch {stock_code}: {e}")
            return None

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch China A-share data for given date using parallel stock fetching."""
        logger.info(
            "fetch_cn_ashare_started",
            date=str(trading_date),
            max_workers=self.max_workers,
            stock_limit=self.stock_limit
        )

        def _fetch():
            # Convert to akshare date format (YYYYMMDD)
            date_str = trading_date.strftime("%Y%m%d")

            try:
                # Get A-share stock list
                logger.info("fetching_stock_list")
                stock_list = ak.stock_info_a_code_name()
                logger.info("stock_list_fetched", total_stocks=len(stock_list))

                # Get sample of major stocks (to avoid rate limits)
                sample_stocks = stock_list.head(self.stock_limit)
                logger.info(
                    "stock_sample_selected",
                    sample_size=len(sample_stocks),
                    total_stocks=len(stock_list)
                )

                df_list = []
                success_count = 0
                failure_count = 0

                # Parallel stock fetching
                with timer("parallel_cn_stock_fetching", stock_count=len(sample_stocks)):
                    with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                        # Submit all fetch jobs
                        future_to_stock = {}
                        for _, row in sample_stocks.iterrows():
                            stock_code = row['code']
                            stock_name = row['name']

                            future = executor.submit(
                                self._fetch_single_stock,
                                stock_code,
                                date_str,
                                trading_date
                            )
                            future_to_stock[future] = stock_code

                        # Collect results as they complete
                        for future in as_completed(future_to_stock):
                            stock_code = future_to_stock[future]

                            try:
                                result = future.result(timeout=30)  # 30s timeout per stock

                                if result is not None:
                                    df_list.append(result)
                                    success_count += 1
                                else:
                                    failure_count += 1

                            except Exception as e:
                                logger.debug(
                                    "stock_fetch_exception",
                                    stock=stock_code,
                                    error=str(e)
                                )
                                failure_count += 1

                logger.info(
                    "stock_fetch_completed",
                    success=success_count,
                    failures=failure_count,
                    total=len(sample_stocks)
                )

                if not df_list:
                    logger.warning(
                        "no_cn_ashare_data",
                        date=str(trading_date),
                        message="No data returned for China A-shares"
                    )
                    return pd.DataFrame()

                # Concatenate all results
                df = pd.concat(df_list, ignore_index=True)

                # Rename akshare columns to standard schema
                column_mapping = {
                    '开盘': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '收盘': 'close',
                    '成交量': 'volume',
                }

                df = df.rename(columns=column_mapping)

                # Add adj_close (same as close for A-shares unless adjustment needed)
                if 'adj_close' not in df.columns:
                    df['adj_close'] = df['close']

                # Select standard columns
                available_cols = [col for col in STANDARD_COLUMNS if col in df.columns]
                df = df[available_cols]

                # Drop rows with all NaN
                df = df.dropna(how='all')

                logger.info(
                    "fetch_cn_ashare_completed",
                    rows=len(df),
                    unique_tickers=df['ticker'].nunique() if 'ticker' in df.columns else 0
                )

                return df

            except Exception as e:
                logger.error(
                    "fetch_cn_ashare_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    date=str(trading_date)
                )
                return pd.DataFrame()

        return self._retry_on_failure(_fetch)


class HKSGEquityFetcher(MarketDataFetcher):
    """Fetch Hong Kong and Singapore equity data using yfinance."""

    def __init__(
        self,
        hk_tickers: Optional[List[str]] = None,
        sg_tickers: Optional[List[str]] = None,
        retry_attempts: int = 3,
        retry_delay: float = 1.0,
        ticker_config: Optional[TickerConfig] = None,
        filters: Optional[Dict] = None,
    ):
        """
        Initialize HK/SG equity fetcher.

        Args:
            hk_tickers: Explicit list of HK tickers (overrides config)
            sg_tickers: Explicit list of SG tickers (overrides config)
            retry_attempts: Number of retry attempts for API calls
            retry_delay: Delay between retries in seconds
            ticker_config: TickerConfig instance (uses default if None)
            filters: Optional filters for selecting tickers from config
        """
        super().__init__(retry_attempts, retry_delay)

        # Load tickers from config or use explicit lists
        if hk_tickers is not None or sg_tickers is not None:
            self.hk_tickers = hk_tickers or []
            self.sg_tickers = sg_tickers or []
            logger.info(
                f"Using explicit ticker lists: "
                f"{len(self.hk_tickers)} HK, {len(self.sg_tickers)} SG"
            )
        else:
            self.hk_tickers, self.sg_tickers = self._load_tickers_from_config(
                ticker_config, filters
            )

    def _load_tickers_from_config(
        self,
        ticker_config: Optional[TickerConfig],
        filters: Optional[Dict]
    ) -> Tuple[List[str], List[str]]:
        """
        Load tickers from configuration with optional filtering.

        Args:
            ticker_config: TickerConfig instance
            filters: Filter criteria

        Returns:
            Tuple of (hk_tickers, sg_tickers)
        """
        try:
            config = ticker_config or TickerConfig()
        except Exception as e:
            logger.warning(f"Failed to load ticker config: {e}. Using fallback lists.")
            return self._get_fallback_tickers()

        # Apply filters if provided
        if filters:
            return self._apply_filters(config, filters)

        # Get all active tickers for each market
        hk_tickers = config.get_tickers_for_market('hk', active_only=True)
        sg_tickers = config.get_tickers_for_market('sg', active_only=True)

        if not hk_tickers and not sg_tickers:
            logger.warning("No active tickers found in config for HK/SG markets")
            return self._get_fallback_tickers()

        logger.info(
            f"Loaded tickers from config: "
            f"{len(hk_tickers)} HK, {len(sg_tickers)} SG"
        )
        return hk_tickers, sg_tickers

    def _apply_filters(
        self,
        config: TickerConfig,
        filters: Dict
    ) -> Tuple[List[str], List[str]]:
        """Apply filters to select tickers from config."""
        # Get all tickers first
        hk_tickers = config.get_tickers_for_market('hk', active_only=True)
        sg_tickers = config.get_tickers_for_market('sg', active_only=True)

        # Apply tag filtering
        if 'tags' in filters:
            tags = filters['tags']
            match_all = filters.get('match_all_tags', False)
            hk_tickers = config.get_tickers_by_tags(tags, match_all=match_all, market='hk')
            sg_tickers = config.get_tickers_by_tags(tags, match_all=match_all, market='sg')
            logger.info(f"Filtered by tags {tags}: {len(hk_tickers)} HK, {len(sg_tickers)} SG")

        # Apply sector filtering
        elif 'sectors' in filters:
            sectors = filters['sectors']
            hk_filtered = []
            sg_filtered = []
            for sector in sectors:
                hk_filtered.extend(config.get_tickers_by_sector(sector, market='hk'))
                sg_filtered.extend(config.get_tickers_by_sector(sector, market='sg'))
            hk_tickers = list(set(hk_filtered))
            sg_tickers = list(set(sg_filtered))
            logger.info(
                f"Filtered by sectors {sectors}: {len(hk_tickers)} HK, {len(sg_tickers)} SG"
            )

        # Apply group filtering
        elif 'groups' in filters:
            groups = filters['groups']
            hk_tickers = []
            sg_tickers = []
            for group in groups:
                group_tickers = config.get_tickers_by_group(group)
                # Separate by market
                for ticker in group_tickers:
                    if ticker.endswith('.HK'):
                        hk_tickers.append(ticker)
                    elif ticker.endswith('.SI'):
                        sg_tickers.append(ticker)
            hk_tickers = list(set(hk_tickers))
            sg_tickers = list(set(sg_tickers))
            logger.info(
                f"Filtered by groups {groups}: {len(hk_tickers)} HK, {len(sg_tickers)} SG"
            )

        # Apply priority filtering
        elif 'min_priority' in filters:
            min_priority = filters['min_priority']
            hk_tickers = config.get_tickers_for_market(
                'hk',
                active_only=True,
                min_priority=min_priority
            )
            sg_tickers = config.get_tickers_for_market(
                'sg',
                active_only=True,
                min_priority=min_priority
            )
            logger.info(
                f"Filtered by min_priority {min_priority}: "
                f"{len(hk_tickers)} HK, {len(sg_tickers)} SG"
            )

        return hk_tickers, sg_tickers

    def _get_fallback_tickers(self) -> Tuple[List[str], List[str]]:
        """
        Fallback ticker lists if config loading fails.

        Returns:
            Tuple of (hk_tickers, sg_tickers)
        """
        logger.warning("Using fallback ticker lists (config-based approach recommended)")
        hk_tickers = [
            "0700.HK", "9988.HK", "0941.HK", "1299.HK", "2318.HK",
            "0939.HK", "1398.HK", "0883.HK", "0857.HK", "1038.HK",
            "0027.HK", "0016.HK", "0005.HK", "0388.HK", "0011.HK"
        ]
        sg_tickers = [
            "D05.SI", "O39.SI", "U11.SI", "Z74.SI", "C6L.SI",
            "S68.SI", "V03.SI", "BS6.SI", "G13.SI", "S63.SI"
        ]
        return hk_tickers, sg_tickers

    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch HK/SG equity data for given date."""
        logger.info(f"Fetching HK/SG equity data for {trading_date}")

        def _fetch():
            all_tickers = self.hk_tickers + self.sg_tickers
            start_date = trading_date.strftime("%Y-%m-%d")
            end_date = (trading_date + timedelta(days=1)).strftime("%Y-%m-%d")

            data = yf.download(
                all_tickers,
                start=start_date,
                end=end_date,
                group_by='ticker',
                progress=False
            )

            if data.empty:
                logger.warning(f"No data returned for HK/SG equities on {trading_date}")
                return pd.DataFrame()

            # Normalize to long format
            df_list = []
            for ticker in all_tickers:
                if ticker in data.columns:
                    ticker_data = data[ticker].reset_index()
                    ticker_data['ticker'] = ticker
                    df_list.append(ticker_data)

            if not df_list:
                return pd.DataFrame()

            df = pd.concat(df_list, ignore_index=True)
            df.columns = [col.lower() for col in df.columns]
            df = df.rename(columns={'adj close': 'adj_close'})
            df['date'] = pd.to_datetime(df['date']).dt.date

            available_cols = [col for col in STANDARD_COLUMNS if col in df.columns]
            df = df[available_cols]
            df = df.dropna(how='all')

            logger.info(f"Fetched {len(df)} rows for HK/SG equities")
            return df

        return self._retry_on_failure(_fetch)


# =============================================================================
# Data Writers
# =============================================================================

def write_to_partitioned_parquet(
    df: pd.DataFrame,
    market: str,
    trading_date: date,
    dry_run: bool = False
) -> bool:
    """
    Write DataFrame to Hive-partitioned Parquet with deduplication.

    Args:
        df: DataFrame to write
        market: Market identifier ('us_equity', 'cn_ashare', 'hk_sg_equity')
        trading_date: Trading date for partition
        dry_run: If True, skip actual write

    Returns:
        True if successful, False otherwise
    """
    if df.empty:
        logger.warning(f"Empty DataFrame for {market} on {trading_date}, skipping write")
        return False

    # Determine output directory
    if market == "us_equity":
        output_dir = US_EQUITY_DIR
    elif market == "cn_ashare":
        output_dir = CN_ASHARE_DIR
    elif market == "hk_sg_equity":
        output_dir = HK_SG_EQUITY_DIR
    else:
        logger.error(f"Unknown market: {market}")
        return False

    # Create partition directory
    partition_dir = output_dir / f"date={trading_date}"
    partition_dir.mkdir(parents=True, exist_ok=True)

    output_file = partition_dir / f"{trading_date}.parquet"

    # Deduplication: Check if file already exists
    if output_file.exists():
        logger.info(f"File exists: {output_file}. Checking for duplicates...")

        try:
            # Load existing data
            existing_df = pd.read_parquet(output_file)

            # Create ticker-date combinations for both datasets
            existing_combos = set(
                existing_df.apply(lambda r: (r['ticker'], r['date']), axis=1).tolist()
            )
            new_combos = df.apply(lambda r: (r['ticker'], r['date']), axis=1).tolist()

            # Filter out duplicates
            duplicate_mask = df.apply(
                lambda r: (r['ticker'], r['date']) in existing_combos,
                axis=1
            )

            duplicate_count = duplicate_mask.sum()
            total_count = len(df)

            if duplicate_count > 0:
                logger.warning(
                    f"Found {duplicate_count} duplicate records "
                    f"({duplicate_count}/{total_count} = {duplicate_count/total_count*100:.1f}%)"
                )

                # Keep only new records
                df = df[~duplicate_mask]

                if df.empty:
                    logger.warning(f"All records are duplicates. Skipping write.")
                    return True

                logger.info(f"Writing {len(df)} new records (skipped {duplicate_count} duplicates)")

        except Exception as e:
            logger.error(f"Failed to check for duplicates: {e}. Continuing with write...")

    if dry_run:
        logger.info(f"[DRY RUN] Would write {len(df)} rows to {output_file}")
        return True

    try:
        # Convert date column to proper format
        df_write = df.copy()
        if 'date' in df_write.columns:
            df_write['date'] = pd.to_datetime(df_write['date'])

        # Write to Parquet
        df_write.to_parquet(output_file, index=False, compression='snappy')

        file_size = output_file.stat().st_size / 1024  # KB
        logger.info(f"✅ Wrote {len(df_write)} rows to {output_file} ({file_size:.1f} KB)")
        return True

    except Exception as e:
        logger.error(f"Failed to write Parquet file: {e}")
        return False


def validate_schema(df: pd.DataFrame, market: str) -> bool:
    """
    Validate DataFrame schema against standard OHLCV schema.

    Args:
        df: DataFrame to validate
        market: Market identifier for logging

    Returns:
        True if valid, False otherwise
    """
    required_cols = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
    missing_cols = set(required_cols) - set(df.columns)

    if missing_cols:
        logger.error(f"{market}: Missing required columns: {missing_cols}")
        return False

    # Check for required columns being all null
    for col in required_cols:
        if df[col].isnull().all():
            logger.warning(f"{market}: Column '{col}' is all null")

    return True


# =============================================================================
# Main Pipeline
# =============================================================================

def fetch_market_data(
    market: str,
    trading_date: date,
    config: Dict
) -> Optional[pd.DataFrame]:
    """
    Fetch data for a specific market.

    Args:
        market: Market identifier ('us', 'cn', 'hk_sg')
        trading_date: Date to fetch
        config: Configuration dictionary

    Returns:
        DataFrame with fetched data or None
    """
    retry_attempts = config.get('retry_attempts', 3)
    retry_delay = config.get('retry_delay', 1.0)

    if market == "us":
        fetcher = USEquityFetcher(retry_attempts=retry_attempts, retry_delay=retry_delay)
    elif market == "cn":
        fetcher = CNAshareFetcher(retry_attempts=retry_attempts, retry_delay=retry_delay)
    elif market == "hk_sg":
        fetcher = HKSGEquityFetcher(retry_attempts=retry_attempts, retry_delay=retry_delay)
    else:
        logger.error(f"Unknown market: {market}")
        return None

    try:
        df = fetcher.fetch(trading_date)
        if not df.empty and validate_schema(df, market):
            return df
        return None
    except Exception as e:
        logger.error(f"Failed to fetch {market} data: {e}")
        return None


def run_daily_ingestion(
    trading_date: date,
    markets: List[str],
    dry_run: bool = False
) -> Dict[str, bool]:
    """
    Run daily ingestion for specified markets.

    Args:
        trading_date: Date to ingest
        markets: List of market identifiers ('us', 'cn', 'hk_sg')
        dry_run: If True, skip actual writes

    Returns:
        Dictionary mapping market to success status
    """
    results = {}

    for market in markets:
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing market: {market.upper()}")
        logger.info(f"{'='*60}")

        try:
            # Fetch data
            df = fetch_market_data(market, trading_date, get_project_config())

            if df is None or df.empty:
                logger.warning(f"No data fetched for {market}, skipping")
                results[market] = False
                continue

            # Write to Parquet
            market_dir_map = {
                'us': 'us_equity',
                'cn': 'cn_ashare',
                'hk_sg': 'hk_sg_equity'
            }
            market_dir = market_dir_map.get(market, market)

            success = write_to_partitioned_parquet(
                df,
                market_dir,
                trading_date,
                dry_run=dry_run
            )

            results[market] = success

        except Exception as e:
            logger.error(f"Error processing {market}: {e}")
            results[market] = False

    return results


# =============================================================================
# CLI Interface
# =============================================================================

def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Daily EOD data ingestion for equity markets",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch yesterday's data for all markets (default config)
  python -m scripts.ingest_daily

  # Fetch specific date
  python -m scripts.ingest_daily --date 2024-12-01

  # Fetch only US and China markets
  python -m scripts.ingest_daily --markets us cn

  # Enable parallel fetching (3x faster)
  python -m scripts.ingest_daily --parallel

  # Parallel fetching with custom worker count
  python -m scripts.ingest_daily --parallel --max-workers 4

  # Filter by tags (blue-chip stocks only)
  python -m scripts.ingest_daily --tags blue-chip

  # Filter by groups (FAANG stocks)
  python -m scripts.ingest_daily --groups faang

  # Filter by sectors (technology and healthcare)
  python -m scripts.ingest_daily --sectors Technology Healthcare

  # Filter by priority (priority 8+ only)
  python -m scripts.ingest_daily --min-priority 8

  # Combine filters (tech stocks with high priority)
  python -m scripts.ingest_daily --sectors Technology --min-priority 9

  # Explicit ticker list (overrides config)
  python -m scripts.ingest_daily --tickers AAPL,GOOGL,MSFT --markets us

  # Use custom config file
  python -m scripts.ingest_daily --config /path/to/custom_tickers.yaml

  # List available tickers in config
  python -m scripts.ingest_daily --list-tickers

  # Dry run (no writes)
  python -m scripts.ingest_daily --dry-run --verbose
        """
    )

    # Basic arguments
    parser.add_argument(
        '--date',
        type=str,
        help='Trading date (YYYY-MM-DD). Default: yesterday',
    )

    parser.add_argument(
        '--markets',
        type=str,
        default='us,cn,hk_sg',
        help='Comma-separated list of markets (default: us,cn,hk_sg)',
    )

    parser.add_argument(
        '--config',
        type=str,
        help='Path to custom ticker config YAML file (default: config/tickers.yaml)',
    )

    # Ticker filtering arguments
    filter_group = parser.add_argument_group('Ticker Filtering (from config)')

    filter_group.add_argument(
        '--tickers',
        type=str,
        help='Comma-separated list of explicit tickers (overrides config)',
    )

    filter_group.add_argument(
        '--tags',
        type=str,
        help='Comma-separated list of tags to filter tickers (e.g., blue-chip,FAANG)',
    )

    filter_group.add_argument(
        '--sectors',
        type=str,
        nargs='+',
        help='Space-separated list of sectors to filter (e.g., Technology Finance)',
    )

    filter_group.add_argument(
        '--groups',
        type=str,
        help='Comma-separated list of predefined groups (e.g., faang,sp500_top_10)',
    )

    filter_group.add_argument(
        '--min-priority',
        type=int,
        choices=range(1, 11),
        metavar='1-10',
        help='Minimum priority level (1-10, higher = more important)',
    )

    filter_group.add_argument(
        '--match-all-tags',
        action='store_true',
        help='When using --tags, require ALL tags instead of ANY tag',
    )

    # Utility arguments
    parser.add_argument(
        '--list-tickers',
        action='store_true',
        help='List all available tickers from config and exit',
    )

    parser.add_argument(
        '--list-stats',
        action='store_true',
        help='Show config statistics and exit',
    )

    # Gap detection arguments
    gap_group = parser.add_argument_group('Gap Detection')

    gap_group.add_argument(
        '--detect-gaps',
        action='store_true',
        help='Detect and report missing data points (no fetching)',
    )

    gap_group.add_argument(
        '--coverage-stats',
        action='store_true',
        help='Show coverage statistics for all tickers',
    )

    gap_group.add_argument(
        '--days-back',
        type=int,
        default=90,
        help='Number of days to check for missing data (default: 90)',
    )

    gap_group.add_argument(
        '--include-weekends',
        action='store_true',
        help='Include weekends in gap detection (default: business days only)',
    )

    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Skip actual Parquet writes (for testing)',
    )

    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose logging',
    )

    parser.add_argument(
        '--parallel', '-p',
        action='store_true',
        help='Enable parallel fetching of multiple markets (3x speedup)',
    )

    parser.add_argument(
        '--max-workers',
        type=int,
        default=None,
        help='Maximum number of parallel workers (default: number of markets)',
    )

    return parser.parse_args()


def main():
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = setup_logging(__name__, level=log_level, log_file="ingest_daily.log")

    # Load ticker configuration
    config_path = Path(args.config) if args.config else None
    try:
        ticker_config = TickerConfig(config_path=config_path)
    except Exception as e:
        logger.error(f"Failed to load ticker config: {e}")
        sys.exit(1)

    # Handle utility commands (list-tickers, list-stats)
    if args.list_tickers:
        list_tickers_command(ticker_config, args)
        return

    if args.list_stats:
        list_stats_command(ticker_config)
        return

    # Handle gap detection commands
    if args.detect_gaps or args.coverage_stats:
        handle_gap_detection(args)
        return

    # Build filters from CLI arguments
    filters = build_filters_from_args(args)

    # Determine trading date
    if args.date:
        trading_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        trading_date = date.today() - timedelta(days=1)

    logger.info(f"{'='*60}")
    logger.info(f"Daily EOD Data Ingestion - {trading_date}")
    logger.info(f"{'='*60}")

    # Log filters if applied
    if filters:
        logger.info("Filters applied:")
        for key, value in filters.items():
            logger.info(f"  - {key}: {value}")

    # Parse markets
    markets = [m.strip() for m in args.markets.split(',')]
    valid_markets = {'us', 'cn', 'hk_sg'}
    invalid = set(markets) - valid_markets

    if invalid:
        logger.error(f"Invalid markets: {invalid}")
        logger.error(f"Valid markets: {valid_markets}")
        sys.exit(1)

    logger.info(f"Markets to process: {markets}")

    if args.dry_run:
        logger.info("🔍 DRY RUN MODE - No files will be written")

    if args.parallel:
        logger.info("🚀 PARALLEL MODE - Fetching markets concurrently")

    # Run ingestion
    try:
        results = run_daily_ingestion(
            trading_date,
            markets,
            dry_run=args.dry_run,
            ticker_config=ticker_config,
            filters=filters,
            explicit_tickers=args.tickers,
            parallel=args.parallel,
            max_workers=args.max_workers
        )

        # Summary
        logger.info(f"\n{'='*60}")
        logger.info("Summary")
        logger.info(f"{'='*60}")

        for market, success in results.items():
            status = "✅ SUCCESS" if success else "❌ FAILED"
            logger.info(f"{market.upper()}: {status}")

        # Exit with error code if any failed
        if not all(results.values()):
            sys.exit(1)

    except Exception as e:
        logger.error(f"Ingestion failed: {e}", exc_info=True)
        sys.exit(1)


def build_filters_from_args(args: argparse.Namespace) -> Dict:
    """
    Build filter dictionary from CLI arguments.

    Args:
        args: Parsed command-line arguments

    Returns:
        Dictionary of filters to pass to fetchers
    """
    filters = {}

    if args.tags:
        filters['tags'] = [t.strip() for t in args.tags.split(',')]
        filters['match_all_tags'] = args.match_all_tags

    if args.sectors:
        filters['sectors'] = args.sectors

    if args.groups:
        filters['groups'] = [g.strip() for g in args.groups.split(',')]

    if args.min_priority:
        filters['min_priority'] = args.min_priority

    return filters


def list_tickers_command(ticker_config: TickerConfig, args: argparse.Namespace) -> None:
    """
    Handle --list-tickers command.

    Args:
        ticker_config: Loaded ticker configuration
        args: Parsed command-line arguments
    """
    print(f"\n{'='*80}")
    print(f"Tickers from: {ticker_config.config_path}")
    print(f"{'='*80}\n")

    # Get markets to list
    if args.markets:
        markets = [m.strip() for m in args.markets.split(',')]
    else:
        markets = ticker_config.get_markets()

    for market in markets:
        market_info = ticker_config.get_market_info(market)

        if not market_info:
            print(f"⚠️  Unknown market: {market}")
            continue

        active_tickers = [t for t in market_info.tickers if t.active]
        inactive_tickers = [t for t in market_info.tickers if not t.active]

        print(f"\n{market.upper()} Market ({market_info.currency})")
        print(f"  Total: {len(market_info.tickers)} tickers")
        print(f"  Active: {len(active_tickers)}")
        print(f"  Inactive: {len(inactive_tickers)}")
        print(f"  Exchanges: {', '.join(sorted(set(t.exchange for t in market_info.tickers)))}")
        print(f"  Sectors: {', '.join(sorted(set(t.sector for t in market_info.tickers)))}")

        if args.verbose:
            print(f"\n  Active Tickers:")
            for ticker in sorted(active_tickers, key=lambda t: t.priority, reverse=True):
                tags_str = ', '.join(ticker.tags[:3]) if ticker.tags else 'none'
                print(f"    {ticker.symbol:12} | {ticker.name:30} | "
                      f"{ticker.sector:20} | Prio: {ticker.priority} | {tags_str}")

            if inactive_tickers:
                print(f"\n  Inactive Tickers:")
                for ticker in sorted(inactive_tickers, key=lambda t: t.symbol):
                    print(f"    {ticker.symbol:12} | {ticker.name:30} | INACTIVE")


def list_stats_command(ticker_config: TickerConfig) -> None:
    """
    Handle --list-stats command.

    Args:
        ticker_config: Loaded ticker configuration
    """
    stats = ticker_config.get_stats()

    print(f"\n{'='*80}")
    print(f"Ticker Configuration Statistics")
    print(f"{'='*80}\n")

    print(f"Config Version: {stats.get('version', 'N/A')}")
    print(f"Total Markets: {stats.get('total_markets', 0)}")
    print(f"Total Groups: {stats.get('total_groups', 0)}")

    # Market statistics
    markets = stats.get('markets', {})
    if markets:
        print(f"\n{'Market':<10} {'Currency':<8} {'Total':<8} {'Active':<8} "
              f"{'Inactive':<10} {'Exchanges':<30}")
        print("-" * 80)

        for market_name, market_stats in sorted(markets.items()):
            exchanges = ', '.join(market_stats.get('exchanges', [])[:3])
            if len(market_stats.get('exchanges', [])) > 3:
                exchanges += f" (+{len(market_stats.get('exchanges', [])) - 3})"

            print(f"{market_name.upper():<10} "
                  f"{market_stats.get('currency', ''):<8} "
                  f"{market_stats.get('total_tickers', 0):<8} "
                  f"{market_stats.get('active_tickers', 0):<8} "
                  f"{market_stats.get('inactive_tickers', 0):<10} "
                  f"{exchanges:<30}")

    # Available groups
    groups = ticker_config.get_groups()
    if groups:
        print(f"\nAvailable Groups ({len(groups)}):")
        for group in sorted(groups):
            group_info = ticker_config.get_group_info(group)
            print(f"  - {group}: {group_info.description if group_info else ''}")


def handle_gap_detection(args: argparse.Namespace) -> None:
    """
    Handle gap detection commands.

    Args:
        args: Parsed command-line arguments
    """
    detector = GapDetector()

    # Parse markets
    if args.markets:
        markets = [m.strip() for m in args.markets.split(',')]
        # Map short names to full directory names
        market_map = {
            'us': 'us_equity',
            'cn': 'cn_ashare',
            'hk': 'hk_sg_equity',
            'sg': 'hk_sg_equity',
            'hk_sg': 'hk_sg_equity'
        }
        markets = [market_map.get(m, m) for m in markets]
    else:
        markets = ['us_equity', 'cn_ashare', 'hk_sg_equity']

    # Calculate date range
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days_back)
    business_days_only = not args.include_weekends

    print(f"\n{'='*70}")
    print(f"Gap Detection: {start_date} to {end_date}")
    print(f"Markets: {', '.join(markets)}")
    print(f"Business days only: {business_days_only}")
    print(f"{'='*70}\n")

    for market in markets:
        market_path = LAKE_DIR / market

        # Check if market exists
        if not market_path.exists():
            print(f"⚠️  No data directory found for {market}")
            continue

        # Get short market name for display
        market_short = market.replace('_equity', '').replace('_ashare', '')

        if args.coverage_stats:
            # Show coverage statistics
            print(f"\n{market_short.upper()} Coverage Statistics:")
            print("-" * 70)

            stats = detector.get_coverage_stats(
                market,
                start_date,
                end_date,
                business_days_only
            )

            if stats:
                print_coverage_stats(stats)
            else:
                print("No coverage data available")

        elif args.detect_gaps:
            # Detect gaps
            missing_dates = detector.find_missing_dates(
                market,
                ticker=None,  # Check all tickers
                start_date=start_date,
                end_date=end_date,
                business_days_only=business_days_only
            )

            print(f"\n{market_short.upper()} Market:")
            print_gap_report(missing_dates, verbose=args.verbose)

    print(f"\n{'='*70}\n")


def run_daily_ingestion(
    trading_date: date,
    markets: List[str],
    dry_run: bool = False,
    ticker_config: Optional[TickerConfig] = None,
    filters: Optional[Dict] = None,
    explicit_tickers: Optional[str] = None,
    parallel: bool = False,
    max_workers: Optional[int] = None,
) -> Dict[str, bool]:
    """
    Run daily ingestion for specified markets.

    Args:
        trading_date: Date to ingest
        markets: List of market identifiers ('us', 'cn', 'hk_sg')
        dry_run: If True, skip actual writes
        ticker_config: TickerConfig instance
        filters: Filter dictionary for config-based selection
        explicit_tickers: Comma-separated explicit ticker list (overrides config)
        parallel: If True, fetch markets concurrently
        max_workers: Maximum number of parallel workers

    Returns:
        Dictionary mapping market to success status
    """
    results = {}

    # Parse explicit tickers if provided
    explicit_ticker_list = None
    if explicit_tickers:
        explicit_ticker_list = [t.strip() for t in explicit_tickers.split(',')]

    if parallel:
        # Parallel fetching mode
        with correlation_context():  # Set correlation ID for this run
            logger.info(
                "parallel_ingestion_mode",
                markets=markets,
                max_workers=max_workers or len(markets)
            )

            # Build fetch function map for parallel execution
            fetch_func_map = {}
            for market in markets:
                # Create a closure for each market
                def make_fetch_func(mkt, explicit_list, config, fltrs):
                    def fetch_func(date):
                        return fetch_market_data_with_config(
                            mkt,
                            date,
                            ticker_config=config,
                            filters=fltrs,
                            explicit_tickers=explicit_list if mkt == 'us' else None,
                        )
                    return fetch_func

                fetch_func_map[market] = (
                    make_fetch_func(market, explicit_ticker_list, ticker_config, filters),
                    {}
                )

            # Fetch all markets in parallel
            with timer("fetch_all_markets_parallel", mode="parallel"):
                fetch_results = fetch_markets_parallel(
                    markets=markets,
                    trading_date=trading_date,
                    fetch_func_map=fetch_func_map,
                    max_workers=max_workers
                )

            # Process results
            market_dir_map = {
                'us': 'us_equity',
                'cn': 'cn_ashare',
                'hk_sg': 'hk_sg_equity'
            }

            for market, fetch_result in fetch_results.items():
                logger.info(f"\n{'='*60}")
                logger.info(f"Processing market: {market.upper()}")
                logger.info(f"{'='*60}")

                if not fetch_result.success:
                    logger.error(
                        f"{market} fetch failed: {fetch_result.error}",
                        duration_seconds=fetch_result.duration_seconds
                    )
                    results[market] = False
                    continue

                df = fetch_result.data
                if df is None or df.empty:
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = False
                    continue

                # Write to Parquet
                market_dir = market_dir_map.get(market, market)

                with timer(f"write_{market}_parquet", market=market):
                    success = write_to_partitioned_parquet(
                        df,
                        market_dir,
                        trading_date,
                        dry_run=dry_run
                    )

                results[market] = success

            # Log summary statistics
            summary = summarize_results(fetch_results)
            logger.info(
                "parallel_ingestion_summary",
                **summary
            )

    else:
        # Sequential fetching mode (original behavior)
        for market in markets:
            logger.info(f"\n{'='*60}")
            logger.info(f"Processing market: {market.upper()}")
            logger.info(f"{'='*60}")

            try:
                # Fetch data
                with timer(f"fetch_{market}_data", market=market):
                    df = fetch_market_data_with_config(
                        market,
                        trading_date,
                        ticker_config=ticker_config,
                        filters=filters,
                        explicit_tickers=explicit_ticker_list if market == 'us' else None,
                    )

                if df is None or df.empty:
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = False
                    continue

                # Write to Parquet
                market_dir_map = {
                    'us': 'us_equity',
                    'cn': 'cn_ashare',
                    'hk_sg': 'hk_sg_equity'
                }
                market_dir = market_dir_map.get(market, market)

                with timer(f"write_{market}_parquet", market=market):
                    success = write_to_partitioned_parquet(
                        df,
                        market_dir,
                        trading_date,
                        dry_run=dry_run
                    )

                results[market] = success

            except Exception as e:
                logger.error(f"Error processing {market}: {e}")
                results[market] = False

    return results


def fetch_market_data_with_config(
    market: str,
    trading_date: date,
    ticker_config: Optional[TickerConfig] = None,
    filters: Optional[Dict] = None,
    explicit_tickers: Optional[List[str]] = None,
) -> Optional[pd.DataFrame]:
    """
    Fetch data for a specific market with config support.

    Args:
        market: Market identifier ('us', 'cn', 'hk_sg')
        trading_date: Date to fetch
        ticker_config: TickerConfig instance
        filters: Filter dictionary for config-based selection
        explicit_tickers: Explicit ticker list (overrides config)

    Returns:
        DataFrame with fetched data or None
    """
    project_config = get_project_config()
    retry_attempts = project_config.get('retry_attempts', 3)
    retry_delay = project_config.get('retry_delay', 1.0)

    if market == "us":
        fetcher = USEquityFetcher(
            tickers=explicit_tickers,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
            filters=filters,
        )
    elif market == "cn":
        fetcher = CNAshareFetcher(
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
            filters=filters,
        )
    elif market == "hk_sg":
        fetcher = HKSGEquityFetcher(
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
            filters=filters,
        )
    else:
        logger.error(f"Unknown market: {market}")
        return None

    try:
        df = fetcher.fetch(trading_date)
        if not df.empty and validate_schema(df, market):
            return df
        return None
    except Exception as e:
        logger.error(f"Failed to fetch {market} data: {e}")
        return None


if __name__ == "__main__":
    main()
