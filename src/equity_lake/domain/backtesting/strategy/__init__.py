"""
Trading strategies for backtesting.

This package contains all trading strategy implementations organized by category.
"""

from equity_lake.domain.backtesting.strategy.base import BaseStrategy
from equity_lake.domain.backtesting.strategy.mean_reversion import (
    BBMeanReversionStrategy,
    CombinedMeanReversionStrategy,
    RSIMeanReversionStrategy,
)

# Import all strategies
from equity_lake.domain.backtesting.strategy.momentum import (
    CrossSectionalMomentumStrategy,
    TimeSeriesMomentumStrategy,
)
from equity_lake.domain.backtesting.strategy.registry import (
    StrategyRegistry,
    get_strategy,
)
from equity_lake.domain.backtesting.strategy.trend_following import (
    AdaptiveTrendStrategy,
    DonchianBreakoutStrategy,
    MACDStrategy,
    SMACrossoverStrategy,
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
