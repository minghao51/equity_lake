"""Machine learning domain APIs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from math import isfinite
from numbers import Integral, Real
from typing import TYPE_CHECKING, Any

import polars as pl
import structlog

if TYPE_CHECKING:
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

from equity_lake.core.instruments import InstrumentKey  # pyright: ignore[reportMissingImports] -- source import verified by uv
from equity_lake.core.schemas import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    FORECAST_PROVENANCE_COLUMNS,
    FORECAST_PROVENANCE_VERSION,
    PREDICTION_SEMANTICS_VERSION,
    PREDICTION_TARGET_CANDIDATE_EXECUTE,
    PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
)

logger = structlog.get_logger()

# ADR-0021: nullable additive proof columns, so a row without a proof stays
# readable instead of forcing a rewrite or a backfill.
_FORECAST_PROVENANCE_DTYPES: dict[str, pl.DataType] = {
    "forecast_provenance_version": pl.Int16(),
    "model_provenance_version": pl.Int16(),
    "model_artifact_sha256": pl.String(),
    "model_trained_at": pl.Date(),
    "model_age_days": pl.Int16(),
    "model_max_age_days": pl.Int16(),
    "training_feature_schema_version": pl.Int16(),
    "inference_feature_schema_version": pl.Int16(),
    "price_adjustment_version": pl.String(),
    "availability_schema_version": pl.Int16(),
    "missing_value_policy": pl.String(),
    "candidate_strategy_fingerprint": pl.String(),
    "horizon_days": pl.Int16(),
    "threshold_used": pl.Float64(),
    "threshold_source": pl.String(),
    "prediction_target": pl.String(),
    "prediction_semantics_version": pl.Int16(),
}
_FORECAST_PROVENANCE_REQUIRED_COLUMNS = tuple(column for column in FORECAST_PROVENANCE_COLUMNS if column != "candidate_strategy_fingerprint")
_PREDICTION_TARGET_BY_MODEL_MODE = {
    "v1_direction": PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
    "v2_meta_label": PREDICTION_TARGET_CANDIDATE_EXECUTE,
}
_PROOF_INTEGER_COLUMNS = frozenset(
    {
        "forecast_provenance_version",
        "model_provenance_version",
        "model_age_days",
        "model_max_age_days",
        "training_feature_schema_version",
        "inference_feature_schema_version",
        "availability_schema_version",
        "horizon_days",
        "prediction_semantics_version",
    }
)
_PROOF_TEXT_COLUMNS = frozenset(
    {
        "model_artifact_sha256",
        "price_adjustment_version",
        "missing_value_policy",
        "candidate_strategy_fingerprint",
        "threshold_source",
        "prediction_target",
    }
)


def _proof_value_is_typed(column: str, value: Any) -> bool:
    """True when a proof input already has the contract type.

    The storage casts use ``strict=False`` so historical frames keep evolving in
    place, but a proof field must never be *laundered* by that cast: ``True`` is
    not ``1.0``, ``"4"`` is not ``4``, and a truncated float is not an age.
    """
    if column in _PROOF_INTEGER_COLUMNS:
        return isinstance(value, Integral) and not isinstance(value, bool)
    if column == "model_trained_at":
        return isinstance(value, date) and not isinstance(value, datetime)
    if column == "threshold_used":
        if isinstance(value, bool):
            return False
        return isinstance(value, Real) and isfinite(float(value))
    if column in _PROOF_TEXT_COLUMNS:
        return isinstance(value, str)
    return False


def _forecast_provenance_columns(prediction: Mapping[str, Any]) -> dict[str, Any]:
    """Return the ADR-0021 proof columns for one inference payload.

    A payload that cannot supply every proof input writes a null
    ``forecast_provenance_version`` (a documented "no proof"); no field is
    defaulted, inferred, or fabricated.
    """
    no_proof: dict[str, Any] = dict.fromkeys(FORECAST_PROVENANCE_COLUMNS)
    model_mode = prediction.get("model_mode")
    prediction_target = _PREDICTION_TARGET_BY_MODEL_MODE.get(model_mode) if isinstance(model_mode, str) else None
    if prediction_target is None:
        return no_proof

    proof = {
        "forecast_provenance_version": FORECAST_PROVENANCE_VERSION,
        "model_provenance_version": prediction.get("model_provenance_version"),
        "model_artifact_sha256": prediction.get("model_artifact_sha256"),
        "model_trained_at": prediction.get("model_trained_at"),
        "model_age_days": prediction.get("model_age_days"),
        "model_max_age_days": prediction.get("model_max_age_days"),
        "training_feature_schema_version": prediction.get("training_feature_schema_version"),
        "inference_feature_schema_version": prediction.get("inference_feature_schema_version"),
        "price_adjustment_version": prediction.get("price_adjustment_version"),
        "availability_schema_version": prediction.get("availability_schema_version"),
        "missing_value_policy": prediction.get("missing_value_policy"),
        # E3 binds the strategy fingerprint only for the meta-label contract.
        "candidate_strategy_fingerprint": prediction.get("candidate_strategy_fingerprint") if model_mode == "v2_meta_label" else None,
        "horizon_days": prediction.get("horizon_days"),
        "threshold_used": prediction.get("threshold_used"),
        "threshold_source": prediction.get("threshold_source"),
        "prediction_target": prediction_target,
        "prediction_semantics_version": PREDICTION_SEMANTICS_VERSION,
    }
    required = list(_FORECAST_PROVENANCE_REQUIRED_COLUMNS)
    if model_mode == "v2_meta_label":
        required.append("candidate_strategy_fingerprint")
    if any(proof[column] is None for column in required):
        return no_proof
    mistyped = [column for column in required if not _proof_value_is_typed(column, proof[column])]
    if mistyped:
        # A proof field that arrives with the wrong type is not persisted and not
        # coerced: the row becomes the documented no-proof row instead of claiming
        # evidence the writer had to infer.
        logger.warning("forecast_provenance_not_typed", columns=mistyped, ticker=prediction.get("ticker"))
        return no_proof
    return proof


def _forecast_provenance_violated(df: pl.DataFrame) -> bool:
    """Return True when a row claims a proof that is missing or contradictory.

    ADR-0021 §2.3: per row, ``forecast_provenance_version`` is null (a documented
    no-proof) or equals ``FORECAST_PROVENANCE_VERSION`` with every rule below
    satisfied. A claiming row failing any rule rejects the batch (fail closed).
    """
    assessed = df
    for column, dtype in (
        *_FORECAST_PROVENANCE_DTYPES.items(),
        ("model_mode", pl.String()),
        ("feature_schema_version", pl.Int16()),
        ("date", pl.Date()),
    ):
        expression = pl.col(column).cast(dtype, strict=False) if column in assessed.columns else pl.lit(None, dtype=dtype).alias(column)
        assessed = assessed.with_columns(expression)

    version = pl.col("forecast_provenance_version")
    target = pl.col("prediction_target")
    rules = (
        (version == FORECAST_PROVENANCE_VERSION)
        & pl.all_horizontal([pl.col(column).is_not_null() for column in _FORECAST_PROVENANCE_REQUIRED_COLUMNS])
        & ((pl.col("model_mode") == "v2_meta_label") == pl.col("candidate_strategy_fingerprint").is_not_null())
        & pl.col("model_artifact_sha256").str.contains(r"^[0-9a-f]{64}$")
        & (pl.col("model_trained_at") <= pl.col("date"))
        & (pl.col("model_age_days") >= 0)
        & (pl.col("model_age_days") <= pl.col("model_max_age_days"))
        & (pl.col("training_feature_schema_version") == pl.col("inference_feature_schema_version"))
        & (pl.col("inference_feature_schema_version") == pl.col("feature_schema_version"))
        & (pl.col("threshold_used") >= 0.0)
        & (pl.col("threshold_used") <= 1.0)
        & (pl.col("horizon_days") >= 1)
        & (
            ((pl.col("model_mode") == "v1_direction") & (target == PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE))
            | ((pl.col("model_mode") == "v2_meta_label") & (target == PREDICTION_TARGET_CANDIDATE_EXECUTE))
        )
        & (pl.col("prediction_semantics_version") == PREDICTION_SEMANTICS_VERSION)
    )
    claimed = version.is_not_null()
    violation = assessed.select((claimed & ~rules.fill_null(value=False)).fill_null(value=False).any()).item()
    return bool(violation)


def validate_predictions(df: pl.DataFrame) -> bool:
    """Validate prediction output before writing to Platinum.

    Uses pointblank to enforce:
    - probability in [0.0, 1.0] (inclusive: float32 ``predict_proba`` can
      return exactly 0.0/1.0, and one boundary row must not reject the batch)
    - direction in {"up", "down"}
    - canonical market/MIC/symbol identity and date are not null

    Then enforces the ADR-0021 row-bound forecast proof rules: a null
    ``forecast_provenance_version`` is a documented "no proof", and a row
    claiming version 1 must carry a complete, self-consistent E1-E4 set.
    """
    import pointblank as pb

    validation = pb.Validate(data=df)
    validation.col_vals_ge(columns="probability", value=0.0)
    validation.col_vals_le(columns="probability", value=1.0)
    validation.col_vals_in_set(columns="direction", set=["up", "down"])
    for column in ("market", "mic", "symbol", "ticker", "date", "identity_schema_version"):
        validation.col_vals_not_null(columns=column)
    validation.interrogate()
    if not bool(validation.all_passed()):
        return False
    return not _forecast_provenance_violated(df)


def run_prediction_job(
    *,
    trading_date: date,
    tickers: Sequence[str | InstrumentKey],
    model_dir: str | None = None,
    candidate_strategies: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Run price-forecast inference for each ticker.

    Predictions are persisted to the Platinum layer
    (``data/lake/04_platinum/predictions/``) as a Delta table.
    """
    try:
        from equity_lake.ml.forecasting import PriceForecaster
    except ImportError as exc:
        raise RuntimeError(
            "ML inference requires the optional 'ml' dependencies.",
        ) from exc

    forecaster_options: dict[str, Any] = {"model_dir": model_dir}
    if candidate_strategies is not None:
        forecaster_options["candidate_strategies"] = [dict(strategy) for strategy in candidate_strategies]
    forecaster = PriceForecaster(**forecaster_options)
    ticker_results: dict[str, dict[str, Any]] = {}
    prediction_rows: list[dict[str, Any]] = []
    all_success = True

    try:
        for ticker in tickers:
            requested_key = ticker if isinstance(ticker, str) else ticker.display_key
            try:
                prediction = forecaster.predict(ticker=ticker, date=trading_date)
                identity = InstrumentKey(market=prediction["market"], mic=prediction["mic"], symbol=prediction["symbol"])
                result_key = requested_key if isinstance(ticker, str) else identity.display_key
                ticker_results[result_key] = {
                    "success": True,
                    # W02/F25: inference success is not durability. ``persisted``
                    # flips to True only after the batch write actually lands;
                    # a validation/write failure must never claim it.
                    "persisted": False,
                    "prediction": prediction,
                }
                prediction_rows.append(
                    {
                        **identity.as_columns(),
                        "identity_schema_version": prediction["identity_schema_version"],
                        "date": trading_date,
                        "direction": "up" if prediction.get("prediction", 0) == 1 else "down",
                        "probability": prediction.get("probability", 0.0),
                        "model_mode": prediction.get("model_mode", "unknown"),
                        "model_version": prediction.get("model_version", "unknown"),
                        # None is an honest unknown for legacy/provided models;
                        # never stamp the current schema as if it were trained.
                        "feature_schema_version": prediction.get("feature_schema_version"),
                        # ADR-0021: persist the evidence ``predict()`` already
                        # verified, or a null envelope version when the payload
                        # cannot supply a proof.
                        **_forecast_provenance_columns(prediction),
                    }
                )
            except Exception as exc:  # pragma: no cover - reported by caller
                ticker_results[requested_key] = {
                    "success": False,
                    "persisted": False,
                    "status": getattr(exc, "status", "error"),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                all_success = False
    finally:
        forecaster.close()

    if prediction_rows:
        try:
            from equity_lake.storage.delta import merge_delta

            predictions_df = pl.DataFrame(prediction_rows).with_columns(
                pl.col("feature_schema_version").cast(pl.Int8, strict=False),
                *[pl.col(column).cast(dtype, strict=False) for column, dtype in _FORECAST_PROVENANCE_DTYPES.items()],
            )
            # float32 ``predict_proba`` can land on (or infinitesimally outside)
            # the [0, 1] boundary; clamp before validation so a boundary row
            # cannot reject — and silently drop — the whole batch (handoff 08 A4).
            out_of_bounds = predictions_df.filter((pl.col("probability") < 0.0) | (pl.col("probability") > 1.0)).height
            if out_of_bounds:
                logger.warning("prediction_probability_clipped", rows=out_of_bounds, date=str(trading_date))
                predictions_df = predictions_df.with_columns(pl.col("probability").clip(0.0, 1.0))
            if not validate_predictions(predictions_df):
                logger.error("predictions_validation_failed", rows=len(prediction_rows), date=str(trading_date))
                all_success = False
                return all_success, ticker_results
            # Surface persistence failure so the pipeline records a failed ML
            # stage instead of reporting success with unwritten predictions.
            persisted = merge_delta(
                predictions_df,
                table="04_platinum/predictions",
                key_columns=["market", "mic", "symbol", "date"],
            )
            if not persisted:
                logger.error("predictions_persist_failed", rows=len(prediction_rows), date=str(trading_date))
                all_success = False
            else:
                for row in prediction_rows:
                    persisted_ticker = row["ticker"]
                    persisted_key = row["market"] + "/" + row["mic"] + "/" + row["symbol"]
                    if persisted_key in ticker_results:
                        ticker_results[persisted_key]["persisted"] = True
                    elif persisted_ticker in ticker_results:
                        ticker_results[persisted_ticker]["persisted"] = True
                logger.info("predictions_persisted", rows=len(prediction_rows), date=str(trading_date))
        except Exception as exc:
            logger.error("predictions_persist_failed", error=str(exc))
            all_success = False

    return all_success, ticker_results


__all__ = [
    "ModelExpiredError",
    "ModelFutureError",
    "ModelIncompatibleError",
    "ModelMissingError",
    "ModelProvenanceError",
    "ModelUnavailableError",
    "NoCandidateConditionError",
    "PriceForecaster",
    "run_prediction_job",
    "validate_predictions",
]


def __getattr__(name: str) -> Any:
    """Defer optional ML imports until the symbol is actually used."""
    if name in {
        "ModelExpiredError",
        "ModelFutureError",
        "ModelIncompatibleError",
        "ModelMissingError",
        "ModelProvenanceError",
        "ModelUnavailableError",
        "NoCandidateConditionError",
        "PriceForecaster",
    }:
        from equity_lake.ml import forecasting

        return getattr(forecasting, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
