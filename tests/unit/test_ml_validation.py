"""Tests for purged walk-forward ML validation."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import polars as pl

from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter, run_purged_walk_forward_validation  # type: ignore[import-untyped]


def test_purged_embargoed_splitter_avoids_overlap() -> None:
    X = pd.DataFrame({"x": range(100)})
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=30,
        test_window=10,
        embargo_window=3,
        label_horizon=5,
    )

    splits = list(splitter.split(X))

    assert splits
    for train_idx, test_idx in splits:
        assert max(train_idx) < min(test_idx)
        assert min(test_idx) - max(train_idx) >= 5


def test_splitter_purges_v1_label_maturing_on_test_start_date() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(10)]
    event_ends = [dates[min(index + 1, len(dates) - 1)] for index in range(len(dates))]
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=5,
        test_window=2,
        embargo_window=0,
        label_horizon=1,
        event_end_dates=event_ends,
        decision_dates=dates,
        decision_cutoff=dates[-1],
    )

    train_idx, test_idx = next(splitter.split(pd.DataFrame({"x": range(10)})))

    # The v1 label for row 4 matures exactly on the first test decision date.
    assert train_idx.tolist() == [0, 1, 2, 3]
    assert test_idx.tolist() == [5, 6]


def test_splitter_purges_v2_by_actual_barrier_end_date() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(10)]
    event_ends = [dates[1], dates[2], dates[7], dates[4], dates[5], dates[6], dates[7], dates[8], dates[9], dates[9]]
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=5,
        test_window=2,
        embargo_window=0,
        label_horizon=5,
        event_end_dates=event_ends,
        decision_dates=dates,
        decision_cutoff=dates[-1],
    )

    train_idx, test_idx = next(splitter.split(pd.DataFrame({"x": range(10)})))

    # Row 2 crosses the boundary; row 4 matures exactly at it. Other rows
    # remain eligible despite the configured five-day maximum barrier.
    assert train_idx.tolist() == [0, 1, 3]
    assert test_idx.tolist() == [5, 6]


def test_splitter_legacy_fallback_purges_full_horizon() -> None:
    splitter = PurgedEmbargoedWalkForwardSplitter(train_window=6, test_window=2, embargo_window=0, label_horizon=2)

    train_idx, test_idx = next(splitter.split(pd.DataFrame({"x": range(8)})))

    assert train_idx.tolist() == [0, 1, 2, 3]
    assert test_idx.tolist() == [6, 7]
    assert test_idx[0] - train_idx[-1] == 3


def test_splitter_excludes_pending_and_labels_after_decision_cutoff() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(10)]
    event_ends = [dates[1], dates[2], None, dates[4], dates[5], dates[6], dates[9], dates[8], dates[9], dates[9]]
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=3,
        test_window=2,
        embargo_window=0,
        label_horizon=1,
        event_end_dates=event_ends,
        decision_dates=dates,
        decision_cutoff=dates[7],
    )

    train_idx, test_idx = next(splitter.split(pd.DataFrame({"x": range(10)})))

    assert train_idx.tolist() == [0, 1]
    assert test_idx.tolist() == [4, 5]
    assert 2 not in train_idx and 2 not in test_idx
    for index in np.concatenate((train_idx, test_idx)):
        maturity = event_ends[int(index)]
        assert maturity is not None
        assert maturity <= dates[7]


def test_run_purged_walk_forward_validation_returns_fold_metrics() -> None:
    X = pd.DataFrame({"x": range(100), "y": [value % 3 for value in range(100)]})
    y = pl.Series("target", [value % 2 for value in range(100)])

    metrics = run_purged_walk_forward_validation(
        X=X,
        y=y,
        train_window=30,
        test_window=10,
        embargo_window=2,
        label_horizon_days=3,
    )

    assert metrics["folds"] > 0
    assert 0.0 <= float(metrics["mean_accuracy"]) <= 1.0
