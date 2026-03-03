"""Data models for signal scanning."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Literal, Optional


@dataclass
class Signal:
    """A single buy/sell/hold signal for a ticker."""

    ticker: str
    date: date
    signal_type: Literal["backtest", "sentiment", "ml"]
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float  # 0-100
    reasoning: str  # Human-readable explanation
    metadata: Dict[str, Any]  # Strategy-specific details

    def __post_init__(self):
        """Validate confidence score is in range."""
        if not 0 <= self.confidence <= 100:
            raise ValueError(f"Confidence must be 0-100, got {self.confidence}")


@dataclass
class Watchlist:
    """Portfolio/watchlist configuration."""

    name: str
    description: Optional[str] = None
    tickers: List[str] = field(default_factory=list)
    groups: Optional[Dict[str, List[str]]] = None  # e.g., {"tech": ["AAPL"]}
    metadata: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        # Ensure all tickers in groups are in main list
        if self.groups:
            for group_tickers in self.groups.values():
                for ticker in group_tickers:
                    if ticker not in self.tickers:
                        self.tickers.append(ticker)


@dataclass
class SignalConfig:
    """Signal generation configuration."""

    backtest: Dict[str, Any]
    sentiment: Dict[str, Any]
    ml: Dict[str, Any]
    aggregation: Optional[Dict[str, Any]] = None

    def is_generator_enabled(self, generator_name: str) -> bool:
        """Check if a signal generator is enabled."""
        config = getattr(self, generator_name, {})
        return config.get("enabled", False)
