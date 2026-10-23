from abc import ABC, abstractmethod
from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)

INSTRUMENT_COLUMNS = ("market", "mic", "symbol")


def strategy_identity_columns(data: pl.DataFrame) -> list[str]:
    """Choose composite identity keys when present, retaining ticker-only compatibility."""
    return list(INSTRUMENT_COLUMNS) if set(INSTRUMENT_COLUMNS).issubset(data.columns) else ["ticker"]


def strategy_weight_columns(data: pl.DataFrame) -> list[str]:
    """Keys emitted by strategies and consumed by the engine's date join."""
    keys = ["date", *strategy_identity_columns(data)]
    if "ticker" in data.columns and "ticker" not in keys:
        keys.append("ticker")
    return keys


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    Lifecycle:
    1. __init__(): Set up strategy parameters
    2. initialize(data): Pre-compute indicators
    3. generate_weights(data): Return target weight column (0.0-1.0)
    4. finalize(): Cleanup
    """

    def __init__(self, params: dict[str, Any] | None = None):
        self.params = params or {}
        self.name = self.__class__.__name__
        self.indicators: dict[str, Any] = {}

        logger.debug(
            "Strategy initialized",
            strategy=self.name,
            params=self.params,
        )

    @abstractmethod
    def initialize(self, data: pl.DataFrame) -> None:
        """Initialize the strategy with historical data.

        Args:
            data: Long-format Polars DataFrame with columns:
                  date, ticker, open, high, low, close, volume
        """
        pass

    @abstractmethod
    def generate_weights(self, data: pl.DataFrame) -> pl.DataFrame:
        """Generate target portfolio weights.

        Args:
            data: Long-format Polars DataFrame.

        Returns:
            DataFrame with columns [date, ticker, weight] where weight
            is 0.0 (no position) to 1.0 (full allocation).
        """
        pass

    def finalize(self) -> None:  # noqa: B027
        """Cleanup after strategy execution. Override if needed."""
        pass

    def get_param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def __repr__(self) -> str:
        params_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({params_str})"


__all__ = ["BaseStrategy"]
