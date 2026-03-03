"""Base class for signal formatters."""

from abc import ABC, abstractmethod
from typing import List

from equity_lake.signals.models import Signal


class SignalFormatter(ABC):
    """Base class for signal output formatters."""

    @abstractmethod
    def format(self, signals: List[Signal]) -> str:
        """Format signals for output.

        Args:
            signals: List of Signal objects

        Returns:
            Formatted string
        """
        pass
