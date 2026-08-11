"""Shared per-fold scoring primitives for the ML comparison/ablation harnesses.

Promoted out of :mod:`equity_lake.ml.comparison` so :mod:`equity_lake.ml.ablation`
(and future harnesses) reuse the *same* fold-scoring helpers instead of importing
``comparison``'s private (``_``-prefixed) API (parent §2 P2). Kept package-private
via the leading-underscore module name.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from sklearn.metrics import accuracy_score, precision_score

from equity_lake.ml.forecasting import NON_FEATURE_COLUMNS

#: Columns that must never be fed to a model as features. Extends
#: ``NON_FEATURE_COLUMNS`` with the v1 target (``target``) and the triple-barrier
#: bookkeeping columns (``barrier_start_idx`` / ``barrier_end_idx``) so the OOS
#: metrics below are honest — these encode the label or the evaluation window.
EXCLUDE_COLUMNS: frozenset[str] = frozenset(
    NON_FEATURE_COLUMNS | {"target", "barrier_start_idx", "barrier_end_idx"},
)

#: Canonical (XGBoost-style) params reused for every fold; ``build_estimator``
#: normalizes per backend. Kept modest so the harness is fast on small frames.
DEFAULT_FIT_PARAMS: dict[str, object] = {
    "max_depth": 3,
    "learning_rate": 0.1,
    "n_estimators": 50,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "random_state": 42,
    "n_jobs": -1,
}


def feature_columns(df: pl.DataFrame) -> list[str]:
    """Return model feature columns, excluding labels/identifiers/bookkeeping."""
    return [col for col in df.columns if col not in EXCLUDE_COLUMNS]


def scale_pos_weight(y_train: np.ndarray) -> float:
    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    if pos <= 0 or neg <= 0:
        return 1.0
    return float(neg) / float(pos)


def aggregate_oos(labels: np.ndarray, probs: np.ndarray, folds: int) -> dict[str, float]:
    """Pool per-fold OOS predictions into accuracy/precision/Brier metrics."""
    n = int(labels.size)
    if n == 0:
        return {
            "oos_accuracy": 0.0,
            "oos_precision": 0.0,
            "oos_brier": 0.0,
            "oos_mean_prob": 0.0,
            "folds": 0.0,
            "n_oos": 0.0,
        }
    preds = (probs >= 0.5).astype(int)
    return {
        "oos_accuracy": float(accuracy_score(labels, preds)),
        "oos_precision": float(precision_score(labels, preds, zero_division=0)),
        "oos_brier": float(np.mean((probs - labels) ** 2)),
        "oos_mean_prob": float(probs.mean()),
        "folds": float(folds),
        "n_oos": float(n),
    }


__all__ = [
    "DEFAULT_FIT_PARAMS",
    "EXCLUDE_COLUMNS",
    "aggregate_oos",
    "feature_columns",
    "scale_pos_weight",
]
