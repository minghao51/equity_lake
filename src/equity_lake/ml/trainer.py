from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
import structlog
import xgboost as xgb

logger = structlog.get_logger(__name__)


def compute_class_weights(y: pl.Series) -> dict[str, Any]:
    values = y.to_numpy()
    if values.size == 0:
        return {"positive_count": 0, "negative_count": 0, "scale_pos_weight": 1.0}
    pos_count = int((values == 1).sum())
    neg_count = int((values == 0).sum())
    if pos_count == 0 or neg_count == 0:
        return {"positive_count": pos_count, "negative_count": neg_count, "scale_pos_weight": 1.0}
    return {
        "positive_count": pos_count,
        "negative_count": neg_count,
        "scale_pos_weight": float(neg_count) / float(pos_count),
    }


def compute_shap_importance(model: xgb.XGBClassifier, X: pl.DataFrame, feature_cols: list[str], top_k: int = 10) -> dict[str, float] | None:
    try:
        import shap
    except ImportError:
        return None
    try:
        X_sample = X.select([col for col in feature_cols if col in X.columns]).to_pandas()
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_sample)
        if hasattr(shap_values, "ndim") and shap_values.ndim == 3:
            shap_values = shap_values[:, :, 1]
        if not hasattr(shap_values, "ndim") or shap_values.ndim != 2:
            return None
        import numpy as np

        mean_abs = np.abs(shap_values).mean(axis=0)
        available_cols = [col for col in feature_cols if col in X.columns]
        ranked = sorted(zip(available_cols, mean_abs.tolist(), strict=False), key=lambda item: item[1], reverse=True)[:top_k]
        return {name: round(float(value), 6) for name, value in ranked}
    except Exception as exc:
        logger.debug("shap_computation_failed", error=str(exc))
        return None


def optimize_threshold(y_true: np.ndarray | pl.Series, y_proba: np.ndarray | pl.Series, metric: str = "f1") -> float:
    try:
        import numpy as np
        from sklearn.metrics import f1_score, precision_score
    except ImportError:
        return 0.5
    y_arr = y_true.to_numpy() if hasattr(y_true, "to_numpy") else np.asarray(y_true)
    p_arr = y_proba.to_numpy() if hasattr(y_proba, "to_numpy") else np.asarray(y_proba)
    if y_arr.size == 0 or p_arr.size == 0:
        return 0.5
    if len(np.unique(y_arr)) < 2:
        return 0.5
    best_threshold = 0.5
    best_score = -1.0
    for threshold in np.arange(0.10, 0.91, 0.01):
        preds = (p_arr >= threshold).astype(int)
        if preds.sum() == 0:
            continue
        score = precision_score(y_arr, preds, zero_division=0) if metric == "precision" else f1_score(y_arr, preds, zero_division=0)
        if score > best_score:
            best_score = score
            best_threshold = float(threshold)
    return round(best_threshold, 2)
