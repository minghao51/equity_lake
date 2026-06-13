"""Statistical profiling and drift detection with pointblank-enhanced validation."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pointblank as pb
import polars as pl
import structlog
from pydantic import BaseModel, Field

from equity_lake.core.polars_utils import FrameLike, ensure_polars

logger = structlog.get_logger()


class _ColumnProfile:
    def __init__(self, summary: dict[str, Any]) -> None:
        self._summary = summary

    def to_summary_dict(self) -> dict[str, Any]:
        return self._summary


class ProfileView:
    """Polars-native statistical profile stored as JSON."""

    def __init__(self, summaries: dict[str, dict[str, Any]]) -> None:
        self._columns = {col_name: _ColumnProfile(summary) for col_name, summary in summaries.items()}

    def get_columns(self) -> dict[str, _ColumnProfile]:
        return self._columns

    def write(self, path: str) -> None:
        payload = {col_name: col_profile.to_summary_dict() for col_name, col_profile in self._columns.items()}
        Path(path).write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def read(cls, path: str) -> ProfileView:
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
    """Statistical profiling and drift detection for data quality monitoring.

    Uses pointblank for structural validation and Polars-native statistics
    for column-level profiling.
    """

    def __init__(self, storage_path: str = "data/profiles") -> None:
        self.storage_path = Path(storage_path)
        self._profiles: dict[str, ProfileView] = {}

    def profile(self, df: FrameLike, name: str, tags: dict[str, str] | None = None) -> ProfileView:
        del tags
        df_polars = ensure_polars(df)
        view = self._build_profile(df_polars)
        self._profiles[name] = view

        self.storage_path.mkdir(parents=True, exist_ok=True)
        view.write(str(self.storage_path / f"{name}.json"))
        logger.info("Created profile", name=name, rows=df_polars.height)
        return view

    def load_profile(self, name: str) -> ProfileView | None:
        path = self.storage_path / f"{name}.json"
        if not path.exists():
            return None
        return ProfileView.read(str(path))

    def validate_structure(self, df: FrameLike, name: str = "structural") -> tuple[bool, list[str]]:
        """Run pointblank structural validation checks."""
        df_polars = ensure_polars(df)
        if df_polars.is_empty():
            return True, []

        validation = pb.Validate(data=df_polars, label=name).col_exists(columns=df_polars.columns[:5]).rows_complete().interrogate()

        errors: list[str] = []
        for step in validation.validation_info:
            if not step.all_passed:
                errors.append(f"{step.autobrief} ({step.n_failed} failed)")
        return len(errors) == 0, errors

    def _build_profile(self, df: pl.DataFrame) -> ProfileView:
        summaries: dict[str, dict[str, Any]] = {}

        for col_name in df.columns:
            series = df[col_name]
            total = int(len(series))
            null_count = int(series.null_count())
            summary: dict[str, Any] = {
                "counts/n": total,
                "counts/null": null_count,
                "cardinality/est": int(series.n_unique()),
            }

            if series.dtype.is_numeric():
                numeric_non_null = series.drop_nulls()
                if len(numeric_non_null) > 0:
                    summary["distribution/mean"] = float(numeric_non_null.mean())
                    std = numeric_non_null.std()
                    summary["distribution/stddev"] = float(std) if std is not None else 0.0
                    summary["distribution/min"] = float(numeric_non_null.min())
                    summary["distribution/max"] = float(numeric_non_null.max())

            summaries[col_name] = summary

        return ProfileView(summaries)

    def get_quality_metrics(self, profile: ProfileView) -> dict[str, dict[str, Any]]:
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

    def compare(self, current: ProfileView, baseline: ProfileView, threshold: float = 0.1) -> DriftReport:
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
