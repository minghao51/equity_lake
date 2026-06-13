"""Time-series validation helpers for financial ML models."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np
import polars as pl
import xgboost as xgb
from sklearn.metrics import accuracy_score, precision_score, recall_score

from equity_lake.core.polars_utils import FrameLike, ensure_polars


@dataclass(frozen=True)
class PurgedEmbargoedWalkForwardSplitter:
    """Rolling splitter that supports purging and post-test embargo windows."""

    train_window: int = 252
    test_window: int = 21
    embargo_window: int = 1
    label_horizon: int = 1

    def split(self, X: object, y: object | None = None, groups: object | None = None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield train/test index pairs suitable for time-series model validation."""
        n_samples = len(X)  # type: ignore[arg-type]
        start = 0
        while start + self.train_window + self.test_window <= n_samples:
            train_end = start + self.train_window
            test_start = train_end
            test_end = test_start + self.test_window

            purged_train_end = max(start, test_start - max(self.label_horizon - 1, 0))
            train_idx = np.arange(start, purged_train_end)
            test_idx = np.arange(test_start, test_end)

            if len(train_idx) > 0 and len(test_idx) > 0:
                yield train_idx, test_idx

            start = test_end + self.embargo_window

    def get_n_splits(self, X: object | None = None, y: object | None = None, groups: object | None = None) -> int:
        """Return the number of folds for the given sample size."""
        if X is None:
            return 0
        return sum(1 for _ in self.split(X, y=y, groups=groups))


def run_purged_walk_forward_validation(
    *,
    X: FrameLike,
    y: pl.Series,
    train_window: int,
    test_window: int,
    embargo_window: int,
    label_horizon_days: int,
) -> dict[str, float | int]:
    """Run purged walk-forward validation and aggregate core metrics."""
    X_pl = ensure_polars(X)
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=train_window,
        test_window=test_window,
        embargo_window=embargo_window,
        label_horizon=label_horizon_days,
    )
    fold_accuracies: list[float] = []
    fold_precisions: list[float] = []
    fold_recalls: list[float] = []

    y_np = y.to_numpy()

    for train_idx, test_idx in splitter.split(X_pl):
        X_tr = X_pl.slice(train_idx[0], train_idx[-1] - train_idx[0] + 1)
        y_tr = y_np[train_idx]
        X_te = X_pl.slice(test_idx[0], test_idx[-1] - test_idx[0] + 1)
        y_te = y_np[test_idx]

        pos_count = int((y_tr == 1).sum())
        neg_count = int((y_tr == 0).sum())
        model_kwargs: dict = {
            "max_depth": 5,
            "learning_rate": 0.05,
            "n_estimators": 200,
            "objective": "binary:logistic",
            "eval_metric": "logloss",
            "random_state": 42,
            "n_jobs": -1,
        }
        if pos_count > 0 and neg_count > 0:
            model_kwargs["scale_pos_weight"] = neg_count / pos_count
        model = xgb.XGBClassifier(**model_kwargs)
        model.fit(X_tr, y_tr, verbose=False)
        preds = (model.predict_proba(X_te)[:, 1] >= 0.5).astype(int)
        fold_accuracies.append(float(accuracy_score(y_te, preds)))
        fold_precisions.append(float(precision_score(y_te, preds, zero_division=0)))
        fold_recalls.append(float(recall_score(y_te, preds, zero_division=0)))

    if not fold_accuracies:
        return {"folds": 0, "mean_accuracy": 0.0, "mean_precision": 0.0, "mean_recall": 0.0}

    return {
        "folds": len(fold_accuracies),
        "mean_accuracy": float(np.mean(fold_accuracies)),
        "std_accuracy": float(np.std(fold_accuracies, ddof=0)),
        "mean_precision": float(np.mean(fold_precisions)),
        "std_precision": float(np.std(fold_precisions, ddof=0)),
        "mean_recall": float(np.mean(fold_recalls)),
        "std_recall": float(np.std(fold_recalls, ddof=0)),
    }
