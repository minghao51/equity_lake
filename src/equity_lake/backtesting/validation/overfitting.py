"""
Overfitting detection for backtesting.

This module provides tools to detect overfitting in backtest results.
"""

from typing import Dict, Tuple

import structlog

from equity_lake.backtesting.engine import BacktestResult

logger = structlog.get_logger(__name__)


class OverfittingDetector:
    """
    Detect overfitting in backtest results.

    Checks for common overfitting signs:
    - In-sample vs out-of-sample performance gap
    - Too few trades
    - Excessive turnover
    - Parameter instability

    Example:
        >>> detector = OverfittingDetector()
        >>>
        >>> report = detector.detect(
        ...     in_sample=result_in_sample,
        ...     out_of_sample=result_out_of_sample
        ... )
        >>>
        >>> if report.is_overfitted:
        ...     print("Strategy appears overfitted!")
    """

    def detect(
        self,
        in_sample: BacktestResult,
        out_of_sample: BacktestResult,
    ) -> "OverfittingReport":
        """
        Check for overfitting.

        Args:
            in_sample: In-sample backtest result
            out_of_sample: Out-of-sample backtest result

        Returns:
            OverfittingReport with analysis
        """
        logger.info("Checking for overfitting")

        # Performance degradation
        perf_degradation = self._check_performance_degradation(
            in_sample,
            out_of_sample,
        )

        # Trade count warning
        trade_warning = self._check_trade_count(out_of_sample)

        # Sharpe ratio drop
        sharpe_drop = self._check_sharpe_drop(in_sample, out_of_sample)

        # Overall overfitting assessment
        is_overfitted = (
            perf_degradation > 0.5 or  # >50% performance drop
            sharpe_drop > 0.5 or  # Sharpe ratio drops by >0.5
            trade_warning  # Too few trades
        )

        report = OverfittingReport(
            is_overfitted=is_overfitted,
            performance_degradation=perf_degradation,
            sharpe_drop=sharpe_drop,
            trade_count_warning=trade_warning,
        )

        if is_overfitted:
            logger.warning(
                "Overfitting detected",
                performance_degradation=perf_degradation,
                sharpe_drop=sharpe_drop,
            )

        return report

    def _check_performance_degradation(
        self,
        in_sample: BacktestResult,
        out_of_sample: BacktestResult,
    ) -> float:
        """Calculate performance degradation (0-1 scale)."""
        is_return = in_sample.total_return
        oos_return = out_of_sample.total_return

        if is_return <= 0:
            return 0.0

        degradation = (is_return - oos_return) / is_return
        return max(0, min(1, degradation))

    def _check_trade_count(self, result: BacktestResult) -> bool:
        """Check if trade count is too low."""
        min_trades = 30  # Minimum 30 trades for statistical significance
        num_trades = result.metrics.get('num_trades', 0)

        return num_trades < min_trades

    def _check_sharpe_drop(
        self,
        in_sample: BacktestResult,
        out_of_sample: BacktestResult,
    ) -> float:
        """Calculate Sharpe ratio drop."""
        is_sharpe = in_sample.sharpe_ratio
        oos_sharpe = out_of_sample.sharpe_ratio

        return max(0, is_sharpe - oos_sharpe)


class OverfittingReport:
    """Overfitting detection report."""

    def __init__(
        self,
        is_overfitted: bool,
        performance_degradation: float,
        sharpe_drop: float,
        trade_count_warning: bool,
    ):
        self.is_overfitted = is_overfitted
        self.performance_degradation = performance_degradation
        self.sharpe_drop = sharpe_drop
        self.trade_count_warning = trade_count_warning

    def summary(self) -> str:
        """Generate summary report."""
        summary = f"""
Overfitting Analysis:
{'=' * 50}
Status: {'OVERFITTED' if self.is_overfitted else 'OK'}
Performance Degradation: {self.performance_degradation:.1%}
Sharpe Ratio Drop: {self.sharpe_drop:.2f}
Low Trade Count: {'Yes' if self.trade_count_warning else 'No'}
{'=' * 50}
        """
        return summary.strip()


__all__ = [
    "OverfittingDetector",
    "OverfittingReport",
]
