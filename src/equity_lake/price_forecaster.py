"""CLI wrapper for the package-backed price forecaster."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import polars as pl
import structlog

from equity_lake.core.logging import setup_logging, timer
from equity_lake.ml.forecasting import PriceForecaster

logger = structlog.get_logger()


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="XGBoost Price Forecaster")
    parser.add_argument(
        "--mode",
        type=str,
        choices=["train", "predict", "backtest"],
        required=True,
        help="Mode: train model, generate prediction, or backtest",
    )
    parser.add_argument("--ticker", type=str, required=True, help="Ticker symbol")
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD format)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD format)")
    parser.add_argument("--date", type=str, help="Single date for prediction")
    parser.add_argument(
        "--model-dir",
        type=str,
        default=None,
        help="Model directory (default: data/models/)",
    )
    parser.add_argument(
        "--tune",
        action="store_true",
        help="Perform hyperparameter tuning (train mode only)",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    return parser.parse_args()


def main() -> None:
    """Run the price forecaster CLI."""
    args = parse_args()
    setup_logging(
        level="DEBUG" if args.verbose else "INFO",
        log_file=Path("price_forecaster.log"),
    )

    forecaster = PriceForecaster(model_dir=args.model_dir)
    try:
        if args.mode == "train":
            if not args.start or not args.end:
                logger.error("--start and --end are required for training")
                sys.exit(1)

            start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
            end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
            with timer("model_training", ticker=args.ticker):
                forecaster.train_model(
                    ticker=args.ticker,
                    start_date=start_date,
                    end_date=end_date,
                    tune_hyperparams=args.tune,
                )
        elif args.mode == "predict":
            if not args.date:
                logger.error("--date is required for prediction")
                sys.exit(1)

            pred_date = datetime.strptime(args.date, "%Y-%m-%d").date()
            with timer("prediction_generation", ticker=args.ticker):
                result = forecaster.predict(ticker=args.ticker, date=pred_date)

            print(f"Ticker: {result['ticker']}")
            print(f"Date: {result['date']}")
            print(f"Prediction: {'UP' if result['prediction'] == 1 else 'DOWN'}")
            print(f"Probability: {result['probability']:.2%}")
            print(f"Model: {result['model_version']!s}")
        else:
            if not args.start or not args.end:
                logger.error("--start and --end are required for backtesting")
                sys.exit(1)

            start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
            end_date = datetime.strptime(args.end, "%Y-%m-%d").date()
            with timer("backtesting", ticker=args.ticker):
                results = forecaster.backtest(
                    ticker=args.ticker,
                    start_date=start_date,
                    end_date=end_date,
                )

            if not results.is_empty():
                results_path = forecaster.model_dir / f"{args.ticker}_backtest_results.csv"
                results.write_csv(results_path)
                accuracy = float(results.select((pl.col("prediction") == pl.col("actual")).mean()).item())
                print(f"Total Predictions: {len(results)}")
                print(f"Accuracy: {accuracy:.2%}")
                print(f"Results saved to: {results_path}")
    finally:
        forecaster.close()


if __name__ == "__main__":
    main()
