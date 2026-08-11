"""OOS comparison harness: meta-label vs direction and backend vs backend.

This module is the Step-4 deliverable that produces two FindingCards
(parent §6):

* ``meta-label-vs-direction`` (axis ``labeling``) — does v2 meta-labeling beat
  v1 raw direction on out-of-sample precision?
* ``xgb-vs-lgbm`` (axis ``model``) — XGBoost vs LightGBM on accuracy,
  calibration, and feature-importance agreement.

The harness is **pure**: ``run_comparison`` takes a single features frame and
emits cards; it performs no lake I/O and is unit-testable with synthetic data.

Reuse contract (parent §4 B4/B5 — do not reimplement):

* :class:`equity_lake.ml.validation.PurgedEmbargoedWalkForwardSplitter` — call
  ``.split()`` directly to get per-fold OOS index pairs (the aggregate
  ``run_purged_walk_forward_validation`` is XGBoost-locked and returns no
  per-fold rows).
* :func:`equity_lake.ml.backends.build_estimator` / :func:`fit_estimator` /
  :func:`backend_of` — the single backend seam.
* :func:`equity_lake.ml.candidates.build_candidate_frame` and
  :func:`equity_lake.ml.labeling.apply_triple_barrier_labels` — the exact
  candidate + triple-barrier prep ``PriceForecaster._prepare_training_frame``
  delegates to (so v2's ``meta_label`` target is produced identically).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import structlog
from sklearn.metrics import accuracy_score, precision_score

from equity_lake.findings.models import FindingCard, FindingVerdict
from equity_lake.findings.writer import write_finding_card
from equity_lake.ml.backends import DEFAULT_BACKEND, build_estimator, fit_estimator
from equity_lake.ml.candidates import DEFAULT_BACKTEST_STRATEGY, build_candidate_frame
from equity_lake.ml.forecasting import DEFAULT_V2_SETTINGS, NON_FEATURE_COLUMNS
from equity_lake.ml.labeling import apply_triple_barrier_labels
from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter

logger = structlog.get_logger(__name__)

#: mode -> backend -> metric -> value
ModeBackendMetrics = dict[str, dict[str, dict[str, float]]]
#: mode -> backend -> feature_importances_
ModeBackendImportances = dict[str, dict[str, list[float]]]

#: Columns that must never be fed to a model as features. Extends
#: ``NON_FEATURE_COLUMNS`` with the v1 target (``target``) and the triple-barrier
#: bookkeeping columns (``barrier_start_idx`` / ``barrier_end_idx``) so the OOS
#: metrics below are honest — these encode the label or the evaluation window.
_EXCLUDE_COLUMNS: frozenset[str] = frozenset(
    NON_FEATURE_COLUMNS | {"target", "barrier_start_idx", "barrier_end_idx"},
)

#: Margin (in metric units) below which two scores are considered tied.
_EPS: float = 0.01

#: Canonical (XGBoost-style) params reused for every fold; ``build_estimator``
#: normalizes per backend. Kept modest so the harness is fast on small frames.
_DEFAULT_FIT_PARAMS: dict[str, object] = {
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


def _feature_columns(df: pl.DataFrame) -> list[str]:
    """Return model feature columns, excluding labels/identifiers/bookkeeping."""
    return [col for col in df.columns if col not in _EXCLUDE_COLUMNS]


def _prepare_training_frame(features: pl.DataFrame, mode: str) -> pl.DataFrame:
    """Build the per-mode training frame (mirrors ``PriceForecaster``).

    v1_direction: append a binary ``target`` (next-day return > 0).
    v2_meta_label: reuse ``build_candidate_frame`` + ``apply_triple_barrier_labels``
    with the same defaults ``PriceForecaster`` uses when no config is supplied.
    """
    frame = features.sort("date")
    if mode == "v2_meta_label":
        candidates = build_candidate_frame(frame, [dict(DEFAULT_BACKTEST_STRATEGY)])
        if candidates.is_empty():
            return candidates
        return apply_triple_barrier_labels(
            candidates,
            frame,
            vertical_barrier_days=int(DEFAULT_V2_SETTINGS["vertical_barrier_days"]),
            pt_mult=float(DEFAULT_V2_SETTINGS["pt_mult"]),
            sl_mult=float(DEFAULT_V2_SETTINGS["sl_mult"]),
        )
    return frame.with_columns((pl.col("next_day_return") > 0).cast(pl.Int8).alias("target"))


def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    """Dep-free Spearman rank correlation (feature-importance agreement proxy)."""
    if a.size < 2 or b.size < 2 or a.size != b.size:
        return 0.0
    rank_a = np.argsort(np.argsort(a)).astype(np.float64)
    rank_b = np.argsort(np.argsort(b)).astype(np.float64)
    rank_a -= rank_a.mean()
    rank_b -= rank_b.mean()
    denom = np.sqrt(float((rank_a**2).sum()) * float((rank_b**2).sum()))
    if denom == 0.0:
        return 0.0
    return float(np.dot(rank_a, rank_b) / denom)


def _scale_pos_weight(y_train: np.ndarray) -> float:
    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    if pos <= 0 or neg <= 0:
        return 1.0
    return float(neg) / float(pos)


def _resolve_ticker(frame: pl.DataFrame, override: str | None) -> str:
    """Pick the per-ticker card scope value.

    An explicit ``override`` (the CLI's ``--ticker``) wins; otherwise read the
    frame's ``ticker`` column so the pure-harness call path (unit tests, library
    use) still records honest reproducibility metadata (parent §2 P1).
    """
    if override:
        return override
    if "ticker" in frame.columns and frame.height > 0:
        value = frame["ticker"][0]
        return "" if value is None else str(value)
    return ""


def _aggregate_oos(labels: np.ndarray, probs: np.ndarray, folds: int) -> dict[str, float]:
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


def _score_mode(
    df: pl.DataFrame,
    target_column: str,
    *,
    splitter: PurgedEmbargoedWalkForwardSplitter,
    backends: Sequence[str],
) -> tuple[dict[str, dict[str, float]], dict[str, list[float]]]:
    """Train per-fold per-backend models; return pooled OOS metrics + importances.

    Returns ``(backend_metrics, backend_importances)`` where importances (aligned
    to ``_feature_columns(df)``) are taken from each backend's final fold model
    and feed the backend agreement metric.
    """
    clean = df.filter(pl.col(target_column).is_not_null())
    feature_cols = _feature_columns(clean)
    empty_metrics = _aggregate_oos(np.array([]), np.array([]), 0)
    if not feature_cols or clean.is_empty():
        return {backend: dict(empty_metrics) for backend in backends}, {backend: [] for backend in backends}

    x_all = clean.select([pl.col(col).cast(pl.Float64, strict=False).alias(col) for col in feature_cols]).to_numpy()
    y_all = clean[target_column].cast(pl.Int64, strict=False).to_numpy()

    folds = list(splitter.split(clean))
    backend_metrics: dict[str, dict[str, float]] = {}
    backend_importances: dict[str, list[float]] = {}
    for backend in backends:
        oos_labels: list[int] = []
        oos_probs: list[float] = []
        last_importances: list[float] = []
        for train_idx, test_idx in folds:
            x_tr, y_tr = x_all[train_idx], y_all[train_idx]
            x_te, y_te = x_all[test_idx], y_all[test_idx]
            model = build_estimator(backend, dict(_DEFAULT_FIT_PARAMS), scale_pos_weight=_scale_pos_weight(y_tr))
            fit_estimator(model, x_tr, y_tr, verbose=False)
            proba = model.predict_proba(x_te)[:, 1]
            oos_probs.extend(float(p) for p in proba)
            oos_labels.extend(int(v) for v in y_te)
            importances = getattr(model, "feature_importances_", None)
            if importances is not None:
                last_importances = [float(v) for v in np.asarray(importances).ravel()]

        backend_metrics[backend] = _aggregate_oos(np.asarray(oos_labels), np.asarray(oos_probs), len(folds))
        backend_importances[backend] = last_importances
    return backend_metrics, backend_importances


def _mode_folds(backend_metrics: dict[str, dict[str, float]]) -> int:
    if not backend_metrics:
        return 0
    return int(max(metrics.get("folds", 0.0) for metrics in backend_metrics.values()))


def _mean_oos(
    by_mode: ModeBackendMetrics,
    metric: str,
    *,
    selector_modes: Sequence[str],
    backend: str,
) -> float:
    vals = [
        by_mode[mode][backend][metric]
        for mode in selector_modes
        if mode in by_mode and backend in by_mode[mode] and by_mode[mode][backend].get("folds", 0.0) > 0
    ]
    return float(np.mean(vals)) if vals else 0.0


def _build_meta_label_card(
    by_mode: ModeBackendMetrics,
    *,
    ticker: str,
    backends: Sequence[str],
    modes: Sequence[str],
    train_window: int,
    test_window: int,
    embargo_window: int,
    label_horizon_days: int,
) -> FindingCard:
    v1_key = "v1_direction"
    v2_key = "v2_meta_label"
    has_v1 = v1_key in modes and v1_key in by_mode
    has_v2 = v2_key in modes and v2_key in by_mode

    v1_precision = _mean_oos(by_mode, "oos_precision", selector_modes=(v1_key,) if has_v1 else (), backend=DEFAULT_BACKEND)
    v2_precision = _mean_oos(by_mode, "oos_precision", selector_modes=(v2_key,) if has_v2 else (), backend=DEFAULT_BACKEND)
    v1_accuracy = _mean_oos(by_mode, "oos_accuracy", selector_modes=(v1_key,) if has_v1 else (), backend=DEFAULT_BACKEND)
    v2_accuracy = _mean_oos(by_mode, "oos_accuracy", selector_modes=(v2_key,) if has_v2 else (), backend=DEFAULT_BACKEND)
    v1_folds = _mode_folds(by_mode.get(v1_key, {})) if has_v1 else 0
    v2_folds = _mode_folds(by_mode.get(v2_key, {})) if has_v2 else 0
    delta = v2_precision - v1_precision

    verdict: FindingVerdict
    if not has_v1 or not has_v2 or v1_folds == 0 or v2_folds == 0:
        verdict = "inconclusive"
        conclusion = (
            f"Insufficient OOS folds to compare labeling strategies (v1 folds={v1_folds}, v2 folds={v2_folds}); rerun over a longer feature history."
        )
    elif delta > _EPS:
        verdict = "positive"
        conclusion = f"Meta-labeling improved primary-side OOS precision by {delta:+.3f} ({v1_precision:.3f} -> {v2_precision:.3f})."
    elif delta < -_EPS:
        verdict = "negative"
        conclusion = f"Meta-labeling did not help: precision fell by {delta:+.3f} ({v1_precision:.3f} -> {v2_precision:.3f})."
    else:
        verdict = "inconclusive"
        conclusion = f"Precision effectively tied ({v1_precision:.3f} vs {v2_precision:.3f}, delta {delta:+.3f}); no clear labeling winner."

    metrics = {
        "v1_direction_precision": v1_precision,
        "v2_meta_label_precision": v2_precision,
        "precision_delta": float(delta),
        "v1_direction_accuracy": v1_accuracy,
        "v2_meta_label_accuracy": v2_accuracy,
        "v1_folds": float(v1_folds),
        "v2_folds": float(v2_folds),
    }
    return FindingCard(
        id="meta-label-vs-direction",
        axis="labeling",
        claim="Meta-labeling (v2) improves primary-side OOS precision over raw direction (v1).",
        verdict=verdict,
        conclusion=conclusion,
        metrics=metrics,
        evidence_refs=[],
        run_date=date.today(),
        scope={
            "tickers": [ticker],
            "backends": list(backends),
            "modes": list(modes),
            "train_window": train_window,
            "test_window": test_window,
            "embargo_window": embargo_window,
            "label_horizon_days": label_horizon_days,
        },
    )


def _build_model_card(
    by_mode: ModeBackendMetrics,
    importances_by_mode: ModeBackendImportances,
    *,
    ticker: str,
    backends: Sequence[str],
    modes: Sequence[str],
    train_window: int,
    test_window: int,
    embargo_window: int,
    label_horizon_days: int,
) -> FindingCard:
    scored_modes = [m for m in modes if m in by_mode and _mode_folds(by_mode[m]) > 0]
    xgb_key, lgbm_key = "xgboost", "lightgbm"

    xgb_acc = _mean_oos(by_mode, "oos_accuracy", selector_modes=scored_modes, backend=xgb_key) if xgb_key in backends else 0.0
    lgbm_acc = _mean_oos(by_mode, "oos_accuracy", selector_modes=scored_modes, backend=lgbm_key) if lgbm_key in backends else 0.0
    xgb_prec = _mean_oos(by_mode, "oos_precision", selector_modes=scored_modes, backend=xgb_key) if xgb_key in backends else 0.0
    lgbm_prec = _mean_oos(by_mode, "oos_precision", selector_modes=scored_modes, backend=lgbm_key) if lgbm_key in backends else 0.0
    xgb_brier = _mean_oos(by_mode, "oos_brier", selector_modes=scored_modes, backend=xgb_key) if xgb_key in backends else 0.0
    lgbm_brier = _mean_oos(by_mode, "oos_brier", selector_modes=scored_modes, backend=lgbm_key) if lgbm_key in backends else 0.0

    # Feature-importance agreement: Spearman of v1-direction importances (most
    # folds / most stable). Falls back to 0.0 if either backend is missing.
    agreement = 0.0
    v1_key = "v1_direction"
    if v1_key in importances_by_mode and xgb_key in importances_by_mode[v1_key] and lgbm_key in importances_by_mode[v1_key]:
        xgb_imp = importances_by_mode[v1_key][xgb_key]
        lgbm_imp = importances_by_mode[v1_key][lgbm_key]
        if len(xgb_imp) == len(lgbm_imp) and len(xgb_imp) >= 2:
            agreement = _spearman(np.asarray(xgb_imp, dtype=float), np.asarray(lgbm_imp, dtype=float))

    delta = lgbm_acc - xgb_acc
    total_folds = sum(_mode_folds(by_mode[m]) for m in scored_modes)

    verdict: FindingVerdict
    if not scored_modes or total_folds == 0 or xgb_key not in backends or lgbm_key not in backends:
        verdict = "inconclusive"
        conclusion = "Insufficient OOS folds to compare backends; rerun over a longer feature history."
    elif delta > _EPS:
        verdict = "positive"
        conclusion = (
            f"LightGBM beat XGBoost on OOS accuracy by {delta:+.3f} ({xgb_acc:.3f} -> {lgbm_acc:.3f}); importance agreement {agreement:+.2f}."
        )
    elif delta < -_EPS:
        verdict = "negative"
        conclusion = (
            f"XGBoost beat LightGBM on OOS accuracy by {-delta:+.3f} ({xgb_acc:.3f} vs {lgbm_acc:.3f}); importance agreement {agreement:+.2f}."
        )
    else:
        verdict = "inconclusive"
        conclusion = f"Backends tied on accuracy ({xgb_acc:.3f} vs {lgbm_acc:.3f}); importance agreement {agreement:+.2f}."

    metrics = {
        "xgboost_accuracy": xgb_acc,
        "lightgbm_accuracy": lgbm_acc,
        "accuracy_delta": float(delta),
        "xgboost_precision": xgb_prec,
        "lightgbm_precision": lgbm_prec,
        "xgboost_brier": xgb_brier,
        "lightgbm_brier": lgbm_brier,
        "feature_importance_agreement": agreement,
        "folds": float(total_folds),
    }
    return FindingCard(
        id="xgb-vs-lgbm",
        axis="model",
        claim="LightGBM outperforms XGBoost on out-of-sample accuracy.",
        verdict=verdict,
        conclusion=conclusion,
        metrics=metrics,
        evidence_refs=[],
        run_date=date.today(),
        scope={
            "tickers": [ticker],
            "backends": list(backends),
            "modes": list(modes),
            "train_window": train_window,
            "test_window": test_window,
            "embargo_window": embargo_window,
            "label_horizon_days": label_horizon_days,
        },
    )


def _log_to_wandb(cards: list[FindingCard]) -> None:
    """Fire-and-forget W&B logging (Step 3 registry adapter; never required)."""
    import contextlib
    import importlib

    try:
        reg = importlib.import_module("equity_lake.ml.registry")
    except ImportError:
        return
    with contextlib.suppress(Exception):
        reg.log_comparison(cards)


def run_comparison(
    *,
    features: pl.DataFrame,
    ticker: str | None = None,
    backends: Sequence[str] = ("xgboost", "lightgbm"),
    modes: Sequence[str] = ("v1_direction", "v2_meta_label"),
    train_window: int = 252,
    test_window: int = 21,
    embargo_window: int = 1,
    label_horizon_days: int = 1,
    base: Path | None = None,
) -> list[FindingCard]:
    """Run the labeling + backend comparison and write two FindingCards.

    Pure: ``features`` is a single (per-ticker) frame; walk-forward folds are
    derived from its row count. Returns the two written cards
    (``meta-label-vs-direction``, ``xgb-vs-lgbm``).
    """
    by_mode: ModeBackendMetrics = {}
    importances_by_mode: ModeBackendImportances = {}
    for mode in modes:
        frame = _prepare_training_frame(features, mode)
        target_column = "meta_label" if mode == "v2_meta_label" else "target"
        # v2's triple barrier spans ``vertical_barrier_days``; purge the test
        # window by that horizon to avoid label leakage across folds.
        horizon = label_horizon_days
        if mode == "v2_meta_label":
            horizon = max(label_horizon_days, int(DEFAULT_V2_SETTINGS["vertical_barrier_days"]))
        splitter = PurgedEmbargoedWalkForwardSplitter(
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            label_horizon=horizon,
        )
        backend_metrics, backend_importances = _score_mode(frame, target_column, splitter=splitter, backends=backends)
        by_mode[mode] = backend_metrics
        importances_by_mode[mode] = backend_importances
        logger.info(
            "comparison_mode_scored",
            mode=mode,
            folds=_mode_folds(backend_metrics),
            backends=list(backends),
        )

    resolved_ticker = _resolve_ticker(features, ticker)
    cards = [
        _build_meta_label_card(
            by_mode,
            ticker=resolved_ticker,
            backends=backends,
            modes=modes,
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            label_horizon_days=label_horizon_days,
        ),
        _build_model_card(
            by_mode,
            importances_by_mode,
            ticker=resolved_ticker,
            backends=backends,
            modes=modes,
            train_window=train_window,
            test_window=test_window,
            embargo_window=embargo_window,
            label_horizon_days=label_horizon_days,
        ),
    ]
    for card in cards:
        write_finding_card(card, base=base)
    _log_to_wandb(cards)
    return cards


__all__ = ["run_comparison"]
