"""Data models for signal scanning."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, InstrumentKey

logger = structlog.get_logger(__name__)


class _StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_values(cls, values: Any) -> Any:
        if isinstance(values, dict):
            null_keys = [key for key, value in values.items() if value is None]
            if null_keys:
                raise ValueError(f"Explicit null is not allowed for: {', '.join(null_keys)}")
        return values


class _StrategyConfig(_StrictConfigModel):
    name: str | None = None
    lookback_days: int | None = Field(default=None, ge=1)
    buy_threshold: float | None = Field(default=None, ge=0, le=1)
    sell_threshold: float | None = Field(default=None, ge=-1, le=0)

    @field_validator("name")
    @classmethod
    def strategy_name_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Strategy name cannot be blank")
        return value


class _BacktestConfig(_StrictConfigModel):
    enabled: bool | None = None
    strategies: list[_StrategyConfig] | None = None


class _SentimentConfig(_StrictConfigModel):
    enabled: bool | None = None
    buy_threshold: float | None = Field(default=None, ge=-1, le=1)
    sell_threshold: float | None = Field(default=None, ge=-1, le=1)
    min_articles: int | None = Field(default=None, ge=1)
    lookback_days: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def thresholds_are_ordered(self) -> _SentimentConfig:
        buy_threshold = self.buy_threshold if self.buy_threshold is not None else 0.5
        sell_threshold = self.sell_threshold if self.sell_threshold is not None else -0.3
        if sell_threshold >= buy_threshold:
            raise ValueError("sentiment.sell_threshold must be less than sentiment.buy_threshold")
        return self


class _MLConfig(_StrictConfigModel):
    enabled: bool | None = None
    model_dir: str | Path | None = None
    model_path: str | Path | None = None
    mode: Literal["v1_direction", "v2_meta_label"] | None = None
    horizon_days: int | None = Field(default=None, ge=1)
    buy_probability_threshold: float | None = Field(default=None, ge=0, le=1)
    sell_probability_threshold: float | None = Field(default=None, ge=0, le=1)
    min_confidence: float | None = Field(default=None, ge=0, le=100)
    vertical_barrier_days: int | None = Field(default=None, ge=1)
    pt_mult: float | None = Field(default=None, gt=0)
    sl_mult: float | None = Field(default=None, gt=0)
    embargo_days: int | None = Field(default=None, ge=0)
    meta_label_threshold: float | None = Field(default=None, ge=0, le=1)
    candidate_strategies: list[_StrategyConfig] | None = None

    @model_validator(mode="after")
    def validate_modes_and_candidate_rules(self) -> _MLConfig:
        if self.model_dir is not None and self.model_path is not None:
            raise ValueError("Specify only one of ml.model_dir and legacy ml.model_path")
        if self.candidate_strategies is not None and not self.candidate_strategies:
            raise ValueError("ml.candidate_strategies must not be empty when explicitly configured")
        default_buy_threshold = (self.min_confidence if self.min_confidence is not None else 60) / 100
        buy_threshold = self.buy_probability_threshold if self.buy_probability_threshold is not None else default_buy_threshold
        sell_threshold = self.sell_probability_threshold if self.sell_probability_threshold is not None else 1 - default_buy_threshold
        if sell_threshold > buy_threshold:
            raise ValueError("ml.sell_probability_threshold cannot exceed ml.buy_probability_threshold")
        return self


class _SignalConfigRoot(_StrictConfigModel):
    backtest: _BacktestConfig
    sentiment: _SentimentConfig
    ml: _MLConfig


class _WatchlistConfig(_StrictConfigModel):
    name: str = Field(min_length=1)
    description: str | None = None
    tickers: list[str] = Field(default_factory=list)
    groups: dict[str, list[str]] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("name")
    @classmethod
    def watchlist_name_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Watchlist name cannot be blank")
        return value

    @field_validator("tickers")
    @classmethod
    def ticker_symbols_are_valid(cls, value: list[str]) -> list[str]:
        if any(not ticker.strip() for ticker in value):
            raise ValueError("Watchlist ticker symbols cannot be blank")
        if len(value) != len(set(value)):
            raise ValueError("Watchlist tickers must be unique")
        return value

    @field_validator("groups")
    @classmethod
    def groups_have_valid_symbols(cls, value: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if value is not None:
            if any(not name.strip() for name in value):
                raise ValueError("Watchlist group names cannot be blank")
            if any(not ticker.strip() for tickers in value.values() for ticker in tickers):
                raise ValueError("Watchlist group ticker symbols cannot be blank")
        return value


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
    market: str | None = None
    mic: str | None = None
    identity_schema_version: int | None = None

    def __post_init__(self) -> None:
        """Validate score and preserve unknown legacy identity without guessing."""
        if not 0 <= self.confidence <= 100:
            raise ValueError(f"Confidence must be 0-100, got {self.confidence}")
        if (self.market is None) != (self.mic is None):
            raise ValueError("Signal market and MIC must both be present or both be unknown")
        if self.market is not None and self.mic is not None:
            identity = InstrumentKey(market=self.market, mic=self.mic, symbol=self.ticker)
            self.market, self.mic = identity.market, identity.mic
            if self.identity_schema_version not in {None, IDENTITY_SCHEMA_VERSION}:
                raise ValueError("Unsupported signal identity schema version")
            self.identity_schema_version = IDENTITY_SCHEMA_VERSION
        elif self.identity_schema_version is not None:
            raise ValueError("Unknown legacy signal identity cannot claim an identity schema version")


@dataclass
class Watchlist:
    """Portfolio/watchlist configuration."""

    name: str
    description: str | None = None
    tickers: list[str] = field(default_factory=list)
    groups: dict[str, list[str]] | None = None  # e.g., {"tech": ["AAPL"]}
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        raw: dict[str, Any] = {"name": self.name, "tickers": self.tickers}
        if self.description is not None:
            raw["description"] = self.description
        if self.groups is not None:
            raw["groups"] = self.groups
        if self.metadata is not None:
            raw["metadata"] = self.metadata
        try:
            validated = _WatchlistConfig.model_validate(raw)
        except ValidationError as exc:
            raise ValueError(f"Invalid watchlist: {exc}") from exc
        self.name = validated.name
        self.description = validated.description
        self.tickers = list(validated.tickers)
        self.groups = validated.groups
        self.metadata = validated.metadata
        # Preserve the established behavior: group membership extends the scan scope.
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
    """Signal generation configuration validated before generator construction."""

    backtest: dict[str, Any]
    sentiment: dict[str, Any]
    ml: dict[str, Any]

    def __post_init__(self) -> None:
        try:
            validated = _SignalConfigRoot.model_validate({"backtest": self.backtest, "sentiment": self.sentiment, "ml": self.ml})
        except ValidationError as exc:
            raise ValueError(f"Invalid signal configuration: {exc}") from exc
        self.backtest = validated.backtest.model_dump(exclude_unset=True)
        self.sentiment = validated.sentiment.model_dump(exclude_unset=True)
        self.ml = validated.ml.model_dump(exclude_unset=True)

    def is_generator_enabled(self, generator_name: str) -> bool:
        """Check if a signal generator is enabled."""
        config = getattr(self, generator_name, {})
        return cast(bool, config.get("enabled", False))


SignalOutcomeStatus = Literal["signal", "no_condition", "unavailable", "missing_model", "error", "not_scheduled"]


class SignalScanOutcome(BaseModel):
    """One explicit generator/instrument evaluation outcome."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    market: str
    mic: str
    identity_schema_version: Literal[1] = 1
    generator: str
    status: SignalOutcomeStatus
    detail: str | None = None

    @model_validator(mode="after")
    def identity_is_canonical(self) -> SignalScanOutcome:
        identity = InstrumentKey(market=self.market, mic=self.mic, symbol=self.ticker)
        if identity.market != self.market or identity.mic != self.mic:
            raise ValueError("scan outcome identity must be canonical")
        return self


class SignalScanReport(BaseModel):
    """Structured scan result and diagnostics, independent of rendered output."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    target_date: date
    watchlist: str
    tickers: list[str]
    instruments: list[InstrumentKey]
    generators: list[str]
    status: Literal["success", "partial", "failed", "not_scheduled"]
    outcomes: list[SignalScanOutcome]
    signals: list[Signal]
    issues: list[str] = Field(default_factory=list)

    @property
    def outcome_counts(self) -> dict[str, int]:
        """Counts by explicit generator/ticker outcome status."""
        counts: dict[str, int] = {}
        for outcome in self.outcomes:
            counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return counts


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
    market: str
    mic: str
    identity_schema_version: int

    @model_validator(mode="after")
    def validate_identity(self) -> SignalRecord:
        identity = InstrumentKey(market=self.market, mic=self.mic, symbol=self.ticker)
        if identity.market != self.market or identity.mic != self.mic or self.identity_schema_version != IDENTITY_SCHEMA_VERSION:
            raise ValueError("Signal history identity is missing, non-canonical, or has an unsupported schema version")
        return self

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
            "market": signal.market,
            "mic": signal.mic,
            "identity_schema_version": signal.identity_schema_version,
        }
        base_columns = {"ticker", "date", "signal_type", "action", "confidence", "reasoning", "market", "mic", "identity_schema_version"}
        json_fields = {"barrier_settings": "barrier_settings_json"}
        # Full threshold provenance is retained in the scan report/output. Keep
        # the established Delta history schema; the effective legacy threshold
        # and source remain visible in meta_label_threshold and reasoning.
        report_only_fields = {"threshold_used", "threshold_source"}
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
            if key in report_only_fields:
                continue
            if key in cls.model_fields:
                record[key] = value
            else:
                logger.warning("signal_metadata_key_dropped", key=key, signal_type=signal.signal_type)
        return cls.model_validate(record)

    def to_signal(self) -> Signal:
        """Reconstruct the in-memory :class:`Signal` (metadata re-expanded)."""
        metadata: dict[str, Any] = {}
        base_columns = {"ticker", "date", "signal_type", "action", "confidence", "reasoning", "market", "mic", "identity_schema_version"}
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
            market=self.market,
            mic=self.mic,
            identity_schema_version=self.identity_schema_version,
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
    "market": pl.Utf8(),
    "mic": pl.Utf8(),
    "identity_schema_version": pl.Int64(),
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
