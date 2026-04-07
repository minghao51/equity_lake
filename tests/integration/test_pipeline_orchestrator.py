"""Unit tests for the in-process pipeline orchestrator."""

from datetime import date

import pandas as pd

from equity_lake.run_pipeline import PipelineOrchestrator, resolve_trading_date


def test_run_ingestion_uses_direct_stage_helper(monkeypatch):
    """Ingestion should call the direct stage helper and record market results."""

    def fake_run_ingestion_stage(*, trading_date, markets, dry_run):
        assert trading_date == date(2024, 1, 2)
        assert markets == ["us", "cn"]
        assert dry_run is True
        return {"us": True, "cn": False}

    monkeypatch.setattr(
        "equity_lake.run_pipeline.run_ingestion_stage",
        fake_run_ingestion_stage,
    )

    orchestrator = PipelineOrchestrator(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL"],
        markets=["us", "cn"],
        dry_run=True,
    )

    success = orchestrator.run_ingestion()

    assert success is False
    assert orchestrator.results["ingestion"]["market_results"] == {
        "us": True,
        "cn": False,
    }


def test_run_feature_engineering_uses_direct_stage_helper(monkeypatch):
    """Feature generation should consume a DataFrame result directly."""

    def fake_run_feature_stage(*, trading_date, tickers):
        assert trading_date == date(2024, 1, 2)
        assert tickers == ["AAPL", "MSFT"]
        return pd.DataFrame(
            {
                "ticker": ["AAPL", "MSFT"],
                "date": pd.to_datetime(["2024-01-02", "2024-01-02"]),
                "rsi_14": [50.0, 60.0],
            }
        )

    monkeypatch.setattr(
        "equity_lake.run_pipeline.run_feature_stage",
        fake_run_feature_stage,
    )

    orchestrator = PipelineOrchestrator(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL", "MSFT"],
        markets=["us"],
    )

    success = orchestrator.run_feature_engineering()

    assert success is True
    assert orchestrator.results["feature_engineering"]["rows_generated"] == 2
    assert orchestrator.results["feature_engineering"]["feature_count"] == 3


def test_run_ml_inference_uses_direct_stage_helper(monkeypatch):
    """ML inference should store structured per-ticker results."""

    def fake_run_ml_stage(*, trading_date, tickers):
        assert trading_date == date(2024, 1, 2)
        assert tickers == ["AAPL"]
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
        "equity_lake.run_pipeline.run_ml_inference_stage",
        fake_run_ml_stage,
    )

    orchestrator = PipelineOrchestrator(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL"],
        markets=["us"],
    )

    success = orchestrator.run_ml_inference()

    assert success is True
    assert orchestrator.results["ml_inference"]["ticker_results"]["AAPL"]["success"] is True


def test_resolve_trading_date_rolls_monday_to_friday() -> None:
    """Default date on Monday should map to previous Friday."""
    resolved = resolve_trading_date(
        explicit_date=None,
        days_back=1,
        today=date(2026, 4, 6),  # Monday
    )
    assert resolved == date(2026, 4, 3)


def test_resolve_trading_date_rolls_sunday_to_friday() -> None:
    """Default date on Sunday should map to previous Friday."""
    resolved = resolve_trading_date(
        explicit_date=None,
        days_back=1,
        today=date(2026, 4, 5),  # Sunday
    )
    assert resolved == date(2026, 4, 3)


def test_resolve_trading_date_preserves_explicit_date() -> None:
    """Explicit date should be used as-is."""
    resolved = resolve_trading_date(
        explicit_date="2026-04-05",
        days_back=1,
        today=date(2026, 4, 6),
    )
    assert resolved == date(2026, 4, 5)
