"""ML prediction signal generator."""

from datetime import date, timedelta
from pathlib import Path

from equity_lake.ml.forecasting import PriceForecaster
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal


class MLPredictionSignalGenerator(SignalGenerator):
    """Generate signals based on XGBoost price forecasts.

    Reuses existing ML forecaster to predict future returns.
    Generates BUY when predicted return is positive and
    confidence exceeds threshold.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_path = Path(
            config.get("model_path", "data/models/xgboost_price_forecaster.pkl")
        )
        self.horizon_days = config.get("horizon_days", 5)
        self.buy_threshold = config.get("buy_return_threshold", 0.03)
        self.sell_threshold = config.get("sell_return_threshold", -0.02)
        self.min_confidence = config.get("min_confidence", 60)

        # Initialize forecaster
        self.forecaster = None
        if self.model_path.exists():
            try:
                self.forecaster = PriceForecaster.load_model(self.model_path)
            except Exception:
                pass  # Model not available

    def generate(self, ticker: str, target_date: date) -> Signal | None:
        """Generate signal based on ML price prediction.

        Args:
            ticker: Stock symbol
            target_date: Date to generate signal for

        Returns:
            Signal with action based on predicted return
        """
        if not self.is_enabled():
            return None

        if self.forecaster is None:
            # Model not available
            return None

        # Fetch historical data for features
        start_date = target_date - timedelta(days=90)

        try:
            # Generate prediction
            prediction = self.forecaster.predict(
                ticker=ticker, current_date=target_date, horizon=self.horizon_days
            )
        except Exception:
            # Prediction failed
            return None

        if not prediction:
            return None

        # Extract prediction and confidence
        predicted_return = prediction.get("predicted_return", 0)
        confidence = prediction.get("confidence", 0)

        # Check confidence threshold
        if confidence < self.min_confidence:
            return None

        # Generate signal
        if predicted_return >= self.buy_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="ml",
                action="BUY",
                confidence=float(confidence),
                reasoning=f"ML predicts {predicted_return:.1%} return in {self.horizon_days} days ({confidence:.0f}% confidence)",
                metadata={
                    "predicted_return": predicted_return,
                    "horizon_days": self.horizon_days,
                    "confidence": confidence,
                    "features": prediction.get("important_features", []),
                },
            )
        elif predicted_return <= self.sell_threshold:
            return Signal(
                ticker=ticker,
                date=target_date,
                signal_type="ml",
                action="SELL",
                confidence=float(confidence),
                reasoning=f"ML predicts {predicted_return:.1%} return in {self.horizon_days} days ({confidence:.0f}% confidence)",
                metadata={
                    "predicted_return": predicted_return,
                    "horizon_days": self.horizon_days,
                    "confidence": confidence,
                },
            )

        return None
