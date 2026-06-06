"""Integrated validation pipeline combining schema checks, profiling, and drift detection."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
import structlog
from pydantic import BaseModel, Field

from equity_lake.domain.validation.profiling import DataProfiler, DriftReport  # noqa: F401 - re-exported
from equity_lake.domain.validation.schemas import SCHEMA_REGISTRY

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

    def set_baseline(self, name: str, df: pd.DataFrame) -> None:
        """Set a baseline profile for drift detection."""
        self._baselines[name] = self.profiler.profile(df, f"baseline_{name}")

    def validate(
        self,
        df: pd.DataFrame,
        data_type: str = "price",
        check_drift: bool = True,
        name: str | None = None,
    ) -> ValidationResult:
        """Validate DataFrame against schema and profile."""
        errors: list[str] = []
        warnings: list[str] = []
        metrics: dict[str, Any] = {}

        # 1. Schema validation
        schema_valid = True
        schema_class = SCHEMA_REGISTRY.get(data_type)
        if schema_class:
            try:
                schema_class.validate(df)
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
                profile = self.profiler.profile(df, name)
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
        errors.extend(self._custom_checks(df))

        return ValidationResult(
            success=len(errors) == 0,
            schema_valid=schema_valid,
            profile_valid=profile_valid,
            drift_detected=drift_detected,
            errors=errors,
            warnings=warnings,
            metrics=metrics,
        )

    def validate_and_fix(self, df: pd.DataFrame, data_type: str = "price") -> tuple[pd.DataFrame, ValidationResult]:
        """Validate and attempt to fix common issues."""
        df_fixed = df.copy()

        before = len(df_fixed)
        df_fixed = df_fixed.drop_duplicates(subset=["ticker", "date"], keep="last")
        if len(df_fixed) < before:
            logger.warning("Removed %d duplicate rows", before - len(df_fixed))

        if "close" in df_fixed.columns:
            df_fixed = df_fixed[df_fixed["close"] > 0]

        if "volume" in df_fixed.columns:
            df_fixed["volume"] = df_fixed["volume"].fillna(0)

        result = self.validate(df_fixed, data_type)
        return df_fixed, result

    def _custom_checks(self, df: pd.DataFrame) -> list[str]:
        """Run additional validation checks."""
        errors: list[str] = []
        if df.empty:
            errors.append("DataFrame is empty")
            return errors

        null_cols = df.columns[df.isnull().all()].tolist()
        if null_cols:
            errors.append(f"Columns with all null values: {null_cols}")

        high_null = df.columns[df.isnull().mean() > 0.5].tolist()
        if high_null:
            errors.append(f"Columns with >50% null values: {high_null}")

        return errors
