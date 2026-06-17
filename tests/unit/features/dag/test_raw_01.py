"""Tests for Bronze layer: raw OHLCV extraction."""

from __future__ import annotations

from datetime import date

import polars as pl

from equity_lake.features.dag.raw_01 import close, high, low, open_price, ticker, volume
from equity_lake.features.dag.raw_01 import date as date_fn


def _sample_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 1, 1), date(2024, 1, 2)],
            "open": [150.0, 380.0],
            "high": [155.0, 385.0],
            "low": [148.0, 378.0],
            "close": [152.0, 382.0],
            "volume": [1_000_000, 800_000],
        }
    )


def test_ticker_extraction() -> None:
    result = ticker(_sample_df())
    assert result.to_list() == ["AAPL", "MSFT"]


def test_date_extraction_from_date_dtype() -> None:
    result = date_fn(_sample_df())
    assert result.dtype == pl.Datetime


def test_date_extraction_from_string() -> None:
    df = pl.DataFrame({"date": ["2024-01-01", "2024-01-02"]})
    result = date_fn(df)
    assert result.dtype == pl.Datetime


def test_close_cast_to_float64() -> None:
    df = pl.DataFrame({"close": [152, 382]}, schema={"close": pl.Int64})
    result = close(df)
    assert result.dtype == pl.Float64


def test_volume_cast_to_float64() -> None:
    df = pl.DataFrame({"volume": [1_000_000, 800_000]}, schema={"volume": pl.Int64})
    result = volume(df)
    assert result.dtype == pl.Float64


def test_ohlcv_extraction() -> None:
    df = _sample_df()
    assert open_price(df).to_list() == [150.0, 380.0]
    assert high(df).to_list() == [155.0, 385.0]
    assert low(df).to_list() == [148.0, 378.0]
