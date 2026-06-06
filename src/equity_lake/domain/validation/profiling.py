"""Statistical profiling and drift detection using whylogs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


class DriftReport(BaseModel):
    """Report on data drift between two profiles."""

    has_drift: bool
    columns: dict[str, dict[str, float]] = Field(default_factory=dict)
    profile_current: str = "current"
    profile_baseline: str = "baseline"
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class DataProfiler:
    """Statistical profiling for data quality monitoring using whylogs."""

    def __init__(self, storage_path: str = "data/profiles") -> None:
        self.storage_path = Path(storage_path)
        self._profiles: dict[str, Any] = {}

    def profile(self, df: pd.DataFrame, name: str, tags: dict[str, str] | None = None) -> Any:
        """Create a statistical profile of the data."""
        import whylogs as why

        result = why.log(df)
        view = result.view()
        self._profiles[name] = view

        self.storage_path.mkdir(parents=True, exist_ok=True)
        view.write(str(self.storage_path / f"{name}.bin"))
        logger.info("Created profile", name=name, rows=len(df))
        return view

    def load_profile(self, name: str) -> Any | None:
        """Load a saved profile from disk."""
        from whylogs.core import DatasetProfileView  # noqa: F811

        path = self.storage_path / f"{name}.bin"
        if path.exists():
            return DatasetProfileView.read(str(path))
        return None

    def get_quality_metrics(self, profile: Any) -> dict[str, dict[str, Any]]:
        """Extract data quality metrics from a profile."""
        metrics: dict[str, dict[str, Any]] = {}
        cols = profile.get_columns()

        for col_name, col_profile in cols.items():
            summary = col_profile.to_summary_dict()
            total = summary.get("counts/n", 0)
            null_count = summary.get("counts/null", 0)
            completeness = (total - null_count) / total if total > 0 else 0
            unique = summary.get("cardinality/est", 0)
            uniqueness = unique / total if total > 0 else 0

            col_metrics: dict[str, Any] = {
                "completeness": completeness,
                "uniqueness": uniqueness,
                "count": total,
                "null_count": null_count,
            }

            if "distribution/mean" in summary:
                col_metrics.update(
                    {
                        "mean": summary["distribution/mean"],
                        "std": summary.get("distribution/stddev"),
                        "min": summary.get("distribution/min"),
                        "max": summary.get("distribution/max"),
                    }
                )

            metrics[col_name] = col_metrics

        return metrics

    def compare(self, current: Any, baseline: Any, threshold: float = 0.1) -> DriftReport:
        """Compare two profiles for drift detection."""
        cols_current = current.get_columns()
        cols_baseline = baseline.get_columns()

        drift_columns: dict[str, dict[str, float]] = {}
        has_drift = False

        for col_name in cols_current:
            if col_name not in cols_baseline:
                continue

            summary_c = cols_current[col_name].to_summary_dict()
            summary_b = cols_baseline[col_name].to_summary_dict()

            mean_c = summary_c.get("distribution/mean", 0)
            mean_b = summary_b.get("distribution/mean", 0)

            if mean_b and mean_c:
                pct_change = abs(mean_c - mean_b) / mean_b
                if pct_change > threshold:
                    has_drift = True
                    drift_columns[col_name] = {
                        "mean_current": float(mean_c),
                        "mean_baseline": float(mean_b),
                        "pct_change": float(pct_change),
                    }

        return DriftReport(has_drift=has_drift, columns=drift_columns)
