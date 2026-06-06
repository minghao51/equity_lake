#!/usr/bin/env python3
"""
News Ingestion CLI for US Equities

Fetches financial news from Finnhub API with sentiment analysis.

Usage:
    equity-news --date 2024-12-01
    equity-news --date 2024-12-01 --tickers AAPL,GOOGL,MSFT
    equity-news --dry-run --verbose
"""

import argparse
import os
import sys

import structlog

from equity_lake.core.dates import resolve_trading_date
from equity_lake.core.logging import setup_structured_logging, timer
from equity_lake.core.paths import US_NEWS_DIR
from equity_lake.ingestion.writers import validate_schema, write_to_partitioned_parquet
from equity_lake.sources.news import FinnhubNewsFetcher

logger = structlog.get_logger()


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch financial news with sentiment analysis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch news for yesterday
  equity-news

  # Fetch news for specific date
  equity-news --date 2024-12-01

  # Fetch news for specific tickers
  equity-news --tickers AAPL,GOOGL,MSFT

  # Fetch with maximum articles per ticker
  equity-news --max-articles 100

  # Dry run (test without writing)
  equity-news --dry-run --verbose
        """,
    )

    parser.add_argument(
        "--date",
        type=str,
        help="Trading date (YYYY-MM-DD). Default: yesterday",
    )

    parser.add_argument(
        "--tickers",
        type=str,
        help='Comma-separated list of tickers (e.g., "AAPL,GOOGL,MSFT")',
    )

    parser.add_argument(
        "--max-articles",
        type=int,
        default=50,
        help="Maximum articles per ticker (default: 50)",
    )

    parser.add_argument(
        "--sentiment-method",
        type=str,
        default="vader",
        choices=["vader", "finbert"],
        help="Sentiment analysis method (default: vader)",
    )

    parser.add_argument(
        "--min-relevance",
        type=float,
        default=0.0,
        help="Minimum relevance score 0.0-1.0 (default: 0.0)",
    )

    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Maximum parallel workers (default: 1, sequential)",
    )

    parser.add_argument(
        "--api-key",
        type=str,
        help="Finnhub API key (default: from FINNHUB_API_KEY env var)",
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

    return parser.parse_args()


def validate_environment() -> bool:
    """Validate required environment variables."""
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        logger.error("FINNHUB_API_KEY not set in environment")
        logger.error("Get your free API key at: https://finnhub.io/")
        logger.error("Then export it: export FINNHUB_API_KEY=your_key_here")
        return False
    return True


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_structured_logging(level=log_level)

    # Validate environment
    if not args.api_key and not validate_environment():
        sys.exit(1)

    # Determine trading date
    trading_date = resolve_trading_date(args.date)

    logger.info(f"{'=' * 60}")
    logger.info(f"News Ingestion - {trading_date}")
    logger.info(f"{'=' * 60}")

    # Parse tickers
    tickers = None
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
        ticker_preview = ", ".join(tickers[:5])
        if len(tickers) > 5:
            ticker_preview += "..."
        logger.info(f"Tickers: {len(tickers)} specified ({ticker_preview})")
    else:
        logger.info("No tickers specified, use --tickers to fetch specific stocks")

    if args.dry_run:
        logger.info("🔍 DRY RUN MODE - No files will be written")

    # Ensure output directory exists
    US_NEWS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Initialize fetcher
        with timer("init_fetcher"):
            fetcher = FinnhubNewsFetcher(
                api_key=args.api_key,
                tickers=tickers,
                max_articles_per_ticker=args.max_articles,
                sentiment_method=args.sentiment_method,
                min_relevance=args.min_relevance,
                max_workers=args.max_workers,
            )

        # Fetch news
        with timer("fetch_news"):
            df = fetcher.fetch(trading_date)

        if df.empty:
            logger.warning("No news articles fetched")
            sys.exit(0)

        # Validate schema
        if not validate_schema(df, "us_news"):
            logger.error("Schema validation failed")
            sys.exit(1)

        # Log summary
        logger.info(f"\n{'=' * 60}")
        logger.info("Summary")
        logger.info(f"{'=' * 60}")
        logger.info(f"Total articles: {len(df)}")
        logger.info(f"Tickers: {df['ticker'].nunique()}")
        logger.info(f"Date range: {df['date'].min()} to {df['date'].max()}")

        # Sentiment distribution
        if "sentiment_label" in df.columns:
            sentiment_dist = df["sentiment_label"].value_counts()
            logger.info("Sentiment distribution:")
            for label, count in sentiment_dist.items():
                logger.info(f"  {label}: {count}")

        # Top sources
        if "source" in df.columns:
            top_sources = df["source"].value_counts().head(5)
            logger.info("Top sources:")
            for source, count in top_sources.items():
                logger.info(f"  {source}: {count}")

        # Write to Parquet
        with timer("write_parquet"):
            success = write_to_partitioned_parquet(
                df,
                "us_news",
                trading_date,
                dry_run=args.dry_run,
            )

        if success:
            logger.info("✅ News ingestion complete")
            if not args.dry_run:
                output_dir = US_NEWS_DIR / f"date={trading_date}"
                output_file = output_dir / f"{trading_date}.parquet"
                logger.info(f"   Output: {output_file}")
        else:
            logger.error("❌ Failed to write Parquet file")
            sys.exit(1)

    except Exception as e:
        logger.error(f"News ingestion failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
