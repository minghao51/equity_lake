"""
Walk-forward validation for backtesting.

This module implements walk-forward analysis to prevent overfitting
and validate strategy robustness.
"""

from typing import List, Tuple

import pandas as pd
import structlog

from equity_lake.backtesting.engine import BacktestEngine, BacktestResult
from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class WalkForwardValidator:
    """
    Walk-forward validation for time series strategies.

    Implements rolling window validation:
    - Train on past data only (no look-ahead)
    - Test on future data
    - Roll window forward in time

    Parameters:
        train_size: Training window size in days (default: 252 = 1 year)
        test_size: Test window size in days (default: 63 = 3 months)
        step_size: Step size in days (default: 21 = 1 month)

    Example:
        >>> validator = WalkForwardValidator(
        ...     train_size=252,
        ...     test_size=63,
        ...     step_size=21
        ... )
        >>>
        >>> result = validator.validate(
        ...     strategy=strategy,
        ...     tickers=["AAPL", "MSFT"],
        ...     data=data
        ... )
    """

    def __init__(
        self,
        train_size: int = 252,
        test_size: int = 63,
        step_size: int = 21,
    ):
        self.train_size = train_size
        self.test_size = test_size
        self.step_size = step_size

    def validate(
        self,
        strategy: BaseStrategy,
        tickers: List[str],
        data: pd.DataFrame,
        initial_cash: float = 100_000.0,
    ) -> "WalkForwardResult":
        """
        Run walk-forward validation.

        Args:
            strategy: Strategy to validate
            tickers: List of tickers
            data: Historical price data
            initial_cash: Starting capital

        Returns:
            WalkForwardResult with aggregated performance
        """
        logger.info(
            "Starting walk-forward validation",
            train_size=self.train_size,
            test_size=self.test_size,
            step_size=self.step_size,
        )

        # Generate folds
        folds = self._generate_folds(data)

        logger.info(f"Generated {len(folds)} validation folds")

        # Run backtests for each fold
        results = []
        for i, (train_data, test_data) in enumerate(folds):
            logger.info(f"Running fold {i + 1}/{len(folds)}")

            # Initialize strategy on training data
            strategy.initialize(train_data)

            # Run backtest on test data
            engine = BacktestEngine(
                strategy=strategy,
                tickers=tickers,
                start_date=test_data.index.min(),
                end_date=test_data.index.max(),
                initial_cash=initial_cash,
            )

            try:
                result = engine.run()
                results.append(result)
            except Exception as e:
                logger.error(f"Fold {i + 1} failed", error=str(e))

        # Aggregate results
        wf_result = WalkForwardResult(
            folds=results,
            train_size=self.train_size,
            test_size=self.test_size,
            step_size=self.step_size,
        )

        logger.info(
            "Walk-forward validation completed",
            mean_sharpe=wf_result.mean_sharpe,
            std_sharpe=wf_result.std_sharpe,
        )

        return wf_result

    def _generate_folds(
        self,
        data: pd.DataFrame,
    ) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """Generate train/test folds."""
        folds = []

        start_idx = 0
        while True:
            # Calculate end indices
            train_end = start_idx + self.train_size
            test_end = train_end + self.test_size

            # Check if we have enough data
            if test_end > len(data):
                break

            # Split data
            train_data = data.iloc[start_idx:train_end]
            test_data = data.iloc[train_end:test_end]

            folds.append((train_data, test_data))

            # Move window forward
            start_idx += self.step_size

        return folds


class WalkForwardResult:
    """
    Walk-forward validation results.

    Aggregates results from multiple validation folds.
    """

    def __init__(
        self,
        folds: List[BacktestResult],
        train_size: int,
        test_size: int,
        step_size: int,
    ):
        self.folds = folds
        self.train_size = train_size
        self.test_size = test_size
        self.step_size = step_size

    @property
    def mean_sharpe(self) -> float:
        """Mean Sharpe ratio across folds."""
        if not self.folds:
            return 0.0
        return sum(f.metrics.get('sharpe_ratio', 0) for f in self.folds) / len(self.folds)

    @property
    def std_sharpe(self) -> float:
        """Standard deviation of Sharpe ratio."""
        if len(self.folds) < 2:
            return 0.0
        sharpe_values = [f.metrics.get('sharpe_ratio', 0) for f in self.folds]
        import statistics
        return statistics.stdev(sharpe_values)

    @property
    def stability_score(self) -> float:
        """
        Strategy stability score.

        Returns percentage of folds with positive Sharpe ratio.
        """
        if not self.folds:
            return 0.0

        positive_sharpe = sum(
            1 for f in self.folds
            if f.metrics.get('sharpe_ratio', 0) > 0
        )

        return positive_sharpe / len(self.folds)


__all__ = [
    "WalkForwardValidator",
    "WalkForwardResult",
]
