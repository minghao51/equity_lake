"""ML inference helpers for library and CLI use."""

from __future__ import annotations

from datetime import date
from typing import Any


def run_prediction_job(
    *,
    trading_date: date,
    tickers: list[str],
    model_dir: str | None = None,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    """Run price-forecast inference for each ticker."""
    try:
        from equity_lake.ml.forecasting import PriceForecaster
    except ImportError as exc:
        raise RuntimeError(
            "ML inference requires the optional 'ml' dependencies.",
        ) from exc

    forecaster = PriceForecaster(model_dir=model_dir)
    ticker_results: dict[str, dict[str, Any]] = {}
    all_success = True

    try:
        for ticker in tickers:
            try:
                prediction = forecaster.predict(ticker=ticker, date=trading_date)
                ticker_results[ticker] = {
                    "success": True,
                    "prediction": prediction,
                }
            except Exception as exc:  # pragma: no cover - reported by caller
                ticker_results[ticker] = {
                    "success": False,
                    "error": str(exc),
                }
                all_success = False
    finally:
        forecaster.close()

    return all_success, ticker_results
