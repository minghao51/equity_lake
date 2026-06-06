"""Statistical profiling and drift detection with an optional whylogs backend."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger()


class _SimpleColumnProfile:
    def __init__(self, summary: dict[str, Any]) -> None:
        self._summary = summary

    def to_summary_dict(self) -> dict[str, Any]:
        return self._summary


class _SimpleProfileView:
    def __init__(self, summaries: dict[str, dict[str, Any]]) -> None:
        self._columns = {col_name: _SimpleColumnProfile(summary) for col_name, summary in summaries.items()}

    def get_columns(self) -> dict[str, _SimpleColumnProfile]:
        return self._columns

    def write(self, path: str) -> None:
        payload = {col_name: col_profile.to_summary_dict() for col_name, col_profile in self._columns.items()}
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def read(cls, path: str) -> _SimpleProfileView:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload)


class DriftReport(BaseModel):
    """Report on data drift between two profiles."""

    has_drift: bool
    columns: dict[str, dict[str, float]] = Field(default_factory=dict)
    profile_current: str = "current"
    profile_baseline: str = "baseline"
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class DataProfiler:
    """Statistical profiling for data quality monitoring."""

    def __init__(self, storage_path: str = "data/profiles") -> None:
        self.storage_path = Path(storage_path)
        self._profiles: dict[str, Any] = {}

    def profile(self, df: pd.DataFrame, name: str, tags: dict[str, str] | None = None) -> Any:
        """Create a statistical profile of the data."""
        try:
            import whylogs as why
        except ImportError:
            view = self._build_simple_profile(df)
        else:
            del tags  # whylogs integration currently ignores tags.
            result = why.log(df)
            view = result.view()

        self._profiles[name] = view

        self.storage_path.mkdir(parents=True, exist_ok=True)
        view.write(str(self.storage_path / f"{name}.bin"))
        logger.info("Created profile", name=name, rows=len(df))
        return view

    def load_profile(self, name: str) -> Any | None:
        """Load a saved profile from disk."""
        path = self.storage_path / f"{name}.bin"
        if not path.exists():
            return None

        try:
            from whylogs.core import DatasetProfileView
        except ImportError:
            return _SimpleProfileView.read(str(path))
        else:
            return DatasetProfileView.read(str(path))

    def _build_simple_profile(self, df: pd.DataFrame) -> _SimpleProfileView:
        summaries: dict[str, dict[str, Any]] = {}

        for col_name in df.columns:
            series = df[col_name]
            total = int(len(series))
            null_count = int(series.isna().sum())
            summary: dict[str, Any] = {
                "counts/n": total,
                "counts/null": null_count,
                "cardinality/est": int(series.nunique(dropna=True)),
            }

            numeric = pd.to_numeric(series, errors="coerce")
            numeric_non_null = numeric.dropna()
            if not numeric_non_null.empty:
                summary["distribution/mean"] = float(numeric_non_null.mean())
                std = numeric_non_null.std()
                summary["distribution/stddev"] = float(std) if pd.notna(std) else 0.0
                summary["distribution/min"] = float(numeric_non_null.min())
                summary["distribution/max"] = float(numeric_non_null.max())

            summaries[col_name] = summary

        return _SimpleProfileView(summaries)

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
