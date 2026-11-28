"""Audit whether text events could have been visible to a close-of-session decision (stage R1).

Module CLI entrypoint (``python -m``, invoked by the operator and by the unit tests): plain
``print``/``stderr`` output instead of structlog, by devtool convention.

The one question this answers before anything is modelled: *could a close-of-session decision
have seen this text, and is the evidence sufficient to test whether it adds anything?* It is
read-only and offline — no network, no LLM, no model fit, and no write into the lake. The single
write path is ``--out``, an auxiliary artifact (ADR-0006: not cataloged, not pointblank-validated);
without it the run touches nothing.

Rules, all failing closed with a named reason (a refused event is never a silent skip):

* **Clock contract.** A missing, naive, date-only or unparseable ``source_available_at`` is
  ``clock unknown``; an ``evidence_kind`` outside
  :data:`~equity_lake.core.source_clock.SOURCE_ISSUED_CLOCK_KINDS` is
  ``clock is not source-issued`` (ADR-0015 decision 4). An aware timestamp that is exactly
  midnight in the venue's own timezone is *not* forgiven: it is counted and named as suspicious,
  and only the clock/evidence contract decides admission. No clock is ever inferred from
  ``filed_on``, ingest time or date equality.
* **Observable session.** :func:`~equity_lake.core.availability.source_observable_session` maps
  each admitted event to the first venue session that could see it; the venue/close/holiday logic
  is reused, never reimplemented.
* **Duplicates vs conflicts.** Identical ``(market, mic, symbol, document_id, section,
  content_digest)`` at the same clock is one duplicate copy, counted once. Differing content
  digests for the same key at the same clock is an ``equal-clock conflict``. A later clock for the
  same ``(market, mic, symbol, document_id, section)`` is a revision: at each decision session only
  the latest revision observable by then is visible, and the rest are counted as superseded.
* **Coverage and leakage.** For every decision session: distinct visible documents, distinct
  visible issuers, visible events that are superseded revisions, and admitted events whose
  observable session is after it (correctly invisible). Plus per-issuer rows and first/last
  observable session.
* **Invariants, computed.** Appending synthetic future-dated events must leave every earlier
  session's as-of set unchanged, and as-of document sets must be non-decreasing in the decision
  session. A failing check is a refusal.

The report is a frozen, ``extra="forbid"`` Pydantic model and is value-free: counts, dates,
identifiers and reasons — never document body text, prices or probabilities.

Exit codes: ``0`` clean, ``1`` any refusal (including a conflict or a failed check), ``2`` nothing
to audit (no events in scope, or no venue session in ``--start``/``--end``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Final, Literal
from zoneinfo import ZoneInfo

import polars as pl
from pydantic import BaseModel, ConfigDict, ValidationError

from equity_lake.core.availability import AvailabilityContractError, source_observable_session
from equity_lake.core.calendar import instrument_sessions_between, is_instrument_trading_day
from equity_lake.core.instruments import InstrumentKey
from equity_lake.core.paths import PRICE_MARKETS
from equity_lake.core.source_clock import SOURCE_ISSUED_CLOCK_KINDS

REPORT_SCHEMA_VERSION: Final[Literal[1]] = 1

#: Columns the audit needs; anything else is tolerated (``content_digest`` is used when present).
REQUIRED_COLUMNS: tuple[str, ...] = (
    "market",
    "mic",
    "symbol",
    "document_id",
    "section",
    "source",
    "source_available_at",
    "evidence_kind",
)
OPTIONAL_COLUMNS: tuple[str, ...] = ("content_digest", "revision_of", "filed_on")

#: Named refusal reasons. Every refusal the audit can raise is one of these strings.
CLOCK_UNKNOWN = "clock unknown"
CLOCK_NOT_SOURCE_ISSUED = "clock is not source-issued"
SECTION_MISSING = "section missing"
DOCUMENT_IDENTITY_MISSING = "document identity missing"
IDENTITY_UNESTABLISHED = "instrument identity unestablished"
OBSERVABLE_SESSION_UNESTABLISHED = "observable session unestablished"
CONTENT_DIGEST_MISSING = "content digest missing"
EQUAL_CLOCK_CONFLICT = "equal-clock conflict"
FUTURE_SAFETY_FAILED = "future-safety invariant failed"
SESSION_MAPPING_FAILED = "session-mapping invariant failed"
#: The two invariant names, and the refusal each one raises when it fails.
SESSION_MAPPING_CHECK = "session-mapping invariant"
FUTURE_SAFETY_CHECK = "future-safety invariant"
_CHECK_REFUSALS: Final[dict[str, str]] = {SESSION_MAPPING_CHECK: SESSION_MAPPING_FAILED, FUTURE_SAFETY_CHECK: FUTURE_SAFETY_FAILED}

#: The one warning: a timestamp that is exactly local midnight is a fallback shape, not a clock.
MIDNIGHT_WARNING = (
    "source_available_at is exactly 00:00:00 in the venue's own timezone; a date-only/midnight fallback is not a "
    "verified first-public clock (ADR-0015 decision 4), so only the clock/evidence contract decides admission"
)

SYNTHETIC_DOCUMENT_ID: Final[str] = "<synthetic-future>"
SYNTHETIC_SECTION: Final[str] = "synthetic"

#: ``(market, mic, symbol, document_id)`` — what a decision session can see.
DocumentKey = tuple[str, str, str, str]
#: ``(market, mic, symbol, document_id, section)`` — one revision chain.
ChainKey = tuple[str, str, str, str, str]

MAX_ISSUER_LINES = 10
MAX_COVERAGE_EXAMPLES = 1


class AuditInputError(ValueError):
    """The event input cannot be audited as given (unreadable, or a required column is absent)."""


class ObservableSessionRow(BaseModel):
    """How many admitted events map to one observable session."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observable_session: date
    events: int


class IssuerCoverage(BaseModel):
    """Coverage for one ``(market, MIC, symbol)`` issuer over the decision-session grid."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    market: str
    mic: str
    symbol: str
    decision_sessions: int
    sessions_with_visible_document: int
    events: int
    documents: int
    first_observable_session: date | None
    last_observable_session: date | None


class SessionCoverage(BaseModel):
    """What one decision session could see, and what it correctly could not."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    decision_session: date
    visible_documents: int
    visible_issuers: int
    superseded_events: int
    invisible_events: int


class Check(BaseModel):
    """One invariant, computed from the data rather than asserted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    passed: bool
    detail: str


class Refusal(BaseModel):
    """One named refusal, aggregated over the events it applies to."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    reason: str
    count: int
    detail: str


class AvailabilityReport(BaseModel):
    """The value-free audit report: counts, dates, identifiers and reasons only."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_schema_version: Literal[1] = REPORT_SCHEMA_VERSION
    events_path: str
    start: date
    end: date
    events_read: int
    events_admitted: int
    duplicate_copies: int
    superseded_events: int
    suspicious_midnight_clocks: int
    decision_sessions: int
    first_observable_session: date | None
    last_observable_session: date | None
    observable_sessions: tuple[ObservableSessionRow, ...]
    issuers: tuple[IssuerCoverage, ...]
    sessions: tuple[SessionCoverage, ...]
    checks: tuple[Check, ...]
    warnings: tuple[str, ...]
    refusals: tuple[Refusal, ...]

    @property
    def refused(self) -> bool:
        """True when anything was refused, conflicted or failed a check."""
        return bool(self.refusals)


@dataclass(frozen=True, slots=True)
class Event:
    """One admitted text event, already mapped to its observable session."""

    instrument: InstrumentKey
    document_id: str
    section: str
    clock: datetime
    observable_session: date
    content_digest: str | None

    @property
    def document_key(self) -> DocumentKey:
        return (self.instrument.market, self.instrument.mic, self.instrument.symbol, self.document_id)

    @property
    def chain_key(self) -> ChainKey:
        return (*self.document_key, self.section)


@dataclass
class _RefusalEntry:
    count: int = 0
    detail: str = ""


class _RefusalLedger:
    """Accumulates named refusals in first-seen order, keeping one value-free example each."""

    def __init__(self) -> None:
        self._by_reason: dict[str, _RefusalEntry] = {}

    def add(self, reason: str, count: int, detail: str) -> None:
        entry = self._by_reason.setdefault(reason, _RefusalEntry(detail=detail))
        entry.count += count
        if not entry.detail:
            entry.detail = detail

    def rows(self) -> tuple[Refusal, ...]:
        return tuple(Refusal(reason=reason, count=entry.count, detail=entry.detail) for reason, entry in self._by_reason.items())


def _text(value: object) -> str:
    """One cell as stripped text; a missing cell is the empty string."""
    if value is None:
        return ""
    return str(value).strip()


def parse_clock(value: object) -> datetime | None:
    """The aware timestamp in a cell, or ``None`` when the cell carries no usable clock.

    A missing cell, an empty string, a bare date (the midnight fallback), an unparseable string
    and a naive timestamp all answer ``None``: none of them is a source clock, and this module
    never repairs one from ``filed_on``, ingest time or date equality.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if _is_aware(value) else None
    if isinstance(value, date):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if _is_aware(parsed) else None


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def venue_timezone(market: str) -> str:
    """The venue timezone for a canonical market (UTC when the registry has no entry)."""
    entry = PRICE_MARKETS.get(market)
    return "UTC" if entry is None else entry.timezone


def is_local_midnight(clock: datetime, market: str) -> bool:
    """True when an aware timestamp is exactly 00:00:00 in the venue's own timezone."""
    local = clock.astimezone(ZoneInfo(venue_timezone(market)))
    return (local.hour, local.minute, local.second, local.microsecond) == (0, 0, 0, 0)


def load_event_rows(path: Path) -> list[dict[str, object]]:
    """Read the event file (Parquet or CSV) into row dicts, refusing an unusable input.

    Raises:
        AuditInputError: the file is absent, unreadable, of another format, or lacks a required
            column (the missing names are reported; no row is guessed).
    """
    path = Path(path)
    if not path.is_file():
        raise AuditInputError(f"{path} is not a readable file")
    suffix = path.suffix.lower()
    try:
        if suffix in {".parquet", ".pq"}:
            frame = pl.read_parquet(path)
        elif suffix in {".csv", ".tsv"}:
            frame = pl.read_csv(path, infer_schema_length=0, separator="\t" if suffix == ".tsv" else ",")
        else:
            raise AuditInputError(f"{path} must be .parquet or .csv; other formats have no defined columns here")
        missing = [column for column in REQUIRED_COLUMNS if column not in frame.columns]
        if missing:
            raise AuditInputError(f"{path} is missing required columns: {', '.join(missing)}")
        return frame.to_dicts()
    except AuditInputError:
        raise
    except Exception as exc:  # a damaged/undecodable file raises library-specific errors
        raise AuditInputError(f"{path} could not be read as {suffix or 'an event table'} ({exc.__class__.__name__}: {exc})") from exc


def revision_chains(events: Sequence[Event]) -> dict[ChainKey, tuple[Event, ...]]:
    """Group admitted events into revision chains (one clock-ordered tuple per key)."""
    chains: dict[ChainKey, list[Event]] = {}
    for event in events:
        chains.setdefault(event.chain_key, []).append(event)
    return {key: tuple(sorted(members, key=lambda event: event.clock)) for key, members in chains.items()}


def superseded_at(chains: Mapping[ChainKey, tuple[Event, ...]], decision_session: date) -> int:
    """How many events a later revision supersedes for a decision at ``decision_session``."""
    total = 0
    for chain in chains.values():
        visible = sum(1 for event in chain if event.observable_session <= decision_session)
        total += max(0, visible - 1)
    return total


def as_of_documents(events: Sequence[Event], sessions: Sequence[date]) -> dict[date, frozenset[DocumentKey]]:
    """The visible document set at each decision session (observable session ``<=`` the session)."""
    return {session: frozenset(event.document_key for event in events if event.observable_session <= session) for session in sessions}


def synthetic_future_event(instrument: InstrumentKey, end: date) -> Event:
    """A synthetic event dated after ``end``; appending it must not change any earlier as-of set."""
    local = datetime.combine(end + timedelta(days=2), time(12, 0), tzinfo=ZoneInfo(venue_timezone(instrument.market)))
    clock = local.astimezone(UTC)
    return Event(
        instrument=instrument,
        document_id=SYNTHETIC_DOCUMENT_ID,
        section=SYNTHETIC_SECTION,
        clock=clock,
        observable_session=source_observable_session(instrument, clock),
        content_digest=None,
    )


def session_mapping_check(events: Sequence[Event]) -> Check:
    """Falsifiable properties every admitted event's observable session must satisfy.

    Unlike an "as-of sets only grow" assertion — which the by-construction snapshot makes true
    by definition — these three properties are measured from the data and break if
    :func:`~equity_lake.core.availability.source_observable_session` regresses: (a) the observable
    session is a trading session for that exact issuer, (b) it does not precede the clock's own UTC
    date, and (c) for one issuer, clock order never inverts observable-session order. Returning
    ``clock.date()``, skipping the venue-close comparison or reading the wrong venue calendar
    violates at least one of them.
    """
    violations: list[str] = []
    for event in events:
        label = f"{event.instrument.mic}:{event.instrument.symbol}/{event.document_id}/{event.section}"
        try:
            is_session = is_instrument_trading_day(event.instrument, event.observable_session)
        except ValueError as exc:  # no calendar for this MIC: refuse rather than accept the mapping
            violations.append(f"{label}: {exc}")
            continue
        if not is_session:
            violations.append(f"{label} maps to {event.observable_session.isoformat()}, which is no trading session")
            continue
        utc_date = event.clock.astimezone(UTC).date()
        if event.observable_session < utc_date:
            violations.append(f"{label} maps to {event.observable_session.isoformat()}, before the clock's UTC date {utc_date.isoformat()}")
    by_instrument: dict[InstrumentKey, list[Event]] = {}
    for event in events:
        by_instrument.setdefault(event.instrument, []).append(event)
    for instrument, own in by_instrument.items():
        ordered = sorted(own, key=lambda event: event.clock)
        for earlier, later in zip(ordered, ordered[1:], strict=False):
            if earlier.observable_session > later.observable_session:
                violations.append(
                    f"{instrument.mic}:{instrument.symbol}: the later clock maps to {later.observable_session.isoformat()}, "
                    f"before the earlier clock's {earlier.observable_session.isoformat()}"
                )
    passed = not violations
    if passed:
        detail = f"{len(events)} events map to a trading session at or after the clock's UTC date, non-decreasing in clock order"
    else:
        detail = f"{violations[0]} ({len(violations)} violations)" if len(violations) > 1 else violations[0]
    return Check(name=SESSION_MAPPING_CHECK, passed=passed, detail=detail)


def future_safety_check(
    before: Mapping[date, frozenset[DocumentKey]],
    after: Mapping[date, frozenset[DocumentKey]],
    synthetic_sessions: Sequence[date],
    last_session: date | None,
) -> Check:
    """Guard on the synthetic events' own session mapping, not proof that leakage is impossible.

    The synthetic events are dated after the window, so each must map beyond the last decision
    session and appending them must leave the recorded as-of sets identical. That proves the
    mapping seam carries a later clock to a later session for these synthetic rows; it cannot
    prove every real future document is invisible, so it is reported as a guard and not as
    independent leakage evidence.
    """
    if last_session is not None:
        leaked = [session for session in synthetic_sessions if session <= last_session]
        if leaked:
            return Check(
                name=FUTURE_SAFETY_CHECK,
                passed=False,
                detail=f"a synthetic future event is observable at {leaked[0].isoformat()}, inside the decision window",
            )
    changed = [session for session in sorted(before) if before[session] != after.get(session, frozenset())]
    passed = not changed
    count = len(synthetic_sessions)
    detail = (
        f"the {count} synthetic future-dated event{'' if count == 1 else 's'} mapped beyond the last decision session and left every "
        f"as-of set unchanged across {len(before)} decision sessions (a guard on the mapping seam, not proof leakage is impossible)"
        if passed
        else f"appending future events changed the as-of set at {changed[0].isoformat()} ({len(changed)} of {len(before)} sessions)"
    )
    return Check(name=FUTURE_SAFETY_CHECK, passed=passed, detail=detail)


def _admit(
    rows: Sequence[Mapping[str, object]],
) -> tuple[list[Event], set[InstrumentKey], int, int, _RefusalLedger]:
    """Validate the clock/evidence/section contract, deduplicate, and map observable sessions."""
    ledger = _RefusalLedger()
    identities: set[InstrumentKey] = set()
    groups: dict[tuple[str, str, str, str, str, datetime], list[tuple[InstrumentKey, str, str, datetime, str | None]]] = {}
    for row in rows:
        try:
            instrument = InstrumentKey.model_validate(
                {"market": _text(row.get("market")), "mic": _text(row.get("mic")), "symbol": _text(row.get("symbol"))}
            )
        except ValidationError as exc:
            ledger.add(
                IDENTITY_UNESTABLISHED,
                1,
                f"market={_text(row.get('market'))!r} mic={_text(row.get('mic'))!r} symbol={_text(row.get('symbol'))!r}: {str(exc).splitlines()[0]}",
            )
            continue
        identities.add(instrument)
        document_id = _text(row.get("document_id"))
        if not document_id:
            ledger.add(DOCUMENT_IDENTITY_MISSING, 1, f"{instrument.mic}:{instrument.symbol} carries no document_id")
            continue
        section = _text(row.get("section"))
        if not section:
            ledger.add(SECTION_MISSING, 1, f"{instrument.mic}:{instrument.symbol}/{document_id} carries no section")
            continue
        clock = parse_clock(row.get("source_available_at"))
        if clock is None:
            ledger.add(CLOCK_UNKNOWN, 1, f"{instrument.mic}:{instrument.symbol}/{document_id}/{section} has no timezone-aware source_available_at")
            continue
        evidence_kind = _text(row.get("evidence_kind"))
        if evidence_kind not in SOURCE_ISSUED_CLOCK_KINDS:
            ledger.add(
                CLOCK_NOT_SOURCE_ISSUED,
                1,
                f"{instrument.mic}:{instrument.symbol}/{document_id}/{section} cites {evidence_kind!r}; source-issued evidence is one of "
                f"{sorted(SOURCE_ISSUED_CLOCK_KINDS)}",
            )
            continue
        digest = _text(row.get("content_digest")) or None
        clock_utc = clock.astimezone(UTC)
        key = (instrument.market, instrument.mic, instrument.symbol, document_id, section, clock_utc)
        groups.setdefault(key, []).append((instrument, document_id, section, clock_utc, digest))

    admitted: list[Event] = []
    duplicate_copies = 0
    midnight = 0
    for key, members in groups.items():
        # Deduplication is only evidence-backed: every member must carry a digest, and they must
        # agree. A missing digest is an unproven copy claim, never an implicit duplicate.
        missing = [member for member in members if member[4] is None]
        if missing:
            ledger.add(
                CONTENT_DIGEST_MISSING,
                len(members),
                f"{key[2]}/{key[3]}/{key[4]} at {key[5].isoformat()}: {len(missing)} of {len(members)} copies carry no "
                "content_digest, so identical-looking copies cannot be proven identical",
            )
            continue
        digests = {member[4] for member in members}
        if len(digests) > 1:
            ledger.add(
                EQUAL_CLOCK_CONFLICT,
                len(members),
                f"{key[2]}/{key[3]}/{key[4]} at {key[5].isoformat()} carries {len(digests)} distinct content digests, "
                "so the copies disagree about what the document is",
            )
            continue
        duplicate_copies += len(members) - 1
        instrument, document_id, section, clock_utc, digest = members[0]
        try:
            observable = source_observable_session(instrument, clock_utc)
        except AvailabilityContractError as exc:
            ledger.add(OBSERVABLE_SESSION_UNESTABLISHED, 1, f"{instrument.mic}:{instrument.symbol}/{document_id}/{section}: {exc}")
            continue
        if is_local_midnight(clock_utc, instrument.market):
            midnight += 1
        admitted.append(
            Event(
                instrument=instrument,
                document_id=document_id,
                section=section,
                clock=clock_utc,
                observable_session=observable,
                content_digest=digest,
            )
        )
    return admitted, identities, duplicate_copies, midnight, ledger


def _issuer_rows(
    admitted: Sequence[Event],
    grid: Mapping[InstrumentKey, tuple[date, ...]],
    snapshots: Mapping[date, frozenset[DocumentKey]],
) -> tuple[IssuerCoverage, ...]:
    rows: list[IssuerCoverage] = []
    for instrument in sorted(grid, key=lambda item: (item.market, item.mic, item.symbol)):
        identity = (instrument.market, instrument.mic, instrument.symbol)
        own = [event for event in admitted if event.instrument == instrument]
        sessions = grid[instrument]
        documents = {event.document_id for event in own}
        observable = [event.observable_session for event in own]
        rows.append(
            IssuerCoverage(
                market=instrument.market,
                mic=instrument.mic,
                symbol=instrument.symbol,
                decision_sessions=len(sessions),
                sessions_with_visible_document=sum(
                    1 for session in sessions if any(key[:3] == identity for key in snapshots.get(session, frozenset()))
                ),
                events=len(own),
                documents=len(documents),
                first_observable_session=min(observable) if observable else None,
                last_observable_session=max(observable) if observable else None,
            )
        )
    return tuple(rows)


def _observable_session_rows(admitted: Sequence[Event]) -> tuple[ObservableSessionRow, ...]:
    counts: dict[date, int] = {}
    for event in admitted:
        counts[event.observable_session] = counts.get(event.observable_session, 0) + 1
    return tuple(ObservableSessionRow(observable_session=session, events=count) for session, count in sorted(counts.items()))


def _session_rows(
    admitted: Sequence[Event],
    sessions: Sequence[date],
    snapshots: Mapping[date, frozenset[DocumentKey]],
    chains: Mapping[ChainKey, tuple[Event, ...]],
) -> tuple[SessionCoverage, ...]:
    rows: list[SessionCoverage] = []
    for session in sessions:
        visible = snapshots[session]
        rows.append(
            SessionCoverage(
                decision_session=session,
                visible_documents=len(visible),
                visible_issuers=len({key[:3] for key in visible}),
                superseded_events=superseded_at(chains, session),
                invisible_events=sum(1 for event in admitted if event.observable_session > session),
            )
        )
    return tuple(rows)


def audit(events_path: Path, start: date, end: date) -> AvailabilityReport:
    """Audit one event file against the decision-session grid ``start..end``.

    Args:
        events_path: Parquet or CSV carrying the required event columns.
        start: first decision session date (inclusive).
        end: last decision session date (inclusive).

    Raises:
        AuditInputError: ``end`` precedes ``start``, or the event input is unusable.
    """
    if end < start:
        raise AuditInputError(f"--end {end.isoformat()} is before --start {start.isoformat()}")
    rows = load_event_rows(Path(events_path))
    admitted, identities, duplicate_copies, midnight, ledger = _admit(rows)

    grid: dict[InstrumentKey, tuple[date, ...]] = {}
    for instrument in sorted(identities, key=lambda item: (item.market, item.mic, item.symbol)):
        try:
            grid[instrument] = tuple(instrument_sessions_between(instrument, start, end))
        except ValueError as exc:  # no calendar for the exact MIC: refuse rather than guess
            raise AuditInputError(f"no exchange calendar for {instrument.market}/{instrument.mic}: {exc}") from exc
    sessions = sorted({session for sessions_of_issuer in grid.values() for session in sessions_of_issuer})

    snapshots = as_of_documents(admitted, sessions)
    chains = revision_chains(admitted)
    synthetic = [synthetic_future_event(instrument, end) for instrument in grid]
    extended = as_of_documents([*admitted, *synthetic], sessions)
    checks = (
        session_mapping_check(admitted),
        future_safety_check(snapshots, extended, [event.observable_session for event in synthetic], sessions[-1] if sessions else None),
    )
    for check in checks:
        if not check.passed:
            ledger.add(_CHECK_REFUSALS[check.name], 1, check.detail)

    observable = [event.observable_session for event in admitted]
    warnings: tuple[str, ...] = ()
    if midnight:
        noun = "event is" if midnight == 1 else "events are"
        warnings = (f"{midnight} admitted {noun} midnight-shaped: {MIDNIGHT_WARNING}",)
    return AvailabilityReport(
        events_path=str(events_path),
        start=start,
        end=end,
        events_read=len(rows),
        events_admitted=len(admitted),
        duplicate_copies=duplicate_copies,
        superseded_events=superseded_at(chains, sessions[-1]) if sessions else 0,
        suspicious_midnight_clocks=midnight,
        decision_sessions=len(sessions),
        first_observable_session=min(observable) if observable else None,
        last_observable_session=max(observable) if observable else None,
        observable_sessions=_observable_session_rows(admitted),
        issuers=_issuer_rows(admitted, grid, snapshots),
        sessions=_session_rows(admitted, sessions, snapshots, chains),
        checks=checks,
        warnings=warnings,
        refusals=ledger.rows(),
    )


def _fmt_session(value: date | None) -> str:
    return "none" if value is None else value.isoformat()


def print_summary(report: AvailabilityReport) -> None:
    """Print the compact, value-free summary (the JSON report carries the per-session detail)."""
    print(f"text availability audit: {report.events_path} ({report.start.isoformat()}..{report.end.isoformat()})")
    print(
        f"events: {report.events_read} read, {report.events_admitted} admitted, {report.duplicate_copies} duplicate copies, "
        f"{report.superseded_events} superseded by the last decision session"
    )
    print(
        f"observable sessions: {len(report.observable_sessions)} distinct, "
        f"{_fmt_session(report.first_observable_session)}..{_fmt_session(report.last_observable_session)}"
    )
    for issuer in report.issuers[:MAX_ISSUER_LINES]:
        print(
            f"issuer {issuer.mic}:{issuer.symbol} documents={issuer.documents} "
            f"sessions_with_text={issuer.sessions_with_visible_document}/{issuer.decision_sessions} "
            f"observable={_fmt_session(issuer.first_observable_session)}..{_fmt_session(issuer.last_observable_session)}"
        )
    if len(report.issuers) > MAX_ISSUER_LINES:
        print(f"issuer ... {len(report.issuers) - MAX_ISSUER_LINES} more (see the JSON report)")
    if report.sessions:
        first, last = report.sessions[0], report.sessions[-1]
        print(
            f"sessions: {len(report.sessions)} decision sessions, visible documents {first.visible_documents}->{last.visible_documents}, "
            f"invisible events {first.invisible_events}->{last.invisible_events} "
            f"(peak {max(row.invisible_events for row in report.sessions)})"
        )
    print("checks: " + ", ".join(f"{check.name}={'PASS' if check.passed else 'FAIL'}" for check in report.checks))
    for warning in report.warnings:
        print(f"SUSPICIOUS: {warning}")
    for refusal in report.refusals:
        print(f"REFUSED {refusal.reason}: {refusal.count} - {refusal.detail}")
    if report.refused:
        noun = "reason" if len(report.refusals) == 1 else "reasons"
        result = f"refused ({len(report.refusals)} named {noun})"
    elif not report.sessions:
        result = "nothing to audit (no decision session in the window)"
    else:
        result = "clean"
    print(f"RESULT: {result}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint: 0 clean, 1 any refusal, 2 nothing to audit."""
    parser = argparse.ArgumentParser(
        description="Audit whether text events could have been visible to a close-of-session decision (read-only, offline)"
    )
    parser.add_argument("--events", required=True, help="Parquet or CSV of text events (required columns: " + ", ".join(REQUIRED_COLUMNS) + ")")
    parser.add_argument("--start", required=True, help="first decision session date, ISO YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="last decision session date, ISO YYYY-MM-DD (inclusive)")
    parser.add_argument("--out", default=None, help="write the JSON report here (an auxiliary artifact, ADR-0006; nothing is written without it)")
    args = parser.parse_args(argv)
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as exc:
        print(f"REFUSED: --start and --end must be ISO dates (YYYY-MM-DD): {exc}", file=sys.stderr)
        return 1
    try:
        report = audit(Path(args.events), start, end)
    except AuditInputError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    if args.out is not None:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    print_summary(report)
    if report.refused:
        return 1
    return 0 if report.sessions else 2


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CLOCK_NOT_SOURCE_ISSUED",
    "CLOCK_UNKNOWN",
    "CONTENT_DIGEST_MISSING",
    "EQUAL_CLOCK_CONFLICT",
    "FUTURE_SAFETY_CHECK",
    "FUTURE_SAFETY_FAILED",
    "SECTION_MISSING",
    "SESSION_MAPPING_CHECK",
    "SESSION_MAPPING_FAILED",
    "AvailabilityReport",
    "AuditInputError",
    "audit",
    "main",
]
