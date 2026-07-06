"""
Trading strategies for backtesting.

This package contains all trading strategy implementations organized by category.
"""

from equity_lake.backtesting.strategy.base import BaseStrategy
from equity_lake.backtesting.strategy.mean_reversion import BBMeanReversionStrategy
from equity_lake.backtesting.strategy.momentum import CrossSectionalMomentumStrategy
from equity_lake.backtesting.strategy.trend_following import SMACrossoverStrategy

__all__ = [
    "BaseStrategy",
    # Momentum strategies
    "CrossSectionalMomentumStrategy",
    # Mean reversion strategies
    "BBMeanReversionStrategy",
    # Trend following strategies
    "SMACrossoverStrategy",
]
