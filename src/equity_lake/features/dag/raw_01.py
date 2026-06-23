"""Bronze layer: raw OHLCV column extraction from price_data input.

These functions are the entry point of the DAG — they extract individual
Polars Series from the root ``price_data`` DataFrame provided at execution
time via ``dr.execute(inputs={"price_data": df})``.
"""

from __future__ import annotations

import polars as pl
from hamilton.function_modifiers import check_output

from equity_lake.features.dag.polars_validators import PolarsDataTypeValidator


def ticker(price_data: pl.DataFrame) -> pl.Series:
    return price_data["ticker"]


def date(price_data: pl.DataFrame) -> pl.Series:
    date_column = price_data["date"]
    if date_column.dtype == pl.Utf8:
        return date_column.str.to_datetime(strict=False)
    if date_column.dtype == pl.Date:
        return date_column.cast(pl.Datetime)
    return date_column


def open_price(price_data: pl.DataFrame) -> pl.Series:
    return price_data["open"]


def high(price_data: pl.DataFrame) -> pl.Series:
    return price_data["high"]


def low(price_data: pl.DataFrame) -> pl.Series:
    return price_data["low"]


@check_output(  # type: ignore[untyped-decorator]
    data_type=float,
    importance="warn",
    default_validator_candidates=[PolarsDataTypeValidator],
)
def close(price_data: pl.DataFrame) -> pl.Series:
    return price_data["close"].cast(pl.Float64)


@check_output(  # type: ignore[untyped-decorator]
    data_type=float,
    importance="warn",
    default_validator_candidates=[PolarsDataTypeValidator],
)
def volume(price_data: pl.DataFrame) -> pl.Series:
    return price_data["volume"].cast(pl.Float64)
