"""Integration tests for @check_output validators on DAG nodes."""

from __future__ import annotations

import warnings

import pandas as pd
import polars as pl

from equity_lake.features.pipeline import FeaturePipeline


def _sample_price_df() -> pl.DataFrame:
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    return pl.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": dates,
            "open": [float(v) for v in range(100, 180)],
            "high": [float(v) for v in range(101, 181)],
            "low": [float(v) for v in range(99, 179)],
            "close": [float(v) for v in range(100, 180)],
            "volume": [1_000_000.0] * len(dates),
        }
    )


def test_close_returns_float_series() -> None:
    """close node produces Float64 Series (PolarsDataTypeValidator)."""
    pipeline = FeaturePipeline()
    result = pipeline.compute_technical(_sample_price_df())
    assert result["close"].dtype == pl.Float64


def test_volume_returns_float_series() -> None:
    """volume node produces Float64 Series (PolarsDataTypeValidator)."""
    pipeline = FeaturePipeline()
    result = pipeline.compute_technical(_sample_price_df())
    assert result["volume"].dtype == pl.Float64


def test_rsi_stays_in_valid_range() -> None:
    """rsi_14 values are within [0, 100] (PolarsRangeValidator)."""
    pipeline = FeaturePipeline()
    result = pipeline.compute_technical(_sample_price_df())
    rsi_values = result["rsi_14"].drop_nulls()
    if not rsi_values.is_empty():
        assert rsi_values.min() >= 0.0
        assert rsi_values.max() <= 100.0


def test_validated_features_boundary_node_works() -> None:
    """validated_features Gold boundary node assembles correctly."""
    pipeline = FeaturePipeline()
    result = pipeline.compute_technical(
        _sample_price_df(),
        features=["ticker", "date", "close", "rsi_14", "macd", "volume", "validated_features"],
    )
    assert isinstance(result, pl.DataFrame)
    assert "rsi_14" in result.columns


def test_dq_warnings_are_not_errors() -> None:
    """Hamilton DQ validators at 'warn' importance don't raise exceptions."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        pipeline = FeaturePipeline()
        result = pipeline.compute_technical(_sample_price_df())
        assert result.height > 0
