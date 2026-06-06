"""Data models for signal scanning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class Signal:
    """A single buy/sell/hold signal for a ticker."""

    ticker: str
    date: date
    signal_type: Literal["backtest", "sentiment", "ml"]
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float  # 0-100
    reasoning: str  # Human-readable explanation
    metadata: dict[str, Any]  # Strategy-specific details

    def __post_init__(self) -> None:
        """Validate confidence score is in range."""
        if not 0 <= self.confidence <= 100:
            raise ValueError(f"Confidence must be 0-100, got {self.confidence}")


@dataclass
class Watchlist:
    """Portfolio/watchlist configuration."""

    name: str
    description: str | None = None
    tickers: list[str] = field(default_factory=list)
    groups: dict[str, list[str]] | None = None  # e.g., {"tech": ["AAPL"]}
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        # Ensure all tickers in groups are in main list
        if self.groups:
            for group_tickers in self.groups.values():
                for ticker in group_tickers:
                    if ticker not in self.tickers:
                        self.tickers.append(ticker)

    def validate_against_tickers(self, known_tickers: set[str]) -> list[str]:
        """Return tickers in this watchlist that are absent from known_tickers."""
        unknown = [t for t in self.tickers if t not in known_tickers]
        if unknown:
            logger.warning(
                "watchlist_tickers_not_in_config",
                unknown=unknown,
                message="These tickers have no configured data source",
            )
        return unknown


@dataclass
class SignalConfig:
    """Signal generation configuration."""

    backtest: dict[str, Any]
    sentiment: dict[str, Any]
    ml: dict[str, Any]
    aggregation: dict[str, Any] | None = None

    def is_generator_enabled(self, generator_name: str) -> bool:
        """Check if a signal generator is enabled."""
        config = getattr(self, generator_name, {})
        return cast(bool, config.get("enabled", False))
