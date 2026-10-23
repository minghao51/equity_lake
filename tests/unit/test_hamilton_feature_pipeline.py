"""Tests for the Hamilton-backed feature pipeline."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

import pandas as pd
import polars as pl
import pytest

from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION  # type: ignore[import-untyped]
from equity_lake.features.pipeline import FeaturePipeline  # type: ignore[import-untyped]


def _sample_frame(n: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": dates,
            "open": range(100, 100 + len(dates)),
            "high": range(101, 101 + len(dates)),
            "low": range(99, 99 + len(dates)),
            "close": range(100, 100 + len(dates)),
            "volume": [1_000_000] * len(dates),
        }
    )


def test_feature_pipeline_computes_expected_columns() -> None:
    result = FeaturePipeline().compute_technical(_sample_frame(), include_target=True)

    assert isinstance(result, pl.DataFrame)
    assert "rsi_14" in result.columns
    assert "macd" in result.columns
    assert "volume_ratio" in result.columns
    assert "next_day_return" in result.columns
    assert set(result["feature_schema_version"].to_list()) == {FEATURE_SCHEMA_VERSION}


def test_feature_pipeline_excludes_target_by_default() -> None:
    result = FeaturePipeline().compute_technical(_sample_frame())

    assert "next_day_return" not in result.columns


def test_feature_pipeline_accepts_mixed_date_formats() -> None:
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    mixed_dates = [dt.strftime("%Y-%m-%d") if idx % 2 == 0 else dt.strftime("%Y-%m-%d %H:%M:%S.%f") for idx, dt in enumerate(dates)]
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": mixed_dates,
            "open": range(100, 100 + len(dates)),
            "high": range(101, 101 + len(dates)),
            "low": range(99, 99 + len(dates)),
            "close": range(100, 100 + len(dates)),
            "volume": [1_000_000] * len(dates),
        }
    )

    result = FeaturePipeline().compute_technical(frame)

    # D01: the EOD key is pl.Date — matching persisted Gold and enrichment tables.
    assert result.schema["date"] == pl.Date


def test_feature_pipeline_accepts_polars_input() -> None:
    dates = pd.date_range("2024-01-01", periods=80, freq="B")
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": [dt.strftime("%Y-%m-%d") if idx % 2 == 0 else dt.strftime("%Y-%m-%d %H:%M:%S.%f") for idx, dt in enumerate(dates)],
            "open": range(100, 100 + len(dates)),
            "high": range(101, 101 + len(dates)),
            "low": range(99, 99 + len(dates)),
            "close": range(100, 100 + len(dates)),
            "volume": [1_000_000] * len(dates),
        }
    )

    result = FeaturePipeline().compute_technical(frame)

    assert isinstance(result, pl.DataFrame)
    assert result.schema["date"] == pl.Date
    assert "trading_day_of_month" in result.columns


class TestConnectedValidation:
    """F02: the real requested graph traverses the validation boundary and
    invalid input fails the public compute path."""

    def test_requested_graph_traverses_validated_ohlcv(self) -> None:
        """Topology assertion: the default request's upstream closure contains
        the Silver validation boundary (pre-W01 it did not)."""
        fp = FeaturePipeline()
        requested = list(fp.TECHNICAL_FEATURES) + fp.TARGET_FEATURES
        upstream = fp._driver.graph.get_upstream_nodes(requested)
        names = {item if isinstance(item, str) else item.name for item in _flatten(upstream)}
        assert "validated_ohlcv" in names
        assert "validated_features" not in names  # emitted-frame check runs at the pipeline seam

    def test_invalid_open_fails_public_compute(self) -> None:
        frame = _sample_frame()
        frame.loc[0, "open"] = 0.0
        from equity_lake.features.dag.clean_02 import OHLCVValidationError  # type: ignore[import-untyped]

        with pytest.raises(OHLCVValidationError, match="strictly positive"):
            FeaturePipeline().compute_technical(frame)

    def test_invalid_row_beyond_sampled_window_fails(self) -> None:
        """A bad row past any 100-row sampling window cannot hide (F02)."""
        frame = _sample_frame(n=150)
        frame.loc[120, "close"] = -1.0
        from equity_lake.features.dag.clean_02 import OHLCVValidationError

        with pytest.raises(OHLCVValidationError):
            FeaturePipeline().compute_technical(frame)

    def test_unsorted_input_emits_aligned_sorted_frame(self) -> None:
        """Review-critical regression (W01): every emitted column — identity and
        indicators alike — must share the validated frame's row order. Pre-fix,
        raw passthrough columns kept input order while indicators were sorted,
        silently misaligning features against their dates."""
        days = [dt.date() for dt in pd.date_range("2024-01-01", periods=30, freq="B")][::-1]
        close = [100.0 + i for i in range(30)][::-1]
        frame = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 30,
                "date": days,
                "open": close,
                "high": [c + 1.0 for c in close],
                "low": [c - 1.0 for c in close],
                "close": close,
                "volume": [1_000_000.0] * 30,
            }
        )
        result = FeaturePipeline().compute_technical(frame, include_target=True)
        assert result.height == 30
        assert result["date"].to_list() == sorted(result["date"].to_list())
        # Calendar and rolling features match the row they are emitted on.
        mismatched = result.filter(pl.col("date").dt.weekday() != pl.col("day_of_week")).height
        assert mismatched == 0
        # Target aligns with the sorted close series, not the input order.
        sorted_close = result["close"]
        expected = sorted_close.shift(-1) / sorted_close - 1
        assert result["next_day_return"].equals(expected)

    def test_duplicate_identity_keeps_first_without_crashing(self) -> None:
        """Review-critical regression (W01): duplicates dedupe to keep-first and
        the emitted frame stays row-aligned (pre-fix: Hamilton ShapeError)."""
        days = [dt.date() for dt in pd.date_range("2024-01-01", periods=30, freq="B")]
        base = pd.DataFrame(
            {
                "ticker": ["AAPL"] * 30,
                "date": days,
                "open": [100.0 + i for i in range(30)],
                "high": [101.0 + i for i in range(30)],
                "low": [99.0 + i for i in range(30)],
                "close": [100.0 + i for i in range(30)],
                "volume": [1_000_000.0] * 30,
            }
        )
        duplicated = pd.concat([base, base.head(1)], ignore_index=True)
        result = FeaturePipeline().compute_technical(duplicated, include_target=True)
        assert result.height == 30
        assert result["date"].is_duplicated().sum() == 0
        mismatched = result.filter(pl.col("date").dt.weekday() != pl.col("day_of_week")).height
        assert mismatched == 0

    def test_warmup_nulls_and_missing_target_do_not_reject(self) -> None:
        """Legitimate warm-up nulls and the unavailable final target are not
        invalid output (D01)."""
        result = FeaturePipeline().compute_technical(_sample_frame(), include_target=True)
        assert result.height == 80
        assert result["next_day_return"][-1] is None
        assert result["rsi_14"][:14].is_null().all()

    def test_emitted_frame_has_no_infinity(self) -> None:
        result = FeaturePipeline().compute_technical(_sample_frame(), include_target=True)
        float_cols = [c for c, dtype in result.schema.items() if dtype in (pl.Float64, pl.Float32)]
        for col in float_cols:
            assert not result[col].is_infinite().any(), col
            assert not result[col].is_nan().any(), col

    def test_zero_volume_baseline_produces_null_not_inf(self) -> None:
        """F15: zero-volume sessions must not poison volume_roc_5 with inf."""
        frame = _sample_frame()
        frame.loc[:5, "volume"] = 0  # leading zero-volume sessions
        result = FeaturePipeline().compute_technical(frame)
        assert not result["volume_roc_5"].is_infinite().any()
        assert result["volume_roc_5"].is_null().any()

    def test_gold_boundary_node_enforces_full_frame(self) -> None:
        """The validated_features node assembles the full emitted frame and
        passes vectorized enforcement on valid input (F02)."""
        fp = FeaturePipeline()
        result = fp._driver.execute(["validated_features"], inputs={"price_data": pl.from_pandas(_sample_frame())})
        assert isinstance(result, pl.DataFrame)
        assert "rsi_14" in result.columns
        assert "volume_ratio" in result.columns
        assert result.schema["date"] == pl.Date


def _flatten(items: Iterable[Any]) -> Iterator[Any]:
    for item in items:
        if isinstance(item, (set, frozenset, list, tuple)):
            yield from _flatten(item)
        else:
            yield item


def test_gold_boundary_node_rejects_infinite_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """If an indicator produced inf, the Gold boundary node rejects the frame."""
    import equity_lake.features.dag.features_03 as features_03  # type: ignore[import-untyped]
    from equity_lake.features.dag.features_03 import FeatureOutputValidationError

    original = features_03.validate_emitted_frame

    def injected(frame: pl.DataFrame, *, target_included: bool) -> None:
        # Simulate a poisoned indicator column, then run the real check.
        original(frame.with_columns(pl.lit(float("inf")).alias("poisoned")), target_included=target_included)

    # validated_features resolves validate_emitted_frame from module globals at
    # call time, so patching before driver construction routes through it.
    monkeypatch.setattr(features_03, "validate_emitted_frame", injected)
    fp = FeaturePipeline()
    with pytest.raises(FeatureOutputValidationError, match="non-finite"):
        fp._driver.execute(["validated_features"], inputs={"price_data": pl.from_pandas(_sample_frame())})
