#!/usr/bin/env python3
"""
CLI interface for backtesting strategies.

Usage:
    equity backtest --strategy sma_crossover --tickers AAPL,MSFT
    equity backtest --strategy sma_crossover --tickers AAPL,MSFT --engine vector
"""

import argparse
import sys
from datetime import date

import structlog

from equity_lake.backtesting import BacktestEngine, VectorBacktestEngine
from equity_lake.backtesting.strategy import (
    BBMeanReversionStrategy,
    CrossSectionalMomentumStrategy,
    SMACrossoverStrategy,
)

logger = structlog.get_logger()


def parse_arguments() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Backtest trading strategies",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--strategy",
        "-s",
        type=str,
        required=True,
        help="Strategy name (sma_crossover, momentum, mean_reversion)",
    )

    parser.add_argument(
        "--tickers",
        "-t",
        type=str,
        required=True,
        help="Comma-separated ticker symbols",
    )

    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )

    parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)",
    )

    parser.add_argument(
        "--initial-cash",
        type=float,
        default=100_000,
        help="Initial capital (default: 100000)",
    )

    parser.add_argument(
        "--engine",
        "-e",
        type=str,
        choices=["loop", "vector"],
        default="vector",
        help="Backtest engine: 'loop' (original) or 'vector' (vectorbt, 10-100x faster, default)",
    )

    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Use walk-forward validation",
    )

    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Output JSON path",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_arguments()

    # Parse tickers
    tickers = args.tickers.split(",")

    # Parse dates
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)

    # Select strategy
    strategy_map = {
        "sma_crossover": SMACrossoverStrategy,
        "momentum": CrossSectionalMomentumStrategy,
        "mean_reversion": BBMeanReversionStrategy,
    }

    if args.strategy not in strategy_map:
        logger.error(f"Unknown strategy: {args.strategy}")
        logger.error(f"Available: {', '.join(strategy_map.keys())}")
        sys.exit(1)

    strategy_class = strategy_map[args.strategy]
    strategy = strategy_class(params={})  # type: ignore[abstract]

    # Run backtest
    try:
        engine_class = VectorBacktestEngine if args.engine == "vector" else BacktestEngine

        if args.engine == "vector":
            logger.info("Using vectorized backtest engine (vectorbt)")
        else:
            logger.info("Using loop-based backtest engine")

        engine = engine_class(
            strategy=strategy,
            tickers=tickers,
            start_date=start_date,
            end_date=end_date,
            initial_cash=args.initial_cash,
        )

        result = engine.run()

        # Print results
        print(result.summary())

        # Save to file if requested
        if args.output:
            import json

            with open(args.output, "w") as f:
                json.dump(result.to_dict(), f, indent=2, default=str)
            logger.info(f"Results saved to {args.output}")

    except Exception as e:
        logger.error(f"Backtest failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
