"""Machine learning domain APIs."""

from __future__ import annotations

from equity_lake.ml_jobs import run_prediction_job

__all__ = ["PriceForecaster", "run_prediction_job"]


def __getattr__(name: str):
    """Defer optional ML imports until the symbol is actually used."""
    if name == "PriceForecaster":
        from equity_lake.ml.forecasting import PriceForecaster

        return PriceForecaster
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
