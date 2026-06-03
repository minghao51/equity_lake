#!/usr/bin/env python3
"""
ML Pipeline Orchestrator - Runs ingestion → features → ML/AI

This module orchestrates the complete equity data pipeline:
1. Daily EOD data ingestion (multi-market)
2. Feature engineering (technical indicators, returns, volume)
3. ML inference (price forecasting)

Usage:
    # Full pipeline for yesterday
    uv run equity-pipeline

    # Full pipeline for specific date
    uv run equity-pipeline --date 2024-12-01

    # Custom tickers
    uv run equity-pipeline --tickers AAPL,GOOGL,MSFT

    # Skip ingestion (if data already exists)
    uv run equity-pipeline --skip-ingestion

    # Run only ML inference
    uv run equity-pipeline --skip-ingestion --skip-features

    # Dry run (test without writes)
    uv run equity-pipeline --dry-run --verbose
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import structlog

from equity_lake import (
    run_feature_stage,
    run_ingestion_stage,
    run_ml_inference_stage,
)
from equity_lake.config.settings import get_settings
from equity_lake.core.logging import correlation_context, setup_logging, timer
from equity_lake.core.paths import LOGS_DIR

# Logger configuration
logger = structlog.get_logger()
DEFAULT_SETTINGS = get_settings()


def configure_third_party_logging() -> None:
    """Keep third-party libraries from flooding pipeline output."""
    for logger_name in ("yfinance", "peewee", "urllib3", "curl_cffi"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


# =============================================================================
# Pipeline Orchestrator
# =============================================================================


class PipelineOrchestrator:
    """
    Orchestrates the complete ML pipeline:
    Ingestion → Feature Engineering → ML/AI Inference
    """

    def __init__(
        self,
        trading_date: date,
        tickers: list[str],
        markets: list[str],
        dry_run: bool = False,
        verbose: bool = False,
    ):
        """
        Initialize the pipeline orchestrator.

        Args:
            trading_date: Date to run pipeline for
            tickers: List of ticker symbols for feature engineering and ML
            markets: List of markets to ingest data from ('us', 'cn', 'hk_sg')
            dry_run: If True, simulate without writing data
            verbose: Enable verbose logging
        """
        self.trading_date = trading_date
        self.tickers = tickers
        self.markets = markets
        self.dry_run = dry_run
        self.verbose = verbose

        # Pipeline results
        self.results: dict[str, dict] = {}
        self.start_time = datetime.now()

        # Ensure directories exist
        LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # Pipeline Stages
    # -------------------------------------------------------------------------

    def run_ingestion(self) -> bool:
        """
        Stage 1: Daily EOD data ingestion.

        Fetches OHLCV data from multiple markets and writes to partitioned Parquet.
        """
        stage = "ingestion"
        logger.info(
            "pipeline_stage_started",
            stage=stage,
            date=str(self.trading_date),
            markets=self.markets,
        )

        try:
            with timer(f"{stage}_stage", markets=len(self.markets)):
                market_results = run_ingestion_stage(
                    trading_date=self.trading_date,
                    markets=self.markets,
                    dry_run=self.dry_run,
                )

            success = all(market_results.values())

            self.results[stage] = {
                "success": success,
                "market_results": market_results,
                "duration_seconds": (datetime.now() - self.start_time).total_seconds(),
            }

            if success:
                logger.info(
                    "pipeline_stage_completed",
                    stage=stage,
                    successful_markets=sum(market_results.values()),
                    duration_seconds=self.results[stage]["duration_seconds"],
                )
            else:
                logger.error(
                    "pipeline_stage_failed",
                    stage=stage,
                    failed_markets=[market for market, stage_success in market_results.items() if not stage_success],
                )

            return success

        except Exception as e:
            logger.error(f"{stage} failed with exception: {e}")
            self.results[stage] = {
                "success": False,
                "error": str(e),
                "duration_seconds": (datetime.now() - self.start_time).total_seconds(),
            }
            return False

    def run_feature_engineering(self) -> bool:
        """
        Stage 2: Feature engineering.

        Computes technical indicators, momentum features, volume features,
        and time-based features from raw OHLCV data.
        """
        stage = "feature_engineering"
        logger.info(
            "pipeline_stage_started",
            stage=stage,
            date=str(self.trading_date),
            tickers=len(self.tickers),
        )

        try:
            with timer(f"{stage}_stage", ticker_count=len(self.tickers)):
                features_df = run_feature_stage(
                    trading_date=self.trading_date,
                    tickers=self.tickers,
                )

            self.results[stage] = {
                "success": True,
                "rows_generated": len(features_df),
                "feature_count": len(features_df.columns),
                "duration_seconds": (datetime.now() - self.start_time).total_seconds(),
            }

            logger.info(
                "pipeline_stage_completed",
                stage=stage,
                features_generated=self.results[stage]["rows_generated"],
                feature_count=self.results[stage]["feature_count"],
                duration_seconds=self.results[stage]["duration_seconds"],
            )

            return True
        except Exception as e:
            logger.error(f"{stage} failed with exception: {e}")
            self.results[stage] = {
                "success": False,
                "error": str(e),
                "duration_seconds": (datetime.now() - self.start_time).total_seconds(),
            }
            return False

    def run_ml_inference(self) -> bool:
        """
        Stage 3: ML/AI inference.

        Runs XGBoost models to predict next-day price movements
        for all configured tickers.
        """
        stage = "ml_inference"
        logger.info(
            "pipeline_stage_started",
            stage=stage,
            date=str(self.trading_date),
            tickers=len(self.tickers),
        )

        try:
            with timer(f"{stage}_stage", ticker_count=len(self.tickers)):
                all_success, ticker_results = run_ml_inference_stage(
                    trading_date=self.trading_date,
                    tickers=self.tickers,
                )
        except Exception as e:
            logger.warning(f"ML inference failed with exception: {e}")
            self.results[stage] = {
                "success": False,
                "error": str(e),
                "duration_seconds": (datetime.now() - self.start_time).total_seconds(),
            }
            return False

        self.results[stage] = {
            "success": all_success,
            "ticker_results": ticker_results,
            "duration_seconds": (datetime.now() - self.start_time).total_seconds(),
        }

        if all_success:
            logger.info(
                "pipeline_stage_completed",
                stage=stage,
                predictions_made=len(self.tickers),
                duration_seconds=self.results[stage]["duration_seconds"],
            )
        else:
            success_count = sum(1 for r in ticker_results.values() if r.get("success"))
            logger.warning(
                "pipeline_stage_partial",
                stage=stage,
                successful=success_count,
                failed=len(self.tickers) - success_count,
            )

        return all_success

    # -------------------------------------------------------------------------
    # Pipeline Execution
    # -------------------------------------------------------------------------

    def run_pipeline(
        self,
        skip_ingestion: bool = False,
        skip_features: bool = False,
        skip_ml: bool = False,
        stop_on_error: bool = True,
    ) -> bool:
        """
        Run the complete pipeline.

        Args:
            skip_ingestion: Skip Stage 1 (ingestion)
            skip_features: Skip Stage 2 (feature engineering)
            skip_ml: Skip Stage 3 (ML inference)
            stop_on_error: Stop pipeline if any stage fails

        Returns:
            True if pipeline completed successfully, False otherwise
        """
        with correlation_context():  # Set correlation ID for this run
            logger.info(
                "pipeline_started",
                date=str(self.trading_date),
                markets=self.markets,
                tickers=len(self.tickers),
                dry_run=self.dry_run,
            )

            success = True

            # Stage 1: Ingestion
            if not skip_ingestion:
                if not self.run_ingestion():
                    if stop_on_error:
                        logger.error("Pipeline aborted: Ingestion failed")
                        return False
                    success = False
            else:
                logger.info("⏭️  Skipping ingestion stage")

            # Stage 2: Feature Engineering
            if not skip_features:
                if not self.run_feature_engineering():
                    if stop_on_error:
                        logger.error("Pipeline aborted: Feature engineering failed")
                        return False
                    success = False
            else:
                logger.info("⏭️  Skipping feature engineering stage")

            # Stage 3: ML Inference
            if not skip_ml:
                if not self.run_ml_inference():
                    # Continue even if ML fails (data is still valuable)
                    logger.warning("ML inference had failures")
                    success = False
            else:
                logger.info("⏭️  Skipping ML inference stage")

            # Pipeline summary
            total_duration = (datetime.now() - self.start_time).total_seconds()

            if success:
                logger.info(
                    "pipeline_completed_successfully",
                    duration_seconds=f"{total_duration:.2f}",
                    stages_completed=len([r for r in self.results.values() if r.get("success")]),
                )
            else:
                logger.warning(
                    "pipeline_completed_with_errors",
                    duration_seconds=f"{total_duration:.2f}",
                    failed_stages=len([r for r in self.results.values() if not r.get("success")]),
                )

            # Print summary
            self._print_summary()

            return success

    def _print_summary(self) -> None:
        """Print pipeline execution summary."""
        print("\n" + "=" * 70)
        print("PIPELINE EXECUTION SUMMARY")
        print("=" * 70)

        print(f"Date: {self.trading_date}")
        print(f"Markets: {', '.join(self.markets).upper()}")
        print(f"Tickers: {len(self.tickers)} ({', '.join(self.tickers[:5])}{'...' if len(self.tickers) > 5 else ''})")
        print(f"Mode: {'DRY RUN' if self.dry_run else 'LIVE'}")
        print(f"Duration: {(datetime.now() - self.start_time).total_seconds():.2f} seconds")
        print()

        for stage, result in self.results.items():
            status = "✅ SUCCESS" if result.get("success") else "❌ FAILED"
            duration = result.get("duration_seconds", 0)
            print(f"{stage.replace('_', ' ').title():<30} {status:<15} {duration:.2f}s")

        print("=" * 70 + "\n")

    def save_results(self, output_file: Path | None = None) -> None:
        """Save pipeline results to JSON file."""
        if output_file is None:
            output_file = LOGS_DIR / f"pipeline_results_{self.trading_date}.json"

        # Convert datetime objects to strings for JSON serialization
        results_serializable = {}
        for key, value in self.results.items():
            results_serializable[key] = value

        with open(output_file, "w") as f:
            json.dump(results_serializable, f, indent=2, default=str)

        logger.info(f"Results saved to {output_file}")


# =============================================================================
# CLI Interface
# =============================================================================


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="ML Pipeline Orchestrator - Ingestion → Features → ML/AI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full pipeline for yesterday
  uv run equity-pipeline

  # Full pipeline for specific date
  uv run equity-pipeline --date 2024-12-01

  # Custom tickers
  uv run equity-pipeline --tickers AAPL,GOOGL,MSFT --markets us

  # Skip ingestion (if data already exists)
  uv run equity-pipeline --skip-ingestion

  # Run only ML inference
  uv run equity-pipeline --skip-ingestion --skip-features

  # Dry run (test without writes)
  uv run equity-pipeline --dry-run --verbose

  # US markets only, specific tickers
  uv run equity-pipeline --markets us --tickers AAPL,MSFT,NVDA,TSLA
        """,
    )

    # Date arguments
    parser.add_argument("--date", type=str, help="Trading date (YYYY-MM-DD format). Default: yesterday")

    parser.add_argument(
        "--days-back",
        type=int,
        default=1,
        help="Days back from today (default: 1 = yesterday)",
    )

    # Market and ticker arguments
    parser.add_argument(
        "--markets",
        type=str,
        default=None,
        help="Comma-separated markets to ingest from settings.yaml",
    )

    parser.add_argument(
        "--tickers",
        type=str,
        default="AAPL,GOOGL,MSFT,NVDA,TSLA,META,AMZN,BRK-B,GOOG,AVGO",
        help="Comma-separated tickers for features/ML (default: top 10 US stocks)",
    )

    # Pipeline control arguments
    pipeline_group = parser.add_argument_group("Pipeline Control")

    pipeline_group.add_argument("--skip-ingestion", action="store_true", help="Skip Stage 1: Data ingestion")

    pipeline_group.add_argument("--skip-features", action="store_true", help="Skip Stage 2: Feature engineering")

    pipeline_group.add_argument("--skip-ml", action="store_true", help="Skip Stage 3: ML inference")

    pipeline_group.add_argument(
        "--stop-on-error",
        action="store_true",
        default=True,
        help="Stop pipeline if any stage fails (default: True)",
    )

    pipeline_group.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue pipeline even if stages fail (overrides --stop-on-error)",
    )

    # Execution arguments
    exec_group = parser.add_argument_group("Execution Options")

    exec_group.add_argument("--dry-run", action="store_true", help="Simulate pipeline without writing data")

    exec_group.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    exec_group.add_argument("--save-results", action="store_true", help="Save pipeline results to JSON file")

    return parser.parse_args()


def resolve_trading_date(
    explicit_date: str | None,
    days_back: int,
    today: date | None = None,
) -> date:
    """Resolve trading date from CLI inputs."""
    if explicit_date:
        return datetime.strptime(explicit_date, "%Y-%m-%d").date()

    if today is None:
        today = date.today()

    trading_date = today - timedelta(days=days_back)
    while trading_date.weekday() >= 5:  # Saturday=5, Sunday=6
        trading_date -= timedelta(days=1)

    return trading_date


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Setup logging
    log_level = "DEBUG" if args.verbose else "INFO"
    setup_logging(__name__, level=log_level, log_file="run_pipeline.log")
    configure_third_party_logging()

    # Determine trading date
    trading_date = resolve_trading_date(args.date, args.days_back)

    # Parse markets and tickers
    if args.markets is None:
        args.markets = ",".join(DEFAULT_SETTINGS.ingestion.default_markets)
    markets = [m.strip() for m in args.markets.split(",")]
    tickers = [t.strip() for t in args.tickers.split(",")]

    # Validate markets
    valid_markets = {"us", "cn", "hk_sg"}
    invalid_markets = set(markets) - valid_markets

    if invalid_markets:
        logger.error(f"Invalid markets: {invalid_markets}")
        logger.error(f"Valid markets: {valid_markets}")
        sys.exit(1)

    # Determine error handling
    stop_on_error = args.stop_on_error and not args.continue_on_error

    # Create orchestrator
    orchestrator = PipelineOrchestrator(
        trading_date=trading_date,
        tickers=tickers,
        markets=markets,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    # Run pipeline
    try:
        success = orchestrator.run_pipeline(
            skip_ingestion=args.skip_ingestion,
            skip_features=args.skip_features,
            skip_ml=args.skip_ml,
            stop_on_error=stop_on_error,
        )

        # Save results if requested
        if args.save_results:
            orchestrator.save_results()

        # Exit with appropriate code
        sys.exit(0 if success else 1)

    except KeyboardInterrupt:
        logger.warning("Pipeline interrupted by user")
        sys.exit(130)
    except Exception as e:
        logger.error(f"Pipeline failed with exception: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
