"""Unit tests for ML package helpers."""

from __future__ import annotations

import sys
from datetime import date
from types import ModuleType

from equity_lake.ml import run_prediction_job


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
