"""Integrated validation pipeline combining schema checks, profiling, and drift detection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import polars as pl
import structlog
from pydantic import BaseModel, Field

from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.validation.profiling import DataProfiler, DriftReport  # noqa: F401 - re-exported
from equity_lake.validation.schemas import SCHEMA_REGISTRY

logger = structlog.get_logger()


class ValidationResult(BaseModel):
    """Result of a validation operation."""

    success: bool
    schema_valid: bool = True
    profile_valid: bool = True
    drift_detected: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class ValidationPipeline:
    """Orchestrates schema validation, profiling, and drift detection."""

    def __init__(self, profiler: DataProfiler | None = None, strict: bool = False) -> None:
        self.profiler = profiler or DataProfiler()
        self.strict = strict
        self._baselines: dict[str, Any] = {}

    def set_baseline(self, name: str, df: FrameLike) -> None:
        """Set a baseline profile for drift detection."""
        self._baselines[name] = self.profiler.profile(df, f"baseline_{name}")

    def validate(
        self,
        df: FrameLike,
        data_type: str = "price",
        check_drift: bool = True,
        name: str | None = None,
    ) -> ValidationResult:
        """Validate DataFrame against schema and profile."""
        df_polars = ensure_polars(df)
        errors: list[str] = []
        warnings: list[str] = []
        metrics: dict[str, Any] = {}

        # 1. Schema validation
        schema_valid = True
        schema_class = SCHEMA_REGISTRY.get(data_type)
        if schema_class:
            try:
                schema_class().validate(df_polars)
            except Exception as exc:
                schema_valid = False
                errors.append(f"Schema validation failed: {exc}")
                if self.strict:
                    return ValidationResult(success=False, schema_valid=False, errors=errors)
        else:
            warnings.append(f"Unknown data type '{data_type}', skipping schema validation")

        # 2. Profiling
        profile_valid = True
        drift_detected = False
        if name:
            try:
                profile = self.profiler.profile(df_polars, name)
                metrics["quality"] = self.profiler.get_quality_metrics(profile)

                if check_drift and name in self._baselines:
                    report: DriftReport = self.profiler.compare(profile, self._baselines[name])
                    drift_detected = report.has_drift
                    metrics["drift"] = report.model_dump()
                    if drift_detected:
                        warnings.append(f"Data drift detected in columns: {list(report.columns.keys())}")
            except Exception as exc:
                profile_valid = False
                warnings.append(f"Profiling failed: {exc}")

        # 3. Custom checks
        errors.extend(self._custom_checks(df_polars))

        return ValidationResult(
            success=len(errors) == 0,
            schema_valid=schema_valid,
            profile_valid=profile_valid,
            drift_detected=drift_detected,
            errors=errors,
            warnings=warnings,
            metrics=metrics,
        )

    def validate_and_fix(self, df: FrameLike, data_type: str = "price") -> tuple[pl.DataFrame, ValidationResult]:
        """Validate and attempt to fix common issues."""
        df_fixed = ensure_polars(df)

        key_columns = [column for column in ("ticker", "date") if column in df_fixed.columns]
        if key_columns:
            before = df_fixed.height
            df_fixed = df_fixed.unique(subset=key_columns, keep="last", maintain_order=True)
            removed = before - df_fixed.height
            if removed > 0:
                logger.warning("Removed %d duplicate rows", removed)

        if "close" in df_fixed.columns:
            df_fixed = df_fixed.filter(pl.col("close") > 0)

        if "volume" in df_fixed.columns:
            df_fixed = df_fixed.with_columns(pl.col("volume").fill_null(0))

        return df_fixed, self.validate(df_fixed, data_type)

    def _custom_checks(self, df: pl.DataFrame) -> list[str]:
        """Run additional validation checks."""
        errors: list[str] = []
        if df.is_empty():
            errors.append("DataFrame is empty")
            return errors

        row_count = df.height
        null_counts = df.null_count().row(0, named=True)

        null_cols = [column for column, count in null_counts.items() if count == row_count]
        if null_cols:
            errors.append(f"Columns with all null values: {null_cols}")

        high_null = [column for column, count in null_counts.items() if row_count > 0 and count / row_count > 0.5]
        if high_null:
            errors.append(f"Columns with >50% null values: {high_null}")

        return errors
