"""Tests for Silver layer: transforms and boundary validation."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from equity_lake.features.dag.clean_02 import (
    OHLCVValidationError,
    returns,
    validated_ohlcv,
)


def _series_frame(**overrides) -> dict[str, pl.Series]:
    base = {
        "ticker": pl.Series("ticker", ["AAPL", "AAPL"]),
        "date": pl.Series("date", [date(2024, 1, 1), date(2024, 1, 2)]),
        "open": pl.Series("open", [150.0, 151.0]),
        "high": pl.Series("high", [155.0, 156.0]),
        "low": pl.Series("low", [148.0, 149.0]),
        "close": pl.Series("close", [152.0, 153.0]),
        "volume": pl.Series("volume", [1e6, 2e6]),
    }
    base.update(overrides)
    return base


def _validated(**overrides) -> pl.DataFrame:
    parts = _series_frame(**overrides)
    return validated_ohlcv(parts["ticker"], parts["date"], parts["open"], parts["high"], parts["low"], parts["close"], parts["volume"])


def test_returns_basic() -> None:
    frame = pl.DataFrame({"close": [100.0, 110.0, 121.0]})
    result = returns(frame)
    assert result[0] is None
    assert abs(result[1] - 0.1) < 1e-9
    assert abs(result[2] - 0.1) < 1e-9


def test_validated_ohlcv_passes_valid_rows() -> None:
    result = _validated()
    assert result.height == 2
    assert result.columns == ["ticker", "date", "open", "high", "low", "close", "volume"]


class TestInvalidInputFailsLoudly:
    """D01/F02: invalid required price input fails; no silent row deletion."""

    def test_zero_open_rejected(self) -> None:
        parts = _series_frame(open=pl.Series("open", [0.0, 151.0]))
        with pytest.raises(OHLCVValidationError, match="strictly positive"):
            _validated(**{"open": parts["open"]})

    def test_negative_close_rejected_beyond_sampled_window(self) -> None:
        # Invalid row past any sampling window cannot hide (F02).
        n = 150
        parts = _series_frame(
            ticker=pl.Series("ticker", ["AAPL"] * n),
            date=pl.Series("date", [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]),
            open=pl.Series("open", [150.0] * n),
            high=pl.Series("high", [155.0] * n),
            low=pl.Series("low", [148.0] * n),
            close=pl.Series("close", [152.0] * n),
            volume=pl.Series("volume", [1e6] * n),
        )
        close = parts["close"].clone()
        close[120] = -1.0
        with pytest.raises(OHLCVValidationError, match="strictly positive"):
            validated_ohlcv(parts["ticker"], parts["date"], parts["open"], parts["high"], parts["low"], close, parts["volume"])

    def test_null_ticker_rejected(self) -> None:
        parts = _series_frame(ticker=pl.Series("ticker", [None, "AAPL"]))
        with pytest.raises(OHLCVValidationError, match="null ticker"):
            _validated(**{"ticker": parts["ticker"]})

    def test_null_date_rejected(self) -> None:
        parts = _series_frame(date=pl.Series("date", [None, date(2024, 1, 2)]))
        with pytest.raises(OHLCVValidationError, match="null date"):
            _validated(**{"date": parts["date"]})

    def test_negative_volume_rejected(self) -> None:
        parts = _series_frame(volume=pl.Series("volume", [1e6, -5.0]))
        with pytest.raises(OHLCVValidationError, match="non-negative"):
            _validated(**{"volume": parts["volume"]})

    def test_ohlcv_relationship_violation_rejected(self) -> None:
        # high < close violates the price-relationship contract.
        parts = _series_frame(high=pl.Series("high", [155.0, 100.0]))
        with pytest.raises(OHLCVValidationError, match="relationship"):
            _validated(**{"high": parts["high"]})

    def test_null_close_rejected(self) -> None:
        parts = _series_frame(close=pl.Series("close", [None, 153.0]))
        with pytest.raises(OHLCVValidationError, match="null close"):
            _validated(**{"close": parts["close"]})

    def test_nan_close_rejected_at_silver_boundary(self) -> None:
        """NaN is not null in Polars — it must fail at the Silver boundary with
        the raw column named, not later at the Gold boundary."""
        parts = _series_frame(close=pl.Series("close", [float("nan"), 153.0]))
        with pytest.raises(OHLCVValidationError, match="NaN close"):
            _validated(**{"close": parts["close"]})


def test_validated_ohlcv_zero_volume_is_valid() -> None:
    # volume >= 0: a zero-volume session is legitimate input.
    result = _validated(**{"volume": pl.Series("volume", [0.0, 2e6])})
    assert result.height == 2


def test_validated_ohlcv_dedupes_keep_first_deterministic() -> None:
    parts = _series_frame(
        volume=pl.Series("volume", [1e6, 2e6]),
        close=pl.Series("close", [152.0, 153.0]),
    )
    parts["date"] = pl.Series("date", [date(2024, 1, 1), date(2024, 1, 1)])
    result = validated_ohlcv(parts["ticker"], parts["date"], parts["open"], parts["high"], parts["low"], parts["close"], parts["volume"])
    assert result.height == 1
    # Documented policy: first input occurrence per (ticker, date) wins.
    assert result["volume"][0] == 1e6


def test_validated_ohlcv_sorts_unsorted_input() -> None:
    """Unsorted input is ordered deterministically before rolling features."""
    parts = _series_frame(
        date=pl.Series("date", [date(2024, 1, 2), date(2024, 1, 1)]),
        close=pl.Series("close", [153.0, 152.0]),
    )
    result = _validated(**{"date": parts["date"], "close": parts["close"]})
    assert result["date"].to_list() == sorted(result["date"].to_list())


def test_validated_ohlcv_empty_frame_passthrough() -> None:
    empty = pl.DataFrame(
        schema={
            "ticker": pl.String,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        }
    )
    result = validated_ohlcv(empty["ticker"], empty["date"], empty["open"], empty["high"], empty["low"], empty["close"], empty["volume"])
    assert result.is_empty()
