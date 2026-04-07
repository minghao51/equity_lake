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
    uv run equity-daily
    uv run equity-daily --date 2024-12-01
    uv run equity-daily --markets us,cn --dry-run
"""

import argparse
import sys
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from equity_lake.config import TickerConfig
from equity_lake.config.settings import get_settings
from equity_lake.core.dates import resolve_trading_date
from equity_lake.core.logging import correlation_context, timer
from equity_lake.core.runtime import (
    LAKE_DIR,
    get_project_config,
    setup_logging,
)
from equity_lake.fetch_macro import (
    MacroDataPipeline,
    write_macro_to_parquet,
)
from equity_lake.ingestion.filters import build_filters_from_args
from equity_lake.ingestion.gap_detection import (
    GapDetector,
    print_coverage_stats,
    print_gap_report,
)
from equity_lake.ingestion.parallel import (
    fetch_markets_parallel,
    summarize_results,
)
from equity_lake.ingestion.sources import (
    CNHybridFetcher,
    HKSGEquityFetcher,
    USEquityFetcher,
)
from equity_lake.ingestion.sources.base import MarketDataFetcher
from equity_lake.ingestion.types import MARKET_DIR_MAP, VALID_MARKETS
from equity_lake.ingestion.writers import validate_schema, write_to_partitioned_parquet

# Logger configuration - use structlog for structured logging
logger = structlog.get_logger()


# =============================================================================
# Main Pipeline
# =============================================================================


def fetch_market_data(
    market: str,
    trading_date: date,
    config: dict,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: list[str] | None = None,
) -> pd.DataFrame | None:
    """
    Fetch data for a specific market.

    Args:
        market: Market identifier ('us', 'cn', 'hk_sg')
        trading_date: Date to fetch
        config: Configuration dictionary
        ticker_config: TickerConfig instance
        filters: Filter dictionary for config-based selection
        explicit_tickers: Explicit ticker list (overrides config)

    Returns:
        DataFrame with fetched data or None
    """
    return fetch_market_data_with_config(
        market=market,
        trading_date=trading_date,
        project_config=config,
        ticker_config=ticker_config,
        filters=filters,
        explicit_tickers=explicit_tickers,
    )


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
  uv run equity-daily

  # Fetch specific date
  uv run equity-daily --date 2024-12-01

  # Fetch only US and China markets
  uv run equity-daily --markets us cn

  # Enable parallel fetching (3x faster)
  uv run equity-daily --parallel

  # Parallel fetching with custom worker count
  uv run equity-daily --parallel --max-workers 4

  # Filter by tags (blue-chip stocks only)
  uv run equity-daily --tags blue-chip

  # Filter by groups (FAANG stocks)
  uv run equity-daily --groups faang

  # Filter by sectors (technology and healthcare)
  uv run equity-daily --sectors Technology Healthcare

  # Filter by priority (priority 8+ only)
  uv run equity-daily --min-priority 8

  # Combine filters (tech stocks with high priority)
  uv run equity-daily --sectors Technology --min-priority 9

  # Explicit ticker list (overrides config)
  uv run equity-daily --tickers AAPL,GOOGL,MSFT --markets us

  # Use custom config file
  uv run equity-daily --config /path/to/custom_tickers.yaml

  # List available tickers in config
  uv run equity-daily --list-tickers

  # Dry run (no writes)
  uv run equity-daily --dry-run --verbose
        """,
    )

    # Basic arguments
    parser.add_argument(
        "--date",
        type=str,
        help="Trading date (YYYY-MM-DD). Default: yesterday",
    )

    parser.add_argument(
        "--markets",
        type=str,
        default=None,
        help="Comma-separated list of markets from settings.yaml",
    )

    parser.add_argument(
        "--macro",
        action="store_true",
        help="Also fetch macro indicators for gold ETF analysis",
    )

    parser.add_argument(
        "--config",
        type=str,
        help="Path to custom ticker config YAML file (default: config/tickers.yaml)",
    )

    # Ticker filtering arguments
    filter_group = parser.add_argument_group("Ticker Filtering (from config)")

    filter_group.add_argument(
        "--tickers",
        type=str,
        help="Comma-separated list of explicit tickers (overrides config)",
    )

    filter_group.add_argument(
        "--tags",
        type=str,
        help="Comma-separated list of tags to filter tickers (e.g., blue-chip,FAANG)",
    )

    filter_group.add_argument(
        "--sectors",
        type=str,
        nargs="+",
        help="Space-separated list of sectors to filter (e.g., Technology Finance)",
    )

    filter_group.add_argument(
        "--groups",
        type=str,
        help="Comma-separated list of predefined groups (e.g., faang,sp500_top_10)",
    )

    filter_group.add_argument(
        "--min-priority",
        type=int,
        choices=range(1, 11),
        metavar="1-10",
        help="Minimum priority level (1-10, higher = more important)",
    )

    filter_group.add_argument(
        "--match-all-tags",
        action="store_true",
        help="When using --tags, require ALL tags instead of ANY tag",
    )

    # Utility arguments
    parser.add_argument(
        "--list-tickers",
        action="store_true",
        help="List all available tickers from config and exit",
    )

    parser.add_argument(
        "--list-stats",
        action="store_true",
        help="Show config statistics and exit",
    )

    # Gap detection arguments
    gap_group = parser.add_argument_group("Gap Detection")

    gap_group.add_argument(
        "--detect-gaps",
        action="store_true",
        help="Detect and report missing data points (no fetching)",
    )

    gap_group.add_argument(
        "--coverage-stats",
        action="store_true",
        help="Show coverage statistics for all tickers",
    )

    gap_group.add_argument(
        "--days-back",
        type=int,
        default=90,
        help="Number of days to check for missing data (default: 90)",
    )

    gap_group.add_argument(
        "--include-weekends",
        action="store_true",
        help="Include weekends in gap detection (default: business days only)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip actual Parquet writes (for testing)",
    )

    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    parser.add_argument(
        "--parallel",
        "-p",
        action="store_true",
        help="Enable parallel fetching of multiple markets (3x speedup)",
    )

    parser.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum number of parallel workers (default: number of markets)",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = setup_logging(__name__, level=log_level, log_file="ingest_daily.log")

    # Load ticker configuration
    settings = get_settings()
    config_path = Path(args.config) if args.config else Path(settings.ingestion.ticker_config_path)
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
    trading_date = resolve_trading_date(args.date)

    logger.info(f"{'=' * 60}")
    logger.info(f"Daily EOD Data Ingestion - {trading_date}")
    logger.info(f"{'=' * 60}")

    # Log filters if applied
    if filters:
        logger.info("Filters applied:")
        for key, value in filters.items():
            logger.info(f"  - {key}: {value}")

    # Parse markets
    markets = [m.strip() for m in args.markets.split(",")]
    invalid = set(markets) - VALID_MARKETS

    if invalid:
        logger.error(f"Invalid markets: {invalid}")
        logger.error(f"Valid markets: {VALID_MARKETS}")
        sys.exit(1)

    # Add macro market if --macro flag is set
    if args.macro and "macro" not in markets:
        markets.append("macro")
        logger.info("Added 'macro' market for gold ETF analysis")

    logger.info(f"Markets to process: {markets}")

    if args.dry_run:
        logger.info("🔍 DRY RUN MODE - No files will be written")

    if args.parallel:
        logger.info("🚀 PARALLEL MODE - Fetching markets concurrently")

    # Run ingestion
    try:
        from equity_lake.ingestion import run_ingestion_job

        results = run_ingestion_job(
            trading_date=trading_date,
            markets=markets,
            dry_run=args.dry_run,
            ticker_config=ticker_config,
            filters=filters,
            explicit_tickers=args.tickers,
            parallel=args.parallel,
            max_workers=args.max_workers,
        )

        # Summary
        logger.info(f"\n{'=' * 60}")
        logger.info("Summary")
        logger.info(f"{'=' * 60}")

        for market, success in results.items():
            status = "✅ SUCCESS" if success else "❌ FAILED"
            logger.info(f"{market.upper()}: {status}")

        # Exit with error code if any failed
        if not all(results.values()):
            sys.exit(1)

    except Exception as e:
        logger.error(f"Ingestion failed: {e}", exc_info=True)
        sys.exit(1)


def list_tickers_command(ticker_config: TickerConfig, args: argparse.Namespace) -> None:
    """
    Handle --list-tickers command.

    Args:
        ticker_config: Loaded ticker configuration
        args: Parsed command-line arguments
    """
    print(f"\n{'=' * 80}")
    print(f"Tickers from: {ticker_config.config_path}")
    print(f"{'=' * 80}\n")

    # Get markets to list
    markets = [m.strip() for m in args.markets.split(",")] if args.markets else ticker_config.get_markets()

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
            print("\n  Active Tickers:")
            for ticker in sorted(active_tickers, key=lambda t: t.priority, reverse=True):
                tags_str = ", ".join(ticker.tags[:3]) if ticker.tags else "none"
                print(f"    {ticker.symbol:12} | {ticker.name:30} | {ticker.sector:20} | Prio: {ticker.priority} | {tags_str}")

            if inactive_tickers:
                print("\n  Inactive Tickers:")
                for ticker in sorted(inactive_tickers, key=lambda t: t.symbol):
                    print(f"    {ticker.symbol:12} | {ticker.name:30} | INACTIVE")


def list_stats_command(ticker_config: TickerConfig) -> None:
    """
    Handle --list-stats command.

    Args:
        ticker_config: Loaded ticker configuration
    """
    stats = ticker_config.get_stats()

    print(f"\n{'=' * 80}")
    print("Ticker Configuration Statistics")
    print(f"{'=' * 80}\n")

    print(f"Config Version: {stats.get('version', 'N/A')}")
    print(f"Total Markets: {stats.get('total_markets', 0)}")
    print(f"Total Groups: {stats.get('total_groups', 0)}")

    # Market statistics
    markets = stats.get("markets", {})
    if markets:
        print(f"\n{'Market':<10} {'Currency':<8} {'Total':<8} {'Active':<8} {'Inactive':<10} {'Exchanges':<30}")
        print("-" * 80)

        for market_name, market_stats in sorted(markets.items()):
            exchanges = ", ".join(market_stats.get("exchanges", [])[:3])
            if len(market_stats.get("exchanges", [])) > 3:
                exchanges += f" (+{len(market_stats.get('exchanges', [])) - 3})"

            print(
                f"{market_name.upper():<10} "
                f"{market_stats.get('currency', ''):<8} "
                f"{market_stats.get('total_tickers', 0):<8} "
                f"{market_stats.get('active_tickers', 0):<8} "
                f"{market_stats.get('inactive_tickers', 0):<10} "
                f"{exchanges:<30}"
            )

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
        markets = [m.strip() for m in args.markets.split(",")]
        # Map short names to full directory names
        market_map = {
            "us": "us_equity",
            "cn": "cn_ashare",
            "hk": "hk_sg_equity",
            "sg": "hk_sg_equity",
            "hk_sg": "hk_sg_equity",
        }
        markets = [market_map.get(m, m) for m in markets]
    else:
        markets = ["us_equity", "cn_ashare", "hk_sg_equity"]

    # Calculate date range
    end_date = date.today()
    start_date = end_date - timedelta(days=args.days_back)
    business_days_only = not args.include_weekends

    print(f"\n{'=' * 70}")
    print(f"Gap Detection: {start_date} to {end_date}")
    valid_markets = [m for m in markets if m is not None]
    print(f"Markets: {', '.join(valid_markets)}")
    print(f"Business days only: {business_days_only}")
    print(f"{'=' * 70}\n")

    for market in markets:
        if market is None:
            continue
        market_str = str(market)
        market_path = LAKE_DIR / market_str

        # Check if market exists
        if not market_path.exists():
            print(f"⚠️  No data directory found for {market}")
            continue

        # Get short market name for display
        market_short = market_str.replace("_equity", "").replace("_ashare", "")

        if args.coverage_stats:
            # Show coverage statistics
            print(f"\n{market_short.upper()} Coverage Statistics:")
            print("-" * 70)

            stats = detector.get_coverage_stats(market, start_date, end_date, business_days_only)

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
                business_days_only=business_days_only,
            )

            print(f"\n{market_short.upper()} Market:")
            print_gap_report(missing_dates, verbose=args.verbose)

    print(f"\n{'=' * 70}\n")


def run_daily_ingestion(
    trading_date: date,
    markets: list[str],
    dry_run: bool = False,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: str | None = None,
    parallel: bool = False,
    max_workers: int | None = None,
) -> dict[str, bool]:
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
        explicit_ticker_list = [t.strip() for t in explicit_tickers.split(",")]

    if parallel:
        # Parallel fetching mode
        with correlation_context():  # Set correlation ID for this run
            logger.info(
                "parallel_ingestion_mode",
                markets=markets,
                max_workers=max_workers or len(markets),
            )

            # Build fetch function map for parallel execution
            fetch_func_map: dict[str, tuple[Callable[[Any], Any], dict[Any, Any]]] = {}
            for market in markets:
                # Create a closure for each market
                def make_fetch_func(mkt: str, explicit_list: Any, config: Any, fltrs: Any) -> Callable[[Any], Any]:
                    def fetch_func(date: Any) -> Any:
                        return fetch_market_data_with_config(
                            mkt,
                            date,
                            ticker_config=config,
                            filters=fltrs,
                            explicit_tickers=explicit_list if mkt == "us" else None,
                        )

                    return fetch_func

                fetch_func_map[market] = (
                    make_fetch_func(market, explicit_ticker_list, ticker_config, filters),
                    {},
                )

            # Fetch all markets in parallel
            with timer("fetch_all_markets_parallel", mode="parallel"):
                fetch_results = fetch_markets_parallel(
                    markets=markets,
                    trading_date=trading_date,
                    fetch_func_map=fetch_func_map,
                    max_workers=max_workers,
                )

            # Process results
            for market, fetch_result in fetch_results.items():
                logger.info(f"\n{'=' * 60}")
                logger.info(f"Processing market: {market.upper()}")
                logger.info(f"{'=' * 60}")

                if not fetch_result.success:
                    logger.error(
                        f"{market} fetch failed: {fetch_result.error}",
                        duration_seconds=fetch_result.duration_seconds,
                    )
                    results[market] = False
                    continue

                df = fetch_result.data
                if df is None or df.empty:
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = False
                    continue

                # Write to Parquet
                market_dir = MARKET_DIR_MAP.get(market, market)

                with timer(f"write_{market}_parquet", market=market):
                    if market == "macro":
                        success = write_macro_to_parquet(df, trading_date, dry_run=dry_run)
                    else:
                        success = write_to_partitioned_parquet(df, market_dir, trading_date, dry_run=dry_run)

                results[market] = success

            # Log summary statistics
            summary = summarize_results(fetch_results)
            logger.info("parallel_ingestion_summary", **summary)

    else:
        # Sequential fetching mode (original behavior)
        for market in markets:
            logger.info(f"\n{'=' * 60}")
            logger.info(f"Processing market: {market.upper()}")
            logger.info(f"{'=' * 60}")

            try:
                # Fetch data
                with timer(f"fetch_{market}_data", market=market):
                    df = fetch_market_data(
                        market,
                        trading_date,
                        config=get_project_config(),
                        ticker_config=ticker_config,
                        filters=filters,
                        explicit_tickers=explicit_ticker_list if market == "us" else None,
                    )

                if df is None or df.empty:
                    logger.warning(f"No data fetched for {market}, skipping")
                    results[market] = False
                    continue

                # Write to Parquet
                market_dir = MARKET_DIR_MAP.get(market, market)

                with timer(f"write_{market}_parquet", market=market):
                    if market == "macro":
                        success = write_macro_to_parquet(df, trading_date, dry_run=dry_run)
                    else:
                        success = write_to_partitioned_parquet(df, market_dir, trading_date, dry_run=dry_run)

                results[market] = success

            except Exception as e:
                logger.error(f"Error processing {market}: {e}")
                results[market] = False

    return results


def fetch_market_data_with_config(
    market: str,
    trading_date: date,
    project_config: dict | None = None,
    ticker_config: TickerConfig | None = None,
    filters: dict | None = None,
    explicit_tickers: list[str] | None = None,
) -> pd.DataFrame | None:
    """
    Fetch data for a specific market with config support.

    Args:
        market: Market identifier ('us', 'cn', 'hk_sg')
        trading_date: Date to fetch
        project_config: Runtime configuration dictionary
        ticker_config: TickerConfig instance
        filters: Filter dictionary for config-based selection
        explicit_tickers: Explicit ticker list (overrides config)

    Returns:
        DataFrame with fetched data or None
    """
    runtime_config = project_config or get_project_config()
    retry_attempts: int = int(runtime_config.get("retry_attempts", 3))
    retry_delay: float = float(runtime_config.get("retry_delay", 1.0))

    if market == "us":
        fetcher: MarketDataFetcher = USEquityFetcher(
            tickers=explicit_tickers,
            retry_attempts=retry_attempts,
            retry_delay=retry_delay,
            ticker_config=ticker_config,
            filters=filters,
        )
    elif market == "cn":
        fetcher = CNHybridFetcher(
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
    elif market == "us_news":
        import os

        api_key = os.getenv("FINNHUB_API_KEY")
        if not api_key:
            logger.error("FINNHUB_API_KEY not set, cannot fetch news")
            return None

        from equity_lake.ingestion.sources.news import FinnhubNewsFetcher

        # Get top 100 tickers by priority if not explicitly specified
        if not explicit_tickers and ticker_config:
            all_tickers = ticker_config.get_tickers_for_market("us", active_only=True)
            explicit_tickers = all_tickers[:100] if all_tickers else None

        fetcher = FinnhubNewsFetcher(
            api_key=api_key,
            tickers=explicit_tickers,
            max_articles_per_ticker=50,
            sentiment_method="vader",
        )
    elif market == "us_social_sentiment":
        import os

        api_key = os.getenv("FINNHUB_API_KEY")
        if not api_key:
            logger.error("FINNHUB_API_KEY not set, cannot fetch social sentiment")
            return None

        from equity_lake.ingestion.sources.sentiment import (
            FinnhubSocialSentimentFetcher,
        )

        # Get top 100 tickers by priority if not explicitly specified
        if not explicit_tickers and ticker_config:
            all_tickers = ticker_config.get_tickers_for_market("us", active_only=True)
            explicit_tickers = all_tickers[:100] if all_tickers else None

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=api_key,
            tickers=explicit_tickers,
        )
    elif market == "macro":
        pipeline = MacroDataPipeline(config=runtime_config)
        df = pipeline.fetch_with_fallback(trading_date)
        if not df.empty:
            return df
        return None
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
