"""Machine learning domain APIs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
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

logger = structlog.get_logger()


def validate_predictions(df: pl.DataFrame) -> bool:
    """Validate prediction output before writing to Platinum.

    Uses pointblank to enforce:
    - probability in [0.0, 1.0] (inclusive: float32 ``predict_proba`` can
      return exactly 0.0/1.0, and one boundary row must not reject the batch)
    - direction in {"up", "down"}
    - canonical market/MIC/symbol identity and date are not null
    """
    import pointblank as pb

    validation = pb.Validate(data=df)
    validation.col_vals_ge(columns="probability", value=0.0)
    validation.col_vals_le(columns="probability", value=1.0)
    validation.col_vals_in_set(columns="direction", set=["up", "down"])
    for column in ("market", "mic", "symbol", "ticker", "date", "identity_schema_version"):
        validation.col_vals_not_null(columns=column)
    validation.interrogate()
    return bool(validation.all_passed())


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

            predictions_df = pl.DataFrame(prediction_rows).with_columns(pl.col("feature_schema_version").cast(pl.Int8, strict=False))
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
