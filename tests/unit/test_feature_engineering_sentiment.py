"""Tests for sentiment-aware feature engineering helpers."""

from __future__ import annotations

import pandas as pd
import polars as pl
import pytest

from equity_lake.features.engineering import FeatureEngineer


def test_zscore_cross_sectional_normalizes_per_date() -> None:
    """Z-scored features should have ~0 mean and ~1 std within each date group."""
    engineer = FeatureEngineer.__new__(FeatureEngineer)
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "GOOG", "AAPL", "MSFT", "GOOG"],
            "date": pd.to_datetime(["2024-01-01"] * 3 + ["2024-01-02"] * 3),
            "rsi_14": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
            "macd": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        }
    )

    enriched = engineer.zscore_cross_sectional(frame)
    assert isinstance(enriched, pl.DataFrame)
    assert "rsi_14_zscore" in enriched.columns
    assert "macd_zscore" in enriched.columns

    for date_value in enriched["date"].unique():
        per_date = enriched.filter(pl.col("date") == date_value)
        mean_z = per_date["rsi_14_zscore"].mean()
        std_z = per_date["rsi_14_zscore"].std()
        assert abs(mean_z) < 1e-6
        assert abs(std_z - 1.0) < 1e-3


def test_zscore_cross_sectional_skips_metadata_columns() -> None:
    """Ticker, date, OHLC, and target columns should not be z-scored."""
    engineer = FeatureEngineer.__new__(FeatureEngineer)
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "close": [100.0, 200.0],
            "rsi_14": [50.0, 60.0],
        }
    )

    enriched = engineer.zscore_cross_sectional(frame)
    assert "close_zscore" not in enriched.columns
    assert "ticker_zscore" not in enriched.columns
    assert "rsi_14_zscore" in enriched.columns


def test_zscore_cross_sectional_stats_ignore_null_values() -> None:
    """A7 (handoff 08): per-date mean/std must be computed on non-null values
    only — nulls (e.g. missing enrichments for some tickers) must not bias the
    cross-sectional statistics. Null inputs are imputed to 0.0 in the final z
    expression alone."""
    engineer = FeatureEngineer.__new__(FeatureEngineer)
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "GOOG"],
            "date": pd.to_datetime(["2024-01-01"] * 3),
            "rsi_14": [1.0, 3.0, None],
        }
    )

    enriched = engineer.zscore_cross_sectional(frame)

    # Statistics over the two non-null values only.
    non_null = pl.Series([1.0, 3.0])
    mean = non_null.mean()
    std = non_null.std()
    eps = 1e-8

    zscores = enriched["rsi_14_zscore"].to_list()
    assert zscores[0] == pytest.approx((1.0 - mean) / (std + eps))
    assert zscores[1] == pytest.approx((3.0 - mean) / (std + eps))
    # The null input is imputed to 0.0 in the z expression only.
    assert zscores[2] == pytest.approx((0.0 - mean) / (std + eps))
    # The original column keeps its null (not overwritten).
    assert enriched["rsi_14"].null_count() == 1
    # No nulls leak into the zscore output.
    assert enriched["rsi_14_zscore"].null_count() == 0
