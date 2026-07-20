"""Tests for the ``run_feature_job`` package entrypoint."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.features import run_feature_job


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

    Previously ``run_feature_job`` discarded the ``write_to_partitioned_parquet``
    return value, so the pipeline could report success with unwritten features.
    """
    today = date(2024, 1, 2)
    _patch_engineer(monkeypatch, _fake_output_window(today))

    with (
        patch(
            "equity_lake.ingestion.writers.write_to_partitioned_parquet",
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
        "equity_lake.ingestion.writers.write_to_partitioned_parquet",
        return_value=True,
    ):
        result = run_feature_job(
            tickers=["AAPL"],
            output_start_date=today,
            output_end_date=today,
        )

    assert result.height == 1
    assert result["ticker"].to_list() == ["AAPL"]
