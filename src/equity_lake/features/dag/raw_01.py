"""Bronze layer: raw OHLCV column extraction from price_data input.

These functions are the entry point of the DAG — they extract individual
Polars Series from the root ``price_data`` DataFrame provided at execution
time via ``dr.execute(inputs={"price_data": df})``.
"""

from __future__ import annotations

import polars as pl
from hamilton.function_modifiers import check_output, tag

from equity_lake.features.dag.polars_validators import PolarsDataTypeValidator


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


@tag(layer="bronze", category="raw_column", produces="close", validators="check_output(data_type=float)")  # type: ignore[untyped-decorator]
@check_output(  # type: ignore[untyped-decorator]
    data_type=float,
    importance="warn",
    default_validator_candidates=[PolarsDataTypeValidator],
)
def close(price_data: pl.DataFrame) -> pl.Series:
    return price_data["close"].cast(pl.Float64)


@tag(layer="bronze", category="raw_column", produces="volume", validators="check_output(data_type=float)")  # type: ignore[untyped-decorator]
@check_output(  # type: ignore[untyped-decorator]
    data_type=float,
    importance="warn",
    default_validator_candidates=[PolarsDataTypeValidator],
)
def volume(price_data: pl.DataFrame) -> pl.Series:
    return price_data["volume"].cast(pl.Float64)
