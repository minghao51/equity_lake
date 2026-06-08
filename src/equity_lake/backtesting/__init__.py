"""
Backtesting module for trading strategies.

VectorBacktestEngine (vectorbt-backed) is the canonical engine.
"""

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy import (
    BaseStrategy,
    StrategyRegistry,
    get_strategy,
)
from equity_lake.backtesting.vector_engine import VectorBacktestEngine

__all__ = [
    "BacktestDataLoader",
    "BacktestResult",
    "BaseStrategy",
    "StrategyRegistry",
    "VectorBacktestEngine",
    "get_strategy",
]
