"""
Backtesting module for trading strategies.

This module provides a comprehensive backtesting framework for testing
trading strategies on historical equity data.
"""

from equity_lake.domain.backtesting.data_loader import BacktestDataLoader
from equity_lake.domain.backtesting.engine import BacktestEngine, BacktestResult
from equity_lake.domain.backtesting.strategy import (
    BaseStrategy,
    StrategyRegistry,
    get_strategy,
)
from equity_lake.domain.backtesting.vector_engine import VectorBacktestEngine

__all__ = [
    "BacktestDataLoader",
    "BacktestEngine",
    "BacktestResult",
    "BaseStrategy",
    "StrategyRegistry",
    "VectorBacktestEngine",
    "get_strategy",
]
