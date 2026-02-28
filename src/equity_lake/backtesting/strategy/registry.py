"""
Strategy registry for plugin-style strategy management.

This module provides a central registry for all trading strategies,
allowing dynamic strategy creation and configuration.
"""

from typing import Dict, List, Type, Optional

import structlog

from equity_lake.backtesting.strategy.base import BaseStrategy

logger = structlog.get_logger(__name__)


class StrategyRegistry:
    """
    Registry for managing trading strategies.

    This class implements a plugin architecture where strategies can be
    registered and then instantiated by name. This allows for:
    - Dynamic strategy loading from configuration files
    - Easy strategy comparison and testing
    - Parameterized strategy creation

    Example:
        >>> # Register a strategy
        >>> StrategyRegistry.register("sma_cross", SMACrossoverStrategy)
        >>>
        >>> # Create strategy with parameters
        >>> strategy = StrategyRegistry.create(
        ...     "sma_cross",
        ...     params={"fast_period": 10, "slow_period": 30}
        ... )
        >>>
        >>> # List all available strategies
        >>> print(StrategyRegistry.list_strategies())
        ['sma_cross', 'momentum', 'mean_reversion']
    """

    _strategies: Dict[str, Type[BaseStrategy]] = {}

    @classmethod
    def register(
        cls,
        name: str,
        strategy_class: Type[BaseStrategy],
        overwrite: bool = False,
    ) -> None:
        """
        Register a strategy class.

        Args:
            name: Unique name for the strategy
            strategy_class: Strategy class (must inherit from BaseStrategy)
            overwrite: Allow overwriting existing strategy (default: False)

        Raises:
            ValueError: If strategy already registered and overwrite=False
            TypeError: If strategy_class doesn't inherit from BaseStrategy

        Example:
            >>> class MyStrategy(BaseStrategy):
            ...     pass
            >>>
            >>> StrategyRegistry.register("my_strategy", MyStrategy)
        """
        if not overwrite and name in cls._strategies:
            raise ValueError(
                f"Strategy '{name}' already registered. "
                f"Use overwrite=True to replace it."
            )

        if not issubclass(strategy_class, BaseStrategy):
            raise TypeError(
                f"Strategy class must inherit from BaseStrategy, "
                f"got {strategy_class.__name__}"
            )

        cls._strategies[name] = strategy_class

        logger.info(
            "Strategy registered",
            name=name,
            strategy_class=strategy_class.__name__,
            overwrite=overwrite,
        )

    @classmethod
    def create(
        cls,
        name: str,
        params: Optional[Dict[str, object]] = None,
    ) -> BaseStrategy:
        """
        Create a strategy instance by name.

        Args:
            name: Registered strategy name
            params: Optional strategy parameters

        Returns:
            Strategy instance

        Raises:
            ValueError: If strategy name not found in registry

        Example:
            >>> strategy = StrategyRegistry.create(
            ...     "sma_cross",
            ...     params={"fast_period": 10, "slow_period": 30}
            ... )
        """
        if name not in cls._strategies:
            available = ", ".join(cls.list_strategies())
            raise ValueError(
                f"Unknown strategy: '{name}'. "
                f"Available strategies: {available}"
            )

        strategy_class = cls._strategies[name]
        strategy = strategy_class(params=params or {})

        logger.info(
            "Strategy created",
            name=name,
            params=params,
            strategy_instance=strategy.name,
        )

        return strategy

    @classmethod
    def list_strategies(cls) -> List[str]:
        """
        List all registered strategy names.

        Returns:
            List of strategy names

        Example:
            >>> strategies = StrategyRegistry.list_strategies()
            >>> print(f"Available strategies: {strategies}")
        """
        return list(cls._strategies.keys())

    @classmethod
    def get_strategy_class(cls, name: str) -> Type[BaseStrategy]:
        """
        Get the strategy class by name.

        Args:
            name: Registered strategy name

        Returns:
            Strategy class

        Raises:
            ValueError: If strategy name not found

        Example:
            >>> cls = StrategyRegistry.get_strategy_class("sma_cross")
            >>> print(cls.__name__)
            'SMACrossoverStrategy'
        """
        if name not in cls._strategies:
            raise ValueError(
                f"Unknown strategy: '{name}'. "
                f"Available: {', '.join(cls.list_strategies())}"
            )

        return cls._strategies[name]

    @classmethod
    def unregister(cls, name: str) -> None:
        """
        Unregister a strategy.

        Args:
            name: Strategy name to unregister

        Raises:
            ValueError: If strategy name not found

        Example:
            >>> StrategyRegistry.unregister("old_strategy")
        """
        if name not in cls._strategies:
            raise ValueError(
                f"Cannot unregister unknown strategy: '{name}'"
            )

        del cls._strategies[name]

        logger.info("Strategy unregistered", name=name)

    @classmethod
    def clear(cls) -> None:
        """
        Clear all registered strategies.

        This is primarily useful for testing.

        Example:
            >>> StrategyRegistry.clear()
            >>> assert len(StrategyRegistry.list_strategies()) == 0
        """
        cls._strategies.clear()
        logger.debug("All strategies unregistered")

    @classmethod
    def register_decorator(cls, name: str):
        """
        Decorator for registering strategies.

        Args:
            name: Strategy name

        Example:
            >>> @StrategyRegistry.register_decorator("my_strategy")
            >>> class MyStrategy(BaseStrategy):
            ...     def initialize(self, data):
            ...         pass
            ...     def generate_signals(self, data):
            ...         pass
        """
        def decorator(strategy_class: Type[BaseStrategy]):
            cls.register(name, strategy_class)
            return strategy_class

        return decorator


# Convenience function for common operations
def get_strategy(name: str, params: Optional[Dict[str, object]] = None) -> BaseStrategy:
    """
    Get a strategy instance by name.

    This is a convenience wrapper around StrategyRegistry.create().

    Args:
        name: Strategy name
        params: Optional parameters

    Returns:
        Strategy instance

    Example:
        >>> strategy = get_strategy("sma_cross", {"fast_period": 10})
    """
    return StrategyRegistry.create(name, params)


__all__ = [
    "StrategyRegistry",
    "get_strategy",
]
