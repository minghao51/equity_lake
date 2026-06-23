"""Machine learning domain APIs."""

from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl
import structlog

logger = structlog.get_logger()


def validate_predictions(df: pl.DataFrame) -> bool:
    """Validate prediction output before writing to Platinum.

    Uses pointblank to enforce:
    - probability in [0.0, 1.0]
    - direction in {"up", "down"}
    - ticker and date are not null
    """
    import pointblank as pb

    validation = pb.Validate(data=df)
    validation.col_vals_gt(columns="probability", value=0.0)
    validation.col_vals_lt(columns="probability", value=1.0)
    validation.col_vals_in_set(columns="direction", set=["up", "down"])
    validation.col_vals_not_null(columns="ticker")
    validation.col_vals_not_null(columns="date")
    validation.interrogate()
    return bool(validation.all_passed())


def run_prediction_job(
    *,
    trading_date: date,
    tickers: list[str],
    model_dir: str | None = None,
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

    forecaster = PriceForecaster(model_dir=model_dir)
    ticker_results: dict[str, dict[str, Any]] = {}
    prediction_rows: list[dict[str, Any]] = []
    all_success = True

    try:
        for ticker in tickers:
            try:
                prediction = forecaster.predict(ticker=ticker, date=trading_date)
                ticker_results[ticker] = {
                    "success": True,
                    "prediction": prediction,
                }
                prediction_rows.append(
                    {
                        "ticker": ticker,
                        "date": trading_date,
                        "direction": "up" if prediction.get("prediction", 0) == 1 else "down",
                        "probability": prediction.get("probability", 0.0),
                        "model_mode": prediction.get("model_mode", "unknown"),
                        "model_version": prediction.get("model_version", "unknown"),
                        "feature_schema_version": prediction.get("feature_schema_version", 3),
                    }
                )
            except Exception as exc:  # pragma: no cover - reported by caller
                ticker_results[ticker] = {
                    "success": False,
                    "error": str(exc),
                }
                all_success = False
    finally:
        forecaster.close()

    if prediction_rows:
        try:
            from equity_lake.storage.delta import merge_delta

            predictions_df = pl.DataFrame(prediction_rows)
            if not validate_predictions(predictions_df):
                logger.error("predictions_validation_failed", rows=len(prediction_rows), date=str(trading_date))
                all_success = False
                return all_success, ticker_results
            merge_delta(
                predictions_df,
                market="04_platinum/predictions",
                key_columns=["ticker", "date"],
            )
            logger.info("predictions_persisted", rows=len(prediction_rows), date=str(trading_date))
        except Exception as exc:
            logger.warning("predictions_persist_failed", error=str(exc))

    return all_success, ticker_results


__all__ = ["PriceForecaster", "run_prediction_job", "validate_predictions"]


def __getattr__(name: str) -> Any:
    """Defer optional ML imports until the symbol is actually used."""
    if name == "PriceForecaster":
        from equity_lake.ml.forecasting import PriceForecaster

        return PriceForecaster
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
