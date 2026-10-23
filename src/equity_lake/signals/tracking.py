"""Private canonical watchlists, exact-session candidates, and local outbox (ADR-0018).

This module never fetches market data. It reads only stored lake tables, requires
verified split-adjustment coverage, and stores product state in a private local
SQLite database outside the cataloged medallion lake.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import exchange_calendars as xc
import polars as pl
import structlog
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_none

from equity_lake.core.calendar import is_instrument_trading_day
from equity_lake.core.config_models import DEFAULT_TICKERS_PATH, TickerConfigRoot
from equity_lake.core.instruments import IDENTITY_SCHEMA_VERSION, InstrumentKey, normalize_identity_frame
from equity_lake.core.paths import LAKE_DIR, PRICE_MARKETS, PRIVATE_WATCHLIST_PATH, TRACKING_DB_PATH, canonical_market
from equity_lake.storage.delta import delta_table_path, read_delta
from equity_lake.storage.lake_reader import with_price_adjustment

logger = structlog.get_logger(__name__)

TRACKING_RULE_VERSION = "sma20-split-only-v1"
PRICE_BUY_DEVIATION = 0.02
PRICE_SELL_DEVIATION = -0.01
SMA_VALID_BARS = 20
MAX_DELIVERY_ATTEMPTS = 3
NEW_YORK = ZoneInfo("America/New_York")
_MIC_CALENDAR = {"XNYS": "XNYS", "XNAS": "XNAS", "XSHG": "XSHG", "XSHE": "XSHG", "XHKG": "XHKG", "XSES": "XSES", "XTKS": "JPX", "XKRX": "XKRX"}
_CONFIG_MARKETS = {"us": "us_equity", "cn": "cn_ashare", "hk": "hk_sg_equity", "sg": "hk_sg_equity", "jpx": "jpx_equity", "krx": "krx_equity"}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class PriceZone(_StrictModel):
    """Optional user-supplied bounds; omitted bounds are never invented."""

    low: float | None = Field(default=None, gt=0)
    high: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def ordered(self) -> PriceZone:
        if self.low is None and self.high is None:
            raise ValueError("price zone requires an explicit low or high bound")
        if self.low is not None and self.high is not None and self.low >= self.high:
            raise ValueError("price zone low must be below high")
        return self


class TrackedInstrument(_StrictModel):
    instrument: InstrumentKey
    held: bool = False
    groups: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=4000)
    price_zone: PriceZone | None = None

    @model_validator(mode="after")
    def clean_membership(self) -> TrackedInstrument:
        for label, values in (("group", self.groups), ("tag", self.tags)):
            if any(not value.strip() for value in values):
                raise ValueError(f"{label} names cannot be blank")
            if len(values) != len(set(values)):
                raise ValueError(f"{label} names must be unique")
        return self


class PrivateWatchlist(_StrictModel):
    schema_version: Literal[1] = 1
    name: str = "Private Watchlist"
    cooldown_minutes: int = Field(default=0, ge=0, le=1440)
    instruments: list[TrackedInstrument] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_instruments(self) -> PrivateWatchlist:
        keys = [item.instrument.tuple for item in self.instruments]
        if len(keys) != len(set(keys)):
            raise ValueError("watchlist instrument identities must be unique")
        if not self.name.strip():
            raise ValueError("watchlist name cannot be blank")
        return self


RuleStatus = Literal["signal", "no_condition", "unavailable", "missing_model", "error", "not_scheduled", "disabled"]
CandidateSource = Literal["price_sma20", "ml"]


class TrackingCandidate(_StrictModel):
    instrument: InstrumentKey
    session: date
    source: CandidateSource
    action: Literal["BUY", "SELL"]
    rule_version: str
    config_fingerprint: str = Field(default="", pattern=r"^[0-9a-f]{64}$|^$")
    config_provenance: Literal["default", "explicit", "synthetic"] = "default"
    reasoning: str
    price: float | None = None
    sma20: float | None = None
    deviation: float | None = None
    threshold: float | None = None
    model_version: str | None = None
    model_age_days: int | None = None
    threshold_source: str | None = None
    probability_score: float | None = Field(default=None, ge=0, le=1)


class TrackingRuleOutcome(_StrictModel):
    source: Literal["price_sma20", "sentiment", "ml"]
    status: RuleStatus
    detail: str | None = None
    rule_version: str | None = None
    config_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    config_provenance: Literal["default", "explicit", "synthetic"] | None = None
    model_version: str | None = None
    candidate: TrackingCandidate | None = None

    @model_validator(mode="after")
    def candidate_matches_status(self) -> TrackingRuleOutcome:
        if (self.status == "signal") != (self.candidate is not None):
            raise ValueError("only signal outcomes may contain a candidate")
        if self.candidate is not None and self.candidate.source != self.source:
            raise ValueError("candidate source must match its rule outcome")
        return self


class TrackingEvaluation(_StrictModel):
    instrument: InstrumentKey
    expected_session: date | None
    evaluated_at: datetime
    status: Literal["success", "partial", "failed", "not_scheduled"]
    rules: list[TrackingRuleOutcome]


class InstrumentTrackingState(_StrictModel):
    """Typed durable transition state for one canonical instrument/rule pair."""

    instrument: InstrumentKey
    source: CandidateSource
    rule_version: str = Field(min_length=1)
    active_action: Literal["BUY", "SELL"] | None
    transition: int = Field(ge=0)
    last_event_at: datetime | None
    updated_at: datetime

    @model_validator(mode="after")
    def timestamps_are_aware(self) -> InstrumentTrackingState:
        timestamps = [self.updated_at, self.last_event_at]
        if any(value is not None and (value.tzinfo is None or value.utcoffset() is None) for value in timestamps):
            raise ValueError("tracking state timestamps must be timezone-aware")
        return self


class TrackingEvent(_StrictModel):
    event_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    instrument: InstrumentKey
    session: date
    source: CandidateSource
    action: Literal["BUY", "SELL"]
    rule_version: str
    config_fingerprint: str = Field(default="", pattern=r"^[0-9a-f]{64}$|^$")
    config_provenance: Literal["default", "explicit", "synthetic"] = "synthetic"
    model_version: str | None = None
    transition: int = Field(ge=1)
    reason: str
    created_at: datetime


class OpportunityWebhookPayload(_StrictModel):
    """Explicit public notification projection; private notes/holding never enter it."""

    event_id: str
    market: str
    mic: str
    symbol: str
    session: date
    source: CandidateSource
    action: Literal["BUY", "SELL"]
    rule_version: str
    reason: str


class TrackingScanReport(_StrictModel):
    schema_version: Literal[1] = 1
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    watchlist: str
    config_fingerprint: str = Field(default="", pattern=r"^[0-9a-f]{64}$|^$")
    config_provenance: Literal["default", "explicit", "synthetic"] = "synthetic"
    started_at: datetime
    completed_at: datetime
    status: Literal["success", "partial", "failed", "not_scheduled"]
    evaluations: list[TrackingEvaluation]
    events: list[TrackingEvent] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)

    @property
    def outcome_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for evaluation in self.evaluations:
            for outcome in evaluation.rules:
                counts[outcome.status] = counts.get(outcome.status, 0) + 1
        return counts


class OutboxObservation(_StrictModel):
    event: TrackingEvent
    status: Literal["pending", "sent", "failed", "expired"]
    attempts: int = Field(ge=0, le=MAX_DELIVERY_ATTEMPTS)
    error_code: str | None = None
    updated_at: datetime


class ReadinessObservation(_StrictModel):
    instrument: InstrumentKey
    status: str
    expected_session: date | None
    last_evaluated_session: date | None = None
    detail: str | None = None
    candidate: TrackingCandidate | None = None


class PrivateTrackingSnapshot(_StrictModel):
    """Loopback-only projection. This intentionally contains private watchlist fields."""

    generated_at: datetime
    watchlist: PrivateWatchlist
    readiness: list[ReadinessObservation]
    recent_reports: list[TrackingScanReport]
    recent_events: list[OutboxObservation]
    conflicts: list[list[TrackingCandidate]] = Field(default_factory=list)
    last_successful_scan_at: datetime | None
    stale_run_warning: str | None
    history_status: Literal["available", "missing", "corrupt"]


class DeliverySummary(_StrictModel):
    status: Literal["disabled", "quiet_hours", "completed"]
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    expired: int = 0
    pending: int = 0


class DigestSummary(_StrictModel):
    generated_at: datetime
    opportunities: list[TrackingEvent]
    failures: list[TrackingRuleOutcome]
    stale_run_warning: str | None
    delivery_counts: dict[str, int]


class TrackingError(RuntimeError):
    """A local tracking workflow failed closed."""


class DeliveryAttemptError(RuntimeError):
    """A send failed; only the exception class/status is retained."""


class DeliveryRevalidationError(RuntimeError):
    """The live condition is no longer current and must not be delivered."""


def tracking_db_path(data_dir: Path | None = None) -> Path:
    """Return the auxiliary local state path, outside the lake/catalog."""
    return TRACKING_DB_PATH if data_dir is None else data_dir / "tracking" / "tracking.sqlite3"


def watchlist_path(path: Path | None = None) -> Path:
    """Default to managed private YAML; legacy config/watchlist.yaml is never auto-read."""
    return path or PRIVATE_WATCHLIST_PATH


def load_private_watchlist(path: Path | None = None, ticker_config_path: Path | None = None) -> PrivateWatchlist:
    """Read private YAML and revalidate each identity against exact active source configuration."""
    source = watchlist_path(path)
    try:
        mode = source.lstat().st_mode
    except FileNotFoundError:
        return PrivateWatchlist()
    except OSError as exc:
        raise TrackingError("private watchlist cannot be inspected") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise TrackingError("private watchlist path must be a regular non-symlink file")
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if raw is None:
            raise ValueError("empty watchlist document")
        watchlist = PrivateWatchlist.model_validate(raw)
        for item in watchlist.instruments:
            configured = _resolve_configured_instrument(item.instrument.symbol, item.instrument.market, item.instrument.mic, ticker_config_path)
            if configured != item.instrument:
                raise TrackingError(f"watchlist identity differs from exact active source configuration: {item.instrument.display_key}")
        return watchlist
    except Exception as exc:
        raise TrackingError(f"private watchlist is invalid ({type(exc).__name__})") from exc


@contextmanager
def _watchlist_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    lock_path = path.with_name(f".{path.name}.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(descriptor, "r+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except BaseException:
        raise


def _write_private_watchlist(path: Path, watchlist: PrivateWatchlist) -> None:
    validated = PrivateWatchlist.model_validate(watchlist.model_dump())
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            yaml.safe_dump(validated.model_dump(mode="json", exclude_none=True), stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _resolve_configured_instrument(
    symbol: str,
    market: str,
    mic: str | None,
    ticker_config_path: Path | None,
) -> InstrumentKey:
    """Resolve exactly one active source-config entry; ticker text is not identity."""
    canonical = canonical_market(market)
    config = TickerConfigRoot.from_yaml(ticker_config_path or DEFAULT_TICKERS_PATH)
    candidates: dict[tuple[str, str, str], InstrumentKey] = {}
    for configured_market, market_config in config.markets.items():
        route = _CONFIG_MARKETS.get(configured_market, configured_market)
        try:
            resolved_route = canonical_market(route)
        except ValueError:
            continue
        if resolved_route != canonical:
            continue
        for ticker in market_config.tickers:
            if ticker.active and ticker.symbol == symbol:
                key = ticker.instrument_key(canonical)
                if mic is None or key.mic == mic.strip().upper():
                    candidates[key.tuple] = key
    if not candidates:
        raise TrackingError(f"no active exact source-config mapping for {canonical}/{mic or '*'}/{symbol}")
    if len(candidates) != 1:
        options = ", ".join(sorted(key[1] for key in candidates))
        raise TrackingError(f"instrument is ambiguous across configured MICs ({options}); specify --mic")
    return next(iter(candidates.values()))


def add_instrument(
    *,
    symbol: str,
    market: str,
    mic: str | None = None,
    groups: list[str] | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    held: bool = False,
    zone_low: float | None = None,
    zone_high: float | None = None,
    path: Path | None = None,
    ticker_config_path: Path | None = None,
    data_dir: Path | None = None,
) -> PrivateWatchlist:
    """Add a config-validated instrument without fetching or backfilling data."""
    key = _resolve_configured_instrument(symbol, market, mic, ticker_config_path)
    zone = PriceZone(low=zone_low, high=zone_high) if zone_low is not None or zone_high is not None else None
    item = TrackedInstrument(instrument=key, held=held, groups=groups or [], tags=tags or [], notes=notes, price_zone=zone)
    source = watchlist_path(path)
    with _watchlist_lock(source):
        current = load_private_watchlist(source, ticker_config_path)
        entries = [existing for existing in current.instruments if existing.instrument != key]
        if any(existing.instrument == key for existing in current.instruments):
            raise TrackingError(f"instrument already exists: {key.display_key}")
        updated = PrivateWatchlist.model_validate({**current.model_dump(), "instruments": [*entries, item]})
        _write_private_watchlist(source, updated)
    _reset_instrument_state(key, data_dir)
    return updated


def update_watchlist_settings(
    *,
    cooldown_minutes: int | None = None,
    name: str | None = None,
    path: Path | None = None,
    ticker_config_path: Path | None = None,
) -> PrivateWatchlist:
    """Update watchlist-level cooldown/name through the same atomic private YAML boundary."""
    source = watchlist_path(path)
    with _watchlist_lock(source):
        current = load_private_watchlist(source, ticker_config_path)
        values = current.model_dump()
        if cooldown_minutes is not None:
            values["cooldown_minutes"] = cooldown_minutes
        if name is not None:
            values["name"] = name
        updated = PrivateWatchlist.model_validate(values)
        _write_private_watchlist(source, updated)
    return updated


def update_instrument(
    instrument: InstrumentKey,
    *,
    held: bool | None = None,
    groups: list[str] | None = None,
    tags: list[str] | None = None,
    notes: str | None = None,
    set_notes: bool = False,
    zone_low: float | None = None,
    zone_high: float | None = None,
    clear_zone: bool = False,
    path: Path | None = None,
    ticker_config_path: Path | None = None,
) -> PrivateWatchlist:
    """Update only explicit fields; unset bounds and notes are not fabricated."""
    source = watchlist_path(path)
    with _watchlist_lock(source):
        current = load_private_watchlist(source, ticker_config_path)
        found = False
        entries: list[TrackedInstrument] = []
        for item in current.instruments:
            if item.instrument != instrument:
                entries.append(item)
                continue
            found = True
            values = item.model_dump()
            if held is not None:
                values["held"] = held
            if groups is not None:
                values["groups"] = groups
            if tags is not None:
                values["tags"] = tags
            if set_notes:
                values["notes"] = notes or None
            if clear_zone:
                values["price_zone"] = None
            elif zone_low is not None or zone_high is not None:
                existing = item.price_zone
                values["price_zone"] = {
                    "low": zone_low if zone_low is not None else (existing.low if existing else None),
                    "high": zone_high if zone_high is not None else (existing.high if existing else None),
                }
            entries.append(TrackedInstrument.model_validate(values))
        if not found:
            raise TrackingError(f"instrument is not in the private watchlist: {instrument.display_key}")
        updated = PrivateWatchlist.model_validate({**current.model_dump(), "instruments": entries})
        _write_private_watchlist(source, updated)
    return updated


def remove_instrument(
    instrument: InstrumentKey,
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
    ticker_config_path: Path | None = None,
) -> PrivateWatchlist:
    """Remove active membership but retain events/history and expire pending deliveries."""
    source = watchlist_path(path)
    # Global lock order is delivery reservation -> watchlist -> SQLite. A sender
    # that already reserved/invoked a request may finish; removal prevents any
    # later reservation and atomically expires every still-pending event.
    with _delivery_lock(data_dir):
        with _watchlist_lock(source):
            current = load_private_watchlist(source, ticker_config_path)
            entries = [item for item in current.instruments if item.instrument != instrument]
            if len(entries) == len(current.instruments):
                raise TrackingError(f"instrument is not in the private watchlist: {instrument.display_key}")
            updated = PrivateWatchlist.model_validate({**current.model_dump(), "instruments": entries})
            _write_private_watchlist(source, updated)
        _cancel_pending(instrument, data_dir)
    return updated


def migrate_legacy_watchlist(
    *,
    source: Path,
    destination: Path | None = None,
    ticker_config_path: Path | None = None,
) -> PrivateWatchlist:
    """Explicitly migrate current membership only; historical ticker-only signals stay unknown."""
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("tickers"), list):
            raise ValueError("legacy watchlist must contain a ticker list")
        name = str(raw.get("name") or "Migrated Watchlist")
        config = TickerConfigRoot.from_yaml(ticker_config_path or DEFAULT_TICKERS_PATH)
        canonical_entries: list[TrackedInstrument] = []
        for symbol in raw["tickers"]:
            matches: dict[tuple[str, str, str], InstrumentKey] = {}
            for configured_market, market_config in config.markets.items():
                route = _CONFIG_MARKETS.get(configured_market, configured_market)
                try:
                    canonical = canonical_market(route)
                except ValueError:
                    continue
                for ticker in market_config.tickers:
                    if ticker.active and ticker.symbol == symbol:
                        key = ticker.instrument_key(canonical)
                        matches[key.tuple] = key
            if len(matches) != 1:
                raise TrackingError(f"legacy ticker {symbol!r} does not have one exact active source-config identity")
            canonical_entries.append(TrackedInstrument(instrument=next(iter(matches.values()))))
        legacy_groups = raw.get("groups", {})
        if not isinstance(legacy_groups, dict):
            raise TrackingError("legacy watchlist groups must be a mapping")
        for index, item in enumerate(canonical_entries):
            labels = [str(group) for group, members in legacy_groups.items() if isinstance(members, list) and raw["tickers"][index] in members]
            if labels:
                canonical_entries[index] = item.model_copy(update={"groups": labels})
        migrated = PrivateWatchlist(name=name, instruments=canonical_entries)
    except TrackingError:
        raise
    except Exception as exc:
        raise TrackingError(f"legacy watchlist cannot be migrated ({type(exc).__name__})") from exc
    target = watchlist_path(destination)
    with _watchlist_lock(target):
        if target.exists():
            raise TrackingError("destination private watchlist already exists")
        _write_private_watchlist(target, migrated)
    return migrated


def _latest_completed_session(instrument: InstrumentKey, now: datetime) -> tuple[date | None, bool]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("tracking clock must be timezone-aware")
    calendar_name = _MIC_CALENDAR[instrument.mic]
    calendar = xc.get_calendar(calendar_name)
    local_now = now.astimezone(ZoneInfo(PRICE_MARKETS[instrument.market].timezone))
    local_day = local_now.date()
    is_open_today = is_instrument_trading_day(instrument, local_day)
    start = local_day - timedelta(days=35)
    sessions = calendar.sessions_in_range(start.isoformat(), local_day.isoformat())
    for session in reversed(list(sessions)):
        session_date = date.fromisoformat(str(session)[:10])
        close = calendar.session_close(session_date.isoformat()).to_pydatetime()
        if close.tzinfo is None:
            close = close.replace(tzinfo=UTC)
        if close.astimezone(UTC) <= now.astimezone(UTC):
            return session_date, is_open_today
    return None, is_open_today


def _read_price_table(instrument: InstrumentKey, lake_dir: Path) -> pl.DataFrame:
    table = f"01_bronze/market_data/{instrument.market}"
    path = delta_table_path(table, lake_dir)
    if not path.exists():
        raise FileNotFoundError("stored price table is missing")
    if (path / "_delta_log").exists():
        return read_delta(table, lake_dir=lake_dir)
    files = sorted(path.rglob("*.parquet"))
    if not files:
        raise FileNotFoundError("stored legacy price table has no Parquet files")
    return pl.read_parquet([str(item) for item in files], hive_partitioning=True)


def _select_instrument_prices(frame: pl.DataFrame, instrument: InstrumentKey) -> pl.DataFrame:
    symbol_column = "symbol" if "symbol" in frame.columns else "ticker"
    if symbol_column not in frame.columns:
        raise TrackingError("price schema has no symbol/ticker identity column")
    matching = frame.filter(pl.col(symbol_column).cast(pl.String, strict=False) == instrument.symbol)
    if matching.is_empty():
        return matching
    normalized = normalize_identity_frame(matching)
    exact = normalized.filter((pl.col("market") == instrument.market) & (pl.col("mic") == instrument.mic) & (pl.col("symbol") == instrument.symbol))
    return exact


def _price_rule(instrument: InstrumentKey, now: datetime, lake_dir: Path) -> TrackingRuleOutcome:
    try:
        expected, open_today = _latest_completed_session(instrument, now)
    except Exception as exc:
        logger.warning(
            "tracking_session_resolution_failed",
            instrument=instrument.display_key,
            error_type=type(exc).__name__,
        )
        return TrackingRuleOutcome(
            source="price_sma20",
            status="unavailable",
            detail=f"venue session resolution unavailable ({type(exc).__name__})",
        )
    if not open_today:
        return TrackingRuleOutcome(
            source="price_sma20",
            status="not_scheduled",
            detail="instrument venue is closed today; persistent condition is unchanged",
        )
    if expected is None:
        return TrackingRuleOutcome(
            source="price_sma20",
            status="unavailable",
            detail="no completed venue session could be established",
        )
    try:
        prices = _select_instrument_prices(_read_price_table(instrument, lake_dir), instrument).filter(
            pl.col("date").cast(pl.Date, strict=False) <= expected
        )
        if prices.is_empty():
            return TrackingRuleOutcome(source="price_sma20", status="unavailable", detail="no canonical stored price rows for instrument")
        from equity_lake.ingestion.corporate_actions import load_corporate_action_inputs

        actions, coverage = load_corporate_action_inputs(instrument.market, lake_dir=lake_dir)
        adjusted = with_price_adjustment(prices, actions, coverage=coverage, method="split_only", as_of=expected)
        window_start = expected - timedelta(days=90)
        calendar = xc.get_calendar(_MIC_CALENDAR[instrument.mic])
        valid_sessions = [
            date.fromisoformat(str(session)[:10]) for session in calendar.sessions_in_range(window_start.isoformat(), expected.isoformat())
        ]
        adjusted = adjusted.filter(pl.col("date").cast(pl.Date).is_in(valid_sessions)).sort("date")
        if "close" not in adjusted.columns:
            raise TrackingError("adjusted price schema has no close column")
        adjusted = adjusted.with_columns(pl.col("close").cast(pl.Float64, strict=False)).filter(
            pl.col("close").is_not_null() & pl.col("close").is_finite() & (pl.col("close") > 0)
        )
        if adjusted.is_empty() or adjusted.filter(pl.col("date").cast(pl.Date) == pl.lit(expected)).is_empty():
            return TrackingRuleOutcome(
                source="price_sma20",
                status="unavailable",
                detail=f"latest completed venue session {expected} is absent or invalid",
            )
        valid = adjusted.select(pl.col("date").cast(pl.Date).alias("date"), pl.col("close")).sort("date")
        if valid["date"].n_unique() != valid.height:
            return TrackingRuleOutcome(source="price_sma20", status="unavailable", detail="stored price table has duplicate rows for a venue session")
        if valid.height < SMA_VALID_BARS:
            return TrackingRuleOutcome(
                source="price_sma20",
                status="unavailable",
                detail=f"need {SMA_VALID_BARS} valid completed venue bars; found {valid.height}",
            )
        valid = valid.with_columns(pl.col("close").rolling_mean(window_size=SMA_VALID_BARS, min_samples=SMA_VALID_BARS).alias("sma20"))
        latest = valid.filter(pl.col("date") == pl.lit(expected)).tail(1)
        if latest.is_empty() or latest["sma20"].null_count():
            return TrackingRuleOutcome(source="price_sma20", status="unavailable", detail="SMA20 is unavailable for the latest completed session")
        close = float(latest["close"][0])
        sma = float(latest["sma20"][0])
        deviation = (close - sma) / sma
        action: Literal["BUY", "SELL"] | None = "BUY" if deviation >= PRICE_BUY_DEVIATION else "SELL" if deviation <= PRICE_SELL_DEVIATION else None
        if action is None:
            return TrackingRuleOutcome(
                source="price_sma20",
                status="no_condition",
                detail="complete split-adjusted SMA20 evaluation; no actionable threshold crossed",
            )
        threshold = PRICE_BUY_DEVIATION if action == "BUY" else PRICE_SELL_DEVIATION
        candidate = TrackingCandidate(
            instrument=instrument,
            session=expected,
            source="price_sma20",
            action=action,
            rule_version=TRACKING_RULE_VERSION,
            reasoning=f"Split-adjusted close is {deviation:+.2%} from SMA20; {action} candidate threshold {threshold:+.0%}.",
            price=close,
            sma20=sma,
            deviation=deviation,
            threshold=threshold,
        )
        return TrackingRuleOutcome(source="price_sma20", status="signal", candidate=candidate)
    except Exception as exc:
        logger.warning(
            "tracking_price_evaluation_unavailable",
            instrument=instrument.display_key,
            error_type=type(exc).__name__,
        )
        return TrackingRuleOutcome(
            source="price_sma20",
            status="unavailable",
            detail=f"strict adjusted price evaluation unavailable ({type(exc).__name__})",
        )


def _config_fingerprint(config: Any) -> str:
    """Hash normalized selected configuration without recording its private path."""
    payload = {"backtest": config.backtest, "sentiment": config.sentiment, "ml": config.ml}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ml_policy_family_prefix(rule_version: str, fingerprint: str) -> str:
    """Return the exact mode/config state family while leaving event identity model-bound."""
    base, separator, _model_token = rule_version.partition(":cfg-")
    if not separator:
        raise TrackingError("ML rule has no configuration-bound state identity")
    return f"{base}:cfg-{fingerprint[:16]}:model-"


def _bind_rule_provenance(
    outcome: TrackingRuleOutcome,
    *,
    fingerprint: str,
    provenance: Literal["default", "explicit", "synthetic"],
    mode: str = "v1_direction",
) -> TrackingRuleOutcome:
    """Make config/model semantics part of every outcome's persistent state key."""
    candidate = outcome.candidate
    model_version = candidate.model_version if candidate is not None else outcome.model_version
    base = candidate.rule_version if candidate is not None else outcome.rule_version
    if base is None:
        base = TRACKING_RULE_VERSION if outcome.source == "price_sma20" else f"ml-{mode}-v1"
    model_token = hashlib.sha256(model_version.encode("utf-8")).hexdigest()[:12] if model_version else "unknown-model"
    rule_version = f"{base}:cfg-{fingerprint[:16]}:model-{model_token}"
    if candidate is not None:
        candidate = candidate.model_copy(
            update={
                "rule_version": rule_version,
                "config_fingerprint": fingerprint,
                "config_provenance": provenance,
                "model_version": model_version,
            }
        )
    return outcome.model_copy(
        update={
            "candidate": candidate,
            "rule_version": rule_version,
            "config_fingerprint": fingerprint,
            "config_provenance": provenance,
            "model_version": model_version,
        }
    )


def _ml_rule(
    instrument: InstrumentKey,
    session: date,
    config: dict[str, Any],
    *,
    fingerprint: str,
    provenance: Literal["default", "explicit", "synthetic"],
) -> TrackingRuleOutcome:
    """Optional strict local inference; unknown model provenance remains unavailable."""
    from equity_lake.ml.forecasting import (
        ModelExpiredError,
        ModelFutureError,
        ModelIncompatibleError,
        ModelMissingError,
        ModelProvenanceError,
        ModelUnavailableError,
        NoCandidateConditionError,
        PriceForecaster,
    )

    mode = str(config.get("mode", "v1_direction"))
    forecaster: PriceForecaster | None = None

    def finish(outcome: TrackingRuleOutcome, model_version: str | None = None) -> TrackingRuleOutcome:
        outcome = outcome.model_copy(update={"model_version": model_version})
        return _bind_rule_provenance(outcome, fingerprint=fingerprint, provenance=provenance, mode=mode)

    try:
        model_dir = config.get("model_dir", config.get("model_path"))
        candidates = config.get("candidate_strategies") or config.get("strategies")
        forecaster = PriceForecaster(
            model_dir=str(model_dir) if model_dir else None,
            model_mode=mode,
            ml_config=config,
            candidate_strategies=candidates,
        )
        prediction = forecaster.predict(instrument, session)
        model_version = str(prediction["model_version"]) if prediction.get("model_version") is not None else None
        age = prediction.get("model_age_days")
        if not isinstance(age, int) or isinstance(age, bool) or age < 0 or age > 7:
            return finish(
                TrackingRuleOutcome(source="ml", status="unavailable", detail="model age is unknown or exceeds the seven-calendar-day limit"),
                model_version,
            )
        if prediction.get("feature_schema_compatibility") != "known" or prediction.get("adjustment_compatibility") != "known":
            return finish(
                TrackingRuleOutcome(
                    source="ml",
                    status="unavailable",
                    detail="model schema or adjustment/missingness compatibility is unknown or incompatible",
                ),
                model_version,
            )
        if prediction.get("identity_schema_version") != IDENTITY_SCHEMA_VERSION:
            return finish(
                TrackingRuleOutcome(source="ml", status="unavailable", detail="model identity schema is unknown or incompatible"),
                model_version,
            )
        threshold_source = prediction.get("threshold_source")
        if threshold_source not in {"stored_optimized", "configured_fallback", "default", "refit_validation"}:
            return finish(
                TrackingRuleOutcome(source="ml", status="unavailable", detail="effective ML threshold provenance is unknown"),
                model_version,
            )
        probability = prediction.get("execution_probability", prediction.get("probability"))
        if not isinstance(probability, int | float) or isinstance(probability, bool):
            return finish(TrackingRuleOutcome(source="ml", status="error", detail="forecaster returned no finite probability"), model_version)
        probability = float(probability)
        if not 0 <= probability <= 1:
            return finish(TrackingRuleOutcome(source="ml", status="error", detail="forecaster returned probability outside [0, 1]"), model_version)
        if mode == "v2_meta_label":
            if not prediction.get("should_execute"):
                return finish(
                    TrackingRuleOutcome(source="ml", status="no_condition", detail="meta-label candidate did not clear the effective threshold"),
                    model_version,
                )
            raw_action = prediction.get("candidate_action")
            if raw_action not in {"BUY", "SELL"}:
                return finish(
                    TrackingRuleOutcome(source="ml", status="error", detail="executed meta-label lacks a BUY/SELL candidate"),
                    model_version,
                )
            action: Literal["BUY", "SELL"] = raw_action
        else:
            threshold = float(prediction.get("threshold_used", config.get("buy_probability_threshold", 0.5)))
            action = "BUY" if probability >= threshold else "SELL"
            minimum = float(config.get("min_confidence", 60)) / 100
            if max(probability, 1 - probability) < minimum:
                return finish(
                    TrackingRuleOutcome(source="ml", status="no_condition", detail="model score did not clear the configured confidence threshold"),
                    model_version,
                )
        candidate = TrackingCandidate(
            instrument=instrument,
            session=session,
            source="ml",
            action=action,
            rule_version=f"ml-{mode}-v1",
            reasoning=f"Optional ML {action} candidate; score is not a calibrated probability of profit.",
            model_version=model_version,
            model_age_days=age,
            threshold_source=str(threshold_source),
            probability_score=probability,
        )
        return finish(TrackingRuleOutcome(source="ml", status="signal", candidate=candidate), model_version)
    except NoCandidateConditionError:
        return finish(
            TrackingRuleOutcome(source="ml", status="no_condition", detail="selected candidate rules produced no candidate condition"),
            "candidate-none",
        )
    except ModelMissingError:
        logger.warning("tracking_ml_model_missing", instrument=instrument.display_key)
        return finish(TrackingRuleOutcome(source="ml", status="missing_model", detail="no trusted compatible model is available"))
    except (ModelProvenanceError, ModelIncompatibleError, ModelExpiredError, ModelFutureError, ModelUnavailableError) as exc:
        logger.warning("tracking_ml_evaluation_unavailable", instrument=instrument.display_key, error_type=type(exc).__name__)
        return finish(TrackingRuleOutcome(source="ml", status="unavailable", detail=f"strict ML inference unavailable ({type(exc).__name__})"))
    except Exception as exc:
        logger.warning("tracking_ml_evaluation_failed", instrument=instrument.display_key, error_type=type(exc).__name__)
        return finish(TrackingRuleOutcome(source="ml", status="error", detail=f"strict ML inference failed ({type(exc).__name__})"))
    finally:
        if forecaster is not None:
            forecaster.close()


def scan_private_watchlist(
    *,
    now: datetime | None = None,
    watchlist: PrivateWatchlist | None = None,
    watchlist_file: Path | None = None,
    ticker_config_path: Path | None = None,
    lake_dir: Path | None = None,
    data_dir: Path | None = None,
    enable_ml: bool | None = None,
    signal_config_path: Path | None = None,
) -> TrackingScanReport:
    """Read stored bars, evaluate exact-session rules, and atomically persist outcomes/events."""
    from uuid import uuid4

    from equity_lake.signals.config import load_signal_config

    clock = now or datetime.now(UTC)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("tracking clock must be timezone-aware")
    clock = clock.astimezone(UTC)
    selected_watchlist = watchlist or load_private_watchlist(watchlist_file, ticker_config_path)
    config = load_signal_config(signal_config_path)
    config_fingerprint = _config_fingerprint(config)
    config_provenance: Literal["default", "explicit", "synthetic"] = "explicit" if signal_config_path is not None else "default"
    use_ml = config.is_generator_enabled("ml") if enable_ml is None else enable_ml
    ml_config = config.ml
    selected_lake = lake_dir or LAKE_DIR
    started = datetime.now(UTC)
    evaluations: list[TrackingEvaluation] = []
    for item in selected_watchlist.instruments:
        instrument = item.instrument
        try:
            expected, open_today = _latest_completed_session(instrument, clock)
            session_error: str | None = None
        except Exception as exc:
            expected, open_today = None, True
            session_error = type(exc).__name__
        if session_error is not None:
            price = TrackingRuleOutcome(
                source="price_sma20",
                status="unavailable",
                detail=f"venue session resolution unavailable ({session_error})",
            )
        elif not open_today:
            price = TrackingRuleOutcome(
                source="price_sma20",
                status="not_scheduled",
                detail="venue is closed today; state is unchanged",
            )
        elif expected is None:
            price = TrackingRuleOutcome(
                source="price_sma20",
                status="unavailable",
                detail="no completed venue session could be established",
            )
        else:
            price = _price_rule(instrument, clock, selected_lake)
        price = _bind_rule_provenance(price, fingerprint=config_fingerprint, provenance=config_provenance)
        rules = [
            price,
            TrackingRuleOutcome(
                source="sentiment",
                status="unavailable",
                detail="sentiment enrichment is unsupported in this private price-only scan",
            ),
        ]
        if not use_ml:
            rules.append(TrackingRuleOutcome(source="ml", status="disabled", detail="ML opportunity inference is disabled"))
        elif session_error is not None or expected is None:
            rules.append(
                _bind_rule_provenance(
                    TrackingRuleOutcome(
                        source="ml",
                        status="unavailable",
                        detail="ML inference requires an exact completed instrument session",
                    ),
                    fingerprint=config_fingerprint,
                    provenance=config_provenance,
                    mode=str(ml_config.get("mode", "v1_direction")),
                )
            )
        elif not open_today:
            rules.append(
                _bind_rule_provenance(
                    TrackingRuleOutcome(
                        source="ml",
                        status="not_scheduled",
                        detail="venue is closed today; ML inference is not scheduled",
                    ),
                    fingerprint=config_fingerprint,
                    provenance=config_provenance,
                    mode=str(ml_config.get("mode", "v1_direction")),
                )
            )
        else:
            rules.append(
                _ml_rule(
                    instrument,
                    expected,
                    ml_config,
                    fingerprint=config_fingerprint,
                    provenance=config_provenance,
                )
            )
        rule_statuses = [rule.status for rule in rules if rule.source != "sentiment" and rule.status != "disabled"]
        successful = sum(status in {"signal", "no_condition"} for status in rule_statuses)
        failures = sum(status in {"unavailable", "missing_model", "error"} for status in rule_statuses)
        if not open_today and expected is not None:
            evaluation_status: Literal["success", "partial", "failed", "not_scheduled"] = "not_scheduled"
        elif successful == 0:
            evaluation_status = "failed"
        elif failures:
            evaluation_status = "partial"
        else:
            evaluation_status = "success"
        evaluations.append(
            TrackingEvaluation(
                instrument=instrument,
                expected_session=expected,
                evaluated_at=clock,
                status=evaluation_status,
                rules=rules,
            )
        )

    price_success = sum(
        rule.status in {"signal", "no_condition"} for evaluation in evaluations for rule in evaluation.rules if rule.source == "price_sma20"
    )
    price_failure = sum(
        rule.status in {"unavailable", "missing_model", "error"}
        for evaluation in evaluations
        for rule in evaluation.rules
        if rule.source == "price_sma20"
    )
    if evaluations and all(item.status == "not_scheduled" for item in evaluations):
        status: Literal["success", "partial", "failed", "not_scheduled"] = "not_scheduled"
    elif not evaluations or price_success == 0:
        status = "failed"
    elif price_failure or any(item.status == "partial" for item in evaluations):
        status = "partial"
    else:
        status = "success"
    run = TrackingScanReport(
        run_id=uuid4().hex,
        watchlist=selected_watchlist.name,
        config_fingerprint=config_fingerprint,
        config_provenance=config_provenance,
        started_at=started,
        completed_at=datetime.now(UTC),
        status=status,
        evaluations=evaluations,
        issues=["Sentiment enrichment is unsupported in this price-only tracking path; no neutral sentiment is inferred."],
    )
    return _persist_scan(
        run,
        selected_watchlist.cooldown_minutes,
        data_dir,
        watchlist_file=watchlist_path(watchlist_file),
        ticker_config_path=ticker_config_path,
    )


def _event_id(instrument: InstrumentKey, session: date, source: str, rule_version: str, transition: int, action: str) -> str:
    material = json.dumps([*instrument.tuple, session.isoformat(), source, rule_version, transition, action], separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _open_database(data_dir: Path | None, *, writable: bool) -> sqlite3.Connection | None:
    path = tracking_db_path(data_dir)
    if not writable and not path.exists():
        return None
    if writable:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, stat.S_IMODE(path.parent.stat().st_mode) & 0o700)
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=10000")
    connection.execute("PRAGMA foreign_keys=ON")
    if writable:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        version_value = connection.execute("PRAGMA user_version").fetchone()[0]
        if not isinstance(version_value, int) or isinstance(version_value, bool):
            connection.close()
            raise TrackingError("private tracking database schema version is unreadable")
        version = version_value
        if version not in {0, 1, 2}:
            connection.close()
            raise TrackingError(f"unsupported private tracking database version {version}")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                run_id TEXT PRIMARY KEY,
                completed_at TEXT NOT NULL,
                status TEXT NOT NULL,
                report_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS instrument_state (
                market TEXT NOT NULL, mic TEXT NOT NULL, symbol TEXT NOT NULL,
                source TEXT NOT NULL, rule_version TEXT NOT NULL,
                active_action TEXT, transition INTEGER NOT NULL DEFAULT 0,
                last_event_at TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY (market, mic, symbol, source, rule_version)
            );
            CREATE TABLE IF NOT EXISTS signal_events (
                event_id TEXT PRIMARY KEY,
                market TEXT NOT NULL, mic TEXT NOT NULL, symbol TEXT NOT NULL,
                session TEXT NOT NULL, source TEXT NOT NULL, action TEXT NOT NULL,
                rule_version TEXT NOT NULL, transition INTEGER NOT NULL,
                reason TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                config_fingerprint TEXT NOT NULL DEFAULT '', config_provenance TEXT NOT NULL DEFAULT 'synthetic',
                model_version TEXT
            );
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY REFERENCES signal_events(event_id),
                status TEXT NOT NULL CHECK(status IN ('pending','sent','failed','expired')),
                attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
                error_code TEXT, updated_at TEXT NOT NULL
            );
            """
        )
        event_columns = {row[1] for row in connection.execute("PRAGMA table_info(signal_events)").fetchall()}
        if "config_fingerprint" not in event_columns:
            connection.execute("ALTER TABLE signal_events ADD COLUMN config_fingerprint TEXT NOT NULL DEFAULT ''")
        if "config_provenance" not in event_columns:
            connection.execute("ALTER TABLE signal_events ADD COLUMN config_provenance TEXT NOT NULL DEFAULT 'synthetic'")
        if "model_version" not in event_columns:
            connection.execute("ALTER TABLE signal_events ADD COLUMN model_version TEXT")
        if version < 2:
            connection.execute("PRAGMA user_version=2")
        os.chmod(path, 0o600)
    return connection


def _state_key(instrument: InstrumentKey, source: str, rule_version: str) -> tuple[str, str, str, str, str]:
    return (*instrument.tuple, source, rule_version)


def _persist_scan_locked(
    run: TrackingScanReport,
    cooldown_minutes: int,
    data_dir: Path | None,
    *,
    watchlist_file: Path | None = None,
    ticker_config_path: Path | None = None,
) -> TrackingScanReport:
    active_identities: set[tuple[str, str, str]] | None = None
    if watchlist_file is not None:
        active_identities = {item.instrument.tuple for item in load_private_watchlist(watchlist_file, ticker_config_path).instruments}
    connection = _open_database(data_dir, writable=True)
    assert connection is not None
    events: list[TrackingEvent] = []
    try:
        connection.execute("BEGIN IMMEDIATE")
        now = run.completed_at
        removed_during_scan = False
        for evaluation in run.evaluations:
            if active_identities is not None and evaluation.instrument.tuple not in active_identities:
                removed_during_scan = True
                continue
            for outcome in evaluation.rules:
                if outcome.source == "sentiment" or outcome.status == "disabled":
                    continue
                source = outcome.source
                candidate = outcome.candidate
                fingerprint = (
                    outcome.config_fingerprint or (candidate.config_fingerprint if candidate is not None else None) or run.config_fingerprint
                )
                provenance = outcome.config_provenance or (candidate.config_provenance if candidate is not None else None) or run.config_provenance
                if not fingerprint:
                    raise TrackingError("signal outcome has no selected configuration fingerprint")
                if outcome.config_fingerprint != fingerprint or outcome.config_provenance != provenance or outcome.rule_version is None:
                    outcome = _bind_rule_provenance(
                        outcome,
                        fingerprint=fingerprint,
                        provenance=provenance,
                        mode="v2_meta_label" if candidate and candidate.rule_version.startswith("ml-v2_meta_label") else "v1_direction",
                    )
                    candidate = outcome.candidate
                rule_version = outcome.rule_version or (candidate.rule_version if candidate is not None else "")
                if not rule_version:
                    raise TrackingError("signal outcome has no persistent rule identity")
                if source == "ml" and outcome.status == "no_condition":
                    # A successful neutral result may have no model version (the
                    # v2 candidate-none path). Re-arm only this exact mode/config
                    # family; candidate event keys remain model-bound for change
                    # detection and exact delivery revalidation.
                    family_prefix = _ml_policy_family_prefix(rule_version, fingerprint)
                    connection.execute(
                        "UPDATE instrument_state SET active_action=NULL, updated_at=? "
                        "WHERE market=? AND mic=? AND symbol=? AND source='ml' "
                        "AND substr(rule_version, 1, ?) = ?",
                        (now.isoformat(), *evaluation.instrument.tuple, len(family_prefix), family_prefix),
                    )
                    continue
                key = _state_key(evaluation.instrument, source, rule_version)
                row = connection.execute(
                    "SELECT active_action, transition, last_event_at, updated_at "
                    "FROM instrument_state WHERE market=? AND mic=? AND symbol=? AND source=? AND rule_version=?",
                    key,
                ).fetchone()
                if row is None:
                    initial_state = InstrumentTrackingState(
                        instrument=evaluation.instrument,
                        source=source,
                        rule_version=rule_version,
                        active_action=None,
                        transition=0,
                        last_event_at=None,
                        updated_at=now,
                    )
                    connection.execute(
                        "INSERT INTO instrument_state(market,mic,symbol,source,rule_version,active_action,transition,last_event_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            *initial_state.instrument.tuple,
                            initial_state.source,
                            initial_state.rule_version,
                            initial_state.active_action,
                            initial_state.transition,
                            None,
                            initial_state.updated_at.isoformat(),
                        ),
                    )
                    active_action, transition, last_event_at = initial_state.active_action, initial_state.transition, initial_state.last_event_at
                else:
                    stored_state = InstrumentTrackingState(
                        instrument=evaluation.instrument,
                        source=source,
                        rule_version=rule_version,
                        active_action=row["active_action"],
                        transition=row["transition"],
                        last_event_at=datetime.fromisoformat(row["last_event_at"]) if row["last_event_at"] else None,
                        updated_at=datetime.fromisoformat(row["updated_at"]),
                    )
                    active_action, transition, last_event_at = stored_state.active_action, stored_state.transition, stored_state.last_event_at
                if outcome.status == "no_condition":
                    neutral_state = InstrumentTrackingState(
                        instrument=evaluation.instrument,
                        source=source,
                        rule_version=rule_version,
                        active_action=None,
                        transition=transition,
                        last_event_at=last_event_at,
                        updated_at=now,
                    )
                    connection.execute(
                        "UPDATE instrument_state SET active_action=?, updated_at=? "
                        "WHERE market=? AND mic=? AND symbol=? AND source=? AND rule_version=?",
                        (neutral_state.active_action, neutral_state.updated_at.isoformat(), *key),
                    )
                    continue
                if candidate is None:
                    continue
                if active_action == candidate.action:
                    unchanged_state = InstrumentTrackingState(
                        instrument=evaluation.instrument,
                        source=source,
                        rule_version=rule_version,
                        active_action=active_action,
                        transition=transition,
                        last_event_at=last_event_at,
                        updated_at=now,
                    )
                    connection.execute(
                        "UPDATE instrument_state SET updated_at=? WHERE market=? AND mic=? AND symbol=? AND source=? AND rule_version=?",
                        (unchanged_state.updated_at.isoformat(), *key),
                    )
                    continue
                next_transition = transition + 1
                allowed_after = last_event_at + timedelta(minutes=cooldown_minutes) if last_event_at else None
                if allowed_after is not None and now < allowed_after:
                    continue
                event_state = InstrumentTrackingState(
                    instrument=evaluation.instrument,
                    source=source,
                    rule_version=rule_version,
                    active_action=candidate.action,
                    transition=next_transition,
                    last_event_at=now,
                    updated_at=now,
                )
                connection.execute(
                    "UPDATE instrument_state SET active_action=?, transition=?, last_event_at=?, updated_at=? "
                    "WHERE market=? AND mic=? AND symbol=? AND source=? AND rule_version=?",
                    (
                        event_state.active_action,
                        event_state.transition,
                        event_state.last_event_at.isoformat() if event_state.last_event_at else None,
                        event_state.updated_at.isoformat(),
                        *key,
                    ),
                )
                event_id = _event_id(
                    candidate.instrument,
                    candidate.session,
                    candidate.source,
                    candidate.rule_version,
                    next_transition,
                    candidate.action,
                )
                event = TrackingEvent(
                    event_id=event_id,
                    instrument=candidate.instrument,
                    session=candidate.session,
                    source=candidate.source,
                    action=candidate.action,
                    rule_version=rule_version,
                    config_fingerprint=fingerprint,
                    config_provenance=provenance,
                    model_version=candidate.model_version,
                    transition=next_transition,
                    reason=candidate.reasoning,
                    created_at=now,
                )
                payload = OpportunityWebhookPayload(
                    event_id=event.event_id,
                    market=event.instrument.market,
                    mic=event.instrument.mic,
                    symbol=event.instrument.symbol,
                    session=event.session,
                    source=event.source,
                    action=event.action,
                    rule_version=event.rule_version,
                    reason=event.reason,
                )
                connection.execute(
                    "INSERT OR IGNORE INTO signal_events("
                    "event_id,market,mic,symbol,session,source,action,rule_version,transition,reason,created_at,payload_json,"
                    "config_fingerprint,config_provenance,model_version"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.instrument.market,
                        event.instrument.mic,
                        event.instrument.symbol,
                        event.session.isoformat(),
                        event.source,
                        event.action,
                        event.rule_version,
                        event.transition,
                        event.reason,
                        event.created_at.isoformat(),
                        payload.model_dump_json(),
                        event.config_fingerprint,
                        event.config_provenance,
                        event.model_version,
                    ),
                )
                connection.execute(
                    "INSERT OR IGNORE INTO outbox(event_id,status,attempts,error_code,updated_at) VALUES(?, 'pending', 0, NULL, ?)",
                    (event.event_id, now.isoformat()),
                )
                events.append(event)
        report_issues = list(run.issues)
        report_status = run.status
        if removed_during_scan:
            report_issues.append("One or more instruments were removed before event persistence; no event was created for removed identities.")
            if report_status == "success":
                report_status = "partial"
        persisted = TrackingScanReport.model_validate({**run.model_dump(), "events": events, "issues": report_issues, "status": report_status})
        connection.execute(
            "INSERT INTO scan_runs(run_id,completed_at,status,report_json) VALUES(?,?,?,?)",
            (persisted.run_id, persisted.completed_at.isoformat(), persisted.status, persisted.model_dump_json()),
        )
        connection.execute("COMMIT")
        return persisted
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _persist_scan(
    run: TrackingScanReport,
    cooldown_minutes: int,
    data_dir: Path | None,
    *,
    watchlist_file: Path | None = None,
    ticker_config_path: Path | None = None,
) -> TrackingScanReport:
    """Serialize event creation with delivery/removal reservation operations."""
    with _delivery_lock(data_dir):
        return _persist_scan_locked(
            run,
            cooldown_minutes,
            data_dir,
            watchlist_file=watchlist_file,
            ticker_config_path=ticker_config_path,
        )


def _reset_instrument_state(instrument: InstrumentKey, data_dir: Path | None) -> None:
    path = tracking_db_path(data_dir)
    if not path.exists():
        return
    connection = _open_database(data_dir, writable=True)
    assert connection is not None
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE instrument_state SET active_action=NULL,updated_at=? WHERE market=? AND mic=? AND symbol=?",
            (datetime.now(UTC).isoformat(), *instrument.tuple),
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _cancel_pending(instrument: InstrumentKey, data_dir: Path | None) -> None:
    path = tracking_db_path(data_dir)
    if not path.exists():
        return
    connection = _open_database(data_dir, writable=True)
    assert connection is not None
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE outbox SET status='expired',updated_at=?,error_code='watchlist_removed' "
            "WHERE status='pending' AND event_id IN "
            "(SELECT event_id FROM signal_events WHERE market=? AND mic=? AND symbol=?)",
            (datetime.now(UTC).isoformat(), *instrument.tuple),
        )
        connection.execute("COMMIT")
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _read_reports(connection: sqlite3.Connection, limit: int = 100) -> tuple[list[TrackingScanReport], Literal["available", "missing", "corrupt"]]:
    try:
        rows = connection.execute("SELECT report_json FROM scan_runs ORDER BY completed_at DESC LIMIT ?", (limit,)).fetchall()
        reports = [TrackingScanReport.model_validate_json(row["report_json"]) for row in rows]
        return reports, "available"
    except Exception:
        return [], "corrupt"


def _read_outbox(connection: sqlite3.Connection, limit: int = 200) -> tuple[list[OutboxObservation], Literal["available", "missing", "corrupt"]]:
    try:
        rows = connection.execute(
            "SELECT e.event_id,e.market,e.mic,e.symbol,e.session,e.source,e.action,e.rule_version,"
            "e.transition,e.reason,e.created_at,e.config_fingerprint,e.config_provenance,e.model_version,"
            "o.status,o.attempts,o.error_code,o.updated_at "
            "FROM signal_events e JOIN outbox o ON e.event_id=o.event_id ORDER BY e.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        observations = [
            OutboxObservation(
                event=TrackingEvent(
                    event_id=row["event_id"],
                    instrument=InstrumentKey(market=row["market"], mic=row["mic"], symbol=row["symbol"]),
                    session=date.fromisoformat(row["session"]),
                    source=row["source"],
                    action=row["action"],
                    rule_version=row["rule_version"],
                    config_fingerprint=row["config_fingerprint"],
                    config_provenance=row["config_provenance"],
                    model_version=row["model_version"],
                    transition=row["transition"],
                    reason=row["reason"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                ),
                status=row["status"],
                attempts=row["attempts"],
                error_code=row["error_code"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        ]
        return observations, "available"
    except Exception:
        return [], "corrupt"


def private_tracking_snapshot(
    *,
    now: datetime | None = None,
    watchlist_file: Path | None = None,
    data_dir: Path | None = None,
    ticker_config_path: Path | None = None,
    lake_dir: Path | None = None,
) -> PrivateTrackingSnapshot:
    """Read private notes/held flags plus readiness, history, conflicts, and outbox locally."""
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("tracking clock must be timezone-aware")
    clock = clock.astimezone(UTC)
    watchlist = load_private_watchlist(watchlist_file, ticker_config_path)
    db = _open_database(data_dir, writable=False)
    reports: list[TrackingScanReport] = []
    outbox: list[OutboxObservation] = []
    history_status: Literal["available", "missing", "corrupt"] = "missing"
    last_success: datetime | None = None
    if db is not None:
        try:
            reports, history_status = _read_reports(db)
            outbox, outbox_status = _read_outbox(db)
            if outbox_status == "corrupt":
                history_status = "corrupt"
            if reports:
                last_success = next((report.completed_at for report in reports if report.status == "success"), None)
        finally:
            db.close()
    latest_report = reports[0] if reports else None
    candidates_by_key: dict[tuple[tuple[str, str, str], date], list[TrackingCandidate]] = {}
    if latest_report is not None:
        for report_evaluation in latest_report.evaluations:
            for outcome in report_evaluation.rules:
                if outcome.candidate is not None:
                    candidates_by_key.setdefault((report_evaluation.instrument.tuple, outcome.candidate.session), []).append(outcome.candidate)
    conflicts = [candidates for candidates in candidates_by_key.values() if {candidate.action for candidate in candidates} == {"BUY", "SELL"}]
    readiness: list[ReadinessObservation] = []
    expected_sessions: list[date] = []
    for item in watchlist.instruments:
        try:
            expected, open_today = _latest_completed_session(item.instrument, clock)
            session_error: str | None = None
        except Exception as exc:
            expected, open_today = None, True
            session_error = type(exc).__name__
        if expected is not None:
            expected_sessions.append(expected)
        evaluation = (
            next((entry for entry in latest_report.evaluations if entry.instrument == item.instrument), None) if latest_report is not None else None
        )
        current_status: str
        detail: str | None
        price_readiness: TrackingRuleOutcome | None = None
        if session_error is not None:
            current_status, detail = "unavailable", f"venue session resolution failed ({session_error})"
        elif not open_today:
            current_status, detail = "not_scheduled", "venue is closed today; this is not a successful neutral scan"
        elif expected is None:
            current_status, detail = "unavailable", "no completed venue session could be established"
        else:
            price_readiness = _price_rule(item.instrument, clock, lake_dir or LAKE_DIR)
            current_status, detail = price_readiness.status, price_readiness.detail
            if evaluation is not None and evaluation.expected_session != expected:
                detail = f"current stored-data status={current_status}; persisted scan is stale for the latest completed session"
        readiness.append(
            ReadinessObservation(
                instrument=item.instrument,
                status=current_status,
                expected_session=expected,
                last_evaluated_session=evaluation.expected_session if evaluation else None,
                detail=detail,
                candidate=price_readiness.candidate if price_readiness is not None else None,
            )
        )
    latest_expected = max(expected_sessions) if expected_sessions else None
    last_successful_report = next((report for report in reports if report.status == "success"), None)
    successful_sessions = (
        [evaluation.expected_session for evaluation in last_successful_report.evaluations if evaluation.expected_session]
        if last_successful_report
        else []
    )
    last_success_session = max(successful_sessions) if successful_sessions else None
    stale_warning = None
    if not reports or last_success is None:
        stale_warning = "No successful private tracking scan is recorded; quiet or empty output is not a healthy-day signal."
    elif latest_expected is not None and last_success_session is not None and last_success_session < latest_expected:
        stale_warning = "The latest successful scan is older than the latest completed instrument session."
    return PrivateTrackingSnapshot(
        generated_at=clock,
        watchlist=watchlist,
        readiness=readiness,
        recent_reports=reports[:20],
        recent_events=outbox,
        conflicts=conflicts,
        last_successful_scan_at=last_success,
        stale_run_warning=stale_warning,
        history_status=history_status,
    )


def _event_from_row(row: sqlite3.Row) -> tuple[TrackingEvent, OpportunityWebhookPayload, int, str | None]:
    instrument = InstrumentKey(market=row["market"], mic=row["mic"], symbol=row["symbol"])
    event = TrackingEvent(
        event_id=row["event_id"],
        instrument=instrument,
        session=date.fromisoformat(row["session"]),
        source=row["source"],
        action=row["action"],
        rule_version=row["rule_version"],
        config_fingerprint=row["config_fingerprint"],
        config_provenance=row["config_provenance"],
        model_version=row["model_version"],
        transition=row["transition"],
        reason=row["reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )
    payload = OpportunityWebhookPayload.model_validate_json(row["payload_json"])
    return event, payload, row["attempts"], row["status"]


def in_quiet_hours(now: datetime) -> bool:
    """21:00–08:00 America/New_York; zoneinfo makes the boundary DST-aware."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("quiet-hour clock must be timezone-aware")
    local = now.astimezone(NEW_YORK).timetz().replace(tzinfo=None)
    return local >= time(21, 0) or local < time(8, 0)


def _event_is_current(
    event: TrackingEvent,
    now: datetime,
    watchlist_file: Path | None,
    ticker_config_path: Path | None,
    lake_dir: Path | None,
    data_dir: Path | None,
    signal_config_path: Path | None = None,
) -> bool:
    """Require exact session, selected config provenance, rule, and model identity."""
    from equity_lake.signals.config import load_signal_config

    if event.config_provenance == "explicit" and signal_config_path is None:
        return False
    selected_provenance: Literal["default", "explicit", "synthetic"] = "explicit" if signal_config_path is not None else "default"
    if selected_provenance != event.config_provenance:
        return False
    try:
        config = load_signal_config(signal_config_path)
        fingerprint = _config_fingerprint(config)
        if fingerprint != event.config_fingerprint:
            return False
        watchlist = load_private_watchlist(watchlist_file, ticker_config_path)
        if not any(row.instrument == event.instrument for row in watchlist.instruments):
            return False
        expected, open_today = _latest_completed_session(event.instrument, now)
        if not open_today or expected is None or expected != event.session:
            return False
        if event.source == "price_sma20":
            raw_outcome = _price_rule(event.instrument, now, lake_dir or LAKE_DIR)
            outcome = _bind_rule_provenance(raw_outcome, fingerprint=fingerprint, provenance=selected_provenance)
        else:
            if not config.is_generator_enabled("ml"):
                return False
            outcome = _ml_rule(
                event.instrument,
                expected,
                config.ml,
                fingerprint=fingerprint,
                provenance=selected_provenance,
            )
        candidate = outcome.candidate
        return (
            outcome.status == "signal"
            and candidate is not None
            and candidate.action == event.action
            and candidate.session == event.session
            and candidate.rule_version == event.rule_version
            and candidate.model_version == event.model_version
            and candidate.config_fingerprint == event.config_fingerprint
        )
    except Exception as exc:
        logger.warning("tracking_event_revalidation_unavailable", event_id=event.event_id, error_type=type(exc).__name__)
        return False


def _mark_delivery(data_dir: Path | None, event_id: str, *, status: str, error_code: str | None = None, increment: bool = False) -> bool:
    connection = _open_database(data_dir, writable=True)
    assert connection is not None
    try:
        connection.execute("BEGIN IMMEDIATE")
        if increment:
            cursor = connection.execute(
                "UPDATE outbox SET status=?,attempts=attempts+1,error_code=?,updated_at=? WHERE event_id=? AND status='pending' AND attempts < ?",
                (status, error_code, datetime.now(UTC).isoformat(), event_id, MAX_DELIVERY_ATTEMPTS),
            )
        else:
            cursor = connection.execute(
                "UPDATE outbox SET status=?,error_code=?,updated_at=? WHERE event_id=? AND status='pending'",
                (status, error_code, datetime.now(UTC).isoformat(), event_id),
            )
        changed = cursor.rowcount == 1
        connection.execute("COMMIT")
        return changed
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _send_http(url: str, payload: OpportunityWebhookPayload) -> None:
    """Credential-bearing client seam. The URL and response body are never logged or persisted."""
    import httpx

    response = httpx.post(url, json=payload.model_dump(mode="json"), timeout=10.0)
    response.raise_for_status()


@contextmanager
def _delivery_lock(data_dir: Path | None) -> Iterator[None]:
    """Serialize local senders across processes; event creation and outbox remain SQLite-transactional."""
    database = tracking_db_path(data_dir)
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = database.with_name(".delivery.lock")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with os.fdopen(descriptor, "r+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    except BaseException:
        raise


def deliver_pending(
    *,
    enabled: bool = False,
    webhook_url: str | None = None,
    sender: Any = None,
    now: datetime | None = None,
    watchlist_file: Path | None = None,
    ticker_config_path: Path | None = None,
    lake_dir: Path | None = None,
    data_dir: Path | None = None,
    signal_config_path: Path | None = None,
) -> DeliverySummary:
    """Deliver a bounded pending batch; the live channel requires an explicit caller opt-in."""
    clock = now or datetime.now(UTC)
    if clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("delivery clock must be timezone-aware")
    if not enabled:
        return DeliverySummary(status="disabled", pending=_count_outbox("pending", data_dir))
    if in_quiet_hours(clock):
        return DeliverySummary(status="quiet_hours", pending=_count_outbox("pending", data_dir))
    if sender is None:
        if not webhook_url:
            raise TrackingError("opportunity webhook is enabled but EQUITY_OPPORTUNITY_WEBHOOK_URL is missing")

        url = webhook_url

        def send_webhook(payload: OpportunityWebhookPayload) -> None:
            _send_http(url, payload)

        sender = send_webhook
    with _delivery_lock(data_dir):
        return _deliver_pending_locked(
            clock=clock,
            sender=sender,
            watchlist_file=watchlist_file,
            ticker_config_path=ticker_config_path,
            lake_dir=lake_dir,
            data_dir=data_dir,
            signal_config_path=signal_config_path,
        )


def _deliver_pending_locked(
    *,
    clock: datetime,
    sender: Any,
    watchlist_file: Path | None,
    ticker_config_path: Path | None,
    lake_dir: Path | None,
    data_dir: Path | None,
    signal_config_path: Path | None,
) -> DeliverySummary:
    """Process a queue while holding the cross-process delivery lock."""
    connection = _open_database(data_dir, writable=False)
    if connection is None:
        return DeliverySummary(status="completed")
    try:
        rows = connection.execute(
            "SELECT e.*,o.status,o.attempts,o.error_code FROM signal_events e JOIN outbox o ON e.event_id=o.event_id "
            "WHERE o.status='pending' ORDER BY e.created_at,e.event_id",
        ).fetchall()
    finally:
        connection.close()
    attempted = sent = failed = expired = 0
    for row in rows:
        event, payload, previous_attempts, _state = _event_from_row(row)
        if previous_attempts >= MAX_DELIVERY_ATTEMPTS:
            _mark_delivery(data_dir, event.event_id, status="failed", error_code="ambiguous_outcome_after_max_attempts")
            failed += 1
            continue
        retryer = Retrying(
            retry=retry_if_exception_type(DeliveryAttemptError),
            stop=stop_after_attempt(MAX_DELIVERY_ATTEMPTS - previous_attempts),
            wait=wait_none(),
            reraise=True,
        )
        try:
            for attempt in retryer:
                with attempt:
                    if not _event_is_current(event, clock, watchlist_file, ticker_config_path, lake_dir, data_dir, signal_config_path):
                        _mark_delivery(data_dir, event.event_id, status="expired", error_code="condition_not_current")
                        raise DeliveryRevalidationError("condition_not_current")
                    if not _mark_delivery(data_dir, event.event_id, status="pending", increment=True):
                        raise DeliveryRevalidationError("reservation_lost")
                    attempted += 1
                    try:
                        sender(payload)
                    except Exception as exc:
                        response = getattr(exc, "response", None)
                        response_status = getattr(response, "status_code", None)
                        code = f"http_{response_status}" if isinstance(response_status, int) else type(exc).__name__
                        _mark_delivery(data_dir, event.event_id, status="pending", error_code=code)
                        raise DeliveryAttemptError(code) from None
                    if not _mark_delivery(data_dir, event.event_id, status="sent", error_code=None):
                        raise DeliveryRevalidationError("reservation_lost_after_send")
                    sent += 1
        except DeliveryRevalidationError:
            expired += 1
        except DeliveryAttemptError:
            _mark_delivery(data_dir, event.event_id, status="failed", error_code="retry_limit_or_send_failure")
            failed += 1
    return DeliverySummary(
        status="completed",
        attempted=attempted,
        sent=sent,
        failed=failed,
        expired=expired,
        pending=_count_outbox("pending", data_dir),
    )


def _count_outbox(status: str, data_dir: Path | None) -> int:
    connection = _open_database(data_dir, writable=False)
    if connection is None:
        return 0
    try:
        row = connection.execute("SELECT COUNT(*) FROM outbox WHERE status=?", (status,)).fetchone()
        return int(row[0])
    except Exception:
        return 0
    finally:
        connection.close()


def build_digest(*, now: datetime | None = None, data_dir: Path | None = None) -> DigestSummary:
    """Combine opportunity inbox and scan failures; stale warnings are independent of quiet hours."""
    snapshot = private_tracking_snapshot(now=now, data_dir=data_dir)
    opportunities = [entry.event for entry in snapshot.recent_events if entry.status in {"pending", "failed"}]
    failures = [
        outcome
        for report in snapshot.recent_reports
        for evaluation in report.evaluations
        for outcome in evaluation.rules
        if outcome.status in {"unavailable", "missing_model", "error"}
    ]
    counts = {status: _count_outbox(status, data_dir) for status in ("pending", "sent", "failed", "expired")}
    return DigestSummary(
        generated_at=snapshot.generated_at,
        opportunities=opportunities,
        failures=failures,
        stale_run_warning=snapshot.stale_run_warning,
        delivery_counts=counts,
    )


def format_schedule_plan() -> dict[str, str | bool]:
    """Describe DST-aware support without registering or activating a scheduler."""
    return {
        "readiness_first": True,
        "daily_target": "17:30",
        "timezone": "America/New_York",
        "quiet_hours": "21:00-08:00 America/New_York",
        "dst_aware": True,
        "scheduler_activated": False,
        "live_delivery_enabled": False,
    }


__all__ = [
    "DeliverySummary",
    "InstrumentTrackingState",
    "DigestSummary",
    "OpportunityWebhookPayload",
    "OutboxObservation",
    "PriceZone",
    "PrivateTrackingSnapshot",
    "PrivateWatchlist",
    "ReadinessObservation",
    "TrackedInstrument",
    "TrackingCandidate",
    "TrackingError",
    "TrackingEvent",
    "TrackingEvaluation",
    "TrackingRuleOutcome",
    "TrackingScanReport",
    "add_instrument",
    "build_digest",
    "deliver_pending",
    "format_schedule_plan",
    "in_quiet_hours",
    "load_private_watchlist",
    "migrate_legacy_watchlist",
    "private_tracking_snapshot",
    "remove_instrument",
    "scan_private_watchlist",
    "tracking_db_path",
    "update_instrument",
    "update_watchlist_settings",
    "watchlist_path",
]
