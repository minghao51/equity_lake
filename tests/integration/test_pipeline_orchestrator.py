"""Tests for the EOD pipeline executor (core/dag.py)."""

from datetime import date

import pandas as pd

from equity_lake.core.dag import execute_eod_pipeline
from equity_lake.core.dates import resolve_trading_date


def test_execute_eod_pipeline_ingestion_stage(monkeypatch):
    """Ingestion stage should record per-market results."""

    def fake_run_daily_ingestion(*, trading_date, markets, dry_run, parallel, ticker_config, filters, explicit_tickers, skip_existing):
        assert trading_date == date(2024, 1, 2)
        assert markets == ["us", "cn"]
        assert dry_run is True
        return {"us": True, "cn": False}

    monkeypatch.setattr(
        "equity_lake.core.dag.run_daily_ingestion",
        fake_run_daily_ingestion,
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us", "cn"],
        tickers=["AAPL"],
        dry_run=True,
        skip_features=True,
        skip_ml=True,
    )

    assert results["ingestion"] == {"us": True, "cn": False}


def test_execute_eod_pipeline_feature_stage(monkeypatch):
    """Feature stage should record row count on success."""

    def fake_run_feature_pipeline(*, tickers, output_start_date, output_end_date, compute_target):
        return pd.DataFrame(
            {
                "ticker": ["AAPL", "MSFT"],
                "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
                "rsi_14": [50.0, 60.0],
            }
        )

    monkeypatch.setattr(
        "equity_lake.core.dag.run_feature_pipeline",
        fake_run_feature_pipeline,
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us"],
        tickers=["AAPL", "MSFT"],
        skip_ingestion=True,
        skip_ml=True,
    )

    assert results["features"]["success"] is True
    assert results["features"]["rows"] == 2


def test_execute_eod_pipeline_ml_stage(monkeypatch):
    """ML stage should record per-ticker inference results."""

    def fake_run_ml_inference(*, trading_date, tickers):
        return True, {
            "AAPL": {
                "success": True,
                "prediction": {
                    "ticker": "AAPL",
                    "date": date(2024, 1, 2),
                    "prediction": 1,
                    "probability": 0.73,
                },
            },
        }

    monkeypatch.setattr(
        "equity_lake.core.dag.run_ml_inference",
        fake_run_ml_inference,
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us"],
        tickers=["AAPL"],
        skip_ingestion=True,
        skip_features=True,
    )

    assert results["ml"]["success"] is True
    assert results["ml"]["results"]["AAPL"]["success"] is True


def test_execute_eod_pipeline_feature_failure_skips_ml(monkeypatch):
    """ML stage should be skipped when features fail."""

    def fake_run_feature_pipeline(*, tickers, output_start_date, output_end_date, compute_target):
        raise RuntimeError("feature pipeline exploded")

    monkeypatch.setattr(
        "equity_lake.core.dag.run_feature_pipeline",
        fake_run_feature_pipeline,
    )

    results = execute_eod_pipeline(
        trading_date=date(2024, 1, 2),
        markets=["us"],
        tickers=["AAPL"],
        skip_ingestion=True,
    )

    assert results["features"]["success"] is False
    assert results["ml"]["skipped"] is True


def test_resolve_trading_date_explicit() -> None:
    """Explicit date should be used as-is."""
    resolved = resolve_trading_date("2026-04-05", days_back=1)
    assert resolved == date(2026, 4, 5)


def test_resolve_trading_date_rolls_monday_to_friday() -> None:
    """Default date on Monday should map to previous Friday."""
    resolved = resolve_trading_date(None, days_back=1, today=date(2026, 4, 6))
    assert resolved == date(2026, 4, 3)


def test_resolve_trading_date_rolls_sunday_to_friday() -> None:
    """Default date on Sunday should map to previous Friday."""
    resolved = resolve_trading_date(None, days_back=1, today=date(2026, 4, 5))
    assert resolved == date(2026, 4, 3)


def test_resolve_trading_date_counts_trading_days() -> None:
    """Relative dates should skip weekends when days_back > 1."""
    resolved = resolve_trading_date(None, days_back=2, today=date(2026, 4, 6))
    assert resolved == date(2026, 4, 2)


def test_resolve_trading_date_relative_weekday() -> None:
    """Relative weekday dates should still subtract one trading day."""
    resolved = resolve_trading_date(None, days_back=1, today=date(2026, 4, 7))
    assert resolved == date(2026, 4, 6)
