"""Tests for sample uniqueness / concurrency weights (de Prado Ch. 4)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from runpy import run_path

import numpy as np
import pandas as pd

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.ml.labeling import apply_triple_barrier_labels  # type: ignore[import-untyped]
from equity_lake.ml.sample_weights import (  # type: ignore[import-untyped]
    compute_sample_uniqueness,
    compute_uniqueness_weights,
)

_pit_fixtures = run_path(str(Path(__file__).with_name("_pit_fixtures.py")))
synthetic_action_coverage_proof = _pit_fixtures["synthetic_action_coverage_proof"]
synthetic_feature_proof_columns = _pit_fixtures["synthetic_feature_proof_columns"]


def test_compute_sample_uniqueness_single_sample() -> None:
    starts = np.array([0])
    ends = np.array([5])
    u = compute_sample_uniqueness(starts, ends)
    np.testing.assert_allclose(u, [1.0])


def test_compute_sample_uniqueness_non_overlapping() -> None:
    starts = np.array([0, 10, 20])
    ends = np.array([5, 15, 25])
    u = compute_sample_uniqueness(starts, ends)
    np.testing.assert_allclose(u, [1.0, 1.0, 1.0])


def test_compute_sample_uniqueness_overlapping_window_has_lower_score() -> None:
    starts = np.array([0, 1, 2])
    ends = np.array([5, 6, 7])
    u = compute_sample_uniqueness(starts, ends)
    assert u[0] < 1.0
    assert u[1] < 1.0
    assert u[2] < 1.0
    assert all(0.0 < value < 1.0 for value in u)


def test_compute_uniqueness_weights_from_labeled_frame() -> None:
    dates = pd.date_range("2024-01-01", periods=10, freq="B")
    full_df = pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": dates,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volatility_20": 0.02,
        }
    )
    candidates = pd.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "AAPL"],
            "date": [dates[0], dates[1], dates[5]],
            "candidate_action": ["BUY", "BUY", "BUY"],
            "candidate_source": ["momentum"] * 3,
            "candidate_score": [0.1, 0.2, 0.3],
        }
    )
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
    proof = synthetic_action_coverage_proof(instrument, date.fromisoformat(str(dates[0])[:10]), date.fromisoformat(str(dates[-1])[:10]))
    for frame in (full_df, candidates):
        for name, values in synthetic_feature_proof_columns(instrument, frame["date"].tolist(), proof=proof).items():
            frame[name] = values

    labeled = apply_triple_barrier_labels(
        candidates,
        full_df,
        vertical_barrier_days=2,
        pt_mult=1.5,
        sl_mult=1.0,
    )

    weights = compute_uniqueness_weights(labeled)
    assert len(weights) == 3
    assert all(weight > 0 for weight in weights)
    assert weights[0] < weights[2]


def test_compute_uniqueness_weights_empty_frame() -> None:
    import polars as pl

    empty = pl.DataFrame()
    weights = compute_uniqueness_weights(empty)
    assert len(weights) == 0
