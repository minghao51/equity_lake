"""
Base strategy interface for backtesting.

This module provides the abstract base class for all trading strategies,
defining the lifecycle and interface that all strategies must implement.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    All strategies must inherit from this class and implement the required
    methods. The strategy lifecycle follows this pattern:

    1. __init__(): Set up strategy parameters
    2. initialize(data): Pre-compute indicators, set up state
    3. generate_signals(data): Return entry/exit signals
    4. finalize(): Cleanup and final calculations

    Attributes:
        params: Dictionary of strategy parameters
        name: Strategy name (defaults to class name)
        indicators: Dictionary of computed indicators (set in initialize())

    Example:
        >>> class MyStrategy(BaseStrategy):
        ...     def initialize(self, data):
        ...         # Compute indicators
        ...         self.indicators['sma_20'] = data['close'].rolling(20).mean()
        ...
        ...     def generate_signals(self, data):
        ...         # Generate entry/exit signals
        ...         entries = data['close'] > self.indicators['sma_20']
        ...         exits = data['close'] < self.indicators['sma_20']
        ...         return pd.DataFrame({'entry': entries, 'exit': exits})
    """

    def __init__(self, params: Optional[Dict[str, Any]] = None):
        """
        Initialize the strategy.

        Args:
            params: Dictionary of strategy parameters
        """
        self.params = params or {}
        self.name = self.__class__.__name__
        self.indicators: Dict[str, Any] = {}

        logger.debug(
            "Strategy initialized",
            strategy=self.name,
            params=self.params,
        )

    @abstractmethod
    def initialize(self, data: pd.DataFrame) -> None:
        """
        Initialize the strategy with historical data.

        This method is called once before backtesting begins. Use it to:
        - Compute technical indicators
        - Set up any required state variables
        - Validate data quality

        Args:
            data: Historical OHLCV data (wide format)

        Example:
            >>> def initialize(self, data):
            ...     # Compute moving averages
            ...     close = data.xs('close', level='field', axis=1)
            ...     self.indicators['sma_fast'] = close.rolling(10).mean()
            ...     self.indicators['sma_slow'] = close.rolling(30).mean()
        """
        pass

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """
        Generate entry and exit signals.

        This method is called during backtesting to generate trading signals.
        It should return a DataFrame with boolean columns indicating when to
        enter and exit positions.

        Args:
            data: Historical OHLCV data (wide format)

        Returns:
            DataFrame with the following columns:
            - entry: Boolean, True when to enter long position
            - exit: Boolean, True when to exit long position
            - Optional: size (float), position size (default: 1.0)

            Index should be dates (datetime or Timestamp)

        Example:
            >>> def generate_signals(self, data):
            ...     # Simple moving average crossover
            ...     crossover = (
            ...         self.indicators['sma_fast'] > self.indicators['sma_slow']
            ...     )
            ...     entries = crossover & ~crossover.shift(1)
            ...     exits = ~crossover & crossover.shift(1)
            ...
            ...     return pd.DataFrame({
            ...         'entry': entries.any(axis=1),
            ...         'exit': exits.any(axis=1),
            ...     })
        """
        pass

    def finalize(self) -> None:
        """
        Finalize the strategy after backtesting.

        Override this method to perform cleanup or final calculations.
        Called once after backtesting completes.

        Example:
            >>> def finalize(self):
            ...     # Log performance summary
            ...     logger.info("Strategy completed", trades=self.total_trades)
        """
        pass

    def validate_params(self) -> bool:
        """
        Validate strategy parameters.

        Override this method to add custom parameter validation.
        Called during strategy initialization.

        Returns:
            True if parameters are valid, False otherwise

        Example:
            >>> def validate_params(self):
            ...     if 'lookback' in self.params:
            ...         return self.params['lookback'] > 0
            ...     return True
        """
        return True

    def get_param(self, key: str, default: Any = None) -> Any:
        """
        Get a strategy parameter with optional default.

        Args:
            key: Parameter name
            default: Default value if not found

        Returns:
            Parameter value or default
        """
        return self.params.get(key, default)

    def set_param(self, key: str, value: Any) -> None:
        """
        Set a strategy parameter.

        Args:
            key: Parameter name
            value: Parameter value
        """
        self.params[key] = value
        logger.debug("Parameter updated", key=key, value=value, strategy=self.name)

    def __repr__(self) -> str:
        """String representation of strategy."""
        params_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({params_str})"


__all__ = ["BaseStrategy"]
