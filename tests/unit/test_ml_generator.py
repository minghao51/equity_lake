"""Test MLPredictionSignalGenerator."""

from datetime import date
from unittest.mock import patch

from equity_lake.signals.generators.ml import MLPredictionSignalGenerator


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_enabled(_mock_forecaster_class):
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "model_dir": "models",
        "min_confidence": 60,
    }
    gen = MLPredictionSignalGenerator(config)
    assert gen.is_enabled() is True


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_buy_signal(mock_forecaster_class):
    """Test BUY signal when prediction positive."""
    mock_forecaster_class.return_value.predict.return_value = {
        "prediction": 1,
        "probability": 0.75,
        "model_version": "AAPL_xgboost_2026-03-02",
    }
    config = {
        "enabled": True,
        "model_dir": "models",
        "min_confidence": 60,
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is not None
    assert signal.action == "BUY"
    assert signal.signal_type == "ml"
    assert signal.metadata["probability"] == 0.75


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_buy_signal_below_probability_threshold(mock_forecaster_class):
    """Test no BUY signal when probability is below threshold."""
    mock_forecaster_class.return_value.predict.return_value = {
        "prediction": 1,
        "probability": 0.59,
    }
    config = {
        "enabled": True,
        "model_dir": "models",
        "buy_probability_threshold": 0.60,
        "min_confidence": 50,
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is None


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_sell_signal(mock_forecaster_class):
    """Test SELL signal when downside probability is strong enough."""
    mock_forecaster_class.return_value.predict.return_value = {
        "prediction": 0,
        "probability": 0.20,
        "model_version": "AAPL_xgboost_2026-03-02",
    }
    config = {
        "enabled": True,
        "model_dir": "models",
        "sell_probability_threshold": 0.40,
        "min_confidence": 60,
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is not None
    assert signal.action == "SELL"
    assert signal.metadata["probability"] == 0.20


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_low_confidence(mock_forecaster_class):
    """Test no signal when confidence too low."""
    mock_forecaster_class.return_value.predict.return_value = {
        "prediction": 1,
        "probability": 0.50,  # 50% confidence
    }
    config = {
        "enabled": True,
        "model_dir": "models",
        "min_confidence": 60,
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())

    assert signal is None


@patch("equity_lake.signals.generators.ml.PriceForecaster", autospec=True)
def test_ml_generator_no_model(mock_forecaster_class):
    """Test no signal when prediction backend fails."""
    mock_forecaster_class.side_effect = RuntimeError("missing feature data")
    config = {
        "enabled": True,
        "model_dir": "models",
    }
    gen = MLPredictionSignalGenerator(config)
    signal = gen.generate("AAPL", date.today())
    assert signal is None
