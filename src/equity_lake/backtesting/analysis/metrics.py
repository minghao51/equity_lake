"""
Performance metrics for backtesting.

This module provides comprehensive performance metrics including
returns, risk metrics, and trading statistics.
"""

from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


class PerformanceMetrics:
    """
    Comprehensive performance metrics calculator.

    Computes various metrics for backtest results:
    - Return metrics (total return, CAGR, etc.)
    - Risk metrics (volatility, drawdown, etc.)
    - Risk-adjusted metrics (Sharpe, Sortino, etc.)
    - Trading metrics (win rate, profit factor, etc.)

    Example:
        >>> metrics = PerformanceMetrics()
        >>> result = metrics.compute(
        ...     equity_curve=equity_series,
        ...     trades=trades_dataframe,
        ...     benchmark=benchmark_series
        ... )
        >>> print(f"Sharpe Ratio: {result['sharpe_ratio']:.2f}")
    """

    def __init__(self, risk_free_rate: float = 0.04):
        """
        Initialize metrics calculator.

        Args:
            risk_free_rate: Annual risk-free rate (default: 4%)
        """
        self.risk_free_rate = risk_free_rate

    def compute(
        self,
        equity_curve: pd.Series,
        trades: Optional[pd.DataFrame] = None,
        benchmark: Optional[pd.Series] = None,
    ) -> Dict[str, float]:
        """
        Compute all performance metrics.

        Args:
            equity_curve: Portfolio value over time
            trades: Optional trade history DataFrame
            benchmark: Optional benchmark equity curve

        Returns:
            Dictionary with all computed metrics
        """
        if equity_curve.empty:
            logger.warning("Empty equity curve, returning empty metrics")
            return {}

        # Calculate returns
        returns = self._calculate_returns(equity_curve)

        metrics = {}

        # Return metrics
        metrics.update(self._calculate_return_metrics(equity_curve, returns))

        # Risk metrics
        metrics.update(self._calculate_risk_metrics(equity_curve, returns))

        # Risk-adjusted metrics
        metrics.update(self._calculate_risk_adjusted_metrics(returns))

        # Trading metrics
        if trades is not None and not trades.empty:
            metrics.update(self._calculate_trading_metrics(trades))

        # Benchmark metrics
        if benchmark is not None and not benchmark.empty:
            metrics.update(self._calculate_benchmark_metrics(returns, benchmark))

        # Additional metrics
        metrics.update(self._calculate_additional_metrics(equity_curve, returns))

        return metrics

    def _calculate_returns(self, equity_curve: pd.Series) -> pd.Series:
        """Calculate daily returns from equity curve."""
        return equity_curve.pct_change().dropna()

    def _calculate_return_metrics(
        self,
        equity_curve: pd.Series,
        returns: pd.Series,
    ) -> Dict[str, float]:
        """Calculate return-based metrics."""
        if equity_curve.empty or len(equity_curve) < 2:
            return {}

        initial_value = equity_curve.iloc[0]
        final_value = equity_curve.iloc[-1]

        # Total return
        total_return = (final_value / initial_value) - 1

        # CAGR
        days = (equity_curve.index[-1] - equity_curve.index[0]).days
        years = days / 365.25
        cagr = (final_value / initial_value) ** (1 / years) - 1 if years > 0 else 0

        # Daily/weekly/monthly returns
        daily_return = returns.mean()
        weekly_return = returns.resample('W').last().pct_change().mean()
        monthly_return = returns.resample('M').last().pct_change().mean()

        return {
            "total_return": total_return,
            "cagr": cagr,
            "daily_return_mean": daily_return,
            "weekly_return_mean": weekly_return,
            "monthly_return_mean": monthly_return,
            "final_value": final_value,
        }

    def _calculate_risk_metrics(
        self,
        equity_curve: pd.Series,
        returns: pd.Series,
    ) -> Dict[str, float]:
        """Calculate risk-based metrics."""
        if returns.empty:
            return {}

        # Volatility (annualized)
        volatility = returns.std() * np.sqrt(252)

        # Downside deviation
        negative_returns = returns[returns < 0]
        downside_deviation = negative_returns.std() * np.sqrt(252)

        # Maximum drawdown
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_drawdown = drawdown.min()

        # Average drawdown
        drawdown_periods = drawdown[drawdown < 0]
        avg_drawdown = drawdown_periods.mean() if len(drawdown_periods) > 0 else 0

        # Value at Risk (95%)
        var_95 = returns.quantile(0.05)

        # Conditional VaR (expected shortfall at 5%)
        cvar_95 = returns[returns <= var_95].mean() if len(returns[returns <= var_95]) > 0 else 0

        # Skewness and kurtosis
        skewness = returns.skew()
        kurtosis = returns.kurtosis()

        return {
            "volatility": volatility,
            "downside_deviation": downside_deviation,
            "max_drawdown": max_drawdown,
            "avg_drawdown": avg_drawdown,
            "var_95": var_95,
            "cvar_95": cvar_95,
            "skewness": skewness,
            "kurtosis": kurtosis,
        }

    def _calculate_risk_adjusted_metrics(
        self,
        returns: pd.Series,
    ) -> Dict[str, float]:
        """Calculate risk-adjusted return metrics."""
        if returns.empty:
            return {}

        # Get CAGR from return metrics
        # For simplicity, using annualized return
        annual_return = returns.mean() * 252
        annual_vol = returns.std() * np.sqrt(252)

        # Sharpe Ratio
        sharpe_ratio = (annual_return - self.risk_free_rate) / annual_vol if annual_vol > 0 else 0

        # Sortino Ratio
        negative_returns = returns[returns < 0]
        downside_dev = negative_returns.std() * np.sqrt(252)
        sortino_ratio = (annual_return - self.risk_free_rate) / downside_dev if downside_dev > 0 else 0

        # Calmar Ratio (CAGR / abs(max_drawdown))
        # Using simplified calculation
        cummax = returns.cumsum().cummax()
        drawdown = returns.cumsum() - cummax
        max_dd = drawdown.min()
        calmar_ratio = annual_return / abs(max_dd) if max_dd != 0 else 0

        return {
            "sharpe_ratio": sharpe_ratio,
            "sortino_ratio": sortino_ratio,
            "calmar_ratio": calmar_ratio,
        }

    def _calculate_trading_metrics(self, trades: pd.DataFrame) -> Dict[str, float]:
        """Calculate trading statistics."""
        if trades.empty:
            return {}

        metrics = {}

        # Number of trades
        metrics["num_trades"] = len(trades)

        # Win rate
        if "pnl" in trades.columns:
            winning_trades = trades[trades["pnl"] > 0]
            losing_trades = trades[trades["pnl"] < 0]

            metrics["win_rate"] = len(winning_trades) / len(trades) if len(trades) > 0 else 0

            # Average win/loss
            metrics["avg_win"] = winning_trades["pnl"].mean() if len(winning_trades) > 0 else 0
            metrics["avg_loss"] = losing_trades["pnl"].mean() if len(losing_trades) > 0 else 0

            # Profit factor
            total_profit = winning_trades["pnl"].sum() if len(winning_trades) > 0 else 0
            total_loss = abs(losing_trades["pnl"].sum()) if len(losing_trades) > 0 else 1
            metrics["profit_factor"] = total_profit / total_loss if total_loss > 0 else 0

            # Expectancy
            metrics["expectancy"] = trades["pnl"].mean()

        return metrics

    def _calculate_benchmark_metrics(
        self,
        returns: pd.Series,
        benchmark: pd.Series,
    ) -> Dict[str, float]:
        """Calculate benchmark-relative metrics."""
        # Align benchmark with strategy
        benchmark_returns = benchmark.pct_change().dropna()
        aligned_returns, aligned_benchmark = returns.align(benchmark_returns, join='inner')

        if aligned_returns.empty or aligned_benchmark.empty:
            return {}

        # Beta
        covariance = np.cov(aligned_returns, aligned_benchmark)[0, 1]
        benchmark_variance = aligned_benchmark.var()
        beta = covariance / benchmark_variance if benchmark_variance > 0 else 0

        # Alpha (annualized)
        strategy_return = aligned_returns.mean() * 252
        benchmark_return = aligned_benchmark.mean() * 252
        alpha = strategy_return - (self.risk_free_rate + beta * (benchmark_return - self.risk_free_rate))

        # Information Ratio
        excess_returns = aligned_returns - aligned_benchmark
        tracking_error = excess_returns.std() * np.sqrt(252)
        information_ratio = excess_returns.mean() * 252 / tracking_error if tracking_error > 0 else 0

        # Correlation
        correlation = aligned_returns.corr(aligned_benchmark)

        return {
            "alpha": alpha,
            "beta": beta,
            "information_ratio": information_ratio,
            "tracking_error": tracking_error,
            "correlation": correlation,
        }

    def _calculate_additional_metrics(
        self,
        equity_curve: pd.Series,
        returns: pd.Series,
    ) -> Dict[str, float]:
        """Calculate additional metrics."""
        if equity_curve.empty or returns.empty:
            return {}

        metrics = {}

        # Best/worst single day returns
        metrics["best_day"] = returns.max()
        metrics["worst_day"] = returns.min()

        # Average daily range (high - low approximation)
        metrics["avg_daily_range"] = returns.std() * 2

        # Recovery factor (total return / max drawdown)
        total_return = (equity_curve.iloc[-1] / equity_curve.iloc[0]) - 1
        cummax = equity_curve.cummax()
        drawdown = (equity_curve - cummax) / cummax
        max_dd = abs(drawdown.min())
        metrics["recovery_factor"] = total_return / max_dd if max_dd > 0 else 0

        # Number of trading days
        metrics["num_trading_days"] = len(equity_curve)

        return metrics


def compute_quick_metrics(
    equity_curve: pd.Series,
    risk_free_rate: float = 0.04,
) -> Dict[str, float]:
    """
    Compute quick summary metrics.

    Convenience function for basic metrics calculation.

    Args:
        equity_curve: Portfolio value over time
        risk_free_rate: Annual risk-free rate (default: 4%)

    Returns:
        Dictionary with key metrics

    Example:
        >>> equity = pd.Series([100000, 101000, 102000, 101500])
        >>> metrics = compute_quick_metrics(equity)
        >>> print(f"Sharpe: {metrics['sharpe_ratio']:.2f}")
    """
    calculator = PerformanceMetrics(risk_free_rate=risk_free_rate)
    return calculator.compute(equity_curve=equity_curve)


__all__ = [
    "PerformanceMetrics",
    "compute_quick_metrics",
]
