"""Tests for Silver layer: transforms and boundary validation."""

from __future__ import annotations

from datetime import date

import polars as pl

from equity_lake.features.dag.clean_02 import returns, validated_ohlcv


def test_returns_basic() -> None:
    close = pl.Series("close", [100.0, 110.0, 121.0])
    result = returns(close)
    assert result[0] is None
    assert abs(result[1] - 0.1) < 1e-9
    assert abs(result[2] - 0.1) < 1e-9


def test_validated_ohlcv_filters_invalid() -> None:
    ticker = pl.Series("ticker", ["AAPL", "AAPL", "BAD"])
    dt = pl.Series("date", [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 2)]).cast(pl.Datetime)
    open_s = pl.Series("open", [150.0, 150.0, 0.0])
    high = pl.Series("high", [155.0, 155.0, 0.0])
    low = pl.Series("low", [148.0, 148.0, 0.0])
    close = pl.Series("close", [152.0, 152.0, -1.0])
    volume = pl.Series("volume", [1e6, 1e6, 1e6])

    result = validated_ohlcv(ticker, dt, open_s, high, low, close, volume)

    assert result.height == 1
    assert result["ticker"].to_list() == ["AAPL"]


def test_validated_ohlcv_dedupes() -> None:
    ticker = pl.Series("ticker", ["AAPL", "AAPL"])
    dt = pl.Series("date", [date(2024, 1, 1), date(2024, 1, 1)]).cast(pl.Datetime)
    open_s = pl.Series("open", [150.0, 151.0])
    high = pl.Series("high", [155.0, 156.0])
    low = pl.Series("low", [148.0, 149.0])
    close = pl.Series("close", [152.0, 153.0])
    volume = pl.Series("volume", [1e6, 2e6])

    result = validated_ohlcv(ticker, dt, open_s, high, low, close, volume)
    assert result.height == 1
