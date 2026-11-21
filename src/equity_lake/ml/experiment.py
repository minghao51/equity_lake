"""R02: one frozen incremental ML experiment on vendor-stamped Gold rows.

ADR-0034 authorizes ML **research** on the real root's ``vendor_events_no_clock``
Gold rows, and nothing else. This module is that one experiment (spec §7 R02): it
freezes one R00 protocol plus one prespecification, fits exactly one primary model
once, and evaluates it against a technical-rule baseline and two further baselines
on matched pre-holdout dates. It promotes nothing, refits nothing, tunes nothing and
never reads the holdout.

The contract, in the order it is enforced:

* **Freeze first** — :func:`freeze_r02_protocol` mints the dated R00 manifest whose
  digest is the experiment's identity; :func:`run_r02` refuses to run unless that
  exact digest (and the prespecification digest bound into it) is already frozen.
* **One basis** — features are read through
  :class:`~equity_lake.ml.feature_loader.FeatureLoader` with the explicit weak
  ``vendor_events_no_clock`` opt-in. A frame carrying any other basis refuses, so no
  artifact can mix bases.
* **One fit, pre-holdout only** — the read window ends the session before the frozen
  holdout starts, so no holdout price reaches a feature or a label. The primary
  model is fitted once on the train fold and applied unchanged to the validation and
  test folds; the folds are separated by label maturity and purging (a row is admitted
  only when its own label matures inside its own fold) plus the frozen embargo.
* **Weakness travels** — every artifact (protocol record, metrics, predictions,
  baselines, report) carries ``coverage_basis = vendor_events_no_clock`` and the fixed
  ADR-0034 decision 3 disclosure. Prediction rows are never written to
  ``04_platinum/predictions``; everything lands under ``data/research/r02/``.

The uncertainty method (moving-block bootstrap, block = 5 sessions, fixed seed), the
decision threshold, the cost model, the baselines and the promotion criteria are all
part of the prespecification digest, so they cannot be chosen after seeing a result.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from equity_lake.backtesting.evidence import CostApplication
from equity_lake.backtesting.protocol import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    FrozenProtocol,
    ProtocolError,
    ResearchProtocol,
    ResourceBudget,
    freeze_protocol,
    protocol_binding,
    require_frozen_protocol,
    require_run_conformance,
)
from equity_lake.core.availability import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    VENDOR_EVENTS_NO_CLOCK_DISCLOSURE,
    AvailabilityContractError,
    CoverageBasis,
)
from equity_lake.core.instruments import InstrumentKey  # pyright: ignore[reportMissingImports] -- source import verified by uv
from equity_lake.core.paths import DATA_DIR
from equity_lake.ml.backends import DEFAULT_BACKEND, DEFAULT_XGB_PARAMS, build_estimator, fit_estimator, scale_pos_weight
from equity_lake.ml.feature_loader import FeatureLoader

logger = structlog.get_logger(__name__)

#: The one experiment identity this module may run (R02, spec §7).
R02_EXPERIMENT = "R02-ml-research-2024"
#: The only coverage basis R02 admits (ADR-0034 decision 1).
R02_BASIS: CoverageBasis = "vendor_events_no_clock"
#: The fixed disclosure every artifact carries verbatim (ADR-0034 decision 3).
R02_DISCLOSURE = VENDOR_EVENTS_NO_CLOCK_DISCLOSURE

#: Frozen universe (canonical display keys). AAPL/MSFT/NVDA on XNAS, JPM/KO on XNYS.
R02_UNIVERSE: tuple[str, ...] = (
    "us_equity/XNAS/AAPL",
    "us_equity/XNAS/MSFT",
    "us_equity/XNAS/NVDA",
    "us_equity/XNYS/JPM",
    "us_equity/XNYS/KO",
)

#: Frozen technical feature list — the Gold technical indicators only. Calendar
#: dummies and macro columns are deliberately excluded: they are not "the Gold
#: technical features" the hypothesis names, and including them would answer a
#: different question than the one frozen here.
R02_FEATURES: tuple[str, ...] = (
    "atr_14",
    "bb_middle",
    "bb_pct",
    "bb_upper",
    "bb_lower",
    "bb_width",
    "hl_range",
    "intraday_return",
    "macd",
    "macd_histogram",
    "macd_signal",
    "obv",
    "overnight_return",
    "return_1d",
    "return_5d",
    "return_10d",
    "return_20d",
    "roc_5",
    "roc_10",
    "roc_20",
    "rsi_14",
    "volatility_20",
    "volume_ma_20",
    "volume_ratio",
    "volume_roc_5",
)

COST_REGIME = "realistic"
FEE_RATIO = 0.001425
TAX_RATIO = 0.003
#: Round-trip cost charged per held name per holding block: a buy fee, a sell fee and
#: the sell tax. Frozen here so the metric never depends on how a cost is read.
ROUND_TRIP_COST = 2.0 * FEE_RATIO + TAX_RATIO
#: Decision threshold on the predicted probability (a long/flat policy, never short).
DECISION_THRESHOLD = 0.5
#: Sessions a signal is held before the equal-weight block rebalances (the label's
#: own horizon, so the bet matches the target).
HOLDING_SESSIONS = 5

#: The primary model: gradient boosting through the existing backend seam, with fixed
#: hyperparameters (no tuning, no refit, no sweep). LightGBM is not installed, so the
#: default XGBoost backend is the supported gradient-boosting implementation.
PRIMARY_MODEL = DEFAULT_BACKEND
PRIMARY_MODEL_PARAMS: dict[str, Any] = {
    "max_depth": 3,
    "learning_rate": 0.1,
    "n_estimators": 50,
    **DEFAULT_XGB_PARAMS,
    "n_jobs": 1,  # one thread: bit-reproducible fits
}
#: The simple-model baseline: plain logistic regression on the same features.
SIMPLE_MODEL = "logistic_regression"
SIMPLE_MODEL_PARAMS: dict[str, Any] = {"C": 1.0, "max_iter": 1000, "solver": "lbfgs"}

#: Prespecified uncertainty: moving-block bootstrap over evaluation sessions.
BOOTSTRAP_METHOD = "moving_block_bootstrap"
BOOTSTRAP_BLOCK_SESSIONS = 5
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261121
CONFIDENCE_LEVEL = 0.95

TECHNICAL_RULE = "momentum_20_session_sign"
NO_SKILL = "no_skill_base_rate"
SIMPLE_BASELINE = "simple_model_logistic_regression"

METRICS: tuple[str, ...] = ("brier_score", "roc_auc", "net_return", "sharpe_ratio", "max_drawdown")

#: Absolute-path-free, research-only results root (auxiliary storage, never cataloged).
R02_RESULTS_DIRNAME = "r02"


class R02Error(ValueError):
    """The R02 experiment refused: basis, protocol, fold or artifact scope is wrong."""


# ---------------------------------------------------------------------------
# Prespecification (digested into the frozen protocol's input digests)
# ---------------------------------------------------------------------------


class R02Spec(BaseModel):
    """Everything R02 decides *before* it runs, and nothing it may decide after.

    The split, the label, the feature list, the two models with their fixed
    hyperparameters, the baselines, the cost model, the uncertainty method, the
    promotion criteria and the decision threshold live here. Its canonical JSON
    digest is bound into the frozen manifest, so a run under a changed
    prespecification is refused rather than silently reinterpreted.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    experiment: str
    universe: list[str]
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    holdout_start: date
    holdout_end: date
    features: list[str]
    label: str
    label_horizon_sessions: int
    purge_sessions: int
    embargo_sessions: int
    primary_model: str
    primary_model_params: dict[str, Any]
    simple_model: str
    simple_model_params: dict[str, Any]
    technical_rule: str
    no_skill_baseline: str
    decision_threshold: float
    holding_sessions: int
    cost_regime: str
    fee_ratio: float
    tax_ratio: float
    round_trip_cost: float
    uncertainty: dict[str, Any]
    promotion_criteria: list[str]
    promoted: bool = False

    @field_validator("universe", "features")
    @classmethod
    def _canonical(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("a frozen prespecification needs at least one identity")
        return sorted(set(value))

    @field_validator("decision_threshold")
    @classmethod
    def _open_unit_interval(cls, value: float) -> float:
        if not 0.0 < value < 1.0:
            raise ValueError("the decision threshold must lie strictly between 0 and 1")
        return value

    @model_validator(mode="after")
    def _ordered_and_pre_holdout(self) -> R02Spec:
        if not (self.train_start <= self.train_end < self.validation_start <= self.validation_end < self.test_start <= self.test_end):
            raise ValueError("folds must be ordered and non-overlapping: train < validation < test")
        if not (self.test_end < self.holdout_start <= self.holdout_end):
            raise ValueError("every fold must end before the holdout starts; R02 never evaluates inside the holdout")
        if self.promoted:
            raise ValueError("R02 promotes nothing: 'promoted' must be false in the prespecification (ADR-0034 decision 4)")
        return self

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json"), separators=(",", ":"), sort_keys=True, ensure_ascii=True, allow_nan=False)

    def digest(self) -> str:
        import hashlib

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def r02_spec() -> R02Spec:
    """The frozen R02 prespecification (never chosen after seeing a result)."""
    return R02Spec(
        experiment=R02_EXPERIMENT,
        universe=list(R02_UNIVERSE),
        train_start=date(2024, 1, 2),
        train_end=date(2024, 3, 31),
        validation_start=date(2024, 4, 1),
        validation_end=date(2024, 6, 30),
        test_start=date(2024, 7, 1),
        test_end=date(2024, 9, 30),
        holdout_start=date(2024, 10, 1),
        holdout_end=date(2024, 12, 31),
        features=list(R02_FEATURES),
        label="sign of the 5-session forward close-to-close return (1 when the forward return is strictly positive)",
        label_horizon_sessions=HOLDING_SESSIONS,
        purge_sessions=5,
        embargo_sessions=1,
        primary_model=PRIMARY_MODEL,
        primary_model_params=dict(PRIMARY_MODEL_PARAMS),
        simple_model=SIMPLE_MODEL,
        simple_model_params=dict(SIMPLE_MODEL_PARAMS),
        technical_rule=TECHNICAL_RULE,
        no_skill_baseline=NO_SKILL,
        decision_threshold=DECISION_THRESHOLD,
        holding_sessions=HOLDING_SESSIONS,
        cost_regime=COST_REGIME,
        fee_ratio=FEE_RATIO,
        tax_ratio=TAX_RATIO,
        round_trip_cost=ROUND_TRIP_COST,
        uncertainty={
            "method": BOOTSTRAP_METHOD,
            "block_sessions": BOOTSTRAP_BLOCK_SESSIONS,
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
            "confidence_level": CONFIDENCE_LEVEL,
        },
        promotion_criteria=[
            "calibration: primary test Brier is lower than both the no-skill base rate and the technical rule on matched dates",
            "discrimination: primary test ROC AUC >= 0.55 and the block-bootstrap 95% interval lower bound is above 0.50",
            "economics: primary test net return exceeds the technical rule on matched dates and the "
            "bootstrap interval of the paired difference excludes zero",
            "risk: primary test Sharpe exceeds the technical rule by at least the frozen acceptance tolerance (0.05)",
            "consistency: the sign of the primary-minus-technical net-return difference agrees on the validation and test folds",
            "none of the above promotes anything: R02 is research evidence (ADR-0034 decisions 4-5) and this stage promotes nothing",
        ],
        promoted=False,
    )


def r02_protocol() -> ResearchProtocol:
    """The frozen R00 protocol R02 runs under (universe, window, split inputs, costs)."""
    spec = r02_spec()
    return ResearchProtocol(
        experiment=R02_EXPERIMENT,
        universe=list(spec.universe),
        start_date=date(2024, 1, 2),
        end_date=date(2024, 12, 31),
        holdout_start=spec.holdout_start,
        holdout_end=spec.holdout_end,
        entry_clock="session_close",
        entry_price="close",
        label_horizon_days=spec.label_horizon_sessions,
        label_maturity_days=spec.label_horizon_sessions,
        purge_days=spec.purge_sessions,
        embargo_days=spec.embargo_sessions,
        costs=[CostApplication(regime=COST_REGIME, fee_ratio=FEE_RATIO, tax_ratio=TAX_RATIO)],
        metrics=list(METRICS),
        baselines=[NO_SKILL, SIMPLE_BASELINE, TECHNICAL_RULE],
        acceptance_tolerances={"brier_score": 0.0, "net_return": 0.0, "roc_auc": 0.0, "sharpe_ratio": 0.05},
        generator=None,
        seeds=[],
        resource_budget=ResourceBudget(max_runtime_seconds=900.0, max_symbols=5, max_sessions=252),
    )


def results_root(root: Path | None = None) -> Path:
    """``data/research/r02`` — auxiliary research storage, never a lake table."""
    return (Path(root) if root is not None else DATA_DIR) / "research" / R02_RESULTS_DIRNAME


def freeze_r02_protocol(
    *,
    recorded_at: datetime,
    root: Path | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[FrozenProtocol, Path]:
    """Freeze the R02 protocol, binding the prespecification digest into it.

    ``recorded_at`` is mandatory: the manifest bytes are a pure function of the
    inputs (including that one timestamp), so the freeze is byte-reproducible.
    """
    spec = r02_spec()
    return freeze_protocol(
        r02_protocol(),
        recorded_at=recorded_at,
        root=root,
        config=dict(config) if config is not None else None,
        input_digests={"r02_prespecification": spec.digest()},
    )


# ---------------------------------------------------------------------------
# Feature read (weak basis, explicit, pre-holdout only)
# ---------------------------------------------------------------------------


def _instruments(universe: Sequence[str]) -> list[InstrumentKey]:
    instruments: list[InstrumentKey] = []
    for display_key in universe:
        parts = display_key.split("/")
        if len(parts) != 3:
            raise R02Error(f"frozen universe entry {display_key!r} is not a canonical display key")
        instruments.append(InstrumentKey(market=parts[0], mic=parts[1], symbol=parts[2]))
    return instruments


def _load_features(
    *,
    lake_dir: Path,
    instruments: Sequence[InstrumentKey],
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Read the weak-basis Gold features for the R02 universe, one root, one basis."""
    loader = FeatureLoader(lake_dir=lake_dir, coverage_basis=R02_BASIS)
    frames: list[pl.DataFrame] = []
    for instrument in instruments:
        frame = loader.load_features(instrument, start_date, end_date)
        if frame.is_empty():
            raise R02Error(f"{instrument.display_key}: no Gold feature rows in {start_date}..{end_date}; R02 has no input to evaluate")
        frames.append(frame)
    features = pl.concat(frames, how="vertical_relaxed")
    if "action_coverage_basis" not in features.columns:
        raise AvailabilityContractError("Gold feature rows carry no action-coverage basis stamp; a weak-basis read cannot be established")
    bases = sorted({str(value) for value in features["action_coverage_basis"].to_list() if value is not None})
    if bases != [R02_BASIS]:
        raise R02Error(f"R02 admits exactly the {R02_BASIS!r} basis; the frame carries {bases}. A scope mixing bases refuses (ADR-0034 decision 3)")
    holdout_rows = features.filter(pl.col("date") >= pl.lit(date(2024, 10, 1))).height
    if holdout_rows:
        raise R02Error(f"{holdout_rows} feature rows fall inside the holdout; R02 never reads the holdout")
    missing = sorted(set(R02_FEATURES) - set(features.columns))
    if missing:
        raise R02Error(f"frozen technical features are missing from the Gold rows: {missing}")
    return features.sort(["market", "mic", "symbol", "date"])


def _label_rows(features: pl.DataFrame, horizon: int) -> pl.DataFrame:
    """Attach the frozen label and its maturity date, strictly inside the read window."""
    identity = ["market", "mic", "symbol"]
    forward = pl.col("close").shift(-horizon).over(identity) / pl.col("close") - 1.0
    mature = pl.col("date").shift(-horizon).over(identity)
    labelled = features.with_columns(
        forward.alias("forward_return"),
        mature.alias("label_end_date"),
    ).with_columns(pl.when(pl.col("forward_return").is_null()).then(None).otherwise((pl.col("forward_return") > 0).cast(pl.Int8)).alias("label"))
    return labelled.sort([*identity, "date"])


# ---------------------------------------------------------------------------
# Folds: maturation, purging, embargo (evidence per fold, not just row counts)
# ---------------------------------------------------------------------------


def _fold_bounds(spec: R02Spec) -> dict[str, tuple[date, date]]:
    return {
        "train": (spec.train_start, spec.train_end),
        "validation": (spec.validation_start, spec.validation_end),
        "test": (spec.test_start, spec.test_end),
    }


def _assign_folds(labelled: pl.DataFrame, spec: R02Spec) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Assign each labelled row to one fold, purging and embargoing by session.

    A row is admitted to a fold only when it is labelled (its forward window is
    complete inside the read window) and its own label matures inside that same
    fold, so no fold's label reaches into the next fold's window — the interval-aware
    purge spec §7 R02 asks for, expressed in sessions rather than row counts. The
    first ``embargo_sessions`` sessions of each evaluation fold are dropped.
    """
    identity = ["market", "mic", "symbol"]
    bounds = _fold_bounds(spec)
    parts: list[pl.DataFrame] = []
    evidence: dict[str, Any] = {}
    for name, (start, end) in bounds.items():
        fold = labelled.filter(pl.col("date").is_between(start, end, closed="both") & pl.col("label").is_not_null())
        # Session-index embargo: drop the fold's first `embargo_sessions` sessions.
        if spec.embargo_sessions > 0:
            session_rank = pl.col("date").rank("dense").over(identity)
            fold = fold.filter(session_rank > spec.embargo_sessions)
        # Maturation/purge: the label must mature inside this fold's own window.
        overlap = fold.filter(pl.col("label_end_date") > pl.lit(end)).height
        fold = fold.filter(pl.col("label_end_date") <= pl.lit(end))
        if fold.is_empty():
            raise R02Error(f"the {name} fold has no labelled, mature rows; R02 cannot be evaluated as frozen")
        parts.append(fold.with_columns(pl.lit(name).alias("fold")))
        evidence[name] = {
            "rows": fold.height,
            "instruments": fold.select(identity).unique().height,
            "decision_date_min": str(fold["date"].min()),
            "decision_date_max": str(fold["date"].max()),
            "label_end_date_min": str(fold["label_end_date"].min()),
            "label_end_date_max": str(fold["label_end_date"].max()),
            "rows_purged_crossing_fold_end": overlap,
            "sessions_embargoed": spec.embargo_sessions,
            "label_maturity_sessions": spec.label_horizon_sessions,
        }
    assigned = pl.concat(parts, how="vertical_relaxed")

    # Explicit purge gap: the train fold's last label maturity must precede the
    # validation fold's first decision session, and likewise validation → test.
    for earlier, later in (("train", "validation"), ("validation", "test")):
        raw_last = assigned.filter(pl.col("fold") == earlier)["label_end_date"].max()
        last_label_end = raw_last if isinstance(raw_last, date) else None
        next_start = bounds[later][0]
        if last_label_end is not None and last_label_end >= next_start:
            raise R02Error(
                f"purging failed: the {earlier} fold's last label matures {last_label_end}, on or after the {later} fold starts {next_start}"
            )
        evidence[earlier]["purge_gap_to_next_fold_days"] = (next_start - last_label_end).days if last_label_end else None
    for name, (start, end) in bounds.items():
        evidence[name]["window"] = f"{start}..{end}"
    evidence["holdout"] = {"window": f"{spec.holdout_start}..{spec.holdout_end}", "rows_read": 0}
    return assigned, evidence


def _standardise(frame: pl.DataFrame, spec: R02Spec) -> tuple[np.ndarray, dict[str, Any]]:
    """Standardise the frozen features per instrument, using train-window statistics.

    Per-instrument z-scores keep OBV/price-level features comparable across the five
    instruments without letting any validation or test observation set the scale: the
    mean and standard deviation come from the train fold only. Missing values
    (feature warm-up) become the train mean, i.e. 0.0 in z-space.
    """
    identity = ["market", "mic", "symbol"]
    train = frame.filter(pl.col("fold") == "train")
    stats_frame = train.group_by(identity).agg(
        [pl.col(column).mean().alias(f"{column}__mean") for column in spec.features]
        + [pl.col(column).std().alias(f"{column}__std") for column in spec.features]
    )
    stats: dict[str, Any] = {}
    for column in spec.features:
        per_instrument = stats_frame[f"{column}__std"].fill_null(0.0)
        stats[column] = {
            "train_null_rows": int(train[column].null_count()),
            "train_instruments_with_zero_spread": int(per_instrument.eq(0.0).sum()),
        }
    scaled = frame.join(stats_frame, on=identity, how="left")
    expressions: list[pl.Expr] = []
    for column in spec.features:
        std = pl.col(f"{column}__std")
        mean = pl.col(f"{column}__mean")
        expressions.append(
            pl.when(std.is_null() | (std == 0.0) | pl.col(column).is_null())
            .then(pl.lit(0.0))
            .otherwise((pl.col(column) - mean) / std)
            .cast(pl.Float64)
            .alias(column)
        )
    matrix = scaled.select(*expressions).to_numpy().astype(np.float64, copy=False)
    if not np.isfinite(matrix).all():
        raise R02Error("the standardised design matrix contains non-finite values; the feature set is not usable as frozen")
    return matrix, stats


# ---------------------------------------------------------------------------
# Baselines and the one fit
# ---------------------------------------------------------------------------


def _technical_rule(frame: pl.DataFrame) -> np.ndarray:
    """20-session momentum sign as a hard 0/1 forecast (no fitting at all)."""
    return (frame["roc_20"] > 0).cast(pl.Float64).fill_null(0.0).to_numpy()


def _fit_primary(matrix: np.ndarray, labels: np.ndarray, train_mask: np.ndarray) -> np.ndarray:
    model = build_estimator(PRIMARY_MODEL, dict(PRIMARY_MODEL_PARAMS), scale_pos_weight=scale_pos_weight(labels[train_mask]))
    fit_estimator(model, matrix[train_mask], labels[train_mask], verbose=False)
    return np.asarray(model.predict_proba(matrix)[:, 1], dtype=np.float64)


def _fit_simple(matrix: np.ndarray, labels: np.ndarray, train_mask: np.ndarray) -> np.ndarray:
    model = LogisticRegression(**SIMPLE_MODEL_PARAMS)
    model.fit(matrix[train_mask], labels[train_mask])
    return np.asarray(model.predict_proba(matrix)[:, 1], dtype=np.float64)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _discrimination(labels: np.ndarray, probs: np.ndarray) -> dict[str, float | None]:
    if labels.size == 0:
        return {"rows": 0.0, "before": None}
    brier = float(np.mean((probs - labels) ** 2))
    auc: float | None = float(roc_auc_score(labels, probs)) if len(np.unique(labels)) > 1 else None
    return {
        "rows": float(labels.size),
        "brier_score": brier,
        "roc_auc": auc,
        "accuracy": float(np.mean((probs >= DECISION_THRESHOLD).astype(np.int64) == labels.astype(np.int64))),
        "mean_probability": float(probs.mean()),
        "base_rate": float(labels.mean()),
    }


def _calibration_bins(labels: np.ndarray, probs: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0.0, 1.0, bins + 1)
    table: list[dict[str, float]] = []
    for index in range(bins):
        lower, upper = float(edges[index]), float(edges[index + 1])
        mask = (probs >= lower) & (probs < upper if index < bins - 1 else probs <= upper)
        count = int(mask.sum())
        table.append(
            {
                "bin_lower": lower,
                "bin_upper": upper,
                "count": float(count),
                "mean_probability": float(probs[mask].mean()) if count else float("nan"),
                "observed_frequency": float(labels[mask].mean()) if count else float("nan"),
            }
        )
    return table


def _holding_blocks(sessions: Sequence[date], holding: int) -> list[tuple[date, list[date]]]:
    ordered = list(sessions)
    return [(ordered[index], ordered[index : index + holding]) for index in range(0, len(ordered), holding)]


def _strategy_returns(
    *,
    picks: Mapping[date, Sequence[tuple[str, str, str]]],
    sessions: Sequence[date],
    closes: Mapping[tuple[str, str, str], Mapping[date, float]],
    cost: float,
    holding: int,
) -> pl.DataFrame:
    """Daily net returns of a long/flat equal-weight policy on non-overlapping blocks."""
    following = {sessions[index]: sessions[index + 1] for index in range(len(sessions) - 1)}
    dates_seen: list[date] = []
    values: list[float] = []
    held_counts: list[int] = []
    for block_start, block in _holding_blocks(sorted(picks), holding):
        held = list(picks[block_start])
        for offset, session in enumerate(block):
            later = following.get(session)
            if later is None:
                continue
            realised: list[float] = []
            for key in held:
                series = closes.get(key)
                if series is None:
                    continue
                first, last = series.get(session), series.get(later)
                if first is None or last is None or first <= 0.0:
                    continue
                realised.append(last / first - 1.0)
            gross = float(np.mean(realised)) if realised else 0.0
            charged = cost if held else 0.0
            dates_seen.append(session)
            values.append(gross - (charged if offset == 0 else 0.0))
            held_counts.append(len(realised))
    return pl.DataFrame(
        {
            "date": pl.Series(dates_seen, dtype=pl.Date),
            "net_return": pl.Series(values, dtype=pl.Float64),
            "held": pl.Series(held_counts, dtype=pl.Int64),
        }
    )


def _economic_summary(returns: pl.DataFrame) -> dict[str, Any]:
    values = returns["net_return"].to_numpy().astype(np.float64, copy=False)
    if values.size == 0:
        return {"sessions": 0.0, "net_return": 0.0, "sharpe_ratio": 0.0, "max_drawdown": 0.0, "mean_daily_return": 0.0, "mean_positions": 0.0}
    equity = np.cumprod(1.0 + values)
    peak = np.maximum.accumulate(equity)
    std = float(np.std(values, ddof=1)) if values.size > 1 else 0.0
    return {
        "sessions": float(values.size),
        "net_return": float(equity[-1] - 1.0),
        "sharpe_ratio": float(np.mean(values) / std * math.sqrt(252.0)) if std > 0.0 else 0.0,
        "max_drawdown": float(np.min(equity / peak - 1.0)),
        "mean_daily_return": float(np.mean(values)),
        "mean_positions": float(np.mean(returns["held"].to_numpy().astype(np.float64))),
    }


def _block_positions(size: int, block: int, resamples: int, seed: int) -> np.ndarray:
    """Moving-block bootstrap position matrix ``(resamples, size)``."""
    rng = np.random.default_rng(seed)
    blocks = max(1, math.ceil(size / block))
    starts = rng.integers(0, max(1, size - block + 1), size=(resamples, blocks))
    return (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(resamples, blocks * block)[:, :size]


def _interval(bootstrap: np.ndarray, level: float = CONFIDENCE_LEVEL) -> dict[str, float]:
    tail = (1.0 - level) / 2.0
    finite = bootstrap[np.isfinite(bootstrap)]
    if finite.size == 0:
        return {"lower": float("nan"), "upper": float("nan")}
    return {"lower": float(np.quantile(finite, tail)), "upper": float(np.quantile(finite, 1.0 - tail))}


def _bootstrap_economics(returns: pl.DataFrame, seed: int) -> dict[str, dict[str, float]]:
    values = returns["net_return"].to_numpy().astype(np.float64, copy=False)
    if values.size < BOOTSTRAP_BLOCK_SESSIONS:
        return {name: {"lower": float("nan"), "upper": float("nan")} for name in ("net_return", "sharpe_ratio", "max_drawdown")}
    positions = _block_positions(values.size, BOOTSTRAP_BLOCK_SESSIONS, BOOTSTRAP_RESAMPLES, seed)
    sample = values[positions]
    equity = np.cumprod(1.0 + sample, axis=1)
    net = equity[:, -1] - 1.0
    std = sample.std(axis=1, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        sharpe = np.where(std > 0.0, sample.mean(axis=1) / std * math.sqrt(252.0), 0.0)
    peak = np.maximum.accumulate(equity, axis=1)
    drawdown = np.min(equity / peak - 1.0, axis=1)
    return {
        "net_return": _interval(net),
        "sharpe_ratio": _interval(sharpe),
        "max_drawdown": _interval(drawdown),
    }


def _bootstrap_paired_difference(primary: pl.DataFrame, technical: pl.DataFrame, seed: int) -> dict[str, float]:
    paired = technical.rename({"net_return": "technical"}).select("date", "technical")
    merged = primary.rename({"net_return": "primary"}).join(paired, on="date", how="inner")
    difference = (merged["primary"] - merged["technical"]).to_numpy().astype(np.float64, copy=False)
    if difference.size < BOOTSTRAP_BLOCK_SESSIONS:
        return {"lower": float("nan"), "upper": float("nan")}
    positions = _block_positions(difference.size, BOOTSTRAP_BLOCK_SESSIONS, BOOTSTRAP_RESAMPLES, seed)
    return _interval(difference[positions].mean(axis=1))


def _bootstrap_discrimination(
    *,
    dates: Sequence[date],
    row_start: Mapping[date, int],
    row_count: Mapping[date, int],
    labels: np.ndarray,
    probs: np.ndarray,
    seed: int,
) -> dict[str, dict[str, float]]:
    ordered = list(dates)
    if len(ordered) < BOOTSTRAP_BLOCK_SESSIONS:
        return {name: {"lower": float("nan"), "upper": float("nan")} for name in ("brier_score", "roc_auc")}
    positions = _block_positions(len(ordered), BOOTSTRAP_BLOCK_SESSIONS, BOOTSTRAP_RESAMPLES, seed)
    starts = np.array([row_start[ordered[pos]] for pos in range(len(ordered))], dtype=np.int64)
    counts = np.array([row_count[ordered[pos]] for pos in range(len(ordered))], dtype=np.int64)
    padded = int(counts.max())
    offsets = np.arange(padded)[None, None, :]
    gather = starts[positions][:, :, None] + offsets
    mask = offsets < counts[positions][:, :, None]
    safe = np.where(mask, gather, 0)
    flat_mask = mask.reshape(mask.shape[0], -1)
    flat_labels = np.where(flat_mask, labels[safe].reshape(flat_mask.shape), 0.0)
    flat_probs = np.where(flat_mask, probs[safe].reshape(flat_mask.shape), 0.0)
    weight = flat_mask.sum(axis=1)
    brier = ((flat_probs - flat_labels) ** 2 * flat_mask).sum(axis=1) / np.maximum(weight, 1)
    aucs = np.full(flat_labels.shape[0], np.nan)
    for index in range(flat_labels.shape[0]):
        selected = flat_labels[index, flat_mask[index]]
        scores = flat_probs[index, flat_mask[index]]
        if selected.size and len(np.unique(selected)) > 1:
            aucs[index] = float(roc_auc_score(selected, scores))
    return {"brier_score": _interval(brier), "roc_auc": _interval(aucs)}


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------


class _ArtifactBase(BaseModel):
    """A research artifact that cannot exist without its basis and its disclosure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    coverage_basis: str = R02_BASIS
    disclosure: str = R02_DISCLOSURE
    protocol_digest: str
    prespecification_digest: str
    experiment: str = R02_EXPERIMENT
    promoted: bool = False

    @field_validator("coverage_basis")
    @classmethod
    def _one_basis(cls, value: str) -> str:
        if value != R02_BASIS:
            raise ValueError(
                f"R02 artifacts record exactly coverage_basis={R02_BASIS!r}; got {value!r}. A scope mixing bases refuses (ADR-0034 decision 3)"
            )
        return value

    @field_validator("disclosure")
    @classmethod
    def _fixed_disclosure(cls, value: str) -> str:
        if value != R02_DISCLOSURE:
            raise ValueError("R02 artifacts carry the fixed ADR-0034 decision 3 disclosure verbatim; a different or missing wording refuses")
        return value

    @field_validator("promoted")
    @classmethod
    def _nothing_promoted(cls, value: bool) -> bool:
        if value:
            raise ValueError("R02 promotes nothing: an artifact claiming a promotion is refused (ADR-0034 decision 4)")
        return value


class R02ProtocolArtifact(_ArtifactBase):
    """The frozen protocol record R02 ran under."""

    manifest_name: str
    recorded_at: datetime
    frozen_protocol: dict[str, Any]
    prespecification: dict[str, Any]


class R02MetricsArtifact(_ArtifactBase):
    """Per-fold discrimination + economic metrics, intervals and promotion verdict."""

    folds: dict[str, Any]
    intervals: dict[str, Any]
    promotion: dict[str, Any]
    folds_evidence: dict[str, Any]
    resource: dict[str, Any]
    calibration: dict[str, Any]
    inputs: dict[str, Any]


class R02BaselinesArtifact(_ArtifactBase):
    """The matched-date baseline comparison (technical rule + no-skill + simple model)."""

    matched_dates: dict[str, Any]
    comparison: dict[str, Any]


class R02RunManifest(_ArtifactBase):
    """What actually ran: inputs, counts, timings, budget, and the refusals taken."""

    protocol_binding: dict[str, str]
    lake_root: str
    read_window: str
    holdout_untouched: dict[str, Any]
    row_counts: dict[str, Any]
    runtime: dict[str, Any]
    model: dict[str, Any]
    uncertainty: dict[str, Any]
    notes: list[str] = Field(default_factory=list)


def _refuse_unsafe_target(path: Path) -> Path:
    """R02 writes research artifacts only: never a lake table, never ``04_platinum``."""
    parts = set(path.resolve().parts)
    if "04_platinum" in parts or "03_gold" in parts or "01_bronze" in parts or "02_silver" in parts:
        raise R02Error(f"R02 refuses to write inside a lake layer ({path}); research artifacts live under data/research/r02 only")
    return path


def _write_json(path: Path, payload: Mapping[str, Any]) -> Path:
    target = _refuse_unsafe_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
    return target


def _write_markdown(path: Path, text: str) -> Path:
    target = _refuse_unsafe_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


def _display_root(path: Path) -> str:
    """Root as a repository-relative name (an artifact never carries an absolute path)."""
    resolved = Path(path).resolve()
    for base in (DATA_DIR.parent.resolve(), Path.cwd().resolve()):
        try:
            return resolved.relative_to(base).as_posix()
        except ValueError:
            continue
    return resolved.name


def _probability_lookup(frame: pl.DataFrame, values: np.ndarray) -> dict[tuple[str, str, str, date], float]:
    keys = zip(frame["market"].to_list(), frame["mic"].to_list(), frame["symbol"].to_list(), frame["date"].to_list(), strict=True)
    return {key: float(value) for key, value in zip(keys, values, strict=True)}


def _promotion_verdict(
    *,
    spec: R02Spec,
    folds: Mapping[str, Any],
    intervals: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate the frozen promotion criteria and promote nothing, ever."""
    test = folds["test"]["strategies"]
    validation = folds["validation"]["strategies"]
    primary, technical, no_skill = test["primary_model"], test["technical_rule"], test["no_skill"]
    tolerance = 0.05
    test_intervals = intervals.get("test", {})
    paired = test_intervals.get("paired_difference_net_return_primary_minus_technical", {})
    auc_interval = test_intervals.get("primary_model", {}).get("discrimination", {}).get("roc_auc", {})
    validation_delta = validation["primary_model"]["economics"]["net_return"] - validation["technical_rule"]["economics"]["net_return"]
    test_delta = primary["economics"]["net_return"] - technical["economics"]["net_return"]

    criteria = [
        {
            "criterion": "calibration",
            "statement": spec.promotion_criteria[0],
            "met": bool(
                primary["brier_score"] is not None
                and no_skill["brier_score"] is not None
                and technical["brier_score"] is not None
                and primary["brier_score"] < no_skill["brier_score"]
                and primary["brier_score"] < technical["brier_score"]
            ),
            "detail": f"primary Brier {primary['brier_score']}, no-skill {no_skill['brier_score']}, technical {technical['brier_score']}",
        },
        {
            "criterion": "discrimination",
            "statement": spec.promotion_criteria[1],
            "met": bool(primary["roc_auc"] is not None and primary["roc_auc"] >= 0.55 and auc_interval.get("lower", 0.0) > 0.5),
            "detail": f"primary ROC AUC {primary['roc_auc']} with 95% interval [{auc_interval.get('lower')}, {auc_interval.get('upper')}]",
        },
        {
            "criterion": "economics",
            "statement": spec.promotion_criteria[2],
            "met": bool(test_delta > 0.0 and paired.get("lower", 0.0) > 0.0),
            "detail": (
                f"test net return primary {primary['economics']['net_return']} vs technical {technical['economics']['net_return']} "
                f"(difference {test_delta}, 95% interval [{paired.get('lower')}, {paired.get('upper')}])"
            ),
        },
        {
            "criterion": "risk",
            "statement": spec.promotion_criteria[3],
            "met": bool(primary["economics"]["sharpe_ratio"] >= technical["economics"]["sharpe_ratio"] + tolerance),
            "detail": (
                f"primary Sharpe {primary['economics']['sharpe_ratio']} vs technical {technical['economics']['sharpe_ratio']} (tolerance {tolerance})"
            ),
        },
        {
            "criterion": "consistency",
            "statement": spec.promotion_criteria[4],
            "met": bool(validation_delta * test_delta > 0.0),
            "detail": f"validation difference {validation_delta}, test difference {test_delta}",
        },
    ]
    met = [entry["criterion"] for entry in criteria if entry["met"]]
    return {
        "promoted": False,
        "policy": "the promotion criteria are frozen in the prespecification digest and are evaluated here for the record only",
        "nothing_promoted_reason": (
            "ADR-0034 decisions 4-5: R02 is research evidence on a vendor-stamped, clockless basis. "
            "It authorizes no deployment, no live signal and no point-in-time claim whatever the sign of the result."
        ),
        "criteria": criteria,
        "criteria_met": met,
        "criteria_unmet": [entry["criterion"] for entry in criteria if not entry["met"]],
        "would_promote_if_authorized": len(met) == len(criteria),
    }


def _fmt(value: object) -> str:
    if isinstance(value, float):
        if math.isnan(value):
            return "n/a"
        return f"{value:.4f}"
    return "n/a" if value is None else str(value)


def _report_markdown(
    *,
    spec: R02Spec,
    frozen: FrozenProtocol,
    metrics: Mapping[str, Any],
    baselines: Mapping[str, Any],
    run: Mapping[str, Any],
) -> str:
    folds = metrics["folds"]
    intervals = metrics["intervals"]
    test = folds["test"]["strategies"]
    validation = folds["validation"]["strategies"]
    lines: list[str] = [
        "# R02 — one incremental ML experiment (research evidence only)",
        "",
        f"- Experiment: `{spec.experiment}`",
        f"- Frozen protocol digest: `{frozen.protocol_digest}`",
        f"- Frozen prespecification digest: `{spec.digest()}`",
        f"- Coverage basis: `{R02_BASIS}`",
        f"- Disclosure: {R02_DISCLOSURE}",
        "",
        "## What was frozen before the run",
        "",
        f"- Universe (5 instruments): {', '.join(spec.universe)}",
        f"- Window read: 2024-01-02..{spec.test_end}; holdout {spec.holdout_start}..{spec.holdout_end} untouched (never read).",
        f"- Folds: train {spec.train_start}..{spec.train_end}, validation {spec.validation_start}..{spec.validation_end}, "
        f"test {spec.test_start}..{spec.test_end}.",
        f"- Label: {spec.label}, horizon/maturity {spec.label_horizon_sessions} sessions, "
        f"purge {spec.purge_sessions}, embargo {spec.embargo_sessions}.",
        f"- Costs: regime `{spec.cost_regime}`, fee {spec.fee_ratio}, tax {spec.tax_ratio}, "
        f"round-trip charge {spec.round_trip_cost:.6f} per held name per block.",
        "- Entry: signal known at the session close of the decision date, entry at that same session close (`session_close`/`close`).",
        f"- Policy: long/flat equal weight, threshold {spec.decision_threshold}, "
        f"hold {spec.holding_sessions} sessions per non-overlapping block; never short.",
        f"- Primary model: {spec.primary_model} {spec.primary_model_params}",
        f"- Simple-model baseline: {spec.simple_model} {spec.simple_model_params} on the same features",
        f"- Technical-rule baseline: {spec.technical_rule} (20-session momentum sign)",
        f"- No-skill baseline: {spec.no_skill_baseline} (train base rate {_fmt(metrics['inputs']['train_base_rate'])})",
        f"- Uncertainty: {spec.uncertainty['method']}, block {spec.uncertainty['block_sessions']} sessions, "
        f"{spec.uncertainty['resamples']} resamples, seed {spec.uncertainty['seed']}, "
        f"{int(spec.uncertainty['confidence_level'] * 100)}% percentile interval.",
        "",
        "## Results on matched out-of-sample dates",
        "",
        "### Test fold (2024-07-01..2024-09-30)",
        "",
        "| Strategy | Brier | ROC AUC | Net return | Sharpe | Max drawdown |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, label in (
        ("primary_model", "primary (gradient boosting)"),
        ("simple_model", "simple model (logistic regression)"),
        ("technical_rule", "technical rule (momentum-20)"),
        ("no_skill", "no skill (base rate)"),
    ):
        entry = test[name]
        economics = entry["economics"]
        lines.append(
            f"| {label} | {_fmt(entry['brier_score'])} | {_fmt(entry['roc_auc'])} | {_fmt(economics['net_return'])} | "
            f"{_fmt(economics['sharpe_ratio'])} | {_fmt(economics['max_drawdown'])} |"
        )
    lines += [
        "",
        "Block-bootstrap 95% intervals (test fold, primary model):",
        "",
        "| Metric | Point estimate | 95% interval |",
        "| --- | --- | --- |",
    ]
    test_intervals = intervals["test"]
    primary_intervals = test_intervals["primary_model"]
    for metric, point in (
        ("brier_score", test["primary_model"]["brier_score"]),
        ("roc_auc", test["primary_model"]["roc_auc"]),
        ("net_return", test["primary_model"]["economics"]["net_return"]),
        ("sharpe_ratio", test["primary_model"]["economics"]["sharpe_ratio"]),
        ("max_drawdown", test["primary_model"]["economics"]["max_drawdown"]),
    ):
        source = primary_intervals.get(metric) or primary_intervals.get("discrimination", {}).get(metric, {})
        lines.append(f"| {metric} | {_fmt(point)} | [{_fmt(source.get('lower'))}, {_fmt(source.get('upper'))}] |")
    paired = test_intervals["paired_difference_net_return_primary_minus_technical"]
    lines += [
        f"| net return difference (primary - technical) | "
        f"{_fmt(test['primary_model']['economics']['net_return'] - test['technical_rule']['economics']['net_return'])} | "
        f"[{_fmt(paired.get('lower'))}, {_fmt(paired.get('upper'))}] |",
        "",
        "### Validation fold (2024-04-01..2024-06-30, not used to select anything)",
        "",
        "| Strategy | Brier | ROC AUC | Net return | Sharpe | Max drawdown |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, label in (
        ("primary_model", "primary (gradient boosting)"),
        ("simple_model", "simple model (logistic regression)"),
        ("technical_rule", "technical rule (momentum-20)"),
        ("no_skill", "no skill (base rate)"),
    ):
        entry = validation[name]
        economics = entry["economics"]
        lines.append(
            f"| {label} | {_fmt(entry['brier_score'])} | {_fmt(entry['roc_auc'])} | {_fmt(economics['net_return'])} | "
            f"{_fmt(economics['sharpe_ratio'])} | {_fmt(economics['max_drawdown'])} |"
        )
    lines += [
        "",
        "## Baseline comparison (matched dates, identical blocks and costs)",
        "",
        f"- Test rows: {folds['test']['rows']} over {folds['test']['sessions']} sessions; "
        f"validation rows: {folds['validation']['rows']} over {folds['validation']['sessions']} sessions.",
        f"- Net return difference (test): primary minus technical "
        f"{_fmt(baselines['comparison']['test']['primary_minus_technical_net_return'])}, primary minus simple model "
        f"{_fmt(baselines['comparison']['test']['primary_minus_simple_model_net_return'])}.",
        f"- Brier difference (test): primary minus technical "
        f"{_fmt(baselines['comparison']['test']['brier_primary_minus_technical'])}. "
        "The technical rule emits a hard 0/1 sign, so its Brier is a misclassification rate.",
        "",
        "## Promotion criteria (stated in advance) and verdict",
        "",
    ]
    for criterion in spec.promotion_criteria:
        lines.append(f"- {criterion}")
    lines += ["", "| Criterion | Met | Detail |", "| --- | --- | --- |"]
    for entry in metrics["promotion"]["criteria"]:
        lines.append(f"| {entry['criterion']} | {'yes' if entry['met'] else 'no'} | {entry['detail']} |")
    lines += [
        "",
        f"**Verdict: nothing is promoted.** {metrics['promotion']['nothing_promoted_reason']}",
        "",
        "## Limits (read these before quoting any number)",
        "",
        "- The rows are `vendor_events_no_clock`: vendor-event completeness only, with no source clock. No action was or was",
        "  not known at a decision date on this basis, and a vendor omission below the action watermark remains undetectable",
        "  (ADR-0034 decision 5). This is not point-in-time evidence.",
        "- The universe is the five instruments the real slice covers (AAPL, MSFT, NVDA, JPM, KO). It was chosen by data",
        "  availability, not by performance, and it is a fixed 2024 panel: no survivorship-free claim is made.",
        "- One year, five instruments, a few hundred labelled rows per fold. The block bootstrap quantifies sampling",
        "  dependence at block 5; it cannot repair a small sample or a single regime.",
        "- Entry and signal share the same session close, which is optimistic; there is no market impact, no volume",
        "  participation limit, no borrow, no FX and no venue-specific cost model.",
        "- The holdout was never read. These numbers are not comparable with R01's (different universe, window and basis).",
        "- Nothing here is a live strategy result, a deployment decision or a promotion.",
        "",
        "## Artifacts",
        "",
        "- `data/research/r02/protocol.json` — frozen manifest name, protocol digest, prespecification",
        "- `data/research/r02/metrics.json` — per-fold metrics, intervals, calibration, promotion verdict",
        f"- `data/research/r02/predictions.parquet` — {run['row_counts']['prediction_rows']} fold predictions "
        "(date, identity, probability, outcome, basis, disclosure)",
        "- `data/research/r02/baselines.json` — matched-date baseline comparison",
        "- `data/research/r02/run.json` — inputs, counts, runtime, budget, refusals taken",
        "",
    ]
    return "\n".join(lines)


def run_r02(
    *,
    lake_dir: Path,
    root: Path | None = None,
    results_dir: Path | None = None,
    protocol_digest: str | None = None,
    started_at: datetime | None = None,
) -> dict[str, Any]:
    """Run the one frozen R02 experiment and write its artifacts.

    Refusals (no model is fitted and no artifact is written):

    * the protocol is absent or changed (R00 :func:`require_frozen_protocol`);
    * the frozen manifest does not bind this prespecification's digest;
    * the claimed protocol digest is not the frozen one;
    * the run's universe/window/costs do not conform to the frozen protocol;
    * the Gold rows are absent, carry another basis, or reach the holdout;
    * the run exceeds its frozen resource budget.

    Returns the run summary, which is also persisted as ``run.json``.
    """
    clock = time.monotonic()
    spec = r02_spec()
    protocol = r02_protocol()
    frozen = require_frozen_protocol(protocol, root=root)
    if protocol_digest is not None and frozen.protocol_digest != protocol_digest:
        raise ProtocolError(
            f"the frozen R02 manifest carries digest {frozen.protocol_digest[:12]}, not the claimed {protocol_digest[:12]}; "
            "an artifact is never bound to a protocol the run did not use"
        )
    bound_digest = frozen.input_digests.get("r02_prespecification")
    if bound_digest != spec.digest():
        raise ProtocolError(
            f"the frozen manifest does not bind this prespecification (frozen {bound_digest}, candidate {spec.digest()[:12]}); "
            "freeze the protocol before running (R00)"
        )

    instruments = _instruments(spec.universe)
    read_start, read_end = date(2024, 1, 2), spec.test_end
    require_run_conformance(frozen, universe=spec.universe, start_date=read_start, end_date=read_end, cost_regimes=[COST_REGIME])

    features = _load_features(lake_dir=lake_dir, instruments=instruments, start_date=read_start, end_date=read_end)
    labelled = _label_rows(features, spec.label_horizon_sessions)
    assigned, fold_evidence = _assign_folds(labelled, spec)
    matrix, scaling = _standardise(assigned, spec)
    labels = assigned["label"].cast(pl.Float64).to_numpy()
    train_mask = (assigned["fold"] == "train").to_numpy()
    if int(train_mask.sum()) < 50:
        raise R02Error(f"the train fold has {int(train_mask.sum())} rows, too few for the frozen design")

    primary = _fit_primary(matrix, labels, train_mask)
    simple = _fit_simple(matrix, labels, train_mask)
    technical = _technical_rule(assigned)
    train_base_rate = float(labels[train_mask].mean())
    probabilities: dict[str, np.ndarray] = {
        "primary_model": primary,
        "simple_model": simple,
        "technical_rule": technical,
        "no_skill": np.full(labels.shape, train_base_rate, dtype=np.float64),
    }

    identity = ["market", "mic", "symbol"]
    closes: dict[tuple[str, str, str], dict[date, float]] = {}
    for row in assigned.select(*identity, "date", "close").to_dicts():
        closes.setdefault((str(row["market"]), str(row["mic"]), str(row["symbol"])), {})[row["date"]] = float(row["close"])
    sessions = sorted(set(assigned["date"].to_list()))
    lookup = {name: _probability_lookup(assigned, values) for name, values in probabilities.items()}

    folds: dict[str, Any] = {}
    fold_returns: dict[str, pl.DataFrame] = {}
    fold_dates: dict[str, list[date]] = {}
    calibration: dict[str, Any] = {}
    for fold in ("validation", "test"):
        fold_frame = assigned.filter(pl.col("fold") == fold).sort(["date", *identity])
        fold_sessions = sorted(set(fold_frame["date"].to_list()))
        fold_dates[fold] = fold_sessions
        fold_labels = fold_frame["label"].cast(pl.Float64).to_numpy()
        keys = list(
            zip(
                fold_frame["market"].to_list(),
                fold_frame["mic"].to_list(),
                fold_frame["symbol"].to_list(),
                fold_frame["date"].to_list(),
                strict=True,
            )
        )
        fold_probs = {name: np.asarray([lookup[name][key] for key in keys], dtype=np.float64) for name in probabilities}
        entry: dict[str, Any] = {
            "window": fold_evidence[fold]["window"],
            "rows": fold_frame.height,
            "sessions": len(fold_sessions),
            "decision_date_min": str(min(fold_sessions)),
            "decision_date_max": str(max(fold_sessions)),
            "label_end_date_max": fold_evidence[fold]["label_end_date_max"],
            "strategies": {},
        }
        for name, probs in fold_probs.items():
            entry["strategies"][name] = _discrimination(fold_labels, probs)
            picks: dict[date, list[tuple[str, str, str]]] = {}
            for session in fold_sessions:
                day = fold_frame.filter(pl.col("date") == session)
                picks[session] = [
                    (str(row["market"]), str(row["mic"]), str(row["symbol"]))
                    for row in day.select(*identity).to_dicts()
                    if lookup[name][(str(row["market"]), str(row["mic"]), str(row["symbol"]), session)] >= DECISION_THRESHOLD
                ]
            returns = _strategy_returns(
                picks=picks,
                sessions=sessions,
                closes=closes,
                cost=ROUND_TRIP_COST,
                holding=HOLDING_SESSIONS,
            ).filter(pl.col("date").is_in(fold_sessions))
            fold_returns[f"{fold}:{name}"] = returns
            entry["strategies"][name]["economics"] = _economic_summary(returns)
        folds[fold] = entry
        calibration[fold] = {name: _calibration_bins(fold_labels, probs) for name, probs in fold_probs.items()}

    intervals: dict[str, Any] = {}
    for fold in ("validation", "test"):
        fold_frame = assigned.filter(pl.col("fold") == fold).sort(["date", *identity])
        fold_labels = fold_frame["label"].cast(pl.Float64).to_numpy()
        keys = list(
            zip(
                fold_frame["market"].to_list(),
                fold_frame["mic"].to_list(),
                fold_frame["symbol"].to_list(),
                fold_frame["date"].to_list(),
                strict=True,
            )
        )
        starts: dict[date, int] = {}
        counts: dict[date, int] = {}
        position = 0
        for session in fold_dates[fold]:
            size = fold_frame.filter(pl.col("date") == session).height
            starts[session] = position
            counts[session] = size
            position += size
        fold_intervals: dict[str, Any] = {}
        for name in probabilities:
            fold_intervals[name] = _bootstrap_economics(fold_returns[f"{fold}:{name}"], BOOTSTRAP_SEED)
            fold_intervals[name]["discrimination"] = _bootstrap_discrimination(
                dates=fold_dates[fold],
                row_start=starts,
                row_count=counts,
                labels=fold_labels,
                probs=np.asarray([lookup[name][key] for key in keys], dtype=np.float64),
                seed=BOOTSTRAP_SEED,
            )
        fold_intervals["paired_difference_net_return_primary_minus_technical"] = _bootstrap_paired_difference(
            fold_returns[f"{fold}:primary_model"], fold_returns[f"{fold}:technical_rule"], BOOTSTRAP_SEED
        )
        intervals[fold] = fold_intervals

    baseline_comparison: dict[str, Any] = {}
    for fold in ("validation", "test"):
        strategy = folds[fold]["strategies"]
        baseline_comparison[fold] = {
            "primary_minus_technical_net_return": strategy["primary_model"]["economics"]["net_return"]
            - strategy["technical_rule"]["economics"]["net_return"],
            "primary_minus_simple_model_net_return": strategy["primary_model"]["economics"]["net_return"]
            - strategy["simple_model"]["economics"]["net_return"],
            "primary_minus_technical_sharpe_ratio": strategy["primary_model"]["economics"]["sharpe_ratio"]
            - strategy["technical_rule"]["economics"]["sharpe_ratio"],
            "brier_primary_minus_technical": (strategy["primary_model"]["brier_score"] or 0.0) - (strategy["technical_rule"]["brier_score"] or 0.0),
            "brier_primary_minus_no_skill": (strategy["primary_model"]["brier_score"] or 0.0) - (strategy["no_skill"]["brier_score"] or 0.0),
            "strategies": strategy,
        }

    promotion = _promotion_verdict(spec=spec, folds=folds, intervals=intervals)

    runtime_seconds = time.monotonic() - clock
    budget = protocol.resource_budget
    if runtime_seconds > budget.max_runtime_seconds:
        raise R02Error(
            f"the run took {runtime_seconds:.1f}s, beyond the frozen budget {budget.max_runtime_seconds}s; "
            "an over-budget run is censored, never recorded as success"
        )
    if len(instruments) > budget.max_symbols or len(sessions) > budget.max_sessions:
        raise R02Error(
            f"the run used {len(instruments)} symbols / {len(sessions)} sessions, beyond the frozen budget "
            f"{budget.max_symbols} / {budget.max_sessions}"
        )

    target_dir = _refuse_unsafe_target(Path(results_dir) if results_dir is not None else results_root(root))
    spec_digest = spec.digest()
    protocol_artifact = R02ProtocolArtifact(
        protocol_digest=frozen.protocol_digest,
        prespecification_digest=spec_digest,
        manifest_name=frozen.recorded_at.strftime("%Y%m%dT%H%M%SZ"),
        recorded_at=frozen.recorded_at,
        frozen_protocol=frozen.protocol.protocol_payload(),
        prespecification=spec.model_dump(mode="json"),
    )
    _write_json(
        target_dir / "protocol.json",
        protocol_artifact.model_dump(mode="json"),
    )

    resource = {
        "runtime_seconds": runtime_seconds,
        "max_runtime_seconds": budget.max_runtime_seconds,
        "symbols": len(instruments),
        "max_symbols": budget.max_symbols,
        "sessions": len(sessions),
        "max_sessions": budget.max_sessions,
    }
    metrics_artifact = R02MetricsArtifact(
        protocol_digest=frozen.protocol_digest,
        prespecification_digest=spec_digest,
        folds=folds,
        intervals=intervals,
        promotion=promotion,
        folds_evidence=fold_evidence,
        resource=resource,
        calibration=calibration,
        inputs={
            "features": list(spec.features),
            "feature_scaling": scaling,
            "train_rows": int(train_mask.sum()),
            "train_base_rate": train_base_rate,
            "sessions_read": sessions,
            "read_window": f"{read_start}..{read_end}",
        },
    )
    _write_json(target_dir / "metrics.json", metrics_artifact.model_dump(mode="json"))

    prediction_frame = pl.DataFrame(
        {
            "fold": pl.Series(assigned["fold"].to_list(), dtype=pl.String),
            "date": pl.Series(assigned["date"].to_list(), dtype=pl.Date),
            "market": pl.Series(assigned["market"].to_list(), dtype=pl.String),
            "mic": pl.Series(assigned["mic"].to_list(), dtype=pl.String),
            "symbol": pl.Series(assigned["symbol"].to_list(), dtype=pl.String),
            "probability_primary_model": pl.Series(primary, dtype=pl.Float64),
            "probability_simple_model": pl.Series(simple, dtype=pl.Float64),
            "probability_technical_rule": pl.Series(technical, dtype=pl.Float64),
            "probability_no_skill": pl.Series(probabilities["no_skill"], dtype=pl.Float64),
            "decision_primary_model": pl.Series((primary >= DECISION_THRESHOLD).astype(np.int8), dtype=pl.Int8),
            "outcome": pl.Series(assigned["label"].to_list(), dtype=pl.Int8),
            "forward_return": pl.Series(assigned["forward_return"].to_list(), dtype=pl.Float64),
            "label_end_date": pl.Series(assigned["label_end_date"].to_list(), dtype=pl.Date),
            "coverage_basis": pl.Series([R02_BASIS] * assigned.height, dtype=pl.String),
            "disclosure": pl.Series([R02_DISCLOSURE] * assigned.height, dtype=pl.String),
            # The prediction artifact binds the exact protocol and prespecification it
            # was produced under (ADR-0034 decision 3), so a row cannot be read as
            # evidence for a different frozen design.
            "protocol_digest": pl.Series([frozen.protocol_digest] * assigned.height, dtype=pl.String),
            "prespecification_digest": pl.Series([spec_digest] * assigned.height, dtype=pl.String),
        }
    ).filter(pl.col("fold").is_in(["validation", "test"]))
    prediction_path = _refuse_unsafe_target(target_dir / "predictions.parquet")
    prediction_path.parent.mkdir(parents=True, exist_ok=True)
    prediction_frame.write_parquet(prediction_path)

    baselines_artifact = R02BaselinesArtifact(
        protocol_digest=frozen.protocol_digest,
        prespecification_digest=spec_digest,
        matched_dates={
            fold: {
                "rows": folds[fold]["rows"],
                "sessions": folds[fold]["sessions"],
                "rebalance_blocks": math.ceil(folds[fold]["sessions"] / HOLDING_SESSIONS),
                "first_decision_date": folds[fold]["decision_date_min"],
                "last_decision_date": folds[fold]["decision_date_max"],
            }
            for fold in ("validation", "test")
        },
        comparison=baseline_comparison,
    )
    _write_json(target_dir / "baselines.json", baselines_artifact.model_dump(mode="json"))

    run_manifest = R02RunManifest(
        protocol_digest=frozen.protocol_digest,
        prespecification_digest=spec_digest,
        protocol_binding=protocol_binding(frozen),
        lake_root=_display_root(lake_dir),
        read_window=f"{read_start}..{read_end}",
        holdout_untouched={
            "window": f"{spec.holdout_start}..{spec.holdout_end}",
            "read": False,
            "rows_read": 0,
            "evidence": "the feature read ends the session before the holdout starts, and the label window matures on or before it",
        },
        row_counts={
            "feature_rows_read": features.height,
            "labelled_rows": int(assigned.height),
            "train_rows": int(train_mask.sum()),
            "validation_rows": folds["validation"]["rows"],
            "test_rows": folds["test"]["rows"],
            "prediction_rows": prediction_frame.height,
            "instruments": len(instruments),
            "sessions_read": len(sessions),
        },
        runtime={"started_at": (started_at or datetime.now(UTC)).isoformat(), "wall_time_seconds": runtime_seconds},
        model={
            "primary": PRIMARY_MODEL,
            "primary_params": dict(PRIMARY_MODEL_PARAMS),
            "simple": SIMPLE_MODEL,
            "simple_params": dict(SIMPLE_MODEL_PARAMS),
            "fits": 1,
            "refits": 0,
            "sweeps": 0,
            "holdout_tuning": False,
        },
        uncertainty={
            "method": BOOTSTRAP_METHOD,
            "block_sessions": BOOTSTRAP_BLOCK_SESSIONS,
            "resamples": BOOTSTRAP_RESAMPLES,
            "seed": BOOTSTRAP_SEED,
            "confidence_level": CONFIDENCE_LEVEL,
        },
        notes=[
            "one experiment only: no sweep, no refit, no holdout tuning, no promotion",
            "predictions are written under data/research/r02 only; no prediction table is written",
            "the technical rule is fitted on nothing; the no-skill forecast is the train base rate",
        ],
    )
    _write_json(target_dir / "run.json", run_manifest.model_dump(mode="json"))

    report = _report_markdown(
        spec=spec,
        frozen=frozen,
        metrics=metrics_artifact.model_dump(mode="json"),
        baselines=baselines_artifact.model_dump(mode="json"),
        run=run_manifest.model_dump(mode="json"),
    )
    _write_markdown(target_dir / "report.md", report)

    logger.info(
        "r02_ran",
        protocol_digest=frozen.protocol_digest,
        test_net_return=folds["test"]["strategies"]["primary_model"]["economics"]["net_return"],
        promoted=False,
    )
    return {
        "experiment": R02_EXPERIMENT,
        "protocol_digest": frozen.protocol_digest,
        "prespecification_digest": spec_digest,
        "coverage_basis": R02_BASIS,
        "disclosure": R02_DISCLOSURE,
        "artifacts": {
            "protocol": str(target_dir / "protocol.json"),
            "metrics": str(target_dir / "metrics.json"),
            "predictions": str(target_dir / "predictions.parquet"),
            "baselines": str(target_dir / "baselines.json"),
            "run": str(target_dir / "run.json"),
            "report": str(target_dir / "report.md"),
        },
        "test": folds["test"]["strategies"],
        "validation": folds["validation"]["strategies"],
        "intervals": intervals["test"],
        "promotion": promotion,
        "row_counts": run_manifest.row_counts,
        "runtime_seconds": runtime_seconds,
        "promoted": False,
    }


__all__ = [
    "R02_BASIS",
    "R02_DISCLOSURE",
    "R02_EXPERIMENT",
    "R02_FEATURES",
    "R02_RESULTS_DIRNAME",
    "R02_UNIVERSE",
    "R02BaselinesArtifact",
    "R02Error",
    "R02MetricsArtifact",
    "R02ProtocolArtifact",
    "R02RunManifest",
    "R02Spec",
    "freeze_r02_protocol",
    "r02_protocol",
    "r02_spec",
    "results_root",
    "run_r02",
]
