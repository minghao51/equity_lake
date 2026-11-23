"""Source-clock qualification records and the strict as-of gate (RS-D01).

Residual plan §2.8: qualifying a source means *source-issued* publication/vintage
evidence, never an ingest-time clock; new evidence is appended and old records are never
restamped. These tests hold the write boundary (what a record must carry, what it can
never carry) and the gate (missing / expired / superseded / unverifiable all fail
closed).

Every record here is synthetic guard input. Passing these tests is not qualified data,
does not qualify any real source and unblocks no live consumer: actual qualification
stays `blocked-input` until an operator supplies source-issued clocks, vintage/expiry
evidence and rights.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from equity_lake.core import paths  # type: ignore[import-untyped]
from equity_lake.core.paths import PROJECT_ROOT  # type: ignore[import-untyped]
from equity_lake.core.source_clock import (  # type: ignore[import-untyped]
    MARKET_SERIES,
    QUALIFICATION_DIR_NAME,
    QUALIFICATION_INPUT_PACKET,
    QUALIFICATION_RECORDS_FILENAME,
    SourceClockRecord,
    SourceClockRecordError,
    SourceClockUnavailable,
    append_source_clock_record,
    qualification_records_path,
    qualify_source_clock,
    read_source_clock_records,
    require_qualified_source_clock,
)

_SOURCE = "synthetic-fixture-source-issued"
_AS_OF = date(2024, 6, 3)
#: Committed machine-readable fixture (`data/*` is gitignored, so fixtures live here).
FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "source_clock" / "qualification-records.jsonl"


def _record(**overrides: Any) -> SourceClockRecord:
    """A syntactically complete synthetic qualification record."""
    values: dict[str, Any] = {
        "source": _SOURCE,
        "market": "us_equity",
        "series": "AAPL",
        "evidence_kind": "source_publication",
        "evidence_digest": "a" * 64,
        "source_available_at": datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        "coverage_start": date(2024, 1, 2),
        "coverage_end": date(2024, 12, 31),
        "expires_on": date(2025, 3, 31),
        "rights_reference": "licence-reference-1",
        "recorded_at": datetime(2024, 6, 1, 12, 0, tzinfo=UTC),
    }
    values.update(overrides)
    return SourceClockRecord.issue(**values)


def _answer(records: Any, **overrides: Any) -> Any:
    values: dict[str, Any] = {"source": _SOURCE, "market": "us_equity", "series": "AAPL", "as_of": _AS_OF}
    values.update(overrides)
    return qualify_source_clock(records, **values)


# ---------------------------------------------------------------------------
# The write boundary: what a record must be
# ---------------------------------------------------------------------------


def test_a_record_carries_source_market_series_evidence_window_expiry_and_rights() -> None:
    """The record is exactly the smallest set a reviewer needs to accept a clock."""
    record = _record()

    assert set(SourceClockRecord.model_fields) == {
        "record_id",
        "source",
        "market",
        "series",
        "evidence_kind",
        "evidence_digest",
        "evidence_uri",
        "source_available_at",
        "coverage_start",
        "coverage_end",
        "expires_on",
        "rights_reference",
        "recorded_at",
        "supersedes",
        "record_schema_version",
    }
    assert record.market == "us_equity"  # the deprecated alias never lands in a record
    assert record.record_id == record.content_digest()
    assert record.expires_on >= record.coverage_end
    record.verify()


def test_an_ingest_time_clock_is_refused_as_proof() -> None:
    """No ingest/fetch/file-write clock can be expressed as evidence (ADR-0015 d4).

    Falsified by: adding ``ingest_time`` to the admitted evidence kinds, which is the
    exact laundering ADR-0032 decision 6 exists to prevent.
    """
    with pytest.raises(ValidationError, match="not source-issued evidence"):
        _record(evidence_kind="ingest_time")


def test_a_record_needs_evidence_and_rights() -> None:
    """A qualification with no document reference or no rights is not a qualification."""
    with pytest.raises(ValidationError, match="digest or URI"):
        _record(evidence_digest=None, evidence_uri=None)
    with pytest.raises(ValidationError, match="cannot be empty"):
        _record(rights_reference="   ")
    with pytest.raises(ValidationError, match="sha256"):
        _record(evidence_digest="not-a-digest")


def test_a_record_cannot_be_appended_before_the_clock_it_quotes() -> None:
    """You cannot hold evidence whose source clock is in your future."""
    with pytest.raises(ValidationError, match="before the source clock it quotes"):
        _record(recorded_at=datetime(2023, 12, 1, tzinfo=UTC))


def test_a_qualification_cannot_expire_before_its_window_ends() -> None:
    """An expiry inside the covered window is contradictory, not merely stale."""
    with pytest.raises(ValidationError, match="expire before"):
        _record(expires_on=date(2024, 6, 30))


def test_a_tampered_record_is_unverifiable() -> None:
    """``record_id`` is the digest of the content, so an edited record cannot pass."""
    record = _record()
    tampered = record.model_copy(update={"source_available_at": datetime(2024, 2, 1, tzinfo=UTC)})

    with pytest.raises(SourceClockRecordError, match="unverifiable"):
        tampered.verify()


# ---------------------------------------------------------------------------
# The store: append-only, never a restamp
# ---------------------------------------------------------------------------


def test_the_store_is_append_only_and_a_correction_supersedes(tmp_path: Path) -> None:
    """A correction is a new line; the record it corrects is left byte-identical."""
    store = tmp_path / "records.jsonl"
    first = _record()
    append_source_clock_record(first, store)
    original = store.read_text(encoding="utf-8")

    correction = _record(
        evidence_kind="source_vintage",
        evidence_digest=None,
        evidence_uri="file:evidence/vendor-vintage-2.pdf",
        coverage_start=date(2024, 7, 1),
        coverage_end=date(2025, 6, 30),
        expires_on=date(2025, 9, 30),
        recorded_at=datetime(2024, 7, 2, tzinfo=UTC),
        supersedes=first.record_id,
    )
    append_source_clock_record(correction, store)

    written = store.read_text(encoding="utf-8").splitlines()
    assert written[0] == original.strip()  # never rewritten, never restamped
    assert len(written) == 2
    assert read_source_clock_records(store) == (first, correction)


def test_reappending_the_same_record_is_refused(tmp_path: Path) -> None:
    """Records are append-only: the same record cannot be restamped into the store."""
    store = tmp_path / "records.jsonl"
    record = _record()
    append_source_clock_record(record, store)

    with pytest.raises(SourceClockRecordError, match="append-only"):
        append_source_clock_record(record, store)


def test_a_correction_must_name_a_recorded_record_and_only_once(tmp_path: Path) -> None:
    """Supersession is linear and must reference a record that exists."""
    store = tmp_path / "records.jsonl"
    first = _record()
    append_source_clock_record(first, store)

    with pytest.raises(SourceClockRecordError, match="names no recorded record"):
        append_source_clock_record(_record(supersedes="b" * 64), store)

    second = _record(recorded_at=datetime(2024, 6, 2, tzinfo=UTC), supersedes=first.record_id)
    append_source_clock_record(second, store)
    with pytest.raises(SourceClockRecordError, match="already superseded"):
        append_source_clock_record(_record(recorded_at=datetime(2024, 6, 4, tzinfo=UTC), supersedes=first.record_id), store)


def test_the_reader_refuses_a_tampered_store_line(tmp_path: Path) -> None:
    """A hand-edited line is unverifiable, never silently dropped."""
    store = tmp_path / "records.jsonl"
    append_source_clock_record(_record(), store)
    payload = json.loads(store.read_text(encoding="utf-8"))
    payload["coverage_end"] = "2026-12-31"
    store.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(SourceClockRecordError, match="unverifiable"):
        read_source_clock_records(store)


def test_no_store_is_created_by_a_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The auxiliary artifact is inert: nothing is created, nothing is defaulted."""
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)

    expected = tmp_path / QUALIFICATION_DIR_NAME / QUALIFICATION_RECORDS_FILENAME
    assert qualification_records_path() == expected
    assert read_source_clock_records() == ()
    assert not expected.parent.exists()


# ---------------------------------------------------------------------------
# The gate: fail closed, then answer
# ---------------------------------------------------------------------------


def test_gate_answers_present_inside_the_covered_window() -> None:
    """One verified, active, in-window, unexpired record qualifies the read."""
    answer = _answer([_record()])

    assert answer.state == "present"
    assert answer.qualified is True
    assert answer.record_id == _record().record_id
    assert "source_publication" in answer.evidence


def test_gate_fails_closed_when_no_record_exists() -> None:
    """Absence is ``unknown`` plus the input packet — never an implicit qualification."""
    answer = _answer([])

    assert answer.state == "unknown"
    assert answer.qualified is False
    assert answer.input_packet == QUALIFICATION_INPUT_PACKET
    assert "no source-clock qualification record" in answer.evidence


def test_gate_fails_closed_after_expiry() -> None:
    """An expired qualification refuses the as-of date past its expiry."""
    answer = _answer([_record()], as_of=date(2025, 6, 2))

    assert answer.state == "unknown"
    assert "expired on 2025-03-31" in answer.evidence


def test_gate_fails_closed_outside_the_covered_window() -> None:
    """A covered window that does not reach the as-of date is not evidence for it."""
    early = _answer([_record()], as_of=date(2023, 12, 29))
    late = _answer([_record(coverage_end=date(2024, 5, 31), expires_on=date(2025, 3, 31))], as_of=date(2024, 7, 1))

    assert early.state == "unknown" and "does not include the as-of date" in early.evidence
    assert late.state == "unknown" and "does not include the as-of date" in late.evidence


def test_a_superseding_record_answers_and_the_withdrawn_one_never_does() -> None:
    """Supersession is honoured in both directions.

    Where both windows cover the as-of date, the successor answers — an implementation
    that ignored ``supersedes`` would see two active records and refuse as ambiguous.
    Where only the withdrawn record's window covers it, the answer is ``unknown``: a
    corrected qualification never keeps qualifying reads the correction narrowed.
    """
    first = _record()
    correction = _record(
        evidence_kind="source_vintage",
        evidence_digest=None,
        evidence_uri="file:evidence/vendor-vintage-2.pdf",
        coverage_start=date(2024, 7, 1),
        coverage_end=date(2025, 6, 30),
        expires_on=date(2025, 9, 30),
        recorded_at=datetime(2024, 7, 2, tzinfo=UTC),
        supersedes=first.record_id,
    )
    inside_both = date(2024, 8, 1)
    inside_only_the_withdrawn_one = date(2024, 6, 3)

    answering = _answer([first, correction], as_of=inside_both)
    assert answering.state == "present"
    assert answering.record_id == correction.record_id  # the correction, never the original
    withdrawn = _answer([first, correction], as_of=inside_only_the_withdrawn_one)
    assert withdrawn.state == "unknown"
    assert "does not include the as-of date" in withdrawn.evidence


def test_gate_fails_closed_on_an_ambiguous_evidence_trail() -> None:
    """Two active records for one scope are an ambiguity, not a preference order."""
    answer = _answer([_record(), _record(evidence_digest="c" * 64, recorded_at=datetime(2024, 6, 2, tzinfo=UTC))])

    assert answer.state == "unknown"
    assert "active qualification records" in answer.evidence


def test_gate_fails_closed_on_an_unverifiable_record() -> None:
    """A tampered in-memory record refuses the whole answer rather than being trusted."""
    payload = json.loads(_record().model_dump_json())
    payload["coverage_end"] = "2026-12-31"

    answer = _answer([payload])

    assert answer.state == "unknown"
    assert "unverifiable" in answer.evidence


def test_gate_ignores_other_sources_markets_and_series() -> None:
    """Scope is exact: a neighbour's record never qualifies this series."""
    neighbour = _record(series="MSFT")
    other_market = _record(market="cn_ashare", series="600000")

    assert _answer([neighbour, other_market]).state == "unknown"
    assert _answer([neighbour], series="MSFT").state == "present"


def test_a_market_series_record_qualifies_each_series() -> None:
    """The explicit ``"*"`` series is the only way one record covers a whole market."""
    assert _answer([_record(series=MARKET_SERIES)]).state == "present"
    assert _answer([_record(series=MARKET_SERIES)], series="NVDA").state == "present"


def test_require_refuses_with_the_named_error_and_returns_the_qualification(tmp_path: Path) -> None:
    """The strict consumer helper raises instead of degrading to a weak basis."""
    store = tmp_path / "records.jsonl"
    append_source_clock_record(_record(), store)

    qualified = require_qualified_source_clock(source=_SOURCE, market="us_equity", series="AAPL", as_of=_AS_OF, records_path=store)
    assert qualified.qualified is True

    with pytest.raises(SourceClockUnavailable, match="is not qualified as of 2025-06-02"):
        require_qualified_source_clock(source=_SOURCE, market="us_equity", series="AAPL", as_of=date(2025, 6, 2), records_path=store)
    with pytest.raises(SourceClockUnavailable, match="is not qualified"):
        require_qualified_source_clock(source="nobody", market="us_equity", series="AAPL", as_of=_AS_OF, records_path=store)


def test_the_committed_fixture_is_a_verifiable_append_only_chain() -> None:
    """The machine-readable fixture stays valid: a superseded record and its successor."""
    records = read_source_clock_records(FIXTURE)

    assert [record.record_schema_version for record in records] == [1, 1]
    assert all(record.source.startswith("synthetic-") for record in records)
    assert records[1].supersedes == records[0].record_id
    assert _answer(list(records), source=records[0].source, as_of=date(2024, 8, 1)).state == "present"
    assert _answer(list(records), source=records[0].source, as_of=date(2024, 3, 1)).state == "unknown"
