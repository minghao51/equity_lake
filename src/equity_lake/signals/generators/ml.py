"""ML prediction signal generator."""

from datetime import date
from pathlib import Path
from typing import Literal

import structlog

from equity_lake.core.instruments import InstrumentKey
from equity_lake.ml.forecasting import (
    ModelExpiredError,
    ModelFutureError,
    ModelIncompatibleError,
    ModelMissingError,
    ModelProvenanceError,
    ModelUnavailableError,
    NoCandidateConditionError,
    PriceForecaster,
)
from equity_lake.signals.generators.base import GenerationOutcome, SignalGenerator
from equity_lake.signals.models import Signal

logger = structlog.get_logger(__name__)


class MLPredictionSignalGenerator(SignalGenerator):
    """Generate signals based on XGBoost next-day direction forecasts.

    Reuses the existing price forecaster to predict next-day direction.
    Generates BUY when the model predicts an up day and SELL when it
    predicts a down day, subject to the configured confidence threshold.
    """

    forecaster: PriceForecaster | None = None

    def __init__(self, config: dict):
        super().__init__(config)
        # Accept legacy `model_path` configs, but treat the setting as a model directory.
        self.model_dir = Path(config.get("model_dir", config.get("model_path", "data/models")))
        self.model_mode = config.get("mode", "v1_direction")
        # Retain the legacy setting for compatibility; it is deprecated/informational and ignored for the v1 missing-horizon fallback.
        self.horizon_days = config.get("horizon_days", 1)
        self.min_confidence = config.get("min_confidence", 60)
        default_buy_threshold = self.min_confidence / 100
        self.buy_threshold = config.get("buy_probability_threshold", default_buy_threshold)
        self.sell_threshold = config.get("sell_probability_threshold", 1 - default_buy_threshold)

        self.initialization_error: str | None = None
        self.initialization_status: Literal["unavailable", "missing_model", "error", "no_condition"] = "error"
        try:
            self.forecaster = PriceForecaster(model_dir=str(self.model_dir), model_mode=self.model_mode, ml_config=config)
        except Exception as exc:
            self.initialization_error = str(exc)
            self.initialization_status = self._error_status(exc)
            logger.warning("ml_generator_forecaster_unavailable", ticker_scope="all", model_dir=str(self.model_dir), error=self.initialization_error)
            self.forecaster = None

    @staticmethod
    def _error_status(exc: Exception) -> Literal["unavailable", "missing_model", "error", "no_condition"]:
        if isinstance(exc, NoCandidateConditionError):
            return "no_condition"
        if isinstance(exc, ModelMissingError):
            return "missing_model"
        if isinstance(exc, (ModelProvenanceError, ModelIncompatibleError, ModelExpiredError, ModelFutureError, ModelUnavailableError)):
            return "unavailable"
        return "error"

    def generate(self, instrument: InstrumentKey, target_date: date) -> Signal | None:
        """Generate a signal for a canonical instrument."""
        return self.generate_outcome(instrument, target_date).signal

    def generate_outcome(self, instrument: InstrumentKey, target_date: date) -> GenerationOutcome:
        """Generate a prediction outcome without hiding model/input failures."""
        if self.forecaster is None:
            return GenerationOutcome(status=self.initialization_status, detail=self.initialization_error)

        try:
            prediction = self.forecaster.predict(ticker=instrument, date=target_date)
        except Exception as exc:
            return GenerationOutcome(status=self._error_status(exc), detail=str(exc))

        if not prediction:
            return GenerationOutcome(status="unavailable", detail="forecaster returned no prediction")
        if prediction.get("market") != instrument.market or prediction.get("mic") != instrument.mic or prediction.get("ticker") != instrument.symbol:
            return GenerationOutcome(status="unavailable", detail="forecaster returned a different or unknown canonical identity")

        probability = float(prediction.get("probability", 0.0))
        direction = int(prediction.get("prediction", probability >= 0.5))
        action: Literal["BUY", "SELL"] = "BUY" if direction == 1 else "SELL"
        confidence = probability * 100 if action == "BUY" else (1 - probability) * 100

        if (action == "BUY" and probability < self.buy_threshold) or (action == "SELL" and probability > self.sell_threshold):
            return GenerationOutcome(status="no_condition")
        if confidence < self.min_confidence:
            return GenerationOutcome(status="no_condition")

        horizon = next(
            (prediction[key] for key in ("horizon_days", "forecast_horizon_days", "label_horizon_days") if prediction.get(key) is not None),
            1,  # v1 is next-day when prediction output provides no horizon; config horizon_days is ignored.
        )
        outlook = "upside" if action == "BUY" else "downside"
        signal = Signal(
            ticker=instrument.symbol,
            date=target_date,
            signal_type="ml",
            action=action,
            confidence=confidence,
            reasoning=(f"ML predicts next-day {outlook} ({confidence:.0f}% confidence, p={probability:.2f})"),
            metadata={
                "prediction": direction,
                "probability": probability,
                "horizon_days": int(horizon),
                "model_mode": prediction.get("model_mode", self.model_mode),
                "model_version": prediction.get("model_version"),
            },
            market=instrument.market,
            mic=instrument.mic,
        )
        return GenerationOutcome(status="signal", signal=signal)
