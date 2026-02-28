"""
Trading strategies for backtesting.

This package contains all trading strategy implementations organized by category.
"""

from equity_lake.backtesting.strategy.base import BaseStrategy
from equity_lake.backtesting.strategy.registry import (
    StrategyRegistry,
    get_strategy,
)

# Import all strategies
from equity_lake.backtesting.strategy.momentum import (
    CrossSectionalMomentumStrategy,
    TimeSeriesMomentumStrategy,
)
from equity_lake.backtesting.strategy.mean_reversion import (
    BBMeanReversionStrategy,
    RSIMeanReversionStrategy,
    CombinedMeanReversionStrategy,
)
from equity_lake.backtesting.strategy.trend_following import (
    SMACrossoverStrategy,
    DonchianBreakoutStrategy,
    MACDStrategy,
    AdaptiveTrendStrategy,
)

__all__ = [
    "BaseStrategy",
    "StrategyRegistry",
    "get_strategy",
    # Momentum strategies
    "CrossSectionalMomentumStrategy",
    "TimeSeriesMomentumStrategy",
    # Mean reversion strategies
    "BBMeanReversionStrategy",
    "RSIMeanReversionStrategy",
    "CombinedMeanReversionStrategy",
    # Trend following strategies
    "SMACrossoverStrategy",
    "DonchianBreakoutStrategy",
    "MACDStrategy",
    "AdaptiveTrendStrategy",
]
