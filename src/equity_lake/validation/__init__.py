"""Data quality framework: schema validation, profiling, and drift detection."""

from equity_lake.validation.pipeline import ValidationPipeline, ValidationResult
from equity_lake.validation.profiling import DataProfiler, DriftReport
from equity_lake.validation.schemas import (
    SCHEMA_REGISTRY,
    MacroDataSchema,
    NewsDataSchema,
    PriceDataSchema,
)

__all__ = [
    "DataProfiler",
    "DriftReport",
    "MacroDataSchema",
    "NewsDataSchema",
    "PriceDataSchema",
    "SCHEMA_REGISTRY",
    "ValidationPipeline",
    "ValidationResult",
]
