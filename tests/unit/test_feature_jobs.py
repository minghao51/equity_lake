"""Tests for the ``run_feature_job`` package entrypoint."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import deltalake
import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.features import NoFeatureHistoryError, run_feature_job  # type: ignore[import-untyped]
from equity_lake.features import engineering as engineering_module
from equity_lake.features.engineering import FeatureEngineer  # type: ignore[import-untyped]


def _fake_output_window(today: date) -> pl.DataFrame:
    """A non-empty frame whose single row lands inside the requested output window."""
    return pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "identity_schema_version": [1],
            "ticker": ["AAPL"],
            "date": [today],
        }
    )


def _patch_engineer(monkeypatch: pytest.MonkeyPatch, frame: pl.DataFrame) -> None:
    """Stub ``_load_feature_engineer`` to return an engineer yielding *frame*."""

    class _FakeEngineer:
        def generate_features(self, *_: object, **__: object) -> pl.DataFrame:
            return frame

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "equity_lake.features._load_feature_engineer",
        lambda: _FakeEngineer,
        raising=True,
    )


def test_run_feature_job_raises_when_write_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test (P0): a ``False`` feature-write result must fail the job.

    Previously ``run_feature_job`` discarded the ``upsert_dataset``
    return value, so the pipeline could report success with unwritten features.
    """
    today = date(2024, 1, 2)
    _patch_engineer(monkeypatch, _fake_output_window(today))

    with (
        patch(
            "equity_lake.ingestion.writers.upsert_dataset",
            return_value=False,
        ) as mock_write,
        pytest.raises(RuntimeError, match="Feature write to 03_gold/features failed"),
    ):
        run_feature_job(
            tickers=["AAPL"],
            output_start_date=today,
            output_end_date=today,
        )

    mock_write.assert_called_once()


def test_run_feature_job_succeeds_when_write_returns_true(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sanity check: a successful write returns the filtered frame."""
    today = date(2024, 1, 2)
    _patch_engineer(monkeypatch, _fake_output_window(today))

    with patch(
        "equity_lake.ingestion.writers.upsert_dataset",
        return_value=True,
    ):
        result = run_feature_job(
            tickers=["AAPL"],
            output_start_date=today,
            output_end_date=today,
        )

    assert result.height == 1
    assert result["ticker"].to_list() == ["AAPL"]


# =============================================================================
# W02/F07: requested/produced/missing coverage with stable reason codes.
# Real technical output over temporary Delta tables (the W01 two-phase fixture
# pattern) — not mocked row counts.
# =============================================================================


def _business_days(start: date, n: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < n:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


def _price_frame(ticker: str, days: list[date], *, close_null: bool = False) -> pl.DataFrame:
    if close_null:
        close: list[float | None] = [None] * len(days)
    else:
        # Oscillating series so RSI/MACD are non-null after warm-up (a strictly
        # monotonic series has no losses, making RSI undefined/null).
        import math

        close = [100.0 + 8 * math.sin(i / 2.5) + i * 0.05 for i in range(len(days))]
    open_ = [value - 0.5 if value is not None else None for value in close]
    return pl.DataFrame(
        {
            "mic": ["XNAS"] * len(days),
            "ticker": [ticker] * len(days),
            "date": days,
            "open": open_,
            "high": [value + 1.0 if value is not None else None for value in close],
            "low": [value - 1.0 if value is not None else None for value in close],
            "close": close,
            "volume": [1_000_000.0 + (i % 10) * 1_000 for i in range(len(days))],
        }
    )


@pytest.fixture()
def price_lake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Temporary bronze price lake with one ticker per exclusion reason."""
    all_days = _business_days(date(2024, 1, 1), 90)
    output_day = all_days[-1]
    frames = [
        _price_frame("AAPL", all_days),  # complete
        _price_frame("MSFT", all_days[:30]),  # insufficient_history (< 60)
        _price_frame("TSLA", all_days[:-1]),  # computed, but no bar on the output date
        _price_frame("GOOGL", all_days[:70], close_null=True),  # invalid_output (validation fails)
        # NVDA absent -> no_price_rows
    ]
    market_dir = tmp_path / "01_bronze" / "market_data" / "us_equity"
    market_dir.mkdir(parents=True)
    write_deltalake(str(market_dir), pl.concat(frames).to_arrow(), mode="append")
    monkeypatch.setattr("equity_lake.features.engineering.LAKE_DIR", tmp_path)
    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path)
    return {"output_day": output_day}


def test_run_feature_job_reports_requested_produced_missing(price_lake: dict[str, object]) -> None:
    """W02/F07: every requested ticker that does not reach the output window is
    reported with a stable reason code; produced scope is explicit."""
    output_day = price_lake["output_day"]
    assert isinstance(output_day, date)
    diagnostics: dict[str, object] = {}

    with pytest.raises(NoFeatureHistoryError):
        run_feature_job(
            tickers=["AAPL", "MSFT", "TSLA", "GOOGL", "NVDA"],
            output_start_date=output_day,
            output_end_date=output_day,
            diagnostics=diagnostics,
        )

    assert diagnostics["requested"] == ["AAPL", "MSFT", "TSLA", "GOOGL", "NVDA"]
    assert diagnostics["produced"] == []
    assert diagnostics["missing"] == {
        "AAPL": "action_coverage_unavailable",
        "MSFT": "insufficient_history",
        "TSLA": "action_coverage_unavailable",
        "GOOGL": "action_coverage_unavailable",
        "NVDA": "no_price_rows",
    }


def test_run_feature_job_reports_coverage_when_output_window_is_empty(price_lake: dict[str, object]) -> None:
    """W02/F07: the missing-history error path still reports per-ticker scope and
    reasons (the pipeline records these on the failed/recovery stage)."""
    output_day = price_lake["output_day"]
    assert isinstance(output_day, date)
    diagnostics: dict[str, object] = {}
    day_after = output_day + timedelta(days=1)

    with pytest.raises(NoFeatureHistoryError):
        run_feature_job(
            tickers=["MSFT", "NVDA"],
            output_start_date=day_after,
            output_end_date=day_after,
            diagnostics=diagnostics,
        )

    assert diagnostics["requested"] == ["MSFT", "NVDA"]
    assert diagnostics["produced"] == []
    assert diagnostics["missing"] == {"MSFT": "insufficient_history", "NVDA": "no_price_rows"}


def test_run_feature_job_does_not_compute_without_action_coverage(price_lake: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> None:
    """Unproven action history prevents technical computation and output."""
    from equity_lake.features.pipeline import FeaturePipeline  # type: ignore[import-untyped]

    original = FeaturePipeline.compute_technical

    def flaky_compute(self: FeaturePipeline, frame: pl.DataFrame, include_target: bool = True) -> pl.DataFrame:
        if frame["ticker"][0] == "TSLA":
            raise ValueError("bad indicator input")
        return cast(pl.DataFrame, original(self, frame, include_target=include_target))

    monkeypatch.setattr(FeaturePipeline, "compute_technical", flaky_compute)

    output_day = price_lake["output_day"]
    assert isinstance(output_day, date)
    diagnostics: dict[str, object] = {}

    with pytest.raises(NoFeatureHistoryError):
        run_feature_job(
            tickers=["AAPL", "TSLA"],
            output_start_date=output_day,
            output_end_date=output_day,
            diagnostics=diagnostics,
        )

    assert diagnostics["produced"] == []
    assert diagnostics["missing"] == {"AAPL": "action_coverage_unavailable", "TSLA": "action_coverage_unavailable"}


def test_run_feature_job_marks_missing_action_coverage_before_output_bar(price_lake: dict[str, object]) -> None:
    """Coverage uncertainty is reported before relying on an unadjusted bar."""
    output_day = price_lake["output_day"]
    assert isinstance(output_day, date)
    diagnostics: dict[str, object] = {}

    with pytest.raises(NoFeatureHistoryError):
        run_feature_job(
            tickers=["TSLA"],
            output_start_date=output_day,
            output_end_date=output_day,
            diagnostics=diagnostics,
        )

    assert diagnostics["missing"] == {"TSLA": "action_coverage_unavailable"}


@pytest.mark.parametrize("storage", ["delta", "parquet"])
def test_price_reader_pushes_symbol_membership_before_materialization(
    storage: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both local scan paths send the ticker predicate to the scanner first."""
    symbols = ["TARGET", *(f"OTHER_{index:04d}" for index in range(3_000))]
    frame = pl.DataFrame(
        {
            "ticker": symbols,
            "symbol": symbols,
            "mic": ["XNAS"] * len(symbols),
            "date": [date(2024, 1, 2)] * len(symbols),
            "open": [10.0] * len(symbols),
            "high": [11.0] * len(symbols),
            "low": [9.0] * len(symbols),
            "close": [10.5] * len(symbols),
            "volume": [1_000.0] * len(symbols),
        }
    )
    source = tmp_path / storage
    source.mkdir()
    if storage == "delta":
        write_deltalake(str(source), frame.to_arrow(), mode="overwrite")
    else:
        frame.write_parquet(source / "prices.parquet")

    engineer = FeatureEngineer.__new__(FeatureEngineer)
    engineer.market_sources = [("us_equity", source)]
    observed_filters: list[str] = []

    if storage == "delta":
        real_delta_table: Any = deltalake.DeltaTable

        class _RecordingDeltaTable:
            @staticmethod
            def is_deltatable(path: str) -> bool:
                return bool(real_delta_table.is_deltatable(path))

            def __init__(self, path: str) -> None:
                self.table: Any = real_delta_table(path)

            def schema(self) -> Any:
                return self.table.schema()

            def to_pyarrow_table(self, **kwargs: Any) -> Any:
                filters: list[tuple[str, str, Any]] = kwargs["filters"]
                observed_filters.extend(f"{column} {operator} {value}" for column, operator, value in filters)
                assert any("symbol" in predicate and "in" in predicate and "TARGET" in predicate for predicate in observed_filters)
                return self.table.to_pyarrow_table(**kwargs)

        monkeypatch.setattr(deltalake, "DeltaTable", _RecordingDeltaTable)
    else:
        real_scan_parquet = pl.scan_parquet

        class _RecordingLazyFrame:
            def __init__(self, frame: pl.LazyFrame) -> None:
                self.frame = frame

            def collect_schema(self) -> pl.Schema:
                return self.frame.collect_schema()

            def filter(self, *predicates: Any, **constraints: Any) -> _RecordingLazyFrame:
                observed_filters.extend(str(predicate) for predicate in predicates)
                self.frame = self.frame.filter(*predicates, **constraints)
                return self

            def select(self, *expressions: Any, **named_expressions: Any) -> _RecordingLazyFrame:
                self.frame = self.frame.select(*expressions, **named_expressions)
                return self

            def collect(self, *args: Any, **kwargs: Any) -> pl.DataFrame:
                assert any("symbol" in predicate and "TARGET" in predicate for predicate in observed_filters)
                return cast(pl.DataFrame, self.frame.collect(*args, **kwargs))

        def scan_parquet(*args: Any, **kwargs: Any) -> _RecordingLazyFrame:
            return _RecordingLazyFrame(real_scan_parquet(*args, **kwargs))

        monkeypatch.setattr(engineering_module.pl, "scan_parquet", scan_parquet)

    result = engineer._load_price_rows(["TARGET"], date(2024, 1, 2), date(2024, 1, 2))
    assert result["symbol"].to_list() == ["TARGET"]
    assert result.height == 1
