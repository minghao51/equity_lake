"""Tests for the Hamilton-backed feature pipeline."""

from __future__ import annotations

import pandas as pd
import polars as pl

from equity_lake.features.pipeline import FeaturePipeline


def test_feature_pipeline_computes_expected_columns() -> None:
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": dates,
            "open": range(100, 100 + len(dates)),
            "high": range(101, 101 + len(dates)),
            "low": range(99, 99 + len(dates)),
            "close": range(100, 100 + len(dates)),
            "volume": [1_000_000] * len(dates),
        }
    )

    result = FeaturePipeline().compute(frame)

    assert isinstance(result, pl.DataFrame)
    assert "rsi_14" in result.columns
    assert "macd" in result.columns
    assert "volume_ratio" in result.columns
    assert "next_day_return" in result.columns
    assert set(result["feature_schema_version"].to_list()) == {2}


def test_feature_pipeline_accepts_mixed_date_formats() -> None:
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    mixed_dates = [dt.strftime("%Y-%m-%d") if idx % 2 == 0 else dt.strftime("%Y-%m-%d %H:%M:%S.%f") for idx, dt in enumerate(dates)]
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": mixed_dates,
            "open": range(100, 100 + len(dates)),
            "high": range(101, 101 + len(dates)),
            "low": range(99, 99 + len(dates)),
            "close": range(100, 100 + len(dates)),
            "volume": [1_000_000] * len(dates),
        }
    )

    result = FeaturePipeline().compute(frame)

    assert result.schema["date"] == pl.Datetime


def test_feature_pipeline_accepts_polars_input() -> None:
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": [dt.strftime("%Y-%m-%d") if idx % 2 == 0 else dt.strftime("%Y-%m-%d %H:%M:%S.%f") for idx, dt in enumerate(dates)],
            "open": range(100, 100 + len(dates)),
            "high": range(101, 101 + len(dates)),
            "low": range(99, 99 + len(dates)),
            "close": range(100, 100 + len(dates)),
            "volume": [1_000_000] * len(dates),
        }
    )

    result = FeaturePipeline().compute(frame)

    assert isinstance(result, pl.DataFrame)
    assert result.schema["date"] == pl.Datetime
    assert "trading_day_of_month" in result.columns
