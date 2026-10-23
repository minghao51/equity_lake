"""Time-series validation helpers for financial ML models."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, cast

import numpy as np
import polars as pl
from sklearn.metrics import accuracy_score, precision_score, recall_score

from equity_lake.core.polars_utils import FrameLike, ensure_polars
from equity_lake.ml.backends import DEFAULT_BACKEND, build_estimator, canonical_training_params, fit_estimator, scale_pos_weight


@dataclass(frozen=True)
class PurgedEmbargoedWalkForwardSplitter:
    """Rolling splitter with interval-aware purging and post-test embargo.

    Callers may provide event maturity dates and decision dates aligned with
    ``X``. When interval metadata is present, rows without a known maturity are
    excluded; ``decision_cutoff`` additionally excludes labels not yet mature
    at that cutoff. Actual interval purging requires both date sequences. For
    legacy callers without both sequences, the full ``label_horizon`` rows are
    conservatively purged (not ``label_horizon - 1``).
    """

    train_window: int = 252
    test_window: int = 21
    embargo_window: int = 1
    label_horizon: int = 1
    event_end_dates: Sequence[date | datetime | str | None] | None = None
    decision_dates: Sequence[date | datetime | str | None] | None = None
    decision_cutoff: date | datetime | str | None = None

    def split(self, X: object, y: object | None = None, groups: object | None = None) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """Yield train/test indices after excluding pending and unavailable labels."""
        n_samples = len(X)  # type: ignore[arg-type]
        mature_indices = self._mature_indices(n_samples)
        if self.decision_dates is not None and len(self.decision_dates) != n_samples:
            raise ValueError("decision_dates must have one entry for each row in X")

        start = 0
        while start + self.train_window + self.test_window <= len(mature_indices):
            train_end = start + self.train_window
            test_start = train_end
            test_end = test_start + self.test_window
            train_candidates = mature_indices[start:train_end]
            test_idx = mature_indices[test_start:test_end]

            if self.event_end_dates is not None and self.decision_dates is not None:
                test_start_date = _as_date(self.decision_dates[int(test_idx[0])])
                train_idx = np.asarray(
                    [index for index in train_candidates if _as_date(self.event_end_dates[int(index)]) < test_start_date],
                    dtype=np.int64,
                )
            else:
                # Without actual start/end intervals, retain the whole declared
                # horizon as an embargo. The label ends at the boundary itself
                # for a horizon-sized event, so subtracting one is insufficient.
                purged_train_end = max(start, test_start - max(self.label_horizon, 0))
                train_idx = mature_indices[start:purged_train_end]

            if len(train_idx) > 0 and len(test_idx) > 0:
                yield train_idx, test_idx

            start = test_end + self.embargo_window

    def _mature_indices(self, n_samples: int) -> np.ndarray:
        if self.event_end_dates is None:
            if self.decision_cutoff is not None:
                raise ValueError("decision_cutoff requires event_end_dates")
            return np.arange(n_samples, dtype=np.int64)
        if len(self.event_end_dates) != n_samples:
            raise ValueError("event_end_dates must have one entry for each row in X")

        cutoff = _as_date(self.decision_cutoff) if self.decision_cutoff is not None else None
        return np.asarray(
            [index for index, raw_end in enumerate(self.event_end_dates) if raw_end is not None and (cutoff is None or _as_date(raw_end) <= cutoff)],
            dtype=np.int64,
        )

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
    event_end_dates: Sequence[date | datetime | str | None] | None = None,
    decision_dates: Sequence[date | datetime | str | None] | None = None,
    decision_cutoff: date | datetime | str | None = None,
    backend: str = DEFAULT_BACKEND,
) -> dict[str, float | int]:
    """Run purged walk-forward validation and aggregate core metrics."""
    X_pl = ensure_polars(X)
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=train_window,
        test_window=test_window,
        embargo_window=embargo_window,
        label_horizon=label_horizon_days,
        event_end_dates=event_end_dates,
        decision_dates=decision_dates,
        decision_cutoff=decision_cutoff,
    )
    fold_accuracies: list[float] = []
    fold_precisions: list[float] = []
    fold_recalls: list[float] = []

    y_np = y.to_numpy()
    # Canonical production params, minus row/column subsampling: walk-forward
    # folds fit deterministically, and ``None`` values are dropped by
    # ``normalize_params`` (so no backend receives a subsample setting).
    fold_params = canonical_training_params({"subsample": None, "colsample_bytree": None})

    for train_idx, test_idx in splitter.split(X_pl):
        X_tr = _select_rows(X_pl, train_idx)
        y_tr = y_np[train_idx]
        X_te = _select_rows(X_pl, test_idx)
        y_te = y_np[test_idx]

        model = build_estimator(backend, fold_params, scale_pos_weight=scale_pos_weight(y_tr))
        fit_estimator(model, X_tr, y_tr, verbose=False)
        preds = (model.predict_proba(X_te)[:, 1] >= 0.5).astype(int)
        fold_accuracies.append(float(accuracy_score(y_te, preds)))
        # sklearn runtime accepts numeric zero; this cast keeps compatibility
        # with its narrower installed type stub without changing behavior.
        fold_precisions.append(float(precision_score(y_te, preds, zero_division=cast(Any, 0))))
        fold_recalls.append(float(recall_score(y_te, preds, zero_division=cast(Any, 0))))

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


def _as_date(value: date | datetime | str | None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    raise ValueError(f"Expected a known event/decision date, got {value!r}")


def _select_rows(frame: pl.DataFrame, indices: np.ndarray) -> pl.DataFrame:
    selected = np.zeros(frame.height, dtype=bool)
    selected[indices] = True
    return frame.filter(pl.Series("selected", selected))
