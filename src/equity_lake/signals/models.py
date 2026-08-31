"""Data models for signal scanning."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast

import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict, Field

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


class SignalRecord(BaseModel):
    """Closed write-boundary model for one persisted signal-history row.

    Every signal saved to the Delta-backed history (``data/signals/``, an
    auxiliary non-catalog artifact) goes through this model, mirroring the
    ``FindingCard`` convention. Metadata is flattened through an explicit
    whitelist so the Delta schema stays stable: nested dicts (e.g.
    ``barrier_settings``) are stored as JSON strings instead of struct columns,
    and metadata keys that collide with base columns (e.g. ``confidence``) are
    rejected rather than silently overwriting the base column. Unknown metadata
    keys are dropped with a warning — add them to the whitelist when intended.
    """

    model_config = ConfigDict(extra="forbid")

    ticker: str
    date: date
    signal_type: Literal["backtest", "sentiment", "ml"]
    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0, le=100)
    reasoning: str

    # --- flattened metadata whitelist (None = key absent for this signal) ---
    # backtest generator
    strategy: str | None = None
    lookback_days: int | None = None
    pct_from_sma: float | None = None
    price: float | None = None
    sma: float | None = None
    # sentiment generator
    sentiment_score: float | None = None
    article_count: int | None = None
    # ml / meta-label generators (confidence intentionally NOT here — it IS the base column)
    prediction: int | None = None
    probability: float | None = None
    horizon_days: int | None = None
    model_mode: str | None = None
    model_version: str | None = None
    # meta-label generator
    execution_probability: float | None = None
    candidate_action: str | None = None
    candidate_source: str | None = None
    meta_label_threshold: float | None = None
    barrier_settings_json: str | None = None

    @classmethod
    def from_signal(cls, signal: Signal) -> SignalRecord:
        """Build a record from a :class:`Signal`, whitelisting its metadata.

        Nested dict values are JSON-serialized into ``<key>_json`` fields;
        keys that duplicate base columns (``confidence``) are rejected; unknown
        keys are dropped with a warning.
        """
        record: dict[str, Any] = {
            "ticker": signal.ticker,
            "date": signal.date,
            "signal_type": signal.signal_type,
            "action": signal.action,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
        }
        base_columns = {"ticker", "date", "signal_type", "action", "confidence", "reasoning"}
        json_fields = {"barrier_settings": "barrier_settings_json"}
        for key, value in signal.metadata.items():
            if key in base_columns:
                raise ValueError(f"Signal metadata key {key!r} collides with a SignalRecord base column")
            if value is None:
                continue
            if key in json_fields:
                if isinstance(value, dict):
                    record[json_fields[key]] = json.dumps(value, sort_keys=True, default=str)
                else:
                    record[json_fields[key]] = str(value)
                continue
            if key in cls.model_fields:
                record[key] = value
            else:
                logger.warning("signal_metadata_key_dropped", key=key, signal_type=signal.signal_type)
        return cls.model_validate(record)

    def to_signal(self) -> Signal:
        """Reconstruct the in-memory :class:`Signal` (metadata re-expanded)."""
        metadata: dict[str, Any] = {}
        base_columns = {"ticker", "date", "signal_type", "action", "confidence", "reasoning"}
        json_fields = {"barrier_settings_json": "barrier_settings"}
        for key, value in self.model_dump().items():
            if value is None or key in base_columns:
                continue
            if key in json_fields:
                try:
                    metadata[json_fields[key]] = json.loads(value)
                except (TypeError, ValueError):
                    metadata[key] = value
                continue
            metadata[key] = value
        return Signal(
            ticker=self.ticker,
            date=self.date,
            signal_type=self.signal_type,
            action=self.action,
            confidence=self.confidence,
            reasoning=self.reasoning,
            metadata=metadata,
        )


#: Explicit, stable polars schema for SignalRecord rows — keeps the Delta table's
#: column set and dtypes fixed (all-None columns must not degrade to Null dtype,
#: which Delta cannot store).
SIGNAL_RECORD_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.Utf8(),
    "date": pl.Date(),
    "signal_type": pl.Utf8(),
    "action": pl.Utf8(),
    "confidence": pl.Float64(),
    "reasoning": pl.Utf8(),
    "strategy": pl.Utf8(),
    "lookback_days": pl.Int64(),
    "pct_from_sma": pl.Float64(),
    "price": pl.Float64(),
    "sma": pl.Float64(),
    "sentiment_score": pl.Float64(),
    "article_count": pl.Int64(),
    "prediction": pl.Int64(),
    "probability": pl.Float64(),
    "horizon_days": pl.Int64(),
    "model_mode": pl.Utf8(),
    "model_version": pl.Utf8(),
    "execution_probability": pl.Float64(),
    "candidate_action": pl.Utf8(),
    "candidate_source": pl.Utf8(),
    "meta_label_threshold": pl.Float64(),
    "barrier_settings_json": pl.Utf8(),
}
