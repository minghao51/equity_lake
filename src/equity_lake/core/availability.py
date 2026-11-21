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
AVAILABILITY_SCHEMA_VERSION = 5
ACTION_COVERAGE_SCHEMA_VERSION = 3
#: Named coverage bases (ADR-0032 decision 5). ``source_clock`` is the strict, default
#: and only point-in-time basis; ``vendor_events_no_clock`` asserts vendor-event
#: completeness only and carries no clock at all.
CoverageBasis = Literal["source_clock", "vendor_events_no_clock"]
COVERAGE_BASES: tuple[CoverageBasis, ...] = ("source_clock", "vendor_events_no_clock")
#: Availability versions whose rows imply the strict basis (decision 7: v4 rows are
#: read as ``source_clock`` exactly as before the basis column existed).
IMPLICIT_STRICT_AVAILABILITY_SCHEMA_VERSION = 4
#: The fixed disclosure every weak-basis research artifact must carry verbatim
#: (ADR-0034 decision 3). A single machine-checkable home so a report, a metrics file
#: and a prediction row cannot drift into different wording.
VENDOR_EVENTS_NO_CLOCK_DISCLOSURE = (
    "Computed on a vendor-event-completeness basis with no source clock (ADR-0032/0034). "
    "Not point-in-time evidence, not indicative of any real strategy's live performance, and not a promotion."
)
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
    "action_coverage_basis",
)

_MIC_CALENDAR: dict[str, str] = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
    "ARCX": "XNYS",
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
    coverage_basis: Literal["source_clock"] = "source_clock"
    # v2 rows predate the basis stamp and are the strict basis by definition.
    coverage_schema_version: Literal[2, 3] = 3

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


class VendorEventCoverageProof(BaseModel):
    """Vendor-event completeness with **no source clock** (ADR-0032 decision 6).

    This is the explicit, weaker basis: it records that the stored events are the
    vendor's own published list for the instrument over ``coverage_start``..
    ``coverage_end`` — and nothing about when any event became visible. There is no
    clock field, so ingestion or fetch time cannot be laundered into source time
    (ADR-0015 decision 4). It never satisfies a point-in-time consumer.

    ``coverage_end`` scopes covered event dates only; freshness ends at the action
    store's per-ticker watermark and is reported, never promised (ADR-0032 d3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    mic: str
    symbol: str
    source: str
    coverage_start: date
    coverage_end: date
    coverage_status: Literal["vendor_events_complete"] = "vendor_events_complete"
    coverage_basis: Literal["vendor_events_no_clock"] = "vendor_events_no_clock"
    coverage_schema_version: Literal[3] = 3

    @field_validator("source")
    @classmethod
    def source_is_present(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("coverage source cannot be empty")
        return normalized

    @model_validator(mode="after")
    def validate_scope(self) -> VendorEventCoverageProof:
        InstrumentKey(market=self.market, mic=self.mic, symbol=self.symbol)
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end must be on or after coverage_start")
        return self

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.market, self.mic, self.symbol)

    def as_row(self) -> dict[str, object]:
        return self.model_dump()


CoverageProof = ActionCoverageProof | VendorEventCoverageProof


def coverage_proof_basis(row: Mapping[str, object]) -> CoverageBasis:
    """Basis a persisted coverage row declares, with legacy rows implying strict.

    The basis is read from the stamp alone (ADR-0032 decision 5); a row without the
    column predates it and is a ``source_clock`` proof.
    """
    basis = row.get("coverage_basis")
    if basis is None:
        return "source_clock"
    if basis not in COVERAGE_BASES:
        raise ValueError(f"unknown coverage basis {basis!r} (expected one of {COVERAGE_BASES})")
    return cast(CoverageBasis, basis)


def parse_coverage_proof(row: Mapping[str, object]) -> CoverageProof:
    """Validate one persisted coverage row against the model its stamp names."""
    if coverage_proof_basis(row) == "vendor_events_no_clock":
        payload = dict(row)
        for column in ("source_available_at", "source_valid_until"):
            if payload.get(column) is not None:
                raise ValueError(f"vendor_events_no_clock proof must not carry {column}")
            payload.pop(column, None)
        return VendorEventCoverageProof.model_validate(payload)
    return ActionCoverageProof.model_validate(dict(row))


def validate_action_coverage_for_decision(proof: ActionCoverageProof, instrument: InstrumentKey, decision_date: date) -> date:
    """Validate source proof at a feature decision's exact venue-session close.

    Returns the source's first observable session so callers can verify the
    corresponding numeric age flag. The source-issued expiry is the only
    freshness limit; it is never inferred from age or coverage version.
    """
    if coverage_proof_basis(proof.model_dump()) != "source_clock":
        raise AvailabilityContractError(f"{instrument.display_key}: vendor_events_no_clock coverage cannot establish a point-in-time decision")
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


def action_coverage_feature_fields(proof: CoverageProof) -> dict[str, object]:
    """Return the source proof fields persisted beside each Gold feature row.

    The basis is a data stamp, never inferred by a reader (ADR-0032 decision 5). A
    weak row carries null clocks and null age, and its status names what it actually
    proves: the vendor's event list, complete, with no source clock.
    """
    fields: dict[str, object] = {
        "action_coverage_market": proof.market,
        "action_coverage_mic": proof.mic,
        "action_coverage_symbol": proof.symbol,
        "action_coverage_source": proof.source,
        "action_coverage_start": proof.coverage_start,
        "action_coverage_end": proof.coverage_end,
        "action_coverage_status": proof.coverage_status,
        "action_coverage_schema_version": proof.coverage_schema_version,
        "action_coverage_basis": proof.coverage_basis,
    }
    if isinstance(proof, VendorEventCoverageProof):
        fields["action_source_available_at"] = None
        fields["action_source_valid_until"] = None
        return fields
    fields["action_source_available_at"] = proof.source_available_at
    fields["action_source_valid_until"] = proof.source_valid_until
    return fields


def _feature_decision_date(row: Mapping[str, object]) -> date:
    raw_date = row["date"]
    if isinstance(raw_date, datetime):
        return raw_date.date()
    if isinstance(raw_date, date):
        return raw_date
    if isinstance(raw_date, str):
        return date.fromisoformat(raw_date[:10])
    raise ValueError("feature decision date is missing or invalid")


def validate_feature_action_coverage(row: Mapping[str, object]) -> ActionCoverageProof:
    """Validate persisted feature proof using the shared venue-close contract.

    Strict and point-in-time by construction (ADR-0032 decision 4): a row stamped
    ``vendor_events_no_clock`` is refused here, and so is a v5 row whose basis stamp
    is missing. Only a v4 legacy row may omit the stamp, and it is strict.
    """
    try:
        availability_version = row.get("availability_schema_version")
        if availability_version not in (IMPLICIT_STRICT_AVAILABILITY_SCHEMA_VERSION, AVAILABILITY_SCHEMA_VERSION):
            raise ValueError("availability schema version is missing or incompatible")
        basis = row.get("action_coverage_basis")
        if basis is None:
            if availability_version != IMPLICIT_STRICT_AVAILABILITY_SCHEMA_VERSION:
                raise ValueError("action coverage basis stamp is missing on an availability v5 row")
            basis = "source_clock"
        if basis != "source_clock":
            raise AvailabilityContractError(f"{basis} coverage basis cannot establish point-in-time availability for a feature row")
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
        decision_date = _feature_decision_date(row)
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


def validate_weak_feature_action_coverage(row: Mapping[str, object]) -> VendorEventCoverageProof:
    """Validate a vendor-stamped feature row against the weak contract only.

    A weak row must carry its basis stamp, null clocks and null age, and a coverage
    window that reaches its decision date. It is never validated as, or upgraded to,
    a point-in-time proof (ADR-0032 decisions 4-5).
    """
    try:
        basis = row.get("action_coverage_basis")
        if basis != "vendor_events_no_clock":
            raise ValueError(f"expected a vendor_events_no_clock row, found basis {basis!r}")
        if row.get("availability_schema_version") != AVAILABILITY_SCHEMA_VERSION:
            raise ValueError("availability schema version is missing or incompatible")
        if row.get("action_coverage") != 1:
            raise ValueError("action coverage flag is missing or unavailable")
        for clock_column in ("action_source_available_at", "action_source_valid_until", "action_source_age_days"):
            if row.get(clock_column) is not None:
                raise ValueError(f"weak coverage row must not carry {clock_column}")
        proof = VendorEventCoverageProof.model_validate(
            {
                "market": row["action_coverage_market"],
                "mic": row["action_coverage_mic"],
                "symbol": row["action_coverage_symbol"],
                "source": row["action_coverage_source"],
                "coverage_start": row["action_coverage_start"],
                "coverage_end": row["action_coverage_end"],
                "coverage_status": row["action_coverage_status"],
                "coverage_schema_version": row["action_coverage_schema_version"],
                "coverage_basis": row["action_coverage_basis"],
            }
        )
        instrument = InstrumentKey.model_validate({column: row[column] for column in ("market", "mic", "symbol")})
        if proof.identity != instrument.tuple:
            raise ValueError(f"weak coverage proof identity does not match {instrument.display_key}")
        decision_date = _feature_decision_date(row)
        if not (proof.coverage_start <= decision_date <= proof.coverage_end):
            raise ValueError(f"weak coverage proof does not cover decision date {decision_date}")
        return proof
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, AvailabilityContractError):
            raise
        raise AvailabilityContractError(f"weak feature action-coverage proof is invalid or unavailable: {exc}") from exc


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
    "COVERAGE_BASES",
    "FEATURE_ACTION_COVERAGE_PROOF_COLUMNS",
    "IMPLICIT_STRICT_AVAILABILITY_SCHEMA_VERSION",
    "AVAILABILITY_SCHEMA_VERSION",
    "PRICE_ADJUSTMENT_VERSION",
    "VENDOR_EVENTS_NO_CLOCK_DISCLOSURE",
    "ActionCoverageProof",
    "AvailabilityContractError",
    "CoverageBasis",
    "CoverageProof",
    "VendorEventCoverageProof",
    "action_coverage_feature_fields",
    "coverage_proof_basis",
    "decision_session_close",
    "parse_coverage_proof",
    "source_observable_session",
    "validate_action_coverage_for_decision",
    "validate_feature_action_coverage",
    "validate_weak_feature_action_coverage",
]
