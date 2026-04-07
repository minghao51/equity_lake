"""Feature engineering domain APIs."""

from __future__ import annotations

from typing import Any

from equity_lake.feature_jobs import run_feature_job

__all__ = ["FeatureEngineer", "run_feature_job"]


def __getattr__(name: str) -> Any:
    """Defer optional ML imports until the symbol is actually used."""
    if name == "FeatureEngineer":
        from equity_lake.features.engineering import FeatureEngineer

        return FeatureEngineer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
