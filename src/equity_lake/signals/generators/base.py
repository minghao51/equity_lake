"""Base class for signal generators."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Literal

from equity_lake.core.instruments import InstrumentKey
from equity_lake.signals.models import Signal


@dataclass(frozen=True)
class GenerationOutcome:
    """Explicit result for one generator/ticker evaluation."""

    status: Literal["signal", "no_condition", "unavailable", "missing_model", "error", "not_scheduled"]
    signal: Signal | None = None
    detail: str | None = None


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
        self.enabled = bool(config.get("enabled", True))

    @abstractmethod
    def generate(self, instrument: InstrumentKey, target_date: date) -> Signal | None:
        """Generate a signal for a single ticker on a given date.

        Args:
            ticker: Stock symbol
            target_date: Target date for signal generation

        Returns:
            Signal object if signal generated, None if no signal

        Raises:
            ValueError: If invalid input data
            RuntimeError: If generator fails (e.g., missing model)
        """
        pass

    def generate_outcome(self, instrument: InstrumentKey, target_date: date) -> GenerationOutcome:
        """Return an explicit outcome for one canonical instrument."""
        signal = self.generate(instrument, target_date)
        if signal is None:
            return GenerationOutcome(status="no_condition")
        return GenerationOutcome(status="signal", signal=signal)

    def is_enabled(self) -> bool:
        """Check if this generator is enabled."""
        return self.enabled
