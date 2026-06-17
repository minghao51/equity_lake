"""Custom Hamilton data quality validators for Polars Series.

Hamilton's built-in ``@check_output`` validators target primitive types and
pandas Series. These validators extend support to ``polars.Series`` so we can
enforce dtype and range constraints at DAG node boundaries.
"""

from __future__ import annotations

import numbers
from typing import Any

import polars as pl
from hamilton.data_quality import base as dq_base


class PolarsDataTypeValidator(dq_base.BaseDefaultValidator):
    """Validates that a ``pl.Series`` has the expected Polars dtype."""

    def __init__(self, data_type: type, importance: str) -> None:
        super().__init__(importance=importance)
        self._python_type = data_type
        self._polars_types = _python_to_polars_dtype(data_type)

    @classmethod
    def applies_to(cls, datatype: type[Any]) -> bool:
        try:
            return issubclass(datatype, pl.Series)
        except TypeError:
            return False

    @classmethod
    def arg(cls) -> str:
        return "data_type"

    def description(self) -> str:
        return f"Validates that the Polars Series dtype is one of {self._polars_types}"

    def validate(self, data: pl.Series) -> dq_base.ValidationResult:
        passes = data.dtype in self._polars_types
        return dq_base.ValidationResult(
            passes=passes,
            message=f"Series dtype {data.dtype} {'matches' if passes else 'does not match'} expected {self._polars_types}",
            diagnostics={"expected": [str(t) for t in self._polars_types], "actual": str(data.dtype)},
        )


class PolarsRangeValidator(dq_base.BaseDefaultValidator):
    """Validates that all non-null values in a ``pl.Series`` fall within ``range``."""

    def __init__(self, range: tuple[numbers.Real, numbers.Real], importance: str) -> None:
        super().__init__(importance=importance)
        self.range = range

    @classmethod
    def applies_to(cls, datatype: type[Any]) -> bool:
        try:
            return issubclass(datatype, pl.Series)
        except TypeError:
            return False

    @classmethod
    def arg(cls) -> str:
        return "range"

    def description(self) -> str:
        return f"Validates that all non-null Series values fall within ({self.range[0]}, {self.range[1]})"

    def validate(self, data: pl.Series) -> dq_base.ValidationResult:
        min_val, max_val = self.range
        non_null = data.drop_nulls()
        if non_null.is_empty():
            return dq_base.ValidationResult(
                passes=True,
                message="Series is empty or all-null, range check skipped",
                diagnostics={"range": self.range},
            )
        out_of_range = non_null.filter((non_null < min_val) | (non_null > max_val))
        passes = out_of_range.is_empty()
        return dq_base.ValidationResult(
            passes=passes,
            message=f"{out_of_range.len()} values outside range ({min_val}, {max_val})" if not passes else "All values within range",
            diagnostics={
                "range": self.range,
                "out_of_range_count": out_of_range.len(),
                "actual_min": non_null.min(),
                "actual_max": non_null.max(),
            },
        )


_POLARS_DTYPE_MAP: dict[type, list[type[pl.DataType]]] = {
    float: [pl.Float64, pl.Float32],
    int: [pl.Int64, pl.Int32, pl.Int16, pl.Int8, pl.UInt64, pl.UInt32, pl.UInt16, pl.UInt8],
    str: [pl.Utf8, pl.String],
    bool: [pl.Boolean],
}


def _python_to_polars_dtype(python_type: type) -> list[type[pl.DataType]]:
    for key, dtypes in _POLARS_DTYPE_MAP.items():
        try:
            if issubclass(python_type, key):
                return dtypes
        except TypeError:
            pass
    return [pl.Float64]
