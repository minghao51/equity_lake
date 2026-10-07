"""Unit tests for div-by-zero guards in feature and backtesting modules.

Since W01 (F02) the indicator nodes consume the ``validated_ohlcv`` frame, so
these tests build minimal validated frames instead of bare series.
"""

from __future__ import annotations

import polars as pl
import pytest

from equity_lake.features.dag.features_03 import bb_pct, volume_ratio


def _frame(**columns: pl.Series) -> pl.DataFrame:
    return pl.DataFrame(columns)


class TestBbPctDivByZero:
    """bb_pct should produce null instead of inf when band width is zero."""

    def test_normal_case(self):
        frame = _frame(
            close=pl.Series([100.0, 105.0]),
            bb_upper=pl.Series([110.0, 115.0]),
            bb_lower=pl.Series([90.0, 95.0]),
        )
        result = bb_pct(frame, frame["bb_upper"], frame["bb_lower"])
        assert result.null_count() == 0
        assert result[0] == pytest.approx(0.5)

    def test_zero_band_width(self):
        """When bb_upper == bb_lower, result should be null not inf."""
        frame = _frame(
            close=pl.Series([100.0]),
            bb_upper=pl.Series([100.0]),
            bb_lower=pl.Series([100.0]),
        )
        result = bb_pct(frame, frame["bb_upper"], frame["bb_lower"])
        assert result.null_count() == 1


class TestVolumeRatioDivByZero:
    """volume_ratio should produce null instead of inf when MA is zero."""

    def test_normal_case(self):
        frame = _frame(
            volume=pl.Series([1000.0, 2000.0]),
        )
        result = volume_ratio(frame, pl.Series([1500.0, 1500.0]))
        assert result.null_count() == 0

    def test_zero_ma(self):
        """When volume_ma_20 is 0, result should be null not inf."""
        frame = _frame(volume=pl.Series([1000.0]))
        result = volume_ratio(frame, pl.Series([0.0]))
        assert result.null_count() == 1
