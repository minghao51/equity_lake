#!/usr/bin/env python3
"""
Test Data Generator for Equity EOD Pipeline

Generates realistic OHLCV (Open, High, Low, Close, Volume) test data
for development and testing purposes.

Features:
- Realistic price movements with trends and volatility
- Support for multiple markets (US, CN, HK, SG)
- Configurable date ranges and ticker sets
- Proper schema compliance with production data
- Hive-partitioned Parquet output

Usage:
    uv run equity-generate-test-data
    uv run equity-generate-test-data --start-date 2023-01-01 --days 365
    uv run equity-generate-test-data --markets us --num-tickers 50
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import structlog

from equity_lake.core.logging import setup_structured_logging
from equity_lake.core.paths import CN_ASHARE_DIR, HK_SG_EQUITY_DIR, US_EQUITY_DIR

logger = structlog.get_logger()


# =============================================================================
# Market Configuration
# =============================================================================

MARKET_CONFIGS = {
    "us_equity": {
        "output_dir": US_EQUITY_DIR,
        "ticker_format": "uppercase",
        "tickers": [
            "AAPL",
            "MSFT",
            "GOOGL",
            "AMZN",
            "NVDA",
            "META",
            "TSLA",
            "BRK-B",
            "JPM",
            "V",
            "JNJ",
            "WMT",
            "MA",
            "PG",
            "UNH",
            "HD",
            "CVX",
            "MRK",
            "KO",
            "PEP",
            "COST",
            "CRM",
            "NFLX",
            "AMD",
            "TMO",
            "LIN",
            "ABT",
            "ORCL",
            "ADBE",
            "CMCSA",
            "WFC",
            "COP",
            "QCOM",
            "INTC",
            "DHR",
            "VZ",
            "IBM",
            "GE",
            "DIS",
            "BA",
            "NKE",
            "CAT",
            "XOM",
            "CSCO",
        ],
        "price_range": (10, 500),
        "volume_range": (1000000, 50000000),
    },
    "cn_ashare": {
        "output_dir": CN_ASHARE_DIR,
        "ticker_format": "numeric_6",
        "tickers": [
            "600000",
            "600036",
            "601318",
            "601398",
            "601857",
            "601988",
            "601939",
            "601288",
            "601328",
            "601601",
            "601668",
            "601628",
            "601766",
            "601818",
            "601933",
            "601985",
            "601988",
            "602008",
            "000001",
            "000002",
            "000063",
            "000066",
            "000069",
            "000100",
            "000157",
            "000166",
            "000333",
            "000338",
            "000651",
            "000725",
            "000858",
            "000895",
            "002008",
            "002415",
            "002594",
            "002714",
        ],
        "price_range": (3, 200),
        "volume_range": (5000000, 100000000),
    },
    "hk_sg_equity": {
        "output_dir": HK_SG_EQUITY_DIR,
        "ticker_format": "suffix",
        "tickers": [
            "0700.HK",
            "9988.HK",
            "0941.HK",
            "1299.HK",
            "2318.HK",
            "0939.HK",
            "1398.HK",
            "0883.HK",
            "0857.HK",
            "1038.HK",
            "0027.HK",
            "0016.HK",
            "0005.HK",
            "0388.HK",
            "0011.HK",
            "D05.SI",
            "O39.SI",
            "U11.SI",
            "Z74.SI",
            "C6L.SI",
            "S68.SI",
            "V03.SI",
            "BS6.SI",
            "G13.SI",
            "S63.SI",
        ],
        "price_range": (1, 300),
        "volume_range": (1000000, 50000000),
    },
}


# =============================================================================
# Data Generation Engine
# =============================================================================


class TestDataGenerator:
    """Generate realistic OHLCV test data."""

    def __init__(
        self,
        seed: int = 42,
        volatility: float = 0.02,
        trend_strength: float = 0.0001,
        gap_probability: float = 0.1,
    ):
        """
        Initialize the data generator.

        Args:
            seed: Random seed for reproducibility
            volatility: Daily price volatility (standard deviation)
            trend_strength: Strength of upward trend (0 = no trend)
            gap_probability: Probability of price gaps (0-1)
        """
        self.seed = seed
        self.volatility = volatility
        self.trend_strength = trend_strength
        self.gap_probability = gap_probability
        np.random.seed(seed)

    def generate_price_series(self, start_price: float, num_days: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        Generate realistic price series using geometric Brownian motion.

        Args:
            start_price: Initial price
            num_days: Number of trading days

        Returns:
            Tuple of (open, high, low, close) arrays
        """
        # Generate daily returns with trend
        returns = np.random.normal(self.trend_strength, self.volatility, num_days)

        # Add occasional price gaps
        gaps = np.random.rand(num_days) < self.gap_probability
        returns[gaps] += np.random.choice([-0.05, 0.05], size=gaps.sum())

        # Calculate prices
        prices = start_price * np.exp(np.cumsum(returns))
        prices = np.concatenate([[start_price], prices[:-1]])

        # Generate OHLC from close prices
        opens = np.roll(prices, 1)
        opens[0] = start_price

        closes = prices.copy()

        # Generate intraday high/low
        daily_ranges = np.abs(np.random.normal(0, self.volatility * 0.5, num_days))
        highs = np.maximum(opens, closes) + daily_ranges
        lows = np.minimum(opens, closes) - daily_ranges

        # Ensure high >= open/close and low <= open/close
        highs = np.maximum.reduce([highs, opens, closes])
        lows = np.minimum.reduce([lows, opens, closes])

        return opens, highs, lows, closes

    def generate_volume(self, num_days: int, base_volume: int, volume_range: tuple[int, int]) -> np.ndarray:
        """
        Generate realistic volume series with random variation.

        Args:
            num_days: Number of days
            base_volume: Base volume level
            volume_range: (min, max) volume range

        Returns:
            Array of volume values
        """
        # Generate volume with lognormal distribution
        volume_std = np.log(volume_range[1] / volume_range[0]) / 4
        volume_mean = np.log(base_volume)

        volumes = np.random.lognormal(volume_mean, volume_std, num_days)

        # Clip to range
        volumes = np.clip(volumes, volume_range[0], volume_range[1])

        # Round to integers
        return volumes.astype(np.int64)

    def generate_ticker_data(
        self,
        ticker: str,
        dates: list[date],
        price_range: tuple[float, float],
        volume_range: tuple[int, int],
        price_override: float | None = None,
    ) -> pd.DataFrame:
        """
        Generate test data for a single ticker.

        Args:
            ticker: Ticker symbol
            dates: List of trading dates
            price_range: (min, max) price range
            volume_range: (min, max) volume range
            price_override: Override starting price

        Returns:
            DataFrame with OHLCV data
        """
        num_days = len(dates)

        # Determine starting price
        start_price = price_override or np.random.uniform(*price_range)

        # Generate price series
        opens, highs, lows, closes = self.generate_price_series(start_price, num_days)

        # Generate volume
        base_volume = int(np.random.uniform(*volume_range))
        volumes = self.generate_volume(num_days, base_volume, volume_range)

        # Create DataFrame
        df = pd.DataFrame(
            {
                "ticker": ticker,
                "date": dates,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": volumes,
                "adj_close": closes,  # Same as close for simplicity
            }
        )

        # Round prices to 2 decimal places
        price_cols = ["open", "high", "low", "close", "adj_close"]
        df[price_cols] = df[price_cols].round(2)

        # Ensure data quality
        df = self._ensure_data_quality(df)

        return df

    def _ensure_data_quality(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Ensure data quality and remove unrealistic values.

        Args:
            df: Input DataFrame

        Returns:
            Cleaned DataFrame
        """
        # Ensure high >= low
        df = df[df["high"] >= df["low"]]

        # Ensure high >= open, close
        df = df[df["high"] >= df["open"]]
        df = df[df["high"] >= df["close"]]

        # Ensure low <= open, close
        df = df[df["low"] <= df["open"]]
        df = df[df["low"] <= df["close"]]

        # Remove negative prices or volume
        df = df[df["close"] > 0]
        df = df[df["volume"] > 0]

        # Remove zero prices
        price_cols = ["open", "high", "low", "close", "adj_close"]
        for col in price_cols:
            df = df[df[col] > 0]

        return df

    def generate_market_data(
        self,
        market: str,
        tickers: list[str],
        dates: list[date],
        num_tickers: int | None = None,
    ) -> pd.DataFrame:
        """
        Generate test data for an entire market.

        Args:
            market: Market identifier ('us_equity', 'cn_ashare', 'hk_sg_equity')
            tickers: List of ticker symbols
            dates: List of trading dates
            num_tickers: Limit number of tickers (for faster generation)

        Returns:
            DataFrame with all tickers' data
        """
        if market not in MARKET_CONFIGS:
            raise ValueError(f"Unknown market: {market}")

        config = MARKET_CONFIGS[market]

        # Sample tickers if limit specified
        if num_tickers and num_tickers < len(tickers):
            tickers = np.random.choice(tickers, num_tickers, replace=False).tolist()

        logger.info(f"Generating data for {len(tickers)} tickers in {market}")

        price_range = cast(tuple[float, float], config["price_range"])
        volume_range = cast(tuple[int, int], config["volume_range"])

        # Generate data for each ticker
        df_list = []
        for i, ticker in enumerate(tickers):
            if (i + 1) % 10 == 0:
                logger.info(f"  Generated {i + 1}/{len(tickers)} tickers...")

            try:
                ticker_df = self.generate_ticker_data(ticker, dates, price_range, volume_range)
                df_list.append(ticker_df)
            except Exception as e:
                logger.warning(f"Failed to generate data for {ticker}: {e}")
                continue

        if not df_list:
            logger.error(f"No data generated for {market}")
            return pd.DataFrame()

        # Combine all tickers
        result = pd.concat(df_list, ignore_index=True)

        logger.info(f"✅ Generated {len(result)} rows for {market}")
        return result


# =============================================================================
# Data Writing
# =============================================================================


def write_partitioned_parquet(df: pd.DataFrame, output_dir: Path, date_column: str = "date") -> bool:
    """
    Write DataFrame to Hive-partitioned Parquet by date.

    Args:
        df: DataFrame to write
        output_dir: Base output directory
        date_column: Name of date column for partitioning

    Returns:
        True if successful
    """
    if df.empty:
        logger.warning("Empty DataFrame, skipping write")
        return False

    logger.info(f"Writing data to {output_dir}")

    try:
        # Group by date and write each partition
        dates = df[date_column].unique()

        for i, trading_date in enumerate(dates):
            if (i + 1) % 50 == 0:
                logger.info(f"  Written {i + 1}/{len(dates)} partitions...")

            # Filter data for this date
            date_df = df[df[date_column] == trading_date]

            # Create partition directory
            partition_dir = output_dir / f"date={trading_date}"
            partition_dir.mkdir(parents=True, exist_ok=True)

            # Write Parquet file
            output_file = partition_dir / f"{trading_date}.parquet"

            # Skip if exists
            if output_file.exists():
                logger.debug(f"Skipping existing file: {output_file}")
                continue

            # Convert date to datetime for Parquet
            date_df_write = date_df.copy()
            date_df_write[date_column] = pd.to_datetime(date_df_write[date_column])

            date_df_write.to_parquet(output_file, index=False, compression="snappy")

        logger.info(f"✅ Wrote {len(dates)} partitions to {output_dir}")
        return True

    except Exception as e:
        logger.error(f"Failed to write Parquet: {e}")
        return False


# =============================================================================
# CLI Interface
# =============================================================================


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate test OHLCV data for equity EOD pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate 1 year of test data for all markets
  uv run equity-generate-test-data --days 365

  # Generate data for specific date range
  uv run equity-generate-test-data --start-date 2023-01-01 --end-date 2024-12-31

  # Generate data for US market only
  uv run equity-generate-test-data --markets us_equity

  # Generate smaller dataset for testing
  uv run equity-generate-test-data --days 30 --num-tickers 20

  # Generate data with different volatility
  uv run equity-generate-test-data --volatility 0.05 --trend 0.001
        """,
    )

    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Start date (YYYY-MM-DD). Default: 365 days ago",
    )

    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="End date (YYYY-MM-DD). Default: today",
    )

    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=365,
        help="Number of trading days to generate (default: 365)",
    )

    parser.add_argument(
        "--markets",
        "-m",
        type=str,
        default="us_equity,cn_ashare,hk_sg_equity",
        help="Comma-separated list of markets (default: all)",
    )

    parser.add_argument(
        "--num-tickers",
        "-n",
        type=int,
        default=None,
        help="Limit number of tickers per market (default: all)",
    )

    parser.add_argument(
        "--volatility",
        "-v",
        type=float,
        default=0.02,
        help="Daily price volatility (default: 0.02)",
    )

    parser.add_argument(
        "--trend",
        "-t",
        type=float,
        default=0.0001,
        help="Upward trend strength (default: 0.0001)",
    )

    parser.add_argument(
        "--seed",
        "-s",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)",
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def generate_trading_dates(start_date: date, end_date: date) -> list[date]:
    """
    Generate list of trading dates (exclude weekends).

    Args:
        start_date: Start date
        end_date: End date

    Returns:
        List of trading dates (Monday-Friday only)
    """
    dates = []
    current = start_date

    while current <= end_date:
        # Exclude weekends (5 = Saturday, 6 = Sunday)
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)

    return dates


def main() -> int:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_structured_logging(level=log_level)

    # Determine date range
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date() if args.start_date else end_date - timedelta(days=args.days)

    logger.info(f"{'=' * 60}")
    logger.info("Test Data Generator for Equity EOD Pipeline")
    logger.info(f"{'=' * 60}")
    logger.info(f"Date range: {start_date} to {end_date}")

    # Generate trading dates
    trading_dates = generate_trading_dates(start_date, end_date)
    logger.info(f"Trading days: {len(trading_dates)}")

    # Parse markets
    markets = [m.strip() for m in args.markets.split(",")]
    valid_markets = set(MARKET_CONFIGS.keys())
    invalid = set(markets) - valid_markets

    if invalid:
        logger.error(f"Invalid markets: {invalid}")
        logger.error(f"Valid markets: {valid_markets}")
        sys.exit(1)

    logger.info(f"Markets: {markets}")
    logger.info(f"Max tickers per market: {args.num_tickers or 'all'}")
    logger.info(f"Volatility: {args.volatility}")
    logger.info(f"Trend: {args.trend}")

    # Initialize generator
    generator = TestDataGenerator(seed=args.seed, volatility=args.volatility, trend_strength=args.trend)

    # Generate data for each market
    success_count = 0
    for market in markets:
        logger.info(f"\n{'=' * 60}")
        logger.info(f"Processing market: {market}")
        logger.info(f"{'=' * 60}")

        try:
            config = MARKET_CONFIGS[market]
            tickers = cast(list[str], config["tickers"])

            # Generate data
            df = generator.generate_market_data(market, tickers, trading_dates, num_tickers=args.num_tickers)

            if df.empty:
                logger.warning(f"No data generated for {market}")
                continue

            # Write to Parquet
            output_dir = cast(Path, config["output_dir"])
            success = write_partitioned_parquet(df, output_dir)

            if success:
                success_count += 1

        except Exception as e:
            logger.error(f"Failed to generate {market} data: {e}", exc_info=True)

    # Summary
    logger.info(f"\n{'=' * 60}")
    logger.info("Summary")
    logger.info(f"{'=' * 60}")
    logger.info(f"Markets processed: {success_count}/{len(markets)}")

    if success_count == len(markets):
        logger.info("✅ All markets generated successfully")
        return 0
    else:
        logger.error("❌ Some markets failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
