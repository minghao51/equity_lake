"""
Backtesting module for trading strategies.

VectorBacktestEngine (polars-backtest-backed) is the canonical engine.
"""

from equity_lake.backtesting.data_loader import BacktestDataLoader
from equity_lake.backtesting.engine import VectorBacktestEngine
from equity_lake.backtesting.result import BacktestResult
from equity_lake.backtesting.strategy import BaseStrategy

__all__ = [
    "BacktestDataLoader",
    "BacktestResult",
    "BaseStrategy",
    "VectorBacktestEngine",
]
