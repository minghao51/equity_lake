"""The R1 synthetic availability audit: clock, observable session, dedup, leakage, invariants.

Every fixture is synthetic and written under ``tmp_path``. Calendar dates are real US 2026
sessions because the whole point of the audit is the venue session arithmetic, and each test's
docstring says why its date is the interesting one.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from equity_lake.core.instruments import InstrumentKey
from equity_lake.devtools import audit_text_availability as audit_tool

AAPL = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
DOC = "0000320193-26-000001"
DIGEST = "a" * 64


def _row(clock: object = "2026-01-05T14:00:00-05:00", **overrides: object) -> dict[str, object]:
    """One synthetic event row; overrides replace a single column (``None`` removes it)."""
    row: dict[str, object] = {
        "market": "us_equity",
        "mic": "XNAS",
        "symbol": "AAPL",
        "document_id": DOC,
        "section": "risk_factors",
        "source": "sec_edgar",
        "source_available_at": clock,
        "evidence_kind": "source_publication",
        "content_digest": DIGEST,
        "filed_on": "2026-01-02",
    }
    row.update(overrides)
    return {key: value for key, value in row.items() if value is not None}


def _write(tmp_path: Path, rows: list[dict[str, object]], name: str = "events.csv") -> Path:
    path = tmp_path / name
    if not rows:
        pl.DataFrame(
            schema={column: pl.Utf8 for column in audit_tool.REQUIRED_COLUMNS},
        ).write_csv(path)
        return path
    pl.DataFrame(rows).write_csv(path)
    return path


def _run(
    tmp_path: Path,
    rows: list[dict[str, object]],
    start: str = "2026-01-02",
    end: str = "2026-01-16",
    *extra: str,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, audit_tool.AvailabilityReport, str]:
    path = _write(tmp_path, rows)
    code = audit_tool.main(["--events", str(path), "--start", start, "--end", end, *extra])
    captured = capsys.readouterr()
    report = audit_tool.audit(path, date.fromisoformat(start), date.fromisoformat(end))
    return code, report, captured.out + captured.err


def _reasons(report: audit_tool.AvailabilityReport) -> set[str]:
    return {refusal.reason for refusal in report.refusals}


def _session_row(report: audit_tool.AvailabilityReport, session: str) -> audit_tool.SessionCoverage:
    wanted = date.fromisoformat(session)
    return next(row for row in report.sessions if row.decision_session == wanted)


# --- observable session mapping ---------------------------------------------------------------


def test_publication_before_the_venue_close_is_visible_the_same_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """2026-01-05 is a plain Monday XNAS session closing 16:00 ET; 10:00 ET is inside it."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T10:00:00-05:00")], capsys=capsys)

    assert code == 0, output
    assert report.observable_sessions[0].observable_session == date(2026, 1, 5)
    assert _session_row(report, "2026-01-05").visible_documents == 1
    assert _session_row(report, "2026-01-02").visible_documents == 0
    assert report.refusals == ()


def test_publication_after_the_venue_close_maps_to_the_next_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """17:30 ET on Monday 2026-01-05 is after that session's 16:00 close, so Tuesday is first."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T17:30:00-05:00")], capsys=capsys)

    assert code == 0, output
    assert report.observable_sessions[0].observable_session == date(2026, 1, 6)
    assert _session_row(report, "2026-01-05").visible_documents == 0
    assert _session_row(report, "2026-01-05").invisible_events == 1
    assert _session_row(report, "2026-01-06").visible_documents == 1


def test_early_close_session_is_respected(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """2026-11-27 closes at 13:00 ET (day after Thanksgiving), so 12:00 ET is in and 14:00 ET is out."""
    rows = [
        _row("2026-11-27T12:00:00-05:00", document_id="early-in"),
        _row("2026-11-27T14:00:00-05:00", document_id="early-out"),
    ]
    code, report, output = _run(tmp_path, rows, "2026-11-25", "2026-12-02", capsys=capsys)

    assert code == 0, output
    mapped = {row.observable_session: row.events for row in report.observable_sessions}
    assert mapped == {date(2026, 11, 27): 1, date(2026, 11, 30): 1}
    assert all(check.passed for check in report.checks)
    assert _session_row(report, "2026-11-27").visible_documents == 1
    # 2026-11-26 is the Thanksgiving holiday and is absent from the grid entirely.
    assert date(2026, 11, 26) not in {row.decision_session for row in report.sessions}


def test_holiday_gap_defers_to_the_next_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """2026-01-19 is Martin Luther King Day (no XNAS session), so a same-day clock lands Tuesday."""
    code, report, output = _run(tmp_path, [_row("2026-01-19T12:00:00-05:00")], "2026-01-15", "2026-01-22", capsys=capsys)

    assert code == 0, output
    assert report.observable_sessions[0].observable_session == date(2026, 1, 20)
    assert all(check.passed for check in report.checks)
    assert date(2026, 1, 19) not in {row.decision_session for row in report.sessions}
    assert _session_row(report, "2026-01-20").visible_documents == 1


def test_weekend_publication_defers_to_monday(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """2026-01-10 is a Saturday and 2026-01-12 the following session; no weekend decision exists."""
    code, report, output = _run(tmp_path, [_row("2026-01-10T10:00:00-05:00")], capsys=capsys)

    assert code == 0, output
    assert report.observable_sessions[0].observable_session == date(2026, 1, 12)
    assert all(check.passed for check in report.checks)
    assert all(row.decision_session.weekday() < 5 for row in report.sessions)


# --- duplicates, conflicts, revisions ---------------------------------------------------------


def test_duplicate_copy_is_counted_once(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Two byte-identical copies at the same clock are one document, not two."""
    rows = [_row("2026-01-05T10:00:00-05:00"), _row("2026-01-05T10:00:00-05:00")]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 0, output
    assert report.events_read == 2
    assert report.duplicate_copies == 1
    assert report.events_admitted == 1
    assert _session_row(report, "2026-01-16").visible_documents == 1


def test_equal_clock_conflict_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Same key and clock with two different content digests is a conflict, never a dedup."""
    rows = [_row("2026-01-05T10:00:00-05:00", content_digest="a" * 64), _row("2026-01-05T10:00:00-05:00", content_digest="b" * 64)]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 1, output
    assert audit_tool.EQUAL_CLOCK_CONFLICT in _reasons(report)
    assert report.events_admitted == 0
    assert report.duplicate_copies == 0
    assert all(row["count"] == 2 for row in [refusal.model_dump() for refusal in report.refusals])


def test_missing_digest_on_one_copy_is_refused_as_content_digest_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A copy whose digest is absent is unproven, so it is refused rather than deduplicated away."""
    rows = [_row("2026-01-05T10:00:00-05:00"), _row("2026-01-05T10:00:00-05:00", content_digest=None)]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 1, output
    assert audit_tool.CONTENT_DIGEST_MISSING in _reasons(report)
    assert audit_tool.EQUAL_CLOCK_CONFLICT not in _reasons(report)
    assert report.events_admitted == 0
    assert report.duplicate_copies == 0
    detail = next(refusal.detail for refusal in report.refusals if refusal.reason == audit_tool.CONTENT_DIGEST_MISSING)
    assert "1 of 2 copies carry no content_digest" in detail


def test_absent_digests_on_every_copy_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Two digest-less copies look identical but prove nothing, so the group is refused, not merged."""
    rows = [_row("2026-01-05T10:00:00-05:00", content_digest=None), _row("2026-01-05T10:00:00-05:00", content_digest=None)]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 1, output
    assert audit_tool.CONTENT_DIGEST_MISSING in _reasons(report)
    assert report.events_admitted == 0
    assert report.duplicate_copies == 0


def test_a_single_digest_plus_an_absent_one_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Mixed evidence is the lenient direction this tool exists to refuse: no digest, no dedup."""
    rows = [_row("2026-01-05T10:00:00-05:00", content_digest="a" * 64), _row("2026-01-05T10:00:00-05:00", content_digest=None)]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 1, output
    assert audit_tool.CONTENT_DIGEST_MISSING in _reasons(report)
    assert report.events_admitted == 0
    assert report.duplicate_copies == 0


def test_revision_supersedes_the_earlier_row(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A 10-Q amendment days after the original is one visible document, not an extra one."""
    rows = [
        _row("2026-01-05T10:00:00-05:00", content_digest="a" * 64),
        _row("2026-01-07T10:00:00-05:00", content_digest="b" * 64, evidence_kind="source_vintage"),
    ]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 0, output
    assert report.events_admitted == 2
    assert _session_row(report, "2026-01-05").visible_documents == 1
    assert _session_row(report, "2026-01-06").superseded_events == 0
    assert _session_row(report, "2026-01-07").visible_documents == 1
    assert _session_row(report, "2026-01-07").superseded_events == 1
    assert report.superseded_events == 1


def test_delayed_extraction_stays_invisible_until_its_own_clock(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """filed_on is 2026-01-02 but the source clock is 2026-01-08: the lag is never backdated away."""
    rows = [_row("2026-01-08T11:00:00-05:00", filed_on="2026-01-02")]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 0, output
    assert report.observable_sessions[0].observable_session == date(2026, 1, 8)
    assert _session_row(report, "2026-01-05").visible_documents == 0
    assert _session_row(report, "2026-01-05").invisible_events == 1
    assert _session_row(report, "2026-01-07").invisible_events == 1
    assert _session_row(report, "2026-01-08").visible_documents == 1


# --- refusals --------------------------------------------------------------------------------


def test_missing_clock_is_refused_as_clock_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty source_available_at is not a midnight clock; it is no clock at all."""
    code, report, output = _run(tmp_path, [_row("")], capsys=capsys)

    assert code == 1, output
    assert audit_tool.CLOCK_UNKNOWN in _reasons(report)
    assert report.events_admitted == 0
    assert report.suspicious_midnight_clocks == 0


def test_naive_timestamp_is_refused_as_clock_unknown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A timestamp without an offset cannot be mapped to a venue session, so it is refused."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T10:00:00")], capsys=capsys)

    assert code == 1, output
    assert audit_tool.CLOCK_UNKNOWN in _reasons(report)


def test_evidence_kind_outside_the_source_issued_set_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An ingest-time clock is refused by name even when it is a perfectly aware timestamp."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T00:00:00-05:00", evidence_kind="ingest_time")], capsys=capsys)

    assert code == 1, output
    assert audit_tool.CLOCK_NOT_SOURCE_ISSUED in _reasons(report)
    assert report.suspicious_midnight_clocks == 0  # refused before the midnight count


def test_missing_section_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A blank section cannot be keyed as a document section, so the event is refused."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T10:00:00-05:00", section="")], capsys=capsys)

    assert code == 1, output
    assert audit_tool.SECTION_MISSING in _reasons(report)
    assert report.events_admitted == 0


def test_suspicious_midnight_clock_warns_but_is_not_the_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A midnight fallback is counted and named, while the clock contract itself decides admission."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T00:00:00-05:00")], capsys=capsys)

    assert code == 0, output
    assert report.events_admitted == 1
    assert report.suspicious_midnight_clocks == 1
    assert len(report.warnings) == 1
    assert "midnight" in report.warnings[0]
    assert "SUSPICIOUS" in output


# --- invariants, computed --------------------------------------------------------------------


def _snapshot(**documents: str) -> frozenset[audit_tool.DocumentKey]:
    """A visible set of ``(market, mic, symbol, document_id)`` keys from the kwargs."""
    keys: set[audit_tool.DocumentKey] = set()
    for session, document_id in documents.items():
        keys.add(("us_equity", "XNAS", session, document_id))
    return frozenset(keys)


def test_both_checks_pass_on_a_clean_fixture(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Session mapping and the future-safety guard are computed from the data and both hold here."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T10:00:00-05:00")], capsys=capsys)

    assert code == 0, output
    assert {check.name for check in report.checks} == {audit_tool.SESSION_MAPPING_CHECK, audit_tool.FUTURE_SAFETY_CHECK}
    assert all(check.passed for check in report.checks)
    mapping = next(check for check in report.checks if check.name == audit_tool.SESSION_MAPPING_CHECK)
    assert "trading session" in mapping.detail


def test_future_safety_check_fails_when_the_as_of_set_changes() -> None:
    """Appending a future event that alters an earlier as-of set is a refusal, not a note."""
    sessions = [date(2026, 1, 5), date(2026, 1, 6)]
    before = {sessions[0]: _snapshot(a="d1"), sessions[1]: _snapshot(a="d1")}
    after = {sessions[0]: _snapshot(a="d1"), sessions[1]: _snapshot(a="d1", b="d2")}

    check = audit_tool.future_safety_check(before, after, [date(2026, 1, 8)], sessions[-1])

    assert not check.passed
    assert "2026-01-06" in check.detail


def test_future_safety_check_fails_when_a_future_event_is_visible_now() -> None:
    """A synthetic future event observable inside the window is look-ahead and fails the check."""
    sessions = [date(2026, 1, 5), date(2026, 1, 6)]
    snapshots = {session: _snapshot(a="d1") for session in sessions}

    check = audit_tool.future_safety_check(snapshots, snapshots, [date(2026, 1, 6)], sessions[-1])

    assert not check.passed
    assert "inside the decision window" in check.detail


def _event(clock: str, observable_session: date, document_id: str = "doc") -> audit_tool.Event:
    """One hand-built admitted event, so a broken mapping can be asserted on directly."""
    return audit_tool.Event(
        instrument=AAPL,
        document_id=document_id,
        section="risk_factors",
        clock=datetime.fromisoformat(clock).astimezone(UTC),
        observable_session=observable_session,
        content_digest=DIGEST,
    )


def test_session_mapping_check_rejects_a_non_session_observable_session() -> None:
    """2026-01-10 is a Saturday; a mapping that returns it is a real, detectable regression."""
    check = audit_tool.session_mapping_check([_event("2026-01-09T10:00:00-05:00", date(2026, 1, 10))])

    assert check.name == audit_tool.SESSION_MAPPING_CHECK
    assert not check.passed
    assert "no trading session" in check.detail


def test_session_mapping_check_rejects_a_session_before_the_clock_utc_date() -> None:
    """An observable session earlier than the clock's own UTC date is look-ahead in the mapping."""
    check = audit_tool.session_mapping_check([_event("2026-01-07T10:00:00-05:00", date(2026, 1, 5))])

    assert not check.passed
    assert "before the clock's UTC date" in check.detail


def test_session_mapping_check_rejects_clock_order_inversion() -> None:
    """Two clocks on one issuer must not map to decreasing sessions (wrong-venue/wrong-close smell).

    Both mapped sessions are real XNAS sessions at or after their own UTC date, so only the
    clock-order property can catch this inversion: the earlier clock points at 2026-01-08 and the
    later one at 2026-01-07.
    """
    events = [
        _event("2026-01-06T10:00:00-05:00", date(2026, 1, 8), document_id="earlier"),
        _event("2026-01-07T10:00:00-05:00", date(2026, 1, 7), document_id="later"),
    ]

    check = audit_tool.session_mapping_check(events)

    assert not check.passed
    assert "later clock" in check.detail


def test_a_broken_session_mapping_is_a_named_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """A mapping that returns ``clock.date()`` without the close check must fail, not pass silently."""
    monkeypatch.setattr(audit_tool, "source_observable_session", lambda instrument, clock: clock.date())

    code, report, output = _run(tmp_path, [_row("2026-01-19T12:00:00-05:00", document_id="holiday-clock")], capsys=capsys)

    assert code == 1, output
    assert audit_tool.SESSION_MAPPING_FAILED in _reasons(report)
    mapping = next(check for check in report.checks if check.name == audit_tool.SESSION_MAPPING_CHECK)
    assert not mapping.passed
    assert "no trading session" in mapping.detail


def test_a_failed_check_is_reported_as_a_named_refusal(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken future-safety check must surface as a refusal and exit 1, not as an advisory."""
    leaked = audit_tool.Event(
        instrument=AAPL,
        document_id="<leaked>",
        section="synthetic",
        clock=datetime(2026, 1, 5, 20, 0, tzinfo=UTC),
        observable_session=date(2026, 1, 5),
        content_digest=None,
    )
    monkeypatch.setattr(audit_tool, "synthetic_future_event", lambda instrument, end: leaked)

    code, report, output = _run(tmp_path, [_row("2026-01-05T10:00:00-05:00")], capsys=capsys)

    assert code == 1, output
    assert audit_tool.FUTURE_SAFETY_FAILED in _reasons(report)
    assert any(not check.passed for check in report.checks)


# --- coverage ----------------------------------------------------------------------------------


def test_coverage_counts_issuers_documents_and_invisible_events(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Two issuers at different clocks: per-issuer and per-session coverage both stay value-free."""
    rows = [
        _row("2026-01-05T10:00:00-05:00", document_id="aapl-q1"),
        _row("2026-01-06T10:00:00-05:00", document_id="aapl-q2"),
        _row("2026-01-07T10:00:00-05:00", document_id="ibm-q1", mic="XNYS", symbol="IBM"),
    ]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 0, output
    issuers = {row.symbol: row for row in report.issuers}
    assert issuers["AAPL"].documents == 2
    assert issuers["AAPL"].first_observable_session == date(2026, 1, 5)
    assert issuers["AAPL"].last_observable_session == date(2026, 1, 6)
    assert issuers["IBM"].documents == 1
    assert _session_row(report, "2026-01-05").visible_issuers == 1
    assert _session_row(report, "2026-01-07").visible_issuers == 2
    assert _session_row(report, "2026-01-05").invisible_events == 2
    assert _session_row(report, "2026-01-16").invisible_events == 0
    assert report.first_observable_session == date(2026, 1, 5)
    assert report.last_observable_session == date(2026, 1, 7)


# --- exit codes and I/O ------------------------------------------------------------------------


def test_nothing_to_audit_when_the_window_holds_no_session(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """2026-01-10..2026-01-11 is a weekend: no decision session exists, so there is nothing to audit."""
    code, report, output = _run(tmp_path, [_row("2026-01-05T10:00:00-05:00")], "2026-01-10", "2026-01-11", capsys=capsys)

    assert code == 2, output
    assert report.sessions == ()
    assert "nothing to audit" in output


def test_empty_event_file_exits_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """No rows at all is 'nothing to audit', not a clean pass."""
    code, _report, output = _run(tmp_path, [], capsys=capsys)

    assert code == 2, output
    assert "nothing to audit" in output


def test_missing_required_column_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Without the section column nothing can be keyed, so the input is refused before any row."""
    path = tmp_path / "events.csv"
    pl.DataFrame([{"market": "us_equity", "mic": "XNAS"}]).write_csv(path)

    code = audit_tool.main(["--events", str(path), "--start", "2026-01-02", "--end", "2026-01-16"])
    captured = capsys.readouterr()

    assert code == 1
    assert "REFUSED" in captured.err
    assert "missing required columns" in captured.err


def test_absent_event_file_is_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An absent input is a refusal with a reason, never an empty clean audit."""
    code = audit_tool.main(["--events", str(tmp_path / "nope.csv"), "--start", "2026-01-02", "--end", "2026-01-16"])
    captured = capsys.readouterr()

    assert code == 1
    assert "not a readable file" in captured.err


def test_report_is_written_only_with_out_and_is_value_free(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The summary always prints; the JSON artifact exists only with --out and carries no text."""
    rows = [_row("2026-01-05T10:00:00-05:00")]
    code, report, output = _run(tmp_path, rows, capsys=capsys)

    assert code == 0, output
    assert sorted(path.name for path in tmp_path.iterdir()) == ["events.csv"]
    payload = json.loads(report.model_dump_json())
    assert set(payload) >= {"events_read", "issuers", "sessions", "checks", "refusals"}
    for forbidden in ("body", "text", "excerpt", "content", "price", "probability"):
        assert forbidden not in json.dumps(payload)

    out = tmp_path / "out" / "report.json"
    code, _report, _output = _run(tmp_path, rows, "2026-01-02", "2026-01-16", "--out", str(out), capsys=capsys)

    assert code == 0
    assert audit_tool.AvailabilityReport.model_validate_json(out.read_text(encoding="utf-8")).events_read == 1


def test_document_text_never_reaches_the_report_or_the_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A body column with a unique sentinel must appear nowhere: not in JSON, stdout, or refusals."""
    sentinel = "SENTINEL-9f3c2b1a-body-text"
    rows = [
        _row("2026-01-05T10:00:00-05:00", document_id="admitted-doc", body=sentinel),
        # a refused row carrying the same sentinel, so refusal details are covered too
        _row("", document_id="refused-doc", body=sentinel),
    ]
    out = tmp_path / "report.json"
    code, report, output = _run(tmp_path, rows, "2026-01-02", "2026-01-16", "--out", str(out), capsys=capsys)

    assert code == 1, output
    assert report.refusals, "the fixture must produce a refusal, otherwise refusal details are untested"
    serialized = out.read_text(encoding="utf-8")
    details = " ".join(refusal.detail for refusal in report.refusals)
    assert sentinel not in serialized
    assert sentinel not in details
    assert sentinel not in output
    assert sentinel not in report.model_dump_json()


def test_report_model_is_frozen_and_forbids_extra_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The report cannot be extended in passing, and cannot be mutated after it is built."""
    _code, report, _output = _run(tmp_path, [_row("2026-01-05T10:00:00-05:00")], capsys=capsys)

    with pytest.raises(ValidationError):
        report.events_read = 99
    with pytest.raises(ValidationError):
        audit_tool.AvailabilityReport.model_validate({**report.model_dump(), "document_text": "leak"})


def test_parquet_input_is_read_the_same_way(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The audit reads Parquet as well as CSV; the observable session is identical."""
    path = tmp_path / "events.parquet"
    pl.DataFrame([_row("2026-01-05T17:30:00-05:00")]).write_parquet(path)

    code = audit_tool.main(["--events", str(path), "--start", "2026-01-02", "--end", "2026-01-16"])
    capsys.readouterr()

    assert code == 0
    assert audit_tool.audit(path, date(2026, 1, 2), date(2026, 1, 16)).observable_sessions[0].observable_session == date(2026, 1, 6)
