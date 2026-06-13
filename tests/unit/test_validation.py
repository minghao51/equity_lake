"""Unit tests for the data quality validation framework."""

from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from equity_lake.validation import (
    MacroDataSchema,
    NewsDataSchema,
    PriceDataSchema,
    ValidationPipeline,
)
from equity_lake.validation.profiling import DataProfiler, DriftReport

_OHLCV_SCHEMA = {
    "ticker": pl.Utf8,
    "date": pl.Date,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.Float64,
}

# ---------------------------------------------------------------------------
# PriceDataSchema tests
# ---------------------------------------------------------------------------


def test_price_schema_valid(sample_ohlcv_data: pl.DataFrame) -> None:
    """Valid OHLCV data passes schema validation."""
    PriceDataSchema.validate(sample_ohlcv_data)


def test_price_schema_valid_polars(sample_ohlcv_data: pl.DataFrame) -> None:
    """Valid OHLCV Polars data passes schema validation."""
    PriceDataSchema.validate(sample_ohlcv_data)


def test_price_schema_negative_price() -> None:
    """Negative close price fails validation."""
    df = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "open": [150.0],
            "high": [155.0],
            "low": [148.0],
            "close": [-1.0],
            "volume": [1000000],
        },
        schema={
            "ticker": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
    )
    with pytest.raises(Exception, match="close"):
        PriceDataSchema.validate(df)


def test_price_schema_high_less_than_low() -> None:
    """high < low fails price_consistency check."""
    df = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "open": [150.0],
            "high": [140.0],
            "low": [155.0],
            "close": [152.0],
            "volume": [1000000],
        },
        schema={
            "ticker": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
    )
    with pytest.raises(Exception, match="expression"):
        PriceDataSchema.validate(df)


def test_price_schema_duplicates() -> None:
    """Duplicate ticker+date fails rows_distinct check."""
    df = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL"],
            "date": [date(2024, 1, 1), date(2024, 1, 1)],
            "open": [150.0, 151.0],
            "high": [155.0, 156.0],
            "low": [148.0, 149.0],
            "close": [152.0, 153.0],
            "volume": [1000000, 1100000],
        },
        schema={
            "ticker": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
    )
    with pytest.raises(Exception, match="distinct"):
        PriceDataSchema.validate(df)


# ---------------------------------------------------------------------------
# MacroDataSchema tests
# ---------------------------------------------------------------------------


def test_macro_schema_valid() -> None:
    """Valid macro data passes validation."""
    df = pl.DataFrame(
        {
            "date": [date(2024, 1, 1)],
            "indicator": ["treasury_10y"],
            "value": [4.2],
            "source": ["yfinance"],
        },
        schema={"date": pl.Date, "indicator": pl.Utf8, "value": pl.Float64, "source": pl.Utf8},
    )
    MacroDataSchema.validate(df)


# ---------------------------------------------------------------------------
# NewsDataSchema tests
# ---------------------------------------------------------------------------


def test_news_schema_valid() -> None:
    """Valid news data passes validation."""
    df = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "datetime": [datetime(2024, 1, 1, 12, 0)],
            "source": ["reuters"],
            "headline": ["Apple announces new product"],
            "url": ["https://example.com/1"],
            "sentiment_score": [0.5],
            "sentiment_label": ["positive"],
        },
    )
    NewsDataSchema.validate(df)


# ---------------------------------------------------------------------------
# ValidationPipeline tests
# ---------------------------------------------------------------------------


def test_pipeline_success(sample_ohlcv_data: pl.DataFrame) -> None:
    """Pipeline returns success on valid data."""
    vp = ValidationPipeline()
    result = vp.validate(sample_ohlcv_data, data_type="price")
    assert result.success
    assert result.schema_valid


def test_pipeline_success_polars(sample_ohlcv_data: pl.DataFrame) -> None:
    """Pipeline returns success on valid Polars data."""
    vp = ValidationPipeline()
    result = vp.validate(sample_ohlcv_data, data_type="price")
    assert result.success
    assert result.schema_valid


def test_pipeline_schema_failure() -> None:
    """Pipeline returns failure on invalid data."""
    df = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 1)],
            "open": [150.0],
            "high": [155.0],
            "low": [148.0],
            "close": [-1.0],
            "volume": [1000000],
        },
        schema={
            "ticker": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
    )
    vp = ValidationPipeline()
    result = vp.validate(df, data_type="price")
    assert not result.success
    assert not result.schema_valid
    assert len(result.errors) > 0


def test_pipeline_with_profiling(sample_ohlcv_data: pl.DataFrame) -> None:
    """Pipeline creates profile and returns quality metrics."""
    vp = ValidationPipeline()
    result = vp.validate(sample_ohlcv_data, data_type="price", name="test_profile")
    assert result.success
    assert "quality" in result.metrics


def test_pipeline_drift_detection(sample_ohlcv_data: pl.DataFrame) -> None:
    """Pipeline detects drift between baseline and current data."""
    vp = ValidationPipeline()
    vp.set_baseline("test", sample_ohlcv_data)

    drifted = sample_ohlcv_data.with_columns([pl.col(col) * 2.0 for col in ["open", "high", "low", "close"]])

    result = vp.validate(drifted, data_type="price", check_drift=True, name="test")
    assert result.drift_detected


def test_validate_and_fix_deduplicates(sample_ohlcv_data: pl.DataFrame) -> None:
    """validate_and_fix removes duplicate rows."""
    dup = pl.concat([sample_ohlcv_data, sample_ohlcv_data], how="vertical")
    vp = ValidationPipeline()
    fixed, result = vp.validate_and_fix(dup, data_type="price")
    assert fixed.height == sample_ohlcv_data.height
    assert isinstance(fixed, pl.DataFrame)
    assert result.success


def test_validate_and_fix_polars_preserves_polars(sample_ohlcv_data: pl.DataFrame) -> None:
    """validate_and_fix keeps Polars outputs for Polars callers."""
    dup = pl.concat([sample_ohlcv_data, sample_ohlcv_data], how="vertical")
    vp = ValidationPipeline()
    fixed, result = vp.validate_and_fix(dup, data_type="price")
    assert isinstance(fixed, pl.DataFrame)
    assert fixed.height == sample_ohlcv_data.height
    assert result.success


# ---------------------------------------------------------------------------
# DriftReport serialization test
# ---------------------------------------------------------------------------


def test_drift_report_serialization() -> None:
    """DriftReport roundtrips through JSON."""
    report = DriftReport(has_drift=True, columns={"close": {"mean_current": 300, "mean_baseline": 150, "pct_change": 1.0}})
    json_str = report.model_dump_json()
    restored = DriftReport.model_validate_json(json_str)
    assert restored.has_drift
    assert "close" in restored.columns


# ---------------------------------------------------------------------------
# DataProfiler tests
# ---------------------------------------------------------------------------


def test_profiler_quality_metrics(sample_ohlcv_data: pl.DataFrame) -> None:
    """DataProfiler extracts quality metrics from a profile."""
    profiler = DataProfiler()
    profile = profiler.profile(sample_ohlcv_data, "test")
    metrics = profiler.get_quality_metrics(profile)

    assert "close" in metrics
    assert metrics["close"]["completeness"] == 1.0
    assert metrics["close"]["null_count"] == 0
