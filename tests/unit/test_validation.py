"""Unit tests for the data quality validation framework."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from equity_lake.validation import (
    ArticleDataSchema,
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
# Graceful degradation without pointblank (B1)
# ---------------------------------------------------------------------------


def test_validation_import_degrades_gracefully_without_pointblank() -> None:
    """Without pointblank, importing the validation boundary stays import-safe.

    Mirrors ml/__init__.py: the missing dependency surfaces as a friendly
    RuntimeError naming the fix when validation is actually used, never as a
    raw ModuleNotFoundError at import time.
    """
    script = textwrap.dedent(
        """
        import sys

        sys.modules["pointblank"] = None  # simulate a broken/missing pointblank install

        import equity_lake.validation.pipeline  # must import cleanly

        import polars as pl
        from equity_lake.validation.schemas import PriceDataSchema

        df = pl.DataFrame({"ticker": ["AAPL"], "date": [None]}, schema={"ticker": pl.Utf8, "date": pl.Date})
        try:
            PriceDataSchema.validate(df)
        except RuntimeError as exc:
            assert "pointblank" in str(exc)
            assert "uv sync" in str(exc)
        else:
            raise SystemExit("expected a friendly RuntimeError without pointblank")
        """
    )
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_validation_reexports_match_schemas_modules() -> None:
    """Package-level re-exports stay wired to the schema implementations."""
    from equity_lake.validation import DataProfiler as ReExportedDataProfiler
    from equity_lake.validation import profiling, schemas

    assert ReExportedDataProfiler is profiling.DataProfiler
    assert ArticleDataSchema is schemas.ArticleDataSchema
    assert set(schemas.SCHEMA_REGISTRY) == {"price", "macro", "news", "article"}


# ---------------------------------------------------------------------------
# ArticleDataSchema tests (B3 silver contract)
# ---------------------------------------------------------------------------


def test_article_schema_allows_sparse_rows() -> None:
    """Null tickers and null enrichment values pass the article contract."""
    df = pl.DataFrame(
        {
            "article_id": ["a-1", "a-2"],
            "ticker": [None, None],
            "date": [date(2024, 1, 1), date(2024, 1, 1)],
            "sentiment_score": [0.5, None],
            "confidence": [None, 0.8],
            "market_relevance": [None, 0.9],
        }
    )
    ArticleDataSchema.validate(df)


def test_article_schema_rejects_out_of_range_sentiment() -> None:
    df = pl.DataFrame({"article_id": ["a-1"], "date": [date(2024, 1, 1)], "sentiment_score": [5.0]})
    with pytest.raises(Exception, match="expression"):
        ArticleDataSchema.validate(df)


def test_article_schema_rejects_null_article_id() -> None:
    df = pl.DataFrame({"article_id": [None], "date": [date(2024, 1, 1)]})
    with pytest.raises(Exception, match="article_id"):
        ArticleDataSchema.validate(df)


def test_article_schema_rejects_out_of_range_confidence() -> None:
    df = pl.DataFrame({"article_id": ["a-1"], "confidence": [1.5]})
    with pytest.raises(Exception, match="expression"):
        ArticleDataSchema.validate(df)


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


def test_pipeline_article_type_tolerates_sparse_columns() -> None:
    """All-null ticker (market-wide news) must not fail the article pipeline."""
    df = pl.DataFrame(
        {
            "article_id": ["a-1", "a-2"],
            "ticker": [None, None],
            "date": [date(2024, 1, 1), date(2024, 1, 1)],
        }
    )
    vp = ValidationPipeline()
    result = vp.validate(df, data_type="article")
    assert result.success
    assert result.schema_valid


def test_pipeline_profiles_are_in_memory_only(tmp_path: Path, sample_ohlcv_data: pl.DataFrame, monkeypatch: pytest.MonkeyPatch) -> None:
    """ValidationPipeline never writes profile artifacts (ingest write boundary)."""
    from equity_lake.validation import profiling

    monkeypatch.setattr(profiling, "PROFILES_DIR", tmp_path)
    vp = ValidationPipeline()
    result = vp.validate(sample_ohlcv_data, data_type="price", name="ingest_check")
    assert result.success
    vp.set_baseline("drift_base", sample_ohlcv_data)
    assert list(tmp_path.rglob("*")) == []


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


def test_profiler_quality_metrics(tmp_path: Path, sample_ohlcv_data: pl.DataFrame) -> None:
    """DataProfiler extracts quality metrics from a profile."""
    profiler = DataProfiler(storage_path=tmp_path)
    profile = profiler.profile(sample_ohlcv_data, "test")
    metrics = profiler.get_quality_metrics(profile)

    assert "close" in metrics
    assert metrics["close"]["completeness"] == 1.0
    assert metrics["close"]["null_count"] == 0


def test_profiler_default_storage_is_auxiliary_profiles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DataProfiler defaults to DATA_DIR / "profiles" (ADR-0006), never CWD-relative."""
    from equity_lake.core import paths as core_paths
    from equity_lake.validation import profiling

    monkeypatch.setattr(profiling, "PROFILES_DIR", tmp_path / "profiles")
    profiler = DataProfiler()
    assert profiler.storage_path == tmp_path / "profiles"
    assert core_paths.PROFILES_DIR == core_paths.DATA_DIR / "profiles"


def test_profiler_default_is_data_dir_profiles_without_patch() -> None:
    from equity_lake.core.paths import DATA_DIR, PROFILES_DIR

    assert DataProfiler().storage_path == PROFILES_DIR
    assert PROFILES_DIR == DATA_DIR / "profiles"


def test_profile_persist_flag_controls_disk_writes(tmp_path: Path, sample_ohlcv_data: pl.DataFrame) -> None:
    """profile(persist=False) keeps the profile in memory; persist=True writes it."""
    profiler = DataProfiler(storage_path=tmp_path)
    profiler.profile(sample_ohlcv_data, "memory_only", persist=False)
    assert not (tmp_path / "memory_only.json").exists()
    assert "memory_only" in profiler._profiles

    profiler.profile(sample_ohlcv_data, "persisted", persist=True)
    assert (tmp_path / "persisted.json").exists()
