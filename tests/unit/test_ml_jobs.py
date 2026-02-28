"""Unit tests for ML package helpers."""

from __future__ import annotations

import sys
from datetime import date
from types import ModuleType

from equity_lake.ml import run_prediction_job


def test_run_prediction_job_uses_package_forecaster(monkeypatch):
    """Inference should resolve the forecaster from the package module."""

    class FakeForecaster:
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

    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.PriceForecaster = FakeForecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)

    success, results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL", "MSFT"],
        model_dir="models-dir",
    )

    assert success is True
    assert results["AAPL"]["prediction"]["model_version"] == "fake"
    assert results["MSFT"]["prediction"]["date"] == date(2024, 1, 2)
