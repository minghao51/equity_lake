"""Versioned point-in-time availability contracts (ADR-0015).

This module is deliberately independent of storage and provider clients. It
validates source-scoped proof and maps aware source clocks to the first
observable session for the exact instrument venue.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Literal, cast

import exchange_calendars as xc
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from equity_lake.core.instruments import InstrumentKey

PRICE_ADJUSTMENT_VERSION = "split-only-v1"
AVAILABILITY_SCHEMA_VERSION = 4
ACTION_COVERAGE_SCHEMA_VERSION = 2
FEATURE_ACTION_COVERAGE_PROOF_COLUMNS = (
    "action_coverage_market",
    "action_coverage_mic",
    "action_coverage_symbol",
    "action_coverage_source",
    "action_coverage_start",
    "action_coverage_end",
    "action_source_available_at",
    "action_source_valid_until",
    "action_coverage_status",
    "action_coverage_schema_version",
)

_MIC_CALENDAR: dict[str, str] = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
    "XSHG": "XSHG",
    "XSHE": "XSHG",
    "XHKG": "XHKG",
    "XSES": "XSES",
    "XTKS": "JPX",
    "XKRX": "XKRX",
}


class AvailabilityContractError(ValueError):
    """Source availability or coverage cannot be established safely."""


class ActionCoverageProof(BaseModel):
    """Verified complete action coverage for one instrument and source window.

    ``source_available_at`` is the source's own publication timestamp for this
    assertion; ingestion time is not a substitute. ``source_valid_until`` is
    the source-issued freshness endpoint and must cover the decision-session
    close. ``coverage_end`` scopes covered event dates only; it does not prove
    freshness. A complete empty interval is the only accepted proof that no
    actions occurred in that scope.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    mic: str
    symbol: str
    source: str
    coverage_start: date
    coverage_end: date
    source_available_at: datetime
    source_valid_until: datetime
    coverage_status: Literal["complete"] = "complete"
    coverage_schema_version: Literal[2] = 2

    @field_validator("source")
    @classmethod
    def source_is_present(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("coverage source cannot be empty")
        return normalized

    @field_validator("source_available_at")
    @classmethod
    def source_clock_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_available_at must be timezone-aware source time")
        return value.astimezone(UTC)

    @field_validator("source_valid_until")
    @classmethod
    def validity_clock_is_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_valid_until must be timezone-aware source time")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_scope(self) -> ActionCoverageProof:
        InstrumentKey(market=self.market, mic=self.mic, symbol=self.symbol)
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end must be on or after coverage_start")
        if self.source_valid_until < self.source_available_at:
            raise ValueError("source_valid_until must be on or after source_available_at")
        return self

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.market, self.mic, self.symbol)

    def as_row(self) -> dict[str, object]:
        return self.model_dump()


def validate_action_coverage_for_decision(proof: ActionCoverageProof, instrument: InstrumentKey, decision_date: date) -> date:
    """Validate source proof at a feature decision's exact venue-session close.

    Returns the source's first observable session so callers can verify the
    corresponding numeric age flag. The source-issued expiry is the only
    freshness limit; it is never inferred from age or coverage version.
    """
    if proof.identity != instrument.tuple:
        raise AvailabilityContractError(f"coverage proof identity does not match {instrument.display_key}")
    if proof.coverage_start > decision_date or proof.coverage_end < decision_date:
        raise AvailabilityContractError(f"coverage proof does not cover decision date {decision_date}")
    source_session = source_observable_session(instrument, proof.source_available_at)
    if source_session > decision_date:
        raise AvailabilityContractError(f"coverage proof was not source-available by decision date {decision_date}")
    if proof.source_valid_until < decision_session_close(instrument, decision_date):
        raise AvailabilityContractError(f"source validity does not cover {instrument.display_key} through decision-session close")
    return source_session


def action_coverage_feature_fields(proof: ActionCoverageProof) -> dict[str, object]:
    """Return the source proof fields persisted beside each Gold feature row."""
    return {
        "action_coverage_market": proof.market,
        "action_coverage_mic": proof.mic,
        "action_coverage_symbol": proof.symbol,
        "action_coverage_source": proof.source,
        "action_coverage_start": proof.coverage_start,
        "action_coverage_end": proof.coverage_end,
        "action_source_available_at": proof.source_available_at,
        "action_source_valid_until": proof.source_valid_until,
        "action_coverage_status": proof.coverage_status,
        "action_coverage_schema_version": proof.coverage_schema_version,
    }


def validate_feature_action_coverage(row: Mapping[str, object]) -> ActionCoverageProof:
    """Validate persisted feature proof using the shared venue-close contract."""
    try:
        instrument = InstrumentKey.model_validate({column: row[column] for column in ("market", "mic", "symbol")})
        proof = ActionCoverageProof.model_validate(
            {
                "market": row["action_coverage_market"],
                "mic": row["action_coverage_mic"],
                "symbol": row["action_coverage_symbol"],
                "source": row["action_coverage_source"],
                "coverage_start": row["action_coverage_start"],
                "coverage_end": row["action_coverage_end"],
                "source_available_at": row["action_source_available_at"],
                "source_valid_until": row["action_source_valid_until"],
                "coverage_status": row["action_coverage_status"],
                "coverage_schema_version": row["action_coverage_schema_version"],
            }
        )
        raw_date = row["date"]
        if isinstance(raw_date, datetime):
            decision_date = raw_date.date()
        elif isinstance(raw_date, date):
            decision_date = raw_date
        elif isinstance(raw_date, str):
            decision_date = date.fromisoformat(raw_date[:10])
        else:
            raise ValueError("feature decision date is missing or invalid")
        if row.get("availability_schema_version") != AVAILABILITY_SCHEMA_VERSION:
            raise ValueError("availability schema version is missing or incompatible")
        if row.get("action_coverage") != 1:
            raise ValueError("action coverage flag is missing or unavailable")
        source_session = validate_action_coverage_for_decision(proof, instrument, decision_date)
        age = row.get("action_source_age_days")
        expected_age = (decision_date - source_session).days
        if not isinstance(age, int) or isinstance(age, bool) or age != expected_age:
            raise ValueError("action source age does not match verified source publication time")
        return proof
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, AvailabilityContractError):
            raise
        raise AvailabilityContractError(f"feature action-coverage proof is invalid or unavailable: {exc}") from exc


def decision_session_close(instrument: InstrumentKey, decision_date: date) -> datetime:
    """Return the last venue close timestamp on or before the decision date."""
    calendar_name = _MIC_CALENDAR.get(instrument.mic)
    if calendar_name is None:
        raise AvailabilityContractError(f"no observable-session calendar for MIC {instrument.mic!r}")
    calendar = xc.get_calendar(calendar_name)
    sessions = calendar.sessions_in_range((decision_date - timedelta(days=14)).isoformat(), decision_date.isoformat())
    if not len(sessions):
        raise AvailabilityContractError(f"no venue session within 14 days before decision date {decision_date}")
    session = str(sessions[-1])[:10]
    close = cast(datetime, calendar.session_close(session).to_pydatetime())
    if close.tzinfo is None:
        close = close.replace(tzinfo=UTC)
    return close.astimezone(UTC)


def source_observable_session(instrument: InstrumentKey, source_time: datetime) -> date:
    """Map an aware source timestamp to the first venue session observable.

    Publications before that session's close map to that session (including
    pre-market publications); off-session and after-close publications map to
    the next session. The venue comes from the canonical MIC, never the market
    union or ticker spelling.
    """
    if source_time.tzinfo is None or source_time.utcoffset() is None:
        raise AvailabilityContractError("source clock is unknown: timezone-aware timestamp required")
    calendar_name = _MIC_CALENDAR.get(instrument.mic)
    if calendar_name is None:
        raise AvailabilityContractError(f"no observable-session calendar for MIC {instrument.mic!r}")
    timestamp = source_time.astimezone(UTC)
    first_day = timestamp.date()
    calendar = xc.get_calendar(calendar_name)
    sessions = calendar.sessions_in_range(first_day.isoformat(), (first_day + timedelta(days=14)).isoformat())
    for session in sessions:
        session_close = calendar.session_close(session).to_pydatetime()
        if session_close.tzinfo is None:
            session_close = session_close.replace(tzinfo=UTC)
        if timestamp <= session_close.astimezone(UTC):
            return cast(date, session.date())
    raise AvailabilityContractError(f"no venue session within 14 days of source time {source_time.isoformat()}")


__all__ = [
    "ACTION_COVERAGE_SCHEMA_VERSION",
    "FEATURE_ACTION_COVERAGE_PROOF_COLUMNS",
    "AVAILABILITY_SCHEMA_VERSION",
    "PRICE_ADJUSTMENT_VERSION",
    "ActionCoverageProof",
    "AvailabilityContractError",
    "action_coverage_feature_fields",
    "decision_session_close",
    "source_observable_session",
    "validate_action_coverage_for_decision",
    "validate_feature_action_coverage",
]
