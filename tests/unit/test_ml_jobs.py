"""Unit tests for ML package helpers."""

from __future__ import annotations

import sys
from datetime import date
from types import ModuleType

import polars as pl
from structlog.testing import capture_logs

from equity_lake.ml import run_prediction_job, validate_predictions


class _FakeForecaster:
    def __init__(self, model_dir=None):
        self.model_dir = model_dir
        self.closed = False

    def predict(self, *, ticker, date):
        return {
            "ticker": ticker,
            "date": date,
            "prediction": 1,
            "probability": 0.8,
            "model_version": "fake",
        }

    def close(self):
        self.closed = True


def _install_fake_forecaster(monkeypatch) -> None:
    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.PriceForecaster = _FakeForecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)


def test_validate_predictions_accepts_exact_boundary_probabilities() -> None:
    """A4 (handoff 08): float32 ``predict_proba`` can return exactly 0.0/1.0;
    inclusive bounds must accept them instead of rejecting the whole batch."""
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
            "direction": ["up", "down"],
            "probability": [0.0, 1.0],
        }
    )

    assert validate_predictions(frame) is True


def test_validate_predictions_still_rejects_out_of_range() -> None:
    """A4: inclusive bounds still reject probabilities outside [0, 1]."""
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [date(2024, 1, 2)],
            "direction": ["up"],
            "probability": [1.5],
        }
    )

    assert validate_predictions(frame) is False


def test_run_prediction_job_clips_and_logs_out_of_bounds_probability(monkeypatch) -> None:
    """A4: values infinitesimally outside [0, 1] are clipped to the boundary
    (with a warning log) and still persist instead of vanishing the batch."""

    class _BoundaryForecaster(_FakeForecaster):
        def predict(self, *, ticker, date):
            result = super().predict(ticker=ticker, date=date)
            result["probability"] = 1.0000002
            return result

    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.PriceForecaster = _BoundaryForecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)

    persisted: dict[str, object] = {}

    def _capture_merge(df, *, table, key_columns):
        persisted["df"] = df
        persisted["table"] = table
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)

    with capture_logs() as logs:
        success, _results = run_prediction_job(
            trading_date=date(2024, 1, 2),
            tickers=["AAPL"],
        )

    assert success is True
    assert persisted["table"] == "04_platinum/predictions"
    persisted_df = persisted["df"]
    assert isinstance(persisted_df, pl.DataFrame)
    assert persisted_df["probability"].to_list() == [1.0]
    assert any(log.get("event") == "prediction_probability_clipped" for log in logs)


def test_run_prediction_job_uses_package_forecaster(monkeypatch):
    """Inference should resolve the forecaster from the package module."""
    _install_fake_forecaster(monkeypatch)

    success, results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL", "MSFT"],
        model_dir="models-dir",
    )

    assert success is True
    assert results["AAPL"]["prediction"]["model_version"] == "fake"
    assert results["MSFT"]["prediction"]["date"] == date(2024, 1, 2)


def test_run_prediction_job_fails_when_persistence_returns_false(monkeypatch):
    """Regression test (P0): a ``False`` merge result must fail the ML stage.

    Previously the merge result was discarded and only a ``logger.warning`` was
    emitted on exception, so the pipeline could report success with unwritten
    predictions.
    """
    _install_fake_forecaster(monkeypatch)
    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", lambda *_, **__: False)

    success, _results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL"],
    )

    assert success is False


def test_run_prediction_job_fails_when_persistence_raises(monkeypatch):
    """Regression test (P0): a persistence exception must fail the ML stage."""
    _install_fake_forecaster(monkeypatch)

    def _boom(*_, **__):
        raise RuntimeError("disk full")

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _boom)

    success, _results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL"],
    )

    assert success is False
