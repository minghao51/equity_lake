"""Meta-label signal generator."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal, cast

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


class MetaLabelSignalGenerator(SignalGenerator):
    """Generate signals from the opt-in v2 meta-label model."""

    forecaster: PriceForecaster | None = None

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_dir = Path(config.get("model_dir", config.get("model_path", "data/models")))
        self.threshold = float(config.get("meta_label_threshold", 0.55))
        candidate_strategies = config.get("candidate_strategies")
        if candidate_strategies is not None and not candidate_strategies:
            raise ValueError("v2_meta_label candidate_strategies must not be empty")
        self.initialization_error: str | None = None
        self.initialization_status: Literal["missing_model", "unavailable", "error", "no_condition"] = "error"

        # Never let PriceForecaster consult the repository's default signals.yaml.
        # SignalScanner injects the selected candidates from this scan config.
        if candidate_strategies is None:
            self.initialization_error = "no candidate strategies selected by scan config"
            self.forecaster = None
            return

        try:
            self.forecaster = PriceForecaster(
                model_dir=str(self.model_dir),
                model_mode="v2_meta_label",
                ml_config=config,
            )
        except Exception as exc:
            self.initialization_error = str(exc)
            self.initialization_status = self._exception_status(exc)
            logger.warning("meta_label_generator_forecaster_unavailable", model_dir=str(self.model_dir), error=self.initialization_error)
            self.forecaster = None

    def generate(self, instrument: InstrumentKey, target_date: date) -> Signal | None:
        """Generate a signal for one canonical instrument."""
        return self.generate_outcome(instrument, target_date).signal

    @staticmethod
    def _exception_status(exc: Exception) -> Literal["unavailable", "missing_model", "error", "no_condition"]:
        if isinstance(exc, NoCandidateConditionError):
            return "no_condition"
        if isinstance(exc, ModelMissingError):
            return "missing_model"
        if isinstance(exc, (ModelProvenanceError, ModelIncompatibleError, ModelExpiredError, ModelFutureError, ModelUnavailableError)):
            return "unavailable"
        return "error"

    def generate_outcome(self, instrument: InstrumentKey, target_date: date) -> GenerationOutcome:
        """Evaluate the v2 candidate/model and preserve every non-signal outcome."""
        if self.forecaster is None:
            status: Literal["unavailable", "missing_model", "error", "no_condition"] = self.initialization_status
            if "no candidate strategies" in (self.initialization_error or ""):
                status = "error"
            return GenerationOutcome(status=status, detail=self.initialization_error)

        try:
            prediction = self.forecaster.predict(ticker=instrument, date=target_date)
        except Exception as exc:
            return GenerationOutcome(status=self._exception_status(exc), detail=str(exc))

        if not prediction:
            return GenerationOutcome(status="unavailable", detail="forecaster returned no prediction")
        if prediction.get("market") != instrument.market or prediction.get("mic") != instrument.mic or prediction.get("ticker") != instrument.symbol:
            return GenerationOutcome(status="unavailable", detail="forecaster returned a different or unknown canonical identity")
        if not prediction.get("should_execute", False):
            return GenerationOutcome(status="no_condition")

        probability = float(prediction.get("execution_probability", prediction.get("probability", 0.0)))
        raw_action = prediction.get("candidate_action")
        if raw_action not in {"BUY", "SELL", "HOLD"}:
            return GenerationOutcome(status="error", detail="executed prediction lacks a valid candidate_action")
        action = cast(Literal["BUY", "SELL", "HOLD"], raw_action)
        confidence = probability * 100
        barrier_settings = prediction.get("barrier_settings", {})
        if not isinstance(barrier_settings, dict):
            return GenerationOutcome(status="error", detail="prediction barrier_settings must be a mapping")
        raw_prediction = prediction.get("prediction")
        prediction_value = int(raw_prediction) if raw_prediction is not None else None

        threshold_used = prediction.get("threshold_used")
        if threshold_used is None:
            threshold_used = prediction.get("meta_label_threshold")
        if threshold_used is None:
            threshold_used = self.threshold
        threshold_source = prediction.get("threshold_source") or "unknown"
        horizon = prediction.get("horizon_days", prediction.get("forecast_horizon_days", prediction.get("label_horizon_days")))
        if horizon is None:
            horizon = barrier_settings.get("vertical_barrier_days")

        signal = Signal(
            ticker=instrument.symbol,
            date=target_date,
            signal_type="ml",
            action=action,
            confidence=confidence,
            reasoning=(
                f"Meta-label accepted {action} candidate from {prediction.get('candidate_source')} "
                f"(p={probability:.2f}, threshold={float(threshold_used):.2f}, source={threshold_source})"
            ),
            metadata={
                "prediction": prediction_value,
                "probability": probability,
                "execution_probability": probability,
                "horizon_days": int(horizon) if horizon is not None else None,
                "candidate_action": action,
                "candidate_source": prediction.get("candidate_source"),
                "meta_label_threshold": float(threshold_used),
                "threshold_used": float(threshold_used),
                "threshold_source": str(threshold_source),
                "barrier_settings": barrier_settings,
                "model_mode": prediction.get("model_mode", "v2_meta_label"),
                "model_version": prediction.get("model_version"),
            },
            market=instrument.market,
            mic=instrument.mic,
        )
        return GenerationOutcome(status="signal", signal=signal)
