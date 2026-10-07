"""Tests for Gold layer: technical indicator computation.

Since W01 (F02/D01) all indicator nodes consume the ``validated_ohlcv`` frame,
so these tests pass validated-shaped frames directly.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from equity_lake.features.dag.clean_02 import OHLCVValidationError, validated_ohlcv
from equity_lake.features.dag.clean_02 import returns as returns_node
from equity_lake.features.dag.features_03 import (
    FeatureOutputValidationError,
    atr_14,
    bb_pct,
    bb_width,
    day_of_month,
    day_of_week,
    days_to_month_end,
    hl_range,
    intraday_return,
    macd,
    macd_frame,
    macd_histogram,
    macd_signal,
    month,
    next_day_return,
    obv,
    overnight_return,
    quarter,
    rsi_14,
    trading_day_of_month,
    validate_emitted_frame,
    volatility_20,
    volume_ma_20,
    volume_ratio,
    volume_roc_5,
)


def _ohlcv(n: int = 80, base: float = 100.0, volume: list[float] | None = None) -> pl.DataFrame:
    rng = np.random.default_rng(42)
    close = pl.Series("close", base + rng.standard_normal(n).cumsum() + 10.0)
    parts = {
        "ticker": pl.Series("ticker", ["AAPL"] * n),
        "date": pl.Series("date", [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]),
        "open": close * 0.999,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": pl.Series("volume", volume if volume is not None else [1e6] * n, dtype=pl.Float64),
    }
    return validated_ohlcv(parts["ticker"], parts["date"], parts["open"], parts["high"], parts["low"], parts["close"], parts["volume"])


def test_rsi_14_returns_series() -> None:
    result = rsi_14(_ohlcv())
    assert isinstance(result, pl.Series)
    valid = result.drop_nulls()
    assert valid.min() >= 0.0
    assert valid.max() <= 100.0


def test_macd_frame_and_components() -> None:
    frame = macd_frame(_ohlcv())
    assert isinstance(frame, pl.DataFrame)
    assert set(frame.columns) == {"macd", "signal", "histogram"}

    assert isinstance(macd(frame), pl.Series)
    assert isinstance(macd_signal(frame), pl.Series)
    assert isinstance(macd_histogram(frame), pl.Series)


def test_bollinger_bands() -> None:
    from equity_lake.features.dag.features_03 import bb_lower, bb_middle, bb_upper, bollinger_frame

    ohlcv = _ohlcv()
    bf = bollinger_frame(ohlcv)
    assert isinstance(bb_upper(bf), pl.Series)
    assert isinstance(bb_middle(bf), pl.Series)
    assert isinstance(bb_lower(bf), pl.Series)
    assert isinstance(bb_width(bb_upper(bf), bb_lower(bf), bb_middle(bf)), pl.Series)
    assert isinstance(bb_pct(ohlcv, bb_upper(bf), bb_lower(bf)), pl.Series)


def test_atr_14() -> None:
    result = atr_14(_ohlcv())
    assert isinstance(result, pl.Series)
    assert result.drop_nulls().min() > 0


def test_overnight_and_intraday_return() -> None:
    ohlcv = _ohlcv(n=5)
    overnight = overnight_return(ohlcv)
    assert isinstance(overnight, pl.Series)

    intraday = intraday_return(ohlcv)
    assert isinstance(intraday, pl.Series)


def test_hl_range() -> None:
    result = hl_range(_ohlcv(n=5))
    assert isinstance(result, pl.Series)
    assert result.drop_nulls().min() > 0


def test_volume_indicators() -> None:
    ohlcv = _ohlcv()
    assert isinstance(volume_ma_20(ohlcv), pl.Series)
    assert isinstance(volume_roc_5(ohlcv), pl.Series)
    assert isinstance(volume_ratio(ohlcv, volume_ma_20(ohlcv)), pl.Series)


def test_obv() -> None:
    assert isinstance(obv(_ohlcv()), pl.Series)


def test_volatility_20() -> None:
    ret = returns_node(_ohlcv())
    assert isinstance(volatility_20(ret), pl.Series)


def test_calendar_features() -> None:
    ohlcv = _ohlcv(n=90)
    assert isinstance(day_of_week(ohlcv), pl.Series)
    assert isinstance(day_of_month(ohlcv), pl.Series)
    assert isinstance(month(ohlcv), pl.Series)
    assert isinstance(quarter(ohlcv), pl.Series)
    assert isinstance(days_to_month_end(ohlcv), pl.Series)


def test_trading_day_of_month() -> None:
    result = trading_day_of_month(_ohlcv(n=5))
    assert isinstance(result, pl.Series)
    assert result.to_list() == [1, 2, 3, 4, 5]


def test_next_day_return() -> None:
    result = next_day_return(_ohlcv(n=3))
    assert isinstance(result, pl.Series)
    assert result[-1] is None
    assert all(v is not None for v in result[:2])


class TestFiniteValueGuards:
    """F15/W01: zero baselines produce null, never infinity."""

    def test_volume_roc_5_zero_baseline_is_null_not_inf(self) -> None:
        volumes = [0.0, 0.0, 0.0, 0.0, 0.0, 1000.0]
        ohlcv = _ohlcv(n=6, volume=volumes)
        result = volume_roc_5(ohlcv)
        assert result.is_infinite().sum() == 0
        assert result[5] is None  # zero baseline: undefined ratio -> null

    def test_volume_roc_5_normal_case(self) -> None:
        volumes = [1000.0 * (i + 1) for i in range(8)]
        ohlcv = _ohlcv(n=8, volume=volumes)
        result = volume_roc_5(ohlcv)
        assert result.is_infinite().sum() == 0
        assert result[5] == pytest.approx(5.0)  # 6000/1000 - 1

    def test_emitted_frame_rejects_infinity(self) -> None:
        frame = _ohlcv(n=5).with_columns(pl.lit(float("inf")).alias("bad_feature"))
        with pytest.raises(FeatureOutputValidationError, match="non-finite"):
            validate_emitted_frame(frame, target_included=False)

    def test_emitted_frame_rejects_nan(self) -> None:
        frame = _ohlcv(n=5).with_columns(pl.lit(float("nan")).alias("bad_feature"))
        with pytest.raises(FeatureOutputValidationError, match="non-finite"):
            validate_emitted_frame(frame, target_included=False)

    def test_emitted_frame_rejects_null_identity(self) -> None:
        frame = _ohlcv(n=5)
        ticker = frame["ticker"].clone()
        ticker[2] = None
        with pytest.raises(FeatureOutputValidationError, match="null ticker"):
            validate_emitted_frame(frame.with_columns(ticker), target_included=False)

    def test_emitted_frame_rejects_duplicate_identity(self) -> None:
        frame = _ohlcv(n=5)
        duplicated = pl.concat([frame, frame.head(1)])
        with pytest.raises(FeatureOutputValidationError, match="duplicate"):
            validate_emitted_frame(duplicated, target_included=False)

    def test_emitted_frame_rejects_null_row_defined_feature(self) -> None:
        frame = _ohlcv(n=5)
        hl = pl.Series("hl_range", [0.01, 0.02, 0.01, 0.02, 0.01])
        hl[3] = None
        with pytest.raises(FeatureOutputValidationError, match="row-defined"):
            validate_emitted_frame(frame.with_columns(hl), target_included=False)

    def test_emitted_frame_allows_warmup_and_first_row_nulls(self) -> None:
        frame = _ohlcv(n=40)
        # rsi_14 has 14 warm-up nulls; overnight_return null at row 0 — both fine.
        warm = frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("rsi_14"))
        validate_emitted_frame(warm, target_included=False)

    def test_emitted_frame_target_null_only_on_last_row(self) -> None:
        frame = _ohlcv(n=5)
        target = pl.Series("next_day_return", [0.01] * 4 + [None])
        validate_emitted_frame(frame.with_columns(target), target_included=True)

        interior = pl.Series("next_day_return", [0.01] * 2 + [None] + [0.01] * 2)
        with pytest.raises(FeatureOutputValidationError, match="next_day_return"):
            validate_emitted_frame(frame.with_columns(interior), target_included=True)


def test_invalid_input_cannot_reach_indicators() -> None:
    """The connected boundary (F02): a bad row fails before any indicator runs."""
    n = 5
    parts = {
        "ticker": pl.Series("ticker", ["AAPL"] * n),
        "date": pl.Series("date", [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]),
        "open": pl.Series("open", [0.0] + [100.0] * (n - 1)),
        "high": pl.Series("high", [101.0] * n),
        "low": pl.Series("low", [99.0] * n),
        "close": pl.Series("close", [100.0] * n),
        "volume": pl.Series("volume", [1e6] * n),
    }
    with pytest.raises(OHLCVValidationError):
        validated_ohlcv(parts["ticker"], parts["date"], parts["open"], parts["high"], parts["low"], parts["close"], parts["volume"])
