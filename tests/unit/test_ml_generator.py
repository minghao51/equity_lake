"""Offline contract tests for canonical-identity ML signal generation."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import patch

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator  # type: ignore[import-untyped]

_INSTRUMENT = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
_IDENTITY = {"market": "us_equity", "mic": "XNAS", "ticker": "AAPL"}


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_enabled(_mock_forecaster_class: Any) -> None:
    config = {"enabled": True, "model_dir": "models", "min_confidence": 60}
    assert MLPredictionSignalGenerator(config).is_enabled() is True


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_buy_signal(mock_forecaster_class: Any) -> None:
    mock_forecaster_class.return_value.predict.return_value = {
        "prediction": 1,
        "probability": 0.75,
        "model_version": "AAPL_xgboost_2026-03-02",
        **_IDENTITY,
    }
    generator = MLPredictionSignalGenerator({"enabled": True, "model_dir": "models", "min_confidence": 60, "horizon_days": 5})

    signal = generator.generate(_INSTRUMENT, date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "ml"
    assert signal.metadata["probability"] == 0.75
    assert signal.metadata["model_mode"] == "v1_direction"
    assert signal.metadata["horizon_days"] == 1
    assert (signal.market, signal.mic) == ("us_equity", "XNAS")


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_prediction_horizon_overrides_config(mock_forecaster_class: Any) -> None:
    mock_forecaster_class.return_value.predict.return_value = {"prediction": 1, "probability": 0.75, "horizon_days": 3, **_IDENTITY}
    generator = MLPredictionSignalGenerator({"enabled": True, "model_dir": "models", "min_confidence": 60, "horizon_days": 5})

    signal = generator.generate(_INSTRUMENT, date.today())

    assert signal is not None
    assert signal.metadata["horizon_days"] == 3


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_buy_signal_below_probability_threshold(mock_forecaster_class: Any) -> None:
    mock_forecaster_class.return_value.predict.return_value = {"prediction": 1, "probability": 0.59, **_IDENTITY}
    generator = MLPredictionSignalGenerator({"enabled": True, "model_dir": "models", "buy_probability_threshold": 0.60, "min_confidence": 50})

    assert generator.generate(_INSTRUMENT, date.today()) is None


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_sell_signal(mock_forecaster_class: Any) -> None:
    mock_forecaster_class.return_value.predict.return_value = {
        "prediction": 0,
        "probability": 0.20,
        "model_version": "AAPL_xgboost_2026-03-02",
        **_IDENTITY,
    }
    generator = MLPredictionSignalGenerator({"enabled": True, "model_dir": "models", "sell_probability_threshold": 0.40, "min_confidence": 60})

    signal = generator.generate(_INSTRUMENT, date.today())

    assert signal is not None
    assert signal.action == "SELL"
    assert signal.metadata["probability"] == 0.20


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_low_confidence(mock_forecaster_class: Any) -> None:
    mock_forecaster_class.return_value.predict.return_value = {"prediction": 1, "probability": 0.50, **_IDENTITY}
    generator = MLPredictionSignalGenerator({"enabled": True, "model_dir": "models", "min_confidence": 60})

    assert generator.generate(_INSTRUMENT, date.today()) is None


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_no_model(mock_forecaster_class: Any) -> None:
    mock_forecaster_class.side_effect = RuntimeError("missing feature data")
    generator = MLPredictionSignalGenerator({"enabled": True, "model_dir": "models"})

    assert generator.generate(_INSTRUMENT, date.today()) is None
