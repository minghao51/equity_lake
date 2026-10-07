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


class MalformedDateKeyError(ValueError):
    """Raised when a date key cannot be normalized to ``pl.Date``.

    Malformed keys fail loudly instead of being silently converted to nulls
    that would later be dropped at the Silver boundary (W01/D01).
    """


def normalize_eod_date_series(column: pl.Series) -> pl.Series:
    """Normalize an EOD date key series to ``pl.Date`` (D01).

    ``pl.Date`` passes through; ``pl.Datetime`` truncates to its calendar date
    (EOD keys carry no intraday time — true event timestamps stay in their own
    columns); strings parse leniently (date or datetime layouts) and raise
    :class:`MalformedDateKeyError` when a non-null value is unparseable.
    """
    if column.dtype == pl.Date:
        return column
    if column.dtype == pl.Datetime:
        return column.dt.date()
    if column.dtype in (pl.Utf8, pl.String):
        parsed = column.str.to_datetime(strict=False)
        malformed = parsed.is_null() & column.is_not_null()
        if malformed.any():
            examples = column.filter(malformed).head(3).to_list()
            raise MalformedDateKeyError(f"unparseable EOD date key(s): {examples}")
        return parsed.dt.date()
    try:
        return column.cast(pl.Date, strict=True)
    except (pl.exceptions.ComputeError, pl.exceptions.InvalidOperationError) as exc:
        raise MalformedDateKeyError(f"cannot normalize {column.dtype} to pl.Date: {exc}") from exc


@tag(layer="bronze", category="raw_column", produces="date")  # type: ignore[untyped-decorator]
def date(price_data: pl.DataFrame) -> pl.Series:
    """EOD join key, normalized to ``pl.Date`` (D01 contract)."""
    return normalize_eod_date_series(price_data["date"])


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
