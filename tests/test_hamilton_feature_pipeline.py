"""Tests for the Hamilton-backed feature pipeline."""

from __future__ import annotations

import pandas as pd

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

    assert "rsi_14" in result.columns
    assert "macd" in result.columns
    assert "volume_ratio" in result.columns
    assert "next_day_return" in result.columns
