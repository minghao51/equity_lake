"""Test MLPredictionSignalGenerator."""

import pytest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

from equity_lake.signals.generators.ml import MLPredictionSignalGenerator


def test_ml_generator_enabled():
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "model_path": "model.pkl",
        "min_confidence": 60,
    }
    gen = MLPredictionSignalGenerator(config)
    assert gen.is_enabled() is True


@patch("equity_lake.signals.generators.ml.PriceForecaster")
def test_ml_generator_buy_signal(mock_forecaster_class):
    """Test BUY signal when prediction positive."""
    # Create a mock forecaster instance
    mock_forecaster_instance = Mock()
    mock_forecaster_instance.predict.return_value = {
        "predicted_return": 0.05,  # 5% above buy_threshold
        "confidence": 75,
    }
    # Make load_model return the instance
    mock_forecaster_instance.load_model.return_value = mock_forecaster_instance

    mock_forecaster_class.return_value = mock_forecaster_instance
    mock_forecaster_class.load_model.return_value = mock_forecaster_instance

    # Mock Path.exists to return True
    with patch.object(Path, "exists", return_value=True):
        config = {
            "enabled": True,
            "model_path": "model.pkl",
            "buy_return_threshold": 0.03,
            "min_confidence": 60,
        }
        gen = MLPredictionSignalGenerator(config)
        signal = gen.generate("AAPL", date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "ml"
    assert signal.metadata["predicted_return"] == 0.05


@patch("equity_lake.signals.generators.ml.PriceForecaster")
def test_ml_generator_low_confidence(mock_forecaster_class):
    """Test no signal when confidence too low."""
    mock_forecaster_instance = Mock()
    mock_forecaster_instance.predict.return_value = {
        "predicted_return": 0.10,
        "confidence": 50,  # Below min_confidence
    }
    mock_forecaster_instance.load_model.return_value = mock_forecaster_instance

    mock_forecaster_class.return_value = mock_forecaster_instance
    mock_forecaster_class.load_model.return_value = mock_forecaster_instance

    with patch.object(Path, "exists", return_value=True):
        config = {
            "enabled": True,
            "model_path": "model.pkl",
            "min_confidence": 60,
        }
        gen = MLPredictionSignalGenerator(config)
        signal = gen.generate("AAPL", date.today())

    assert signal is None


def test_ml_generator_no_model():
    """Test no signal when model file missing."""
    config = {
        "enabled": True,
        "model_path": "nonexistent_model.pkl",
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())
    assert signal is None
