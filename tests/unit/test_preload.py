"""Tests for the bounded Gold preload helper (T2).

The helper must (a) refuse unbounded scopes, (b) drive the real pipeline stage
oldest -> newest over the sessions actually stored in Bronze, and (c) never
fabricate labels: the only finalization it reports is what the label lifecycle
returned, and sessions with nothing computable are skipped and reported.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.core.instruments import InstrumentKey
from equity_lake.devtools.preload import (
    BRONZE_PRICE_TABLE,
    DEFAULT_PRELOAD_TICKERS,
    MAX_PRELOAD_SESSIONS,
    PreloadScopeError,
    main,
    observed_sessions,
    preload_features,
)
from equity_lake.features import NoFeatureHistoryError

_DAYS = [date(2026, 8, 3) + timedelta(days=offset) for offset in range(10)]


def _bronze_lake(tmp_path: Path, days: list[date]) -> Path:
    table = tmp_path / "01_bronze" / "market_data" / "us_equity"
    table.mkdir(parents=True)
    write_deltalake(str(table), pl.DataFrame({"ticker": ["AAPL"] * len(days), "date": days}).to_arrow(), mode="append")
    return tmp_path


def test_observed_sessions_are_the_stored_bronze_sessions_oldest_first(tmp_path: Path) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)

    sessions = observed_sessions(4, end=date(2026, 8, 20), lake_dir=lake)

    assert sessions == _DAYS[-4:]
    assert sessions == sorted(sessions)
    assert observed_sessions(1, end=date(2026, 8, 20), lake_dir=lake) == [_DAYS[-1]]


def test_observed_sessions_respect_the_end_bound(tmp_path: Path) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)

    sessions = observed_sessions(3, end=_DAYS[5], lake_dir=lake)

    assert sessions == _DAYS[3:6]
    assert all(day <= _DAYS[5] for day in sessions)


@pytest.mark.parametrize("days", [0, -1, MAX_PRELOAD_SESSIONS + 1])
def test_observed_sessions_refuses_unbounded_or_empty_scope(tmp_path: Path, days: int) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)
    with pytest.raises(PreloadScopeError):
        observed_sessions(days, end=date(2026, 8, 20), lake_dir=lake)


def test_observed_sessions_requires_a_seeded_price_table(tmp_path: Path) -> None:
    with pytest.raises(PreloadScopeError, match="cannot read"):
        observed_sessions(5, end=date(2026, 8, 20), lake_dir=tmp_path)


def test_observed_sessions_reports_an_insufficient_window(tmp_path: Path) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)
    with pytest.raises(PreloadScopeError, match="only 10 observed sessions"):
        observed_sessions(25, end=date(2026, 8, 20), lake_dir=lake)


def test_preload_features_drives_the_feature_stage_oldest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)
    calls: list[date] = []
    instruments = [InstrumentKey(market="us_equity", mic="XNAS", symbol=symbol) for symbol in DEFAULT_PRELOAD_TICKERS[:2]]

    def fake_run_feature_job(**kwargs: object) -> pl.DataFrame:
        calls.append(kwargs["output_start_date"])  # type: ignore[arg-type]
        assert kwargs["compute_target"] is True
        diagnostics = kwargs["diagnostics"]
        assert isinstance(diagnostics, dict)
        diagnostics["label_finalization"] = {"eligible": 1, "finalized": 1, "pending": 0, "unmatched": 0}
        return pl.DataFrame({"date": [kwargs["output_start_date"]]})

    monkeypatch.setattr("equity_lake.devtools.preload.run_feature_job", fake_run_feature_job)

    report = preload_features(days=3, end=date(2026, 8, 20), tickers=["AAPL", "AMZN"], instruments=instruments, lake_dir=lake)

    assert calls == list(report.sessions) == _DAYS[-3:]  # oldest -> newest, one stage call per session
    assert report.rows_written == 3
    assert report.labels_finalized == 3
    assert report.errors == ()
    assert report.tickers == ("AAPL", "AMZN")


def test_preload_features_skips_a_session_with_no_computable_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)
    empty_session = _DAYS[-2]

    def fake_run_feature_job(**kwargs: object) -> pl.DataFrame:
        if kwargs["output_start_date"] == empty_session:
            raise NoFeatureHistoryError("No features generated for the requested output window")
        diagnostics = kwargs["diagnostics"]
        assert isinstance(diagnostics, dict)
        diagnostics["label_finalization"] = {"eligible": 0, "finalized": 0, "pending": 0}
        return pl.DataFrame({"date": [kwargs["output_start_date"]]})

    monkeypatch.setattr("equity_lake.devtools.preload.run_feature_job", fake_run_feature_job)

    report = preload_features(
        days=2,
        end=date(2026, 8, 20),
        tickers=["AAPL"],
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
        lake_dir=lake,
    )

    assert report.skipped_sessions == (empty_session,)
    assert report.rows_written == 1
    assert report.errors == ()


def test_preload_features_reports_label_errors_without_hiding_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)

    def fake_run_feature_job(**kwargs: object) -> pl.DataFrame:
        diagnostics = kwargs["diagnostics"]
        assert isinstance(diagnostics, dict)
        diagnostics["label_finalization"] = {"eligible": 1, "finalized": 0, "error": "update failed"}
        return pl.DataFrame({"date": [kwargs["output_start_date"]]})

    monkeypatch.setattr("equity_lake.devtools.preload.run_feature_job", fake_run_feature_job)

    report = preload_features(
        days=2,
        end=date(2026, 8, 20),
        tickers=["AAPL"],
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
        lake_dir=lake,
    )

    assert len(report.errors) == 2
    assert "update failed" in report.errors[0]


def test_preload_features_requires_a_ticker(tmp_path: Path) -> None:
    lake = _bronze_lake(tmp_path, _DAYS)
    with pytest.raises(PreloadScopeError):
        preload_features(days=2, end=date(2026, 8, 20), tickers=[], instruments=[], lake_dir=lake)


def test_main_refuses_an_unbounded_scope() -> None:
    assert main(["--days", str(MAX_PRELOAD_SESSIONS + 1)]) == 2


def test_bronze_table_matches_the_canonical_relative_layout() -> None:
    assert BRONZE_PRICE_TABLE == "01_bronze/market_data/us_equity"
