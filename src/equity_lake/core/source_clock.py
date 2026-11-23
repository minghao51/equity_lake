"""Source-clock qualification records and the strict as-of gate (RS-D01).

Residual plan §2.8 / §RS-D01: point-in-time correctness on ``vendor_events_no_clock``
rows stays impossible, and qualifying an alternative source means *source-issued*
publication or vintage evidence — never an ingest-time clock. This module is that
qualification procedure in code:

- :class:`SourceClockRecord` — the smallest validated record at the write boundary: the
  source, the market and series, the source-issued evidence (kind plus a digest or URI),
  the source clock, the covered window, its expiry, the rights reference, when the record
  was taken, and the record it supersedes.
- :func:`append_source_clock_record` / :func:`read_source_clock_records` — the
  append-only store under ``DATA_DIR / "source_clock_qualification"``. It is an
  auxiliary artifact (ADR-0006): not cataloged, not pointblank-validated, and never
  written unless an operator appends a record.
- :func:`qualify_source_clock` / :func:`require_qualified_source_clock` — the strict
  consumer gate, which fails closed on missing, expired, superseded and unverifiable
  records.

An ingest-time clock cannot be expressed as proof. The evidence vocabulary is closed
(:data:`CLOCK_EVIDENCE_KINDS`), and ``ingest_time`` appears in it only so the refusal is
explicit and testable: constructing a record with it raises. A record also cannot be
appended before the clock it quotes, and its ``record_id`` is the digest of its own
content, so a hand-edited row reads as unverifiable rather than as evidence.

Nothing here qualifies a real source. Every market answers ``unknown`` until an operator
supplies source-issued clocks, vintage/expiry evidence and rights; passing the synthetic
guards in the test suite is not qualified data and does not unblock a live consumer.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from equity_lake.core import paths as lake_paths
from equity_lake.core.paths import canonical_market

#: Bumped in one direction only; a future shape adds a version instead of rewriting.
SOURCE_CLOCK_SCHEMA_VERSION: Final[Literal[1]] = 1

#: Closed evidence vocabulary. Only the source-issued document kinds are admitted as
#: proof; ``ingest_time`` is declared so the refusal names the thing it refuses
#: (ADR-0015 decision 4, ADR-0032 decision 6 — an ingest/fetch clock is not source time).
ClockEvidenceKind = Literal["source_publication", "source_vintage", "ingest_time"]
CLOCK_EVIDENCE_KINDS: tuple[ClockEvidenceKind, ...] = ("source_publication", "source_vintage", "ingest_time")
SOURCE_ISSUED_CLOCK_KINDS: frozenset[str] = frozenset({"source_publication", "source_vintage"})

#: Gate answer vocabulary. There is no third state: a record either qualifies the read
#: or it does not, and "not" always carries the reason plus the input that would close it.
QualificationState = Literal["present", "unknown"]

#: The one operator input packet that would close an unqualified source. Named verbatim
#: in every ``unknown`` answer so a reviewer never has to infer what is missing.
QUALIFICATION_INPUT_PACKET = (
    "the source-issued publication or vintage document showing when this series became visible "
    "(exchange notice, vendor release note, vintage table), referenced by sha256 digest or URI, "
    "with the covered window, its expiry date, and the rights/licence reference that authorizes "
    "local storage; an ingest, fetch or file-write timestamp is not a substitute"
)

#: A record with this series qualifies every series of its market for its source.
MARKET_SERIES = "*"

#: Auxiliary artifact directory/file name under ``DATA_DIR`` (ADR-0006).
QUALIFICATION_DIR_NAME = "source_clock_qualification"
QUALIFICATION_RECORDS_FILENAME = "records.jsonl"

_UNSET_RECORD_ID = "0" * 64


class SourceClockRecordError(ValueError):
    """An append-only source-clock record is invalid, unverifiable or misordered."""


class SourceClockUnavailable(RuntimeError):
    """A strict consumer asked for a qualified source clock and did not get one."""


def _content_digest(payload: Mapping[str, object]) -> str:
    """``sha256`` of the canonical JSON of a record body (key order fixed)."""
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SourceClockRecord(BaseModel):
    """One appended source-clock qualification record.

    ``record_id`` is the digest of the record's own content, so a rewritten or
    hand-edited row is unverifiable instead of silently accepted. ``evidence_digest``
    or ``evidence_uri`` must reference the source-issued document that carries the
    clock; ``rights_reference`` names the licence/entitlement that authorizes keeping
    the series locally. ``expires_on`` bounds the claim: the gate refuses an as-of date
    after it. ``supersedes`` names the record this one corrects — a correction is
    always a new record, never an edit of the old one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    record_id: str
    source: str
    market: str
    series: str
    evidence_kind: ClockEvidenceKind
    evidence_digest: str | None = None
    evidence_uri: str | None = None
    source_available_at: datetime
    coverage_start: date
    coverage_end: date
    expires_on: date
    rights_reference: str
    recorded_at: datetime
    supersedes: str | None = None
    record_schema_version: Literal[1] = SOURCE_CLOCK_SCHEMA_VERSION

    @field_validator("record_id")
    @classmethod
    def record_id_is_a_digest(cls, value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("record_id must be the 64-character lowercase sha256 hex digest of the record body")
        return normalized

    @field_validator("source", "series", "rights_reference")
    @classmethod
    def text_is_present(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source, series and rights_reference cannot be empty")
        return normalized

    @field_validator("market")
    @classmethod
    def market_is_canonical(cls, value: str) -> str:
        return canonical_market(value.strip())

    @field_validator("evidence_kind")
    @classmethod
    def evidence_is_source_issued(cls, value: str) -> str:
        if value not in SOURCE_ISSUED_CLOCK_KINDS:
            raise ValueError(
                f"{value!r} is not source-issued evidence: an ingest, fetch or file-write clock is never a substitute for the "
                "source's own publication/vintage time (ADR-0015 decision 4, ADR-0032 decision 6)"
            )
        return value

    @field_validator("evidence_digest")
    @classmethod
    def digest_is_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("evidence_digest must be a lowercase sha256 hex digest")
        return normalized

    @field_validator("evidence_uri")
    @classmethod
    def uri_is_a_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or ":" not in normalized:
            raise ValueError("evidence_uri must be a reference with a scheme (the document is never fetched here)")
        return normalized

    @field_validator("source_available_at", "recorded_at")
    @classmethod
    def clocks_are_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source_available_at and recorded_at must be timezone-aware UTC timestamps")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_scope(self) -> SourceClockRecord:
        if self.coverage_end < self.coverage_start:
            raise ValueError("coverage_end must be on or after coverage_start")
        if self.expires_on < self.coverage_end:
            raise ValueError("a qualification cannot expire before the window it covers ends")
        if self.evidence_digest is None and self.evidence_uri is None:
            raise ValueError("a qualification must reference its source-issued evidence by digest or URI")
        if self.source_available_at > self.recorded_at:
            raise ValueError("a record cannot be appended before the source clock it quotes")
        if self.supersedes is not None and self.supersedes == self.record_id:
            raise ValueError("a record cannot supersede itself")
        return self

    @classmethod
    def issue(cls, **values: object) -> SourceClockRecord:
        """Build a record whose ``record_id`` is the digest of its validated content."""
        draft = cls.model_validate({**values, "record_id": _UNSET_RECORD_ID})
        return draft.model_copy(update={"record_id": draft.content_digest()})

    def content_digest(self) -> str:
        """The digest this record's ``record_id`` must equal."""
        payload = self.model_dump(mode="json")
        payload.pop("record_id", None)
        return _content_digest(payload)

    def verify(self) -> None:
        """Refuse a record whose content no longer matches its ``record_id``."""
        if self.record_id != self.content_digest():
            raise SourceClockRecordError("record_id does not match the record content; records are append-only, so a rewritten row is unverifiable")


@dataclass(frozen=True, slots=True)
class ClockQualification:
    """The gate's answer for one ``(source, market, series, as_of)`` question.

    ``state='unknown'`` always carries why and the input packet that would close it;
    ``state='present'`` names the record it relied on. There is no weaker "probably
    qualified" state, so a consumer cannot read a missing record as satisfied.
    """

    state: QualificationState
    evidence: str
    input_packet: str = ""
    record_id: str | None = None

    @property
    def qualified(self) -> bool:
        return self.state == "present"


def qualification_records_path(records_path: Path | str | None = None) -> Path:
    """Where the append-only records live (``DATA_DIR`` resolved at call time)."""
    if records_path is not None:
        return Path(records_path)
    return lake_paths.DATA_DIR / QUALIFICATION_DIR_NAME / QUALIFICATION_RECORDS_FILENAME


def read_source_clock_records(records_path: Path | str | None = None) -> tuple[SourceClockRecord, ...]:
    """Read the append-only store in file order; refuse an unverifiable row.

    A missing store is an empty store — no directory is created by a read, and nothing
    is ever repaired in place. A row that fails validation or whose content no longer
    matches its ``record_id`` raises instead of being dropped, because dropping it
    would silently weaken an evidence trail.
    """
    path = qualification_records_path(records_path)
    if not path.exists():
        return ()
    records: list[SourceClockRecord] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = SourceClockRecord.model_validate_json(line)
            record.verify()
        except (ValidationError, SourceClockRecordError) as exc:
            raise SourceClockRecordError(f"{path}:{line_number}: qualification record is unverifiable ({exc})") from exc
        records.append(record)
    return tuple(records)


def append_source_clock_record(record: SourceClockRecord, records_path: Path | str | None = None) -> Path:
    """Append one record to the store, never rewriting an existing one.

    Refuses a duplicate ``record_id`` (a correction is a new record, not a restamp), a
    ``supersedes`` naming no recorded record, and a second correction of a record that
    was already superseded. Existing lines are left byte-identical.

    Returns:
        The store path that received the record.
    """
    record.verify()
    path = qualification_records_path(records_path)
    existing = read_source_clock_records(path)
    recorded = {entry.record_id for entry in existing}
    if record.record_id in recorded:
        raise SourceClockRecordError(
            "this exact record is already recorded; the store is append-only and a correction is a new record that supersedes"
        )
    if record.supersedes is not None:
        if record.supersedes not in recorded:
            raise SourceClockRecordError(f"supersedes names no recorded record: {record.supersedes}")
        if any(entry.supersedes == record.supersedes for entry in existing):
            raise SourceClockRecordError(f"record {record.supersedes} was already superseded; a correction chain is linear")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.model_dump_json() + "\n")
    return path


def _coerce(record: SourceClockRecord | Mapping[str, object]) -> SourceClockRecord:
    """One validated, verified record from either form."""
    candidate = record if isinstance(record, SourceClockRecord) else SourceClockRecord.model_validate(record)
    candidate.verify()
    return candidate


def qualify_source_clock(
    records: Iterable[SourceClockRecord | Mapping[str, object]],
    *,
    source: str,
    market: str,
    series: str,
    as_of: date,
) -> ClockQualification:
    """Answer whether *source* qualifies *series* in *market* as of *as_of*.

    Fails closed on every uncertain case: an unverifiable record anywhere in the
    supplied set, no record for the scope, only superseded records, more than one active
    record (an ambiguous evidence trail), an ``as_of`` outside the covered window, and an
    ``as_of`` after ``expires_on``. Only a single, verified, active, in-window and
    unexpired record answers ``present``.
    """
    canonical = canonical_market(market)
    requested_source = source.strip()
    requested_series = series.strip()
    in_scope: list[SourceClockRecord] = []
    for raw in records:
        try:
            record = _coerce(raw)
        except (ValidationError, SourceClockRecordError, ValueError) as exc:
            return ClockQualification(
                state="unknown",
                evidence=f"a supplied qualification record is unverifiable ({exc})",
                input_packet=QUALIFICATION_INPUT_PACKET,
            )
        if record.source == requested_source and record.market == canonical and record.series in (requested_series, MARKET_SERIES):
            in_scope.append(record)
    if not in_scope:
        return ClockQualification(
            state="unknown",
            evidence=f"no source-clock qualification record is recorded for {requested_source}/{canonical}/{requested_series}",
            input_packet=QUALIFICATION_INPUT_PACKET,
        )
    superseded = {record.supersedes for record in in_scope if record.supersedes is not None}
    active = [record for record in in_scope if record.record_id not in superseded]
    if not active:
        return ClockQualification(
            state="unknown",
            evidence=f"every qualification record for {requested_source}/{canonical}/{requested_series} is superseded",
            input_packet=QUALIFICATION_INPUT_PACKET,
        )
    if len(active) > 1:
        return ClockQualification(
            state="unknown",
            evidence=(
                f"{len(active)} active qualification records for {requested_source}/{canonical}/{requested_series} "
                f"({sorted(record.record_id for record in active)}); append one superseding record to close the ambiguity"
            ),
            input_packet=QUALIFICATION_INPUT_PACKET,
        )
    record = active[0]
    if record.evidence_kind not in SOURCE_ISSUED_CLOCK_KINDS:
        return ClockQualification(
            state="unknown",
            evidence=f"{record.record_id} cites {record.evidence_kind!r}, which is not source-issued evidence",
            input_packet=QUALIFICATION_INPUT_PACKET,
        )
    if as_of > record.expires_on:
        return ClockQualification(
            state="unknown",
            evidence=f"{record.record_id} expired on {record.expires_on.isoformat()}, before the as-of date {as_of.isoformat()}",
            input_packet=QUALIFICATION_INPUT_PACKET,
        )
    if not record.coverage_start <= as_of <= record.coverage_end:
        return ClockQualification(
            state="unknown",
            evidence=(
                f"{record.record_id} covers {record.coverage_start.isoformat()}..{record.coverage_end.isoformat()}, "
                f"which does not include the as-of date {as_of.isoformat()}"
            ),
            input_packet=QUALIFICATION_INPUT_PACKET,
        )
    reference = record.evidence_digest or record.evidence_uri
    return ClockQualification(
        state="present",
        evidence=(
            f"{record.record_id} qualifies {record.source}/{record.market}/{record.series} from {record.evidence_kind} "
            f"{reference}, covering {record.coverage_start.isoformat()}..{record.coverage_end.isoformat()} "
            f"and valid through {record.expires_on.isoformat()} under rights {record.rights_reference}"
        ),
        record_id=record.record_id,
    )


def require_qualified_source_clock(
    *,
    source: str,
    market: str,
    series: str,
    as_of: date,
    records: Iterable[SourceClockRecord | Mapping[str, object]] | None = None,
    records_path: Path | str | None = None,
) -> ClockQualification:
    """Strict consumer gate: return the qualification or raise, never degrade.

    ``records`` is an explicit in-memory set (the test/offline form); ``None`` reads the
    append-only store. An unqualified source raises :class:`SourceClockUnavailable` with
    the gate's reason and the input packet — it never falls back to the weak
    ``vendor_events_no_clock`` basis and never invents a clock.

    Raises:
        SourceClockUnavailable: the source/series is not qualified as of *as_of*.
    """
    supplied = read_source_clock_records(records_path) if records is None else records
    answer = qualify_source_clock(supplied, source=source, market=market, series=series, as_of=as_of)
    if not answer.qualified:
        raise SourceClockUnavailable(f"{source}/{market}/{series} is not qualified as of {as_of.isoformat()}: {answer.evidence}")
    return answer


__all__ = [
    "CLOCK_EVIDENCE_KINDS",
    "MARKET_SERIES",
    "QUALIFICATION_DIR_NAME",
    "QUALIFICATION_INPUT_PACKET",
    "QUALIFICATION_RECORDS_FILENAME",
    "SOURCE_CLOCK_SCHEMA_VERSION",
    "SOURCE_ISSUED_CLOCK_KINDS",
    "ClockEvidenceKind",
    "ClockQualification",
    "QualificationState",
    "SourceClockRecord",
    "SourceClockRecordError",
    "SourceClockUnavailable",
    "append_source_clock_record",
    "qualification_records_path",
    "qualify_source_clock",
    "read_source_clock_records",
    "require_qualified_source_clock",
]
