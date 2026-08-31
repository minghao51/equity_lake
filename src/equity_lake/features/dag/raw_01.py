"""Bronze layer: raw OHLCV column extraction from price_data input.

These functions are the entry point of the DAG — they extract individual
Polars Series from the root ``price_data`` DataFrame provided at execution
time via ``dr.execute(inputs={"price_data": df})``.
"""

from __future__ import annotations

from typing import Any, Final

import polars as pl
from hamilton.function_modifiers import check_output, tag

from equity_lake.features.dag.polars_validators import PolarsDataTypeValidator

#: Single source of truth for the float-dtype output check on ``close`` /
#: ``volume`` (handoff 08 A8 — the decorator kwargs and the catalog-facing
#: ``@tag(validators=...)`` string were previously duplicated by hand and could
#: drift). ``@check_output`` consumes the kwargs at runtime; the tag string is
#: derived from the same spec so the catalog stays byte-identical.
_FLOAT_CHECK_OUTPUT_KWARGS: Final[dict[str, Any]] = {
    "data_type": float,
    "importance": "warn",
    "default_validator_candidates": [PolarsDataTypeValidator],
}
_FLOAT_CHECK_OUTPUT_TAG: Final[str] = f"check_output(data_type={_FLOAT_CHECK_OUTPUT_KWARGS['data_type'].__name__})"


@tag(layer="bronze", category="raw_column", produces="ticker")  # type: ignore[untyped-decorator]
def ticker(price_data: pl.DataFrame) -> pl.Series:
    return price_data["ticker"]


@tag(layer="bronze", category="raw_column", produces="date")  # type: ignore[untyped-decorator]
def date(price_data: pl.DataFrame) -> pl.Series:
    date_column = price_data["date"]
    if date_column.dtype == pl.Utf8:
        return date_column.str.to_datetime(strict=False)
    if date_column.dtype == pl.Date:
        return date_column.cast(pl.Datetime)
    return date_column


@tag(layer="bronze", category="raw_column", produces="open_price")  # type: ignore[untyped-decorator]
def open_price(price_data: pl.DataFrame) -> pl.Series:
    return price_data["open"]


@tag(layer="bronze", category="raw_column", produces="high")  # type: ignore[untyped-decorator]
def high(price_data: pl.DataFrame) -> pl.Series:
    return price_data["high"]


@tag(layer="bronze", category="raw_column", produces="low")  # type: ignore[untyped-decorator]
def low(price_data: pl.DataFrame) -> pl.Series:
    return price_data["low"]


@tag(layer="bronze", category="raw_column", produces="close", validators=_FLOAT_CHECK_OUTPUT_TAG)  # type: ignore[untyped-decorator]
@check_output(**_FLOAT_CHECK_OUTPUT_KWARGS)  # type: ignore[untyped-decorator]
def close(price_data: pl.DataFrame) -> pl.Series:
    return price_data["close"].cast(pl.Float64)


@tag(layer="bronze", category="raw_column", produces="volume", validators=_FLOAT_CHECK_OUTPUT_TAG)  # type: ignore[untyped-decorator]
@check_output(**_FLOAT_CHECK_OUTPUT_KWARGS)  # type: ignore[untyped-decorator]
def volume(price_data: pl.DataFrame) -> pl.Series:
    return price_data["volume"].cast(pl.Float64)
