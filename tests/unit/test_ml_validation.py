"""Tests for purged walk-forward ML validation."""

from __future__ import annotations

import pandas as pd

from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter, run_purged_walk_forward_validation


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


def test_run_purged_walk_forward_validation_returns_fold_metrics() -> None:
    X = pd.DataFrame({"x": range(100), "y": [value % 3 for value in range(100)]})
    y = pd.Series([value % 2 for value in range(100)])

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
