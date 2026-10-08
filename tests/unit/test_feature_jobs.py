"""Tests for the ``run_feature_job`` package entrypoint."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.features import NoFeatureHistoryError, run_feature_job


def _fake_output_window(today: date) -> pl.DataFrame:
    """A non-empty frame whose single row lands inside the requested output window."""
    return pl.DataFrame({"ticker": ["AAPL"], "date": [today]})


def _patch_engineer(monkeypatch, frame: pl.DataFrame) -> None:
    """Stub ``_load_feature_engineer`` to return an engineer yielding *frame*."""

    class _FakeEngineer:
        def generate_features(self, *_, **__):
            return frame

        def close(self):
            return None

    monkeypatch.setattr(
        "equity_lake.features._load_feature_engineer",
        lambda: _FakeEngineer,
        raising=True,
    )


def test_run_feature_job_raises_when_write_returns_false(monkeypatch):
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


def test_run_feature_job_succeeds_when_write_returns_true(monkeypatch):
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
def price_lake(tmp_path, monkeypatch) -> dict[str, object]:
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


def test_run_feature_job_reports_requested_produced_missing(price_lake):
    """W02/F07: every requested ticker that does not reach the output window is
    reported with a stable reason code; produced scope is explicit."""
    output_day = price_lake["output_day"]
    assert isinstance(output_day, date)
    diagnostics: dict[str, object] = {}

    result = run_feature_job(
        tickers=["AAPL", "MSFT", "TSLA", "GOOGL", "NVDA"],
        output_start_date=output_day,
        output_end_date=output_day,
        diagnostics=diagnostics,
    )

    assert result["ticker"].drop_nulls().unique().to_list() == ["AAPL"]
    assert diagnostics["requested"] == ["AAPL", "MSFT", "TSLA", "GOOGL", "NVDA"]
    assert diagnostics["produced"] == ["AAPL"]
    assert diagnostics["missing"] == {
        "MSFT": "insufficient_history",
        "TSLA": "missing_output_bar",
        "GOOGL": "invalid_output",
        "NVDA": "no_price_rows",
    }


def test_run_feature_job_reports_coverage_when_output_window_is_empty(price_lake):
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


def test_run_feature_job_compute_failure_is_invalid_output(price_lake, monkeypatch):
    """W02/F07: a ticker whose per-ticker compute raises is reported as
    invalid_output (isolated) while sibling tickers still produce."""
    from equity_lake.features.pipeline import FeaturePipeline

    original = FeaturePipeline.compute_technical

    def flaky_compute(self, frame, include_target=True):
        if frame["ticker"][0] == "TSLA":
            raise ValueError("bad indicator input")
        return original(self, frame, include_target=include_target)

    monkeypatch.setattr(FeaturePipeline, "compute_technical", flaky_compute)

    output_day = price_lake["output_day"]
    assert isinstance(output_day, date)
    diagnostics: dict[str, object] = {}

    result = run_feature_job(
        tickers=["AAPL", "TSLA"],
        output_start_date=output_day,
        output_end_date=output_day,
        diagnostics=diagnostics,
    )

    assert result["ticker"].drop_nulls().unique().to_list() == ["AAPL"]
    assert diagnostics["missing"] == {"TSLA": "invalid_output"}


def test_run_feature_job_missing_output_bar_is_not_no_price_rows(price_lake):
    """W02/F07: TSLA has 89 price rows (so it is not ``no_price_rows``) yet no bar
    on the requested output date — a distinct, actionable reason."""
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

    assert diagnostics["missing"] == {"TSLA": "missing_output_bar"}
