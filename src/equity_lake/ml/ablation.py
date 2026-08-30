"""Feature-enrichment ablation harness.

Step-4 deliverable producing the ``enrichment-ablation`` FindingCard
(axis ``ablation``, parent §6): do enriched features (macro/sentiment/SEC/analyst)
beat a technical-only feature set on out-of-sample accuracy?

The harness is **pure**: ``run_ablation`` takes the two pre-computed frames
(enriched and technical-only) and scores each arm over the *same* walk-forward
folds. The technical-only arm is produced upstream via
``FeatureEngineer.generate_features(..., include_macro=False)`` (parent §4 B6).

D12 caveat: the two arms carry different feature columns, so cross-scoring one
arm's model against the other's frame would trip the warn-only ``_check_feature_skew``
guard (``forecasting.py``). This harness scores each arm on its own columns but
emits the same warn-only ``feature_skew_detected`` log when the column sets
differ, and documents the caveat in the card conclusion.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import polars as pl
import structlog

from equity_lake.findings.models import FindingCard, FindingVerdict
from equity_lake.findings.writer import write_finding_card
from equity_lake.ml._metrics import DEFAULT_FIT_PARAMS, aggregate_oos, feature_columns, log_to_wandb, resolve_ticker, scale_pos_weight
from equity_lake.ml.backends import build_estimator, fit_estimator
from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter

logger = structlog.get_logger(__name__)

#: Margin (in accuracy units) below which the two arms are considered tied.
_EPS: float = 0.01


def _feature_columns_for(df: pl.DataFrame) -> list[str]:
    return feature_columns(df)


def _score_arm(
    df: pl.DataFrame,
    feature_cols: list[str],
    *,
    folds: list[tuple[np.ndarray, np.ndarray]],
) -> dict[str, float]:
    """Train per-fold models on this arm's own features and pool OOS metrics.

    ``df`` must already be row-aligned to ``folds`` (same height, non-null
    ``next_day_return``); the caller is responsible for that alignment.
    """
    if not feature_cols or df.is_empty():
        return aggregate_oos(np.array([]), np.array([]), 0)

    x_all = df.select([pl.col(col).cast(pl.Float64, strict=False).alias(col) for col in feature_cols]).to_numpy()
    y_all = (df["next_day_return"] > 0).cast(pl.Int64).to_numpy()

    oos_labels: list[int] = []
    oos_probs: list[float] = []
    for train_idx, test_idx in folds:
        x_tr, y_tr = x_all[train_idx], y_all[train_idx]
        x_te, y_te = x_all[test_idx], y_all[test_idx]
        model = build_estimator("xgboost", dict(DEFAULT_FIT_PARAMS), scale_pos_weight=scale_pos_weight(y_tr))
        fit_estimator(model, x_tr, y_tr, verbose=False)
        proba = model.predict_proba(x_te)[:, 1]
        oos_probs.extend(float(p) for p in proba)
        oos_labels.extend(int(v) for v in y_te)

    return aggregate_oos(np.asarray(oos_labels), np.asarray(oos_probs), len(folds))


def _build_ablation_card(
    enriched: dict[str, float],
    technical: dict[str, float],
    *,
    ticker: str,
    enriched_feature_count: int,
    technical_feature_count: int,
    train_window: int,
    test_window: int,
    embargo_window: int,
    label_horizon_days: int,
    skew_detected: bool,
) -> FindingCard:
    enriched_acc = float(enriched.get("oos_accuracy", 0.0))
    technical_acc = float(technical.get("oos_accuracy", 0.0))
    enriched_prec = float(enriched.get("oos_precision", 0.0))
    technical_prec = float(technical.get("oos_precision", 0.0))
    folds = int(max(float(enriched.get("folds", 0.0)), float(technical.get("folds", 0.0))))
    delta = enriched_acc - technical_acc

    skew_note = " Cross-scoring arms trips warn-only feature-skew (D12)." if skew_detected else ""

    verdict: FindingVerdict
    if folds == 0:
        verdict = "inconclusive"
        conclusion = "Insufficient OOS folds to compare feature arms; rerun over a longer feature history."
    elif delta > _EPS:
        verdict = "positive"
        conclusion = f"Enriched features beat technical-only on OOS accuracy by {delta:+.3f} ({technical_acc:.3f} -> {enriched_acc:.3f}).{skew_note}"
    elif delta < -_EPS:
        verdict = "negative"
        conclusion = f"Enriched features did not help: accuracy fell by {delta:+.3f} ({technical_acc:.3f} -> {enriched_acc:.3f}).{skew_note}"
    else:
        verdict = "inconclusive"
        conclusion = (
            f"Feature arms tied on accuracy ({technical_acc:.3f} vs "
            f"{enriched_acc:.3f}, delta {delta:+.3f}); no enrichment benefit detected.{skew_note}"
        )

    metrics = {
        "enriched_accuracy": enriched_acc,
        "technical_accuracy": technical_acc,
        "accuracy_delta": float(delta),
        "enriched_precision": enriched_prec,
        "technical_precision": technical_prec,
        "enriched_brier": float(enriched.get("oos_brier", 0.0)),
        "technical_brier": float(technical.get("oos_brier", 0.0)),
        "enriched_feature_count": float(enriched_feature_count),
        "technical_feature_count": float(technical_feature_count),
        "folds": float(folds),
    }
    return FindingCard(
        id="enrichment-ablation",
        axis="ablation",
        claim="Enriched features (macro/sentiment/SEC/analyst) improve OOS accuracy over technical-only features.",
        verdict=verdict,
        conclusion=conclusion,
        metrics=metrics,
        evidence_refs=[],
        run_date=date.today(),
        scope={
            "tickers": [ticker],
            "train_window": train_window,
            "test_window": test_window,
            "embargo_window": embargo_window,
            "label_horizon_days": label_horizon_days,
            "feature_skew_warn_only": True,
        },
    )


def run_ablation(
    *,
    enriched_features: pl.DataFrame,
    technical_features: pl.DataFrame,
    ticker: str | None = None,
    train_window: int = 252,
    test_window: int = 21,
    embargo_window: int = 1,
    label_horizon_days: int = 1,
    base: Path | None = None,
) -> FindingCard:
    """Score enriched vs technical-only features on the same OOS folds.

    Both frames must be row-aligned (same dates/OHLCV); the technical arm is the
    enriched frame regenerated with ``include_macro=False``. The same
    :class:`PurgedEmbargoedWalkForwardSplitter` folds are applied to both arms
    so the comparison is apple-to-apple. Writes and returns the card.
    """
    enriched_df = enriched_features.sort("date").filter(pl.col("next_day_return").is_not_null())
    technical_df = technical_features.sort("date").filter(pl.col("next_day_return").is_not_null())

    enriched_cols = _feature_columns_for(enriched_df)
    technical_cols = _feature_columns_for(technical_df)
    skew_detected = set(enriched_cols) != set(technical_cols)
    if skew_detected:
        # D12: warn-only (mirrors ``forecasting._check_feature_skew``). Cross-
        # scoring one arm's model on the other's frame would repeat this warning.
        logger.warning(
            "feature_skew_detected",
            enriched_feature_count=len(enriched_cols),
            technical_feature_count=len(technical_cols),
            only_in_enriched=sorted(set(enriched_cols) - set(technical_cols)),
            only_in_technical=sorted(set(technical_cols) - set(enriched_cols)),
        )

    # Same folds for both arms: split on the shared row count so OOS windows line up.
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=train_window,
        test_window=test_window,
        embargo_window=embargo_window,
        label_horizon=label_horizon_days,
    )
    shared_n = min(enriched_df.height, technical_df.height)
    folds = list(splitter.split(range(shared_n)))

    enriched_metrics = _score_arm(enriched_df.head(shared_n), enriched_cols, folds=folds)
    technical_metrics = _score_arm(technical_df.head(shared_n), technical_cols, folds=folds)
    logger.info(
        "ablation_scored",
        folds=len(folds),
        enriched_accuracy=enriched_metrics["oos_accuracy"],
        technical_accuracy=technical_metrics["oos_accuracy"],
        skew_detected=skew_detected,
    )

    resolved_ticker = resolve_ticker(enriched_features, ticker)
    card = _build_ablation_card(
        enriched_metrics,
        technical_metrics,
        ticker=resolved_ticker,
        enriched_feature_count=len(enriched_cols),
        technical_feature_count=len(technical_cols),
        train_window=train_window,
        test_window=test_window,
        embargo_window=embargo_window,
        label_horizon_days=label_horizon_days,
        skew_detected=skew_detected,
    )
    write_finding_card(card, base=base)
    log_to_wandb([card])
    return card


__all__ = ["run_ablation"]
