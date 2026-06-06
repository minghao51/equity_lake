"""Hamilton nodes for ML training and inference.

Wraps PriceForecaster in Hamilton-compatible node functions.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog

logger = structlog.get_logger()


def load_features(
    trading_date: date,
    tickers: list[str],
) -> Any:
    """Load feature data for the given trading date and tickers.

    Returns a dict mapping ticker -> feature DataFrame.
    """
    import pandas as pd

    from equity_lake.core.paths import LAKE_DIR

    features_dir = LAKE_DIR / "features" / f"date={trading_date}"
    feature_file = features_dir / f"{trading_date}.parquet"

    if not feature_file.exists():
        logger.warning("features_not_found", path=str(feature_file))
        return {}

    df = pd.read_parquet(feature_file)
    return {ticker: df[df["ticker"] == ticker] for ticker in tickers if ticker in df["ticker"].values}


def run_inference(
    trading_date: date,
    tickers: list[str],
    model_dir: str | None = None,
    model_mode: str = "v1_direction",
    ml_config: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run price-forecast inference for each ticker."""
    from equity_lake.ml.forecasting import PriceForecaster

    forecaster = PriceForecaster(model_dir=model_dir, model_mode=model_mode, ml_config=ml_config)
    ticker_results: dict[str, dict[str, Any]] = {}

    try:
        for ticker in tickers:
            try:
                prediction = forecaster.predict(ticker=ticker, date=trading_date)
                ticker_results[ticker] = {"success": True, "prediction": prediction}
            except Exception as exc:
                error = str(exc)
                if error.startswith("No trained model found for"):
                    logger.warning("ml_inference_skipped_no_model", ticker=ticker, trading_date=str(trading_date))
                    ticker_results[ticker] = {"success": True, "skipped": True, "reason": error}
                else:
                    ticker_results[ticker] = {"success": False, "error": error}
    finally:
        forecaster.close()

    return ticker_results


def write_predictions(
    run_inference: dict[str, dict[str, Any]],
    trading_date: date,
) -> dict[str, dict[str, Any]]:
    """Persist prediction results (placeholder for future persistence)."""
    logger.info("predictions_written", date=str(trading_date), tickers=len(run_inference))
    return run_inference


def run_ml_inference(
    trading_date: date,
    tickers: list[str],
    model_dir: str | None = None,
    model_mode: str = "v1_direction",
    ml_config: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Run ML inference pipeline (replaces run_prediction_job)."""
    results = run_inference(
        trading_date=trading_date,
        tickers=tickers,
        model_dir=model_dir,
        model_mode=model_mode,
        ml_config=ml_config,
    )
    all_success = all(r.get("success", False) for r in results.values())
    return all_success, results
