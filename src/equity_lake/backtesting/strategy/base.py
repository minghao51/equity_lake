from abc import ABC, abstractmethod
from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger(__name__)


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

    def validate_params(self) -> bool:
        return True

    def get_param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)

    def set_param(self, key: str, value: Any) -> None:
        self.params[key] = value
        logger.debug("Parameter updated", key=key, value=value, strategy=self.name)

    def __repr__(self) -> str:
        params_str = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({params_str})"


__all__ = ["BaseStrategy"]
