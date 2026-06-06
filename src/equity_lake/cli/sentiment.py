#!/usr/bin/env python3
"""
Social Sentiment Ingestion CLI for US Equities

Fetches social sentiment (Reddit/Twitter) from Finnhub API.

Usage:
    equity-sentiment --date 2024-12-01
    equity-sentiment --date 2024-12-01 --tickers AAPL,GOOGL,MSFT
    equity-sentiment --dry-run --verbose
"""

import argparse
import os
import sys

import structlog

from equity_lake.core.dates import resolve_trading_date
from equity_lake.core.logging import setup_structured_logging, timer
from equity_lake.core.paths import US_SOCIAL_SENTIMENT_DIR
from equity_lake.ingestion.writers import validate_schema, write_to_partitioned_parquet
from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher

logger = structlog.get_logger()


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fetch social sentiment (Reddit/Twitter) from Finnhub API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch social sentiment for yesterday
  equity-sentiment

  # Fetch social sentiment for specific date
  equity-sentiment --date 2024-12-01

  # Fetch social sentiment for specific tickers
  equity-sentiment --tickers AAPL,GOOGL,MSFT

  # Fetch with parallel workers
  equity-sentiment --max-workers 4

  # Dry run (test without writing)
  equity-sentiment --dry-run --verbose
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
    logger.info(f"Social Sentiment Ingestion - {trading_date}")
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
    US_SOCIAL_SENTIMENT_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # Initialize fetcher
        with timer("init_fetcher"):
            fetcher = FinnhubSocialSentimentFetcher(
                api_key=args.api_key,
                tickers=tickers,
                max_workers=args.max_workers,
            )

        # Fetch social sentiment
        with timer("fetch_sentiment"):
            df = fetcher.fetch(trading_date)

        if df.empty:
            logger.warning("No social sentiment data fetched")
            sys.exit(0)

        # Validate schema
        if not validate_schema(df, "us_social_sentiment"):
            logger.error("Schema validation failed")
            sys.exit(1)

        # Log summary
        logger.info(f"\n{'=' * 60}")
        logger.info("Summary")
        logger.info(f"{'=' * 60}")
        logger.info(f"Total records: {len(df)}")
        logger.info(f"Tickers: {df['ticker'].nunique()}")
        logger.info(f"Date: {df['date'].min()}")

        # Source distribution
        if "source" in df.columns:
            source_dist = df["source"].value_counts()
            logger.info("Source distribution:")
            for source, count in source_dist.items():
                logger.info(f"  {source}: {count}")

        # Aggregate metrics
        if "mention_count" in df.columns:
            total_mentions = df["mention_count"].sum()
            avg_mentions = df["mention_count"].mean()
            logger.info(f"Total mentions: {int(total_mentions):,}")
            logger.info(f"Average mentions per ticker: {avg_mentions:.1f}")

        # Sentiment distribution
        if "score" in df.columns:
            avg_score = df["score"].mean()
            logger.info(f"Average sentiment score: {avg_score:.3f} (-1.0 to 1.0)")

        # Top mentioned stocks
        if "mention_count" in df.columns:
            top_mentions = df.groupby("ticker")["mention_count"].sum().nlargest(5)
            logger.info("Top mentioned stocks:")
            for ticker, mentions in top_mentions.items():
                logger.info(f"  {ticker}: {int(mentions):,} mentions")

        # Write to Parquet
        with timer("write_parquet"):
            success = write_to_partitioned_parquet(
                df,
                "us_social_sentiment",
                trading_date,
                dry_run=args.dry_run,
            )

        if success:
            logger.info("✅ Social sentiment ingestion complete")
            if not args.dry_run:
                output_dir = US_SOCIAL_SENTIMENT_DIR / f"date={trading_date}"
                output_file = output_dir / f"{trading_date}.parquet"
                logger.info(f"   Output: {output_file}")
        else:
            logger.error("❌ Failed to write Parquet file")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Social sentiment ingestion failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
