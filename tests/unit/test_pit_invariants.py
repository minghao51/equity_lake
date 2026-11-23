"""Point-in-time invariance for real-root reads and the strict source-clock gate (RS-D01).

Residual plan §2.8 asks for as-of invariance, future-data invariance and revision
semantics on the real-read seams. Where point-in-time is impossible by construction —
the ``vendor_events_no_clock`` basis — the tests assert the REFUSAL and the preserved
stamps instead of relabelling the rows.

The strict-refuses-weak and mixed-bases-refuse regressions already live in
``tests/unit/test_feature_loader_coverage_basis.py`` (``test_strict_default_refuses_weak_rows``,
``test_mixed_bases_refuse_under_either_request``, ``test_the_default_reader_is_still_strict``);
this module adds the invariance properties those tests do not cover and re-uses their
weak fixture so one basis is not proved twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from runpy import run_path

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from equity_lake.core.availability import AvailabilityContractError  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION  # type: ignore[import-untyped]
from equity_lake.core.source_clock import (  # type: ignore[import-untyped]
    SourceClockRecord,
    SourceClockUnavailable,
)
from equity_lake.features.labels import filter_as_of_available  # type: ignore[import-untyped]
from equity_lake.ml.feature_loader import FeatureLoader  # type: ignore[import-untyped]
from equity_lake.storage.delta import merge_delta  # type: ignore[import-untyped]

_pit = run_path(str(Path(__file__).with_name("_pit_fixtures.py")))
us_equity_session_dates = _pit["us_equity_session_dates"]
with_synthetic_feature_proof = _pit["with_synthetic_feature_proof"]
_weak = run_path(str(Path(__file__).with_name("_weak_basis_fixtures.py")))
weak_gold_frame = _weak["weak_gold_frame"]
write_gold_lake = _weak["write_gold_lake"]

_KEYS = ["market", "mic", "symbol", "date"]
FEATURES_TABLE = "03_gold/features"
_AAPL = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")


@dataclass(frozen=True)
class _Bar:
    """One synthetic Gold decision row: session, close, label availability."""

    session: date
    close: float
    label_available_date: date


def _sessions(count: int) -> list[date]:
    return us_equity_session_dates(date(2024, 1, 2), count)


def _gold_frame(instrument: InstrumentKey, bars: Sequence[_Bar]) -> pl.DataFrame:
    """A strict Gold frame carrying the ADR-0015 proof for exactly these sessions."""
    frame = pl.DataFrame(
        {
            "date": pl.Series([bar.session for bar in bars], dtype=pl.Date),
            "close": pl.Series([bar.close for bar in bars], dtype=pl.Float64),
            "next_day_return": pl.Series([0.001 * index for index in range(len(bars))], dtype=pl.Float64),
            "label_available_date": pl.Series([bar.label_available_date for bar in bars], dtype=pl.Date),
            "feature_schema_version": pl.Series([FEATURE_SCHEMA_VERSION] * len(bars), dtype=pl.Int8),
        }
    )
    return with_synthetic_feature_proof(frame, instrument)


def _write(root: Path, instrument: InstrumentKey, bars: Sequence[_Bar]) -> None:
    merge_delta(_gold_frame(instrument, bars), FEATURES_TABLE, key_columns=_KEYS, partition_by=["date"], lake_dir=root)


def _read(root: Path, instrument: InstrumentKey, start: date, end: date, cutoff: date | None = None) -> pl.DataFrame:
    loader = FeatureLoader(lake_dir=root)
    frame = loader.load_features(instrument, start, end, require_available_by=cutoff)
    return frame.sort("date") if "date" in frame.columns else frame


def test_an_as_of_read_is_invariant_to_later_appended_rows(tmp_path: Path) -> None:
    """The same cutoff returns the same rows before and after later data lands.

    Falsified by: judging availability from the store's current extent instead of the
    row-bound stamp, which would retroactively change an already-issued as-of read.
    """
    sessions = _sessions(7)
    first_window = [_Bar(session=sessions[index], close=100.0 + index, label_available_date=sessions[index + 1]) for index in range(3)]
    _write(tmp_path, _AAPL, first_window)
    cutoff = sessions[2]

    before = _read(tmp_path, _AAPL, sessions[0], cutoff, cutoff)

    _write(
        tmp_path,
        _AAPL,
        [_Bar(session=sessions[index], close=200.0 + index, label_available_date=sessions[index + 1]) for index in range(3, 6)],
    )
    after = _read(tmp_path, _AAPL, sessions[0], cutoff, cutoff)

    assert before["date"].to_list() == sessions[0:2]  # the cutoff row's label is not yet known
    assert_frame_equal(before, after)
    assert after["close"].to_list() == [100.0, 101.0]  # no restamp, no replacement


def test_later_rows_keep_their_own_stamps_and_never_restamp_earlier_ones(tmp_path: Path) -> None:
    """Each appended frame keeps the proof it was written with; nothing is relabelled."""
    sessions = _sessions(7)
    _write(tmp_path, _AAPL, [_Bar(session=sessions[index], close=100.0, label_available_date=sessions[index + 1]) for index in range(3)])
    _write(tmp_path, _AAPL, [_Bar(session=sessions[index], close=200.0, label_available_date=sessions[index + 1]) for index in range(3, 6)])

    frame = _read(tmp_path, _AAPL, sessions[0], sessions[5])

    coverage_end = dict(zip(frame["date"].to_list(), frame["action_coverage_end"].to_list(), strict=True))
    assert coverage_end[sessions[0]] == sessions[2]  # the first window's own proof
    assert coverage_end[sessions[5]] == sessions[5]  # the appended window's own proof
    assert set(frame["action_coverage_basis"].to_list()) == {"source_clock"}


def test_future_dated_rows_cannot_enter_an_earlier_as_of_read() -> None:
    """A row dated (or finalized) after the cutoff is excluded, never tolerated.

    Falsified by: keeping unknown/late availability in the as-of read, which admits the
    row whose label was finalized after the cutoff (ADR-0014 §5).
    """
    sessions = _sessions(6)
    frame = pl.DataFrame(
        {
            "date": pl.Series(sessions[:5], dtype=pl.Date),
            "label_available_date": pl.Series([sessions[index + 1] for index in range(5)], dtype=pl.Date),
        }
    )

    kept = filter_as_of_available(frame, sessions[2])

    assert kept["date"].to_list() == sessions[0:2]
    assert sessions[4] not in kept["date"].to_list()  # a row after the cutoff never appears
    assert sessions[3] not in kept["date"].to_list()  # nor one whose label finalizes later


def test_a_correction_restamps_only_its_own_row(tmp_path: Path) -> None:
    """A revised vendor row is a new vintage at that key; neighbours keep their stamps."""
    sessions = _sessions(4)
    bars = [_Bar(session=sessions[index], close=100.0 + index, label_available_date=sessions[index + 1]) for index in range(3)]
    _write(tmp_path, _AAPL, bars)
    before = _read(tmp_path, _AAPL, sessions[0], sessions[2])

    corrected = _Bar(session=sessions[1], close=999.0, label_available_date=sessions[2])
    _write(tmp_path, _AAPL, [corrected])
    after = _read(tmp_path, _AAPL, sessions[0], sessions[2])

    untouched = [sessions[0], sessions[2]]
    assert_frame_equal(before.filter(pl.col("date").is_in(untouched)), after.filter(pl.col("date").is_in(untouched)))
    revised = after.filter(pl.col("date") == sessions[1])
    assert revised["close"].to_list() == [999.0]
    assert revised["action_coverage_start"].to_list() == [sessions[1]]  # the correction's own vintage
    assert before.filter(pl.col("date") == sessions[1])["action_coverage_start"].to_list() == [sessions[0]]


def test_a_prior_as_of_read_does_not_change_when_a_later_correction_lands(tmp_path: Path) -> None:
    """A correction outside the cutoff window leaves the earlier as-of read untouched."""
    sessions = _sessions(6)
    _write(tmp_path, _AAPL, [_Bar(session=sessions[index], close=100.0 + index, label_available_date=sessions[index + 1]) for index in range(5)])
    cutoff = sessions[2]
    first = _read(tmp_path, _AAPL, sessions[0], cutoff, cutoff)

    _write(tmp_path, _AAPL, [_Bar(session=sessions[4], close=999.0, label_available_date=sessions[5])])
    second = _read(tmp_path, _AAPL, sessions[0], cutoff, cutoff)

    assert_frame_equal(first, second)


def test_weak_basis_rows_keep_null_clocks_and_strict_still_refuses_after_a_revision(tmp_path: Path) -> None:
    """PIT stays impossible on ``vendor_events_no_clock``: the refusal and stamps hold.

    A revision of the weak store is a new row under the same weak stamp; it never grows
    a clock and it never becomes point-in-time evidence.
    """
    sessions = _sessions(40)
    write_gold_lake(tmp_path, [weak_gold_frame(_AAPL, sessions)])
    window = (sessions[0], sessions[20])

    with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
        FeatureLoader(lake_dir=tmp_path).load_features(_AAPL, *window)

    weak = FeatureLoader(lake_dir=tmp_path, coverage_basis="vendor_events_no_clock").load_features(_AAPL, *window)
    assert weak["action_source_available_at"].null_count() == weak.height
    assert weak["action_source_valid_until"].null_count() == weak.height
    assert weak["action_source_age_days"].null_count() == weak.height

    write_gold_lake(tmp_path, [weak_gold_frame(_AAPL, sessions[30:], seed=13)])
    revised = FeatureLoader(lake_dir=tmp_path, coverage_basis="vendor_events_no_clock").load_features(_AAPL, *window)

    assert_frame_equal(weak, revised)  # the earlier window is unchanged by the revision
    assert revised["action_source_available_at"].null_count() == revised.height
    with pytest.raises(AvailabilityContractError, match="one read admits one basis"):
        FeatureLoader(lake_dir=tmp_path).load_features(_AAPL, *window)


def _record(series: str = "AAPL") -> SourceClockRecord:
    return SourceClockRecord.issue(
        source="synthetic-fixture-source-issued",
        market="us_equity",
        series=series,
        evidence_kind="source_publication",
        evidence_digest="1" * 64,
        source_available_at=datetime(2024, 1, 2, 21, 0, tzinfo=UTC),
        coverage_start=date(2024, 1, 2),
        coverage_end=date(2024, 12, 31),
        expires_on=date(2025, 3, 31),
        rights_reference="licence-reference-1",
        recorded_at=datetime(2024, 6, 1, tzinfo=UTC),
    )


def test_the_strict_feature_read_asks_the_gate_and_refuses_when_it_fails(tmp_path: Path) -> None:
    """The consumer gate is wired opt-in: a qualified clock passes, a missing one raises.

    Defaults are unchanged — ``source_clock_source=None`` reads exactly as before — and a
    refused gate never falls back to the weak basis.
    """
    sessions = _sessions(3)
    _write(tmp_path, _AAPL, [_Bar(session=sessions[index], close=10.0, label_available_date=sessions[index + 1]) for index in range(2)])
    loader = FeatureLoader(lake_dir=tmp_path)

    unqualified = loader.load_features(_AAPL, sessions[0], sessions[1])
    qualified = loader.load_features(
        _AAPL,
        sessions[0],
        sessions[1],
        source_clock_source="synthetic-fixture-source-issued",
        source_clock_records=[_record()],
    )
    assert_frame_equal(unqualified, qualified)

    with pytest.raises(SourceClockUnavailable, match="is not qualified"):
        loader.load_features(_AAPL, sessions[0], sessions[1], source_clock_source="unknown-source", source_clock_records=[_record()])
    with pytest.raises(SourceClockUnavailable, match="append-only qualification records"):
        loader.load_features(_AAPL, sessions[0], sessions[1], source_clock_source="synthetic-fixture-source-issued")
    with pytest.raises(SourceClockUnavailable, match="append-only qualification records"):
        loader.load_features(_AAPL, sessions[0], sessions[1], source_clock_records=[_record()])
