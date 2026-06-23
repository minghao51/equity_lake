"""Unit tests for div-by-zero guards in feature and backtesting modules."""

import polars as pl
import pytest

from equity_lake.features.dag.features_03 import bb_pct, volume_ratio


class TestBbPctDivByZero:
    """bb_pct should produce null instead of inf when band width is zero."""

    def test_normal_case(self):
        close = pl.Series([100.0, 105.0])
        bb_upper = pl.Series([110.0, 115.0])
        bb_lower = pl.Series([90.0, 95.0])
        result = bb_pct(close, bb_upper, bb_lower)
        assert result.null_count() == 0
        assert result[0] == pytest.approx(0.5)

    def test_zero_band_width(self):
        """When bb_upper == bb_lower, result should be null not inf."""
        close = pl.Series([100.0])
        bb_upper = pl.Series([100.0])
        bb_lower = pl.Series([100.0])
        result = bb_pct(close, bb_upper, bb_lower)
        assert result.null_count() == 1


class TestVolumeRatioDivByZero:
    """volume_ratio should produce null instead of inf when MA is zero."""

    def test_normal_case(self):
        volume = pl.Series([1000.0, 2000.0])
        volume_ma_20 = pl.Series([1500.0, 1500.0])
        result = volume_ratio(volume, volume_ma_20)
        assert result.null_count() == 0

    def test_zero_ma(self):
        """When volume_ma_20 is 0, result should be null not inf."""
        volume = pl.Series([1000.0])
        volume_ma_20 = pl.Series([0.0])
        result = volume_ratio(volume, volume_ma_20)
        assert result.null_count() == 1
