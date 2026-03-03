"""Base class for signal generators."""

from abc import ABC, abstractmethod
from datetime import date

from equity_lake.signals.models import Signal


class SignalGenerator(ABC):
    """Base class for all signal generators.

    Each generator (backtest, sentiment, ML) inherits from this and
    implements the generate() method.
    """

    def __init__(self, config: dict):
        """Initialize generator with configuration.

        Args:
            config: Generator-specific configuration dict
        """
        self.config = config
        self.enabled = config.get("enabled", True)

    @abstractmethod
    def generate(self, ticker: str, date: date) -> Signal | None:
        """Generate a signal for a single ticker on a given date.

        Args:
            ticker: Stock symbol
            date: Target date for signal generation

        Returns:
            Signal object if signal generated, None if no signal

        Raises:
            ValueError: If invalid input data
            RuntimeError: If generator fails (e.g., missing model)
        """
        pass

    def is_enabled(self) -> bool:
        """Check if this generator is enabled."""
        return self.enabled
