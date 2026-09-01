"""Meta-label signal generator."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, cast

import structlog

from equity_lake.ml.forecasting import PriceForecaster
from equity_lake.signals.generators.base import SignalGenerator
from equity_lake.signals.models import Signal

logger = structlog.get_logger(__name__)


class MetaLabelSignalGenerator(SignalGenerator):
    """Generate signals from the opt-in v2 meta-label model."""

    forecaster: PriceForecaster | None = None

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_dir = Path(config.get("model_dir", config.get("model_path", "data/models")))
        self.threshold = float(config.get("meta_label_threshold", 0.55))

        try:
            self.forecaster = PriceForecaster(
                model_dir=str(self.model_dir),
                model_mode="v2_meta_label",
                ml_config=config,
            )
        except Exception as exc:
            # A missing/unloadable model is not a scan-fatal error, but it must be
            # distinguishable from "no signal": zero signals ≠ broken generator.
            logger.warning("meta_label_generator_forecaster_unavailable", model_dir=str(self.model_dir), error=str(exc))
            self.forecaster = None

    def generate(self, ticker: str, target_date: date) -> Signal | None:
        """Generate signal only when a candidate entry exists and clears the v2 threshold."""
        # Enablement is gated once, at scanner construction time.
        if self.forecaster is None:
            return None

        try:
            prediction = self.forecaster.predict(ticker=ticker, date=target_date)
        except Exception as exc:
            logger.warning("meta_label_generator_predict_failed", ticker=ticker, date=str(target_date), error=str(exc))
            return None

        if not prediction.get("should_execute", False):
            return None

        probability = float(prediction.get("execution_probability", 0.0))
        action = cast(Literal["BUY", "SELL", "HOLD"], prediction.get("candidate_action", "BUY"))
        confidence = probability * 100
        barrier_settings = prediction.get("barrier_settings", {})
        raw_prediction = prediction.get("prediction")
        prediction_value = int(raw_prediction) if raw_prediction is not None else None

        return Signal(
            ticker=ticker,
            date=target_date,
            signal_type="ml",
            action=action,
            confidence=confidence,
            reasoning=(
                f"Meta-label accepted {action} candidate from {prediction.get('candidate_source')} "
                f"(p={probability:.2f}, threshold={self.threshold:.2f})"
            ),
            metadata={
                "prediction": prediction_value,
                "probability": probability,
                "execution_probability": probability,
                "candidate_action": action,
                "candidate_source": prediction.get("candidate_source"),
                "meta_label_threshold": prediction.get("meta_label_threshold", self.threshold),
                "barrier_settings": barrier_settings,
                "model_mode": prediction.get("model_mode", "v2_meta_label"),
                "model_version": prediction.get("model_version"),
            },
        )
