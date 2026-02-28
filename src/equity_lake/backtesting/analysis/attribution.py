"""
Performance attribution for backtesting.

This module provides performance attribution analysis across
different dimensions (time, sector, trade type).
"""

from typing import Dict, List, Optional

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


class AttributionAnalyzer:
    """
    Performance attribution analyzer.

    Analyzes portfolio performance across different dimensions:
    - Time-based (monthly, quarterly, yearly)
    - Market regime (bull vs bear)
    - Trade type (winners vs losers)

    Example:
        >>> analyzer = AttributionAnalyzer()
        >>> attribution = analyzer.analyze(
        ...     equity_curve=equity_series,
        ...     trades=trades_df,
        ...     benchmark=benchmark_series
        ... )
    """

    def analyze(
        self,
        equity_curve: pd.Series,
        trades: Optional[pd.DataFrame] = None,
        benchmark: Optional[pd.Series] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Perform comprehensive attribution analysis.

        Args:
            equity_curve: Portfolio value over time
            trades: Optional trade history
            benchmark: Optional benchmark for comparison

        Returns:
            Dictionary with attribution DataFrames
        """
        results = {}

        # Time-based attribution
        results["monthly"] = self._monthly_attribution(equity_curve)
        results["yearly"] = self._yearly_attribution(equity_curve)

        # Trade attribution
        if trades is not None and not trades.empty:
            results["trades"] = self._trade_attribution(trades)

        # Benchmark comparison
        if benchmark is not None:
            results["benchmark_comparison"] = self._benchmark_comparison(
                equity_curve,
                benchmark
            )

        return results

    def _monthly_attribution(self, equity_curve: pd.Series) -> pd.DataFrame:
        """Calculate monthly performance attribution."""
        if equity_curve.empty:
            return pd.DataFrame()

        # Resample to month-end
        monthly_values = equity_curve.resample('M').last()

        # Calculate monthly returns
        monthly_returns = monthly_values.pct_change()

        # Create attribution DataFrame
        attribution = pd.DataFrame({
            'month': monthly_returns.index,
            'value': monthly_values.values,
            'return': monthly_returns.values,
            'cumulative_return': monthly_values.values / monthly_values.iloc[0] - 1,
        })

        return attribution

    def _yearly_attribution(self, equity_curve: pd.Series) -> pd.DataFrame:
        """Calculate yearly performance attribution."""
        if equity_curve.empty:
            return pd.DataFrame()

        # Resample to year-end
        yearly_values = equity_curve.resample('Y').last()

        # Calculate yearly returns
        yearly_returns = yearly_values.pct_change()

        attribution = pd.DataFrame({
            'year': yearly_values.index.year,
            'value': yearly_values.values,
            'return': yearly_returns.values,
        })

        return attribution

    def _trade_attribution(self, trades: pd.DataFrame) -> pd.DataFrame:
        """Analyze trades by performance."""
        if trades.empty or 'pnl' not in trades.columns:
            return pd.DataFrame()

        # Categorize trades
        winning_trades = trades[trades['pnl'] > 0]
        losing_trades = trades[trades['pnl'] <= 0]

        attribution = pd.DataFrame([
            {
                'category': 'Winners',
                'count': len(winning_trades),
                'total_pnl': winning_trades['pnl'].sum(),
                'avg_pnl': winning_trades['pnl'].mean() if len(winning_trades) > 0 else 0,
            },
            {
                'category': 'Losers',
                'count': len(losing_trades),
                'total_pnl': losing_trades['pnl'].sum(),
                'avg_pnl': losing_trades['pnl'].mean() if len(losing_trades) > 0 else 0,
            },
        ])

        return attribution

    def _benchmark_comparison(
        self,
        equity_curve: pd.Series,
        benchmark: pd.Series,
    ) -> pd.DataFrame:
        """Compare with benchmark."""
        # Align data
        aligned_equity, aligned_benchmark = equity_curve.align(benchmark, join='inner')

        if aligned_equity.empty:
            return pd.DataFrame()

        # Calculate returns
        strategy_returns = aligned_equity.pct_change().dropna()
        benchmark_returns = aligned_benchmark.pct_change().dropna()

        # Create comparison DataFrame
        comparison = pd.DataFrame({
            'strategy_value': aligned_equity.values,
            'benchmark_value': aligned_benchmark.values,
            'strategy_return': strategy_returns.values,
            'benchmark_return': benchmark_returns.values,
            'excess_return': (strategy_returns - benchmark_returns).values,
        }, index=strategy_returns.index)

        return comparison


__all__ = ["AttributionAnalyzer"]
