"""ML prediction signal generator."""

from datetime import date
from pathlib import Path
from typing import Literal

from equity_lake.ml.forecasting import PriceForecaster
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal


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
        self.horizon_days = config.get("horizon_days", 5)
        self.min_confidence = config.get("min_confidence", 60)
        default_buy_threshold = self.min_confidence / 100
        self.buy_threshold = config.get("buy_probability_threshold", default_buy_threshold)
        self.sell_threshold = config.get("sell_probability_threshold", 1 - default_buy_threshold)

        try:
            self.forecaster = PriceForecaster(model_dir=str(self.model_dir))
        except Exception:
            self.forecaster = None

    def generate(self, ticker: str, target_date: date) -> Signal | None:
        """Generate signal based on ML direction prediction.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action based on predicted direction
        """
        if not self.is_enabled():
            return None

        if self.forecaster is None:
            # Model not available
            return None

        try:
            # Generate prediction
            prediction = self.forecaster.predict(ticker=ticker, date=target_date)
        except Exception:
            # Prediction failed
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

        if action == "BUY":
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="ml",
                action=action,
                confidence=confidence,
                reasoning=(f"ML predicts next-day upside ({confidence:.0f}% confidence, p={probability:.2f})"),
                metadata={
                    "prediction": direction,
                    "probability": probability,
                    "horizon_days": self.horizon_days,
                    "confidence": confidence,
                    "model_version": prediction.get("model_version"),
                },
            )

        return Signal(
            ticker=ticker,
            date=target_date,
            signal_type="ml",
            action=action,
            confidence=confidence,
            reasoning=(f"ML predicts next-day downside ({confidence:.0f}% confidence, p={probability:.2f})"),
            metadata={
                "prediction": direction,
                "probability": probability,
                "horizon_days": self.horizon_days,
                "confidence": confidence,
                "model_version": prediction.get("model_version"),
            },
        )
