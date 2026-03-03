"""Base class for signal formatters."""

from abc import ABC, abstractmethod

from equity_lake.signals.models import Signal


class SignalFormatter(ABC):
    """Base class for signal output formatters."""

    @abstractmethod
    def format(self, signals: list[Signal]) -> str:
        """Format signals for output.

        Args:
            signals: List of Signal objects

        Returns:
            Formatted string
        """
        pass
