#!/usr/bin/env python3
"""
Test Data Generator for Equity EOD Pipeline

Generates realistic OHLCV (Open, High, Low, Close, Volume) test data
for development and testing purposes.

Features:
- Realistic price movements with trends, volatility, and occasional gaps
- Support for multiple markets (US, CN, HK, SG)
- Configurable date ranges and ticker sets
- Proper schema compliance with production data
- Hive-partitioned Parquet output

Generation itself is delegated to :mod:`equity_lake.devtools.seeding` (shared
with ``equity bootstrap sample`` and ``equity demo seed``); what is unique here
is the large per-market universes, the CLI knobs, and the Hive-partitioned
Parquet writer.

Safety: output goes to the auxiliary sandbox ``data/sandbox/test_data/<market>/``
by default — never into the canonical Delta lake directories (``data/lake/**``),
whose Delta tables must only be written by the canonical writer + validation
boundary. There is no flag to opt into writing the canonical lake; use
``equity pipeline`` for real ingestion or ``equity demo seed --lake`` for the
showcase lake.

Usage:
    uv run python -m equity_lake.devtools.test_data
    uv run python -m equity_lake.devtools.test_data --start-date 2023-01-01 --days 365
    uv run python -m equity_lake.devtools.test_data --markets us --num-tickers 50
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl
import structlog

from equity_lake.core.logging import setup_structured_logging
from equity_lake.core.paths import DATA_DIR
from equity_lake.devtools.seeding import TEST_DATA_TICKERS, OhlcvProfile, business_days, synthetic_ohlcv

logger = structlog.get_logger()

# Auxiliary output root — deliberately outside the canonical lake (data/lake/**).
TEST_DATA_SANDBOX_DIR = DATA_DIR / "sandbox" / "test_data"

# Probability of an extra +/-5% return shock (not exposed as a CLI flag).
GAP_PROBABILITY = 0.1


# =============================================================================
# Market Configuration
# =============================================================================

MARKET_CONFIGS = {
    "us_equity": {
        "output_dir": TEST_DATA_SANDBOX_DIR / "us_equity",
        "ticker_format": "uppercase",
        "tickers": TEST_DATA_TICKERS["us_equity"],
        "price_range": (10, 500),
        "volume_range": (1000000, 50000000),
    },
    "cn_ashare": {
        "output_dir": TEST_DATA_SANDBOX_DIR / "cn_ashare",
        "ticker_format": "numeric_6",
        "tickers": TEST_DATA_TICKERS["cn_ashare"],
        "price_range": (3, 200),
        "volume_range": (5000000, 100000000),
    },
    "hk_sg_equity": {
        "output_dir": TEST_DATA_SANDBOX_DIR / "hk_sg_equity",
        "ticker_format": "suffix",
        "tickers": TEST_DATA_TICKERS["hk_sg_equity"],
        "price_range": (1, 300),
        "volume_range": (1000000, 50000000),
    },
}


# =============================================================================
# Data Generation
# =============================================================================


def market_profile(market: str, volatility: float, trend_strength: float) -> OhlcvProfile:
    """Build the market's :class:`OhlcvProfile` from its configured ranges."""
    if market not in MARKET_CONFIGS:
        raise ValueError(f"Unknown market: {market}")
    config = MARKET_CONFIGS[market]
    return OhlcvProfile(
        price_range=cast(tuple[float, float], config["price_range"]),
        volume_range=cast(tuple[int, int], config["volume_range"]),
        drift=trend_strength,
        volatility=volatility,
        range_scale=volatility * 0.5,
        gap_probability=GAP_PROBABILITY,
    )


def generate_market_data(
    market: str,
    dates: list[date],
    *,
    volatility: float,
    trend_strength: float,
    rng: np.random.Generator,
    num_tickers: int | None = None,
) -> pl.DataFrame:
    """Generate test data for an entire market.

    Args:
        market: Market identifier ('us_equity', 'cn_ashare', 'hk_sg_equity')
        dates: List of trading dates
        volatility: Daily price volatility (standard deviation)
        trend_strength: Mean daily return
        rng: Shared random generator (deterministic per seed)
        num_tickers: Limit number of tickers (for faster generation)

    Returns:
        DataFrame with all tickers' data
    """
    profile = market_profile(market, volatility, trend_strength)
    tickers = cast(list[str], MARKET_CONFIGS[market]["tickers"])

    # Sample tickers if limit specified
    if num_tickers and num_tickers < len(tickers):
        tickers = rng.choice(tickers, num_tickers, replace=False).tolist()

    logger.info("generating_market_data", ticker_count=len(tickers), market=market)
    result = synthetic_ohlcv(tickers, dates, profile=profile, rng=rng)
    logger.info("market_data_generated", row_count=result.height, market=market)
    return result


# =============================================================================
# Data Writing
# =============================================================================


def write_partitioned_parquet(df: pl.DataFrame, output_dir: Path, date_column: str = "date") -> bool:
    """
    Write DataFrame to Hive-partitioned Parquet by date.

    Args:
        df: DataFrame to write
        output_dir: Base output directory
        date_column: Name of date column for partitioning

    Returns:
        True if successful
    """
    if df.is_empty():
        logger.warning("Empty DataFrame, skipping write")
        return False

    logger.info("writing_parquet", output_dir=str(output_dir))

    try:
        # Group by date and write each partition
        dates = df[date_column].unique().to_list()

        for i, trading_date in enumerate(dates):
            if (i + 1) % 50 == 0:
                logger.info("partition_progress", current=i + 1, total=len(dates))

            # Create partition directory
            partition_dir = output_dir / f"date={trading_date}"
            partition_dir.mkdir(parents=True, exist_ok=True)

            # Write Parquet file
            output_file = partition_dir / f"{trading_date}.parquet"

            # Skip if exists
            if output_file.exists():
                logger.debug("skipping_existing_file", file=str(output_file))
                continue

            # Filter data for this date; store the partition column as a timestamp.
            date_df = df.filter(pl.col(date_column) == trading_date).with_columns(pl.col(date_column).cast(pl.Datetime("us")))
            date_df.write_parquet(output_file, compression="snappy")

        logger.info("parquet_write_complete", partition_count=len(dates), output_dir=str(output_dir))
        return True

    except Exception as e:
        logger.error("parquet_write_failed", error=str(e))
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
  # Generate 1 year of test data for all markets (into data/sandbox/test_data/)
  uv run python -m equity_lake.devtools.test_data --days 365

  # Generate data for specific date range
  uv run python -m equity_lake.devtools.test_data --start-date 2023-01-01 --end-date 2024-12-31

  # Generate data for US market only
  uv run python -m equity_lake.devtools.test_data --markets us_equity

  # Generate smaller dataset for testing
  uv run python -m equity_lake.devtools.test_data --days 30 --num-tickers 20

  # Generate data with different volatility
  uv run python -m equity_lake.devtools.test_data --volatility 0.05 --trend 0.001
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


def main() -> int:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_structured_logging(level=log_level)

    # Determine date range
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date() if args.end_date else date.today()
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date() if args.start_date else end_date - timedelta(days=args.days)

    logger.info("test_data_generator_start")
    logger.info("test_data_date_range", start_date=str(start_date), end_date=str(end_date))

    # Generate trading dates
    trading_dates = business_days(start_date, end_date)
    logger.info("trading_days_count", count=len(trading_dates))

    # Parse markets
    markets = [m.strip() for m in args.markets.split(",")]
    valid_markets = set(MARKET_CONFIGS.keys())
    invalid = set(markets) - valid_markets

    if invalid:
        logger.error("invalid_markets", invalid=invalid)
        logger.error("valid_markets", valid=valid_markets)
        sys.exit(1)

    logger.info("markets", markets=markets)
    logger.info("max_tickers_per_market", count=args.num_tickers or "all")
    logger.info("volatility", value=args.volatility)
    logger.info("trend", value=args.trend)

    # One RNG for the whole run so each market continues the same stream.
    rng = np.random.default_rng(args.seed)

    # Generate data for each market
    success_count = 0
    for market in markets:
        logger.info("market_processing_start", market=market)

        try:
            df = generate_market_data(
                market,
                trading_dates,
                volatility=args.volatility,
                trend_strength=args.trend,
                rng=rng,
                num_tickers=args.num_tickers,
            )

            if df.is_empty():
                logger.warning("no_data_for_market", market=market)
                continue

            # Write to Parquet
            output_dir = cast(Path, MARKET_CONFIGS[market]["output_dir"])
            success = write_partitioned_parquet(df, output_dir)

            if success:
                success_count += 1

        except Exception as e:
            logger.error("market_generation_failed", market=market, error=str(e), exc_info=True)

    # Summary
    logger.info("generation_summary")
    logger.info("markets_processed", success=success_count, total=len(markets))

    if success_count == len(markets):
        logger.info("✅ All markets generated successfully")
        return 0
    else:
        logger.error("❌ Some markets failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
