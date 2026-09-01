"""ML prediction signal generator."""

from datetime import date
from pathlib import Path
from typing import Literal

import structlog

from equity_lake.ml.forecasting import PriceForecaster
from equity_lake.signals.generators.base import SignalGenerator
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
        self.horizon_days = config.get("horizon_days", 5)
        self.min_confidence = config.get("min_confidence", 60)
        default_buy_threshold = self.min_confidence / 100
        self.buy_threshold = config.get("buy_probability_threshold", default_buy_threshold)
        self.sell_threshold = config.get("sell_probability_threshold", 1 - default_buy_threshold)

        try:
            self.forecaster = PriceForecaster(model_dir=str(self.model_dir), model_mode=self.model_mode, ml_config=config)
        except Exception as exc:
            # A missing/unloadable model is not a scan-fatal error, but it must be
            # distinguishable from "no signal": zero signals ≠ broken generator.
            logger.warning("ml_generator_forecaster_unavailable", ticker_scope="all", model_dir=str(self.model_dir), error=str(exc))
            self.forecaster = None

    def generate(self, ticker: str, target_date: date) -> Signal | None:
        """Generate signal based on ML direction prediction.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action based on predicted direction
        """
        # Enablement is gated once, at scanner construction time (SignalScanner
        # only builds generators whose config enables them).
        if self.forecaster is None:
            # Model not available
            return None

        try:
            # Generate prediction
            prediction = self.forecaster.predict(ticker=ticker, date=target_date)
        except Exception as exc:
            logger.warning("ml_generator_predict_failed", ticker=ticker, date=str(target_date), error=str(exc))
            return None

        if not prediction:
            return None

        probability = float(prediction.get("probability", 0.0))
        direction = int(prediction.get("prediction", probability >= 0.5))
        action: Literal["BUY", "SELL"] = "BUY" if direction == 1 else "SELL"
        confidence = probability * 100 if action == "BUY" else (1 - probability) * 100

        if action == "BUY" and probability < self.buy_threshold:
            return None

        if action == "SELL" and probability > self.sell_threshold:
            return None

        if confidence < self.min_confidence:
            return None

        outlook = "upside" if action == "BUY" else "downside"
        return Signal(
            ticker=ticker,
            date=target_date,
            signal_type="ml",
            action=action,
            confidence=confidence,
            reasoning=(f"ML predicts next-day {outlook} ({confidence:.0f}% confidence, p={probability:.2f})"),
            # NOTE: no "confidence" metadata key — it duplicates the base
            # Signal.confidence column and is rejected by SignalRecord.
            metadata={
                "prediction": direction,
                "probability": probability,
                "horizon_days": self.horizon_days,
                "model_mode": prediction.get("model_mode", self.model_mode),
                "model_version": prediction.get("model_version"),
            },
        )
