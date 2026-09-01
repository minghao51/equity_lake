"""Base class and shared helpers for signal formatters."""

from abc import ABC, abstractmethod
from collections import Counter

from equity_lake.signals.models import Signal

#: Fixed report order for the per-action summary shared by all formatters.
ACTION_ORDER: tuple[str, ...] = ("BUY", "SELL", "HOLD")


def summarize(signals: list[Signal]) -> dict[str, int]:
    """Count signals per action in :data:`ACTION_ORDER` (missing actions are 0)."""
    counts: Counter[str] = Counter(signal.action for signal in signals)
    return {action: counts.get(action, 0) for action in ACTION_ORDER}


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
