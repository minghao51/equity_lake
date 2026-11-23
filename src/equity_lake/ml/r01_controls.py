"""R01: measured R00 controls — planted/null statistical controls and the six leakage controls.

Residual stage RS-R01 (residual plan §2.12, §5) turns R00's shipped *freeze/refuse*
controls into *measured* controls. This module is the preregistered harness, and its
contract is, in the order it is enforced:

* **Preregister, then freeze** — :func:`r01_spec` holds everything the stage decides
  *before* it looks at a result: the predeclared seed sets, the planted effect size, the
  statistic and the estimand it names, the dependence-aware resampling rule, the
  pass/fail bounds, the scale sizes and the single-host ceilings. Its canonical digest is
  bound into the frozen R00 manifest (:func:`freeze_r01_protocol`), and
  :func:`run_controls` refuses to run unless that exact protocol and that exact
  prespecification are already frozen.
* **One estimand per statistic** — the decision statistic is
  :data:`PRIMARY_STATISTIC`, the arithmetic **mean per-session net return** of the
  long/flat policy over the evaluation sessions. It is deliberately *not* the compounded
  net return: R02's archive showed those are different estimands (a compounded point of
  ``-2.79 pp`` against an interval built on a ``-0.0374 pp/session`` mean), so this
  harness names the mean and never mixes the two. The compounded figure is still
  recorded, labelled, beside it.
* **Dependence-aware resampling** — :data:`RESAMPLING_RULE`: a moving-block bootstrap
  over *sessions* (block = 5 sessions, 2000 resamples) seeded by the predeclared control
  seed, with a one-sided 95% lower bound on the mean per-session net return. A seed
  "detects" something only when that lower bound clears zero.
* **Controls are measured, never assumed** — the six §5 leakage controls each return a
  record naming their regression test, their expected result and what was observed; the
  planted positive control and both null variants run over the predeclared seeds.
* **Censored is a first-class outcome** — a seed set or a scale point that cannot
  complete inside the frozen ceilings is recorded ``censored`` with the measured per-seed
  cost, and a censored set can never establish a passing power or scale claim. Nothing
  here widens a budget: a run that would exceed it refuses.

The synthetic fixture reuses :func:`~equity_lake.devtools.seeding.synthetic_ohlcv` (no
new generator) and the economics/uncertainty estimators of
:mod:`equity_lake.ml.experiment` (no refit, no retune, nothing promoted, and the R02
artifacts are neither read nor written). Control results are *harness* evidence, never
evidence about markets, and computational scale is not economic capacity (spec §7).
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl
import structlog
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from sklearn.linear_model import LogisticRegression

from equity_lake.backtesting.evidence import CostApplication
from equity_lake.backtesting.protocol import (
    FrozenProtocol,
    ProtocolError,
    ResearchProtocol,
    ResourceBudget,
    freeze_protocol,
    protocols_root,
    require_frozen_protocol,
)
from equity_lake.core.availability import (
    AVAILABILITY_SCHEMA_VERSION,
    ActionCoverageProof,
    action_coverage_feature_fields,
    decision_session_close,
    source_observable_session,
    validate_feature_action_coverage,
)
from equity_lake.core.instruments import InstrumentKey
from equity_lake.core.paths import DATA_DIR
from equity_lake.devtools.seeding import OhlcvProfile, business_days, synthetic_ohlcv
from equity_lake.features.labels import AVAILABILITY_COLUMN, filter_as_of_available
from equity_lake.ml.experiment import (
    BOOTSTRAP_BLOCK_SESSIONS,
    BOOTSTRAP_RESAMPLES,
    DECISION_THRESHOLD,
    FEE_RATIO,
    HOLDING_SESSIONS,
    R02_UNIVERSE,
    ROUND_TRIP_COST,
    TAX_RATIO,
    _block_positions,
    _economic_summary,
    _strategy_returns,
)
from equity_lake.ml.validation import PurgedEmbargoedWalkForwardSplitter

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Frozen identities and the preregistered defaults (residual plan §2.12)
# ---------------------------------------------------------------------------

#: The one experiment identity R01 may run.
R01_EXPERIMENT = "R01-controls-scale-2026"
#: Version of the prespecification payload digested into the frozen manifest.
R01_PREREGISTRATION_VERSION: Literal["r01-preregistration/1"] = "r01-preregistration/1"
#: The five-instrument control fixture: R02's own frozen universe (AAPL/MSFT/NVDA on
#: XNAS, JPM/KO on XNYS), reused so the control fixture is not a new selection.
CONTROL_UNIVERSE: tuple[str, ...] = R02_UNIVERSE
#: The generator identity frozen into the protocol (a reused generator, never a new one).
R01_GENERATOR = "equity_lake.devtools.seeding.synthetic_ohlcv"
R01_COST_REGIME = "r01-controls"
#: Recognition: the technical rule and the no-skill rate are the frozen baselines.
BASELINES: tuple[str, ...] = ("momentum_20_session_sign", "no_skill_base_rate")
#: Metrics recorded; the decision statistic is named separately and is the mean per session.
PRIMARY_METRICS: tuple[str, ...] = ("mean_per_session_net_return", "net_return", "sharpe_ratio", "max_drawdown")

#: Nominal one-sided alpha (§2.12).
NOMINAL_ALPHA_ONE_SIDED = 0.05
#: Null seeds 1729-1828 (100) and planted-control seeds 11729-11778 (50), predeclared.
NULL_SEEDS: tuple[int, ...] = tuple(range(1729, 1829))
PLANTED_SEEDS: tuple[int, ...] = tuple(range(11729, 11779))
#: Pass only when the one-sided 95% binomial **upper** bound on the null false-positive
#: rate is <= 10% and the one-sided 95% binomial **lower** bound on planted-signal power
#: is >= 80%. Individual null false positives are allowed; no seed is required to lose.
FPR_UPPER_BOUND_MAX = 0.10
POWER_LOWER_BOUND_MIN = 0.80
#: The statistic, and the estimand it names. The compounded net return is NOT this.
PRIMARY_STATISTIC = "mean_per_session_net_return"
PRIMARY_ESTIMAND = (
    "arithmetic mean of the per-session net returns of the long/flat policy over the evaluation sessions "
    "(a per-session rate); explicitly NOT the compounded net return over the window, which is recorded separately and never compared to this interval"
)
#: Prespecified uncertainty: moving-block bootstrap over sessions, block = 5, 2000 resamples,
#: bootstrap seed = the predeclared control seed, one-sided 5% quantile lower bound.
RESAMPLING_RULE: dict[str, Any] = {
    "method": "moving_block_bootstrap_over_sessions",
    "block_sessions": BOOTSTRAP_BLOCK_SESSIONS,
    "resamples": BOOTSTRAP_RESAMPLES,
    "bootstrap_seed": "the predeclared control seed itself (no seed is chosen after a result)",
    "interval": "one-sided 95% lower bound (5% quantile of the resampled means)",
    "decision_rule": "a seed detects signal only when the one-sided 95% lower bound on the mean per-session net return is above zero",
    "dependence_note": "resampling units are sessions, so overlapping labels and serially dependent returns are carried inside a block",
}
#: The planted positive control's preregistered daily drift (a fixture transform).
PLANTED_DAILY_SHIFT = 0.01
#: The planted positive control's preregistered effect size.
PLANTED_EFFECT: dict[str, Any] = {
    "kind": "long-side trailing-momentum drift added to the generator's own daily returns",
    "lookback_sessions": 20,
    "daily_return_shift": PLANTED_DAILY_SHIFT,
    "applied_when": "the trailing 20-session momentum is positive; nothing is added on negative-momentum sessions",
    "expected_lift": (
        "a known positive edge of roughly +40 bp per session of net return for the frozen long/flat policy, "
        "after the frozen round-trip cost of 58.5 bp per five-session block (about 11.7 bp per session)"
    ),
    "design_calculation": (
        "fixed before the freeze by a design calculation on fixture seeds 1001-1005 (outside both predeclared sets): "
        "all five detected the plant, with mean per-session net returns between +0.23% and +0.65%. At the superseded "
        "0.4%/session strength, predeclared planted seeds 11729-11730 were inspected during harness development; that "
        "observation is disclosed and those seeds stay in the frozen set unchanged"
    ),
    "disclosure": "the plant is a fixture transform of the generator's own returns; it is harness evidence, not a market claim",
}
#: Fixed control-fixture shape (240 run sessions + 20 untouched holdout sessions).
CONTROL_START = date(2022, 1, 3)
CONTROL_END = date(2022, 12, 30)
HOLDOUT_SESSIONS = 20
WARMUP_SESSIONS = 20
TRAIN_FRACTION = 0.4
CONTROL_FEATURES: tuple[str, ...] = ("return_1d", "roc_20", "volume_ratio", "volatility_20")
CONTROL_MODEL = "logistic_regression"
CONTROL_MODEL_PARAMS: dict[str, Any] = {"C": 1.0, "max_iter": 1000, "solver": "lbfgs"}
MOMENTUM_SESSIONS = 20
#: The leakage-control fixture's one frozen seed (never chosen after a result).
FIXTURE_SEED = 20260601
#: Scale sizes and the three-year design (§2.12, §5).
SCALE_SIZES: tuple[int, ...] = (5, 50, 200)
SCALE_START = date(2022, 1, 3)
SCALE_END = date(2024, 12, 31)
SCALE_WARMUP_SESSIONS = 25
SCALE_VOLATILITY = 0.02
SCALE_TREND = 0.0001
#: Single-host ceilings: 30 min per scale size, 2 h aggregate for controls + scale.
PER_SIZE_CEILING_SECONDS = 1800.0
AGGREGATE_CEILING_SECONDS = 7200.0
#: Peak RSS ceiling is min(8 GiB, 50% of host RAM); an unmonitorable RSS aborts the run.
RSS_ABSOLUTE_CEILING_BYTES = 8 * 1024**3
RSS_HOST_FRACTION = 0.5

R01_RESULTS_DIRNAME = "r01"

_SORT_KEYS: list[str] = ["market", "mic", "symbol", "date"]
_IDENTITY_KEYS: list[str] = ["market", "mic", "symbol"]
_ACTION_SOURCE = "r01-synthetic-source-issued-action-coverage"


class R01Error(ValueError):
    """An R01 control run refused: unfrozen assumptions, a broken control or a ceiling."""


# ---------------------------------------------------------------------------
# The prespecification (everything decided before a result is seen)
# ---------------------------------------------------------------------------


class R01Spec(BaseModel):
    """Everything R01 decides *before* it runs, and nothing it may decide after.

    Its canonical JSON digest is bound into the frozen R00 manifest, so a run under a
    changed prespecification is refused rather than silently reinterpreted — the same
    rule R02 applies to its own prespecification.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    prespecification_version: str
    experiment: str
    control_universe: list[str]
    control_start: date
    control_end: date
    holdout_sessions: int
    warmup_sessions: int
    train_fraction: float
    features: list[str]
    label: str
    label_horizon_sessions: int
    label_maturity_sessions: int
    purge_sessions: int
    embargo_sessions: int
    entry_clock: str
    entry_price: str
    model: str
    model_params: dict[str, Any]
    decision_threshold: float
    holding_sessions: int
    cost_regime: str
    fee_ratio: float
    tax_ratio: float
    round_trip_cost: float
    generator: str
    null_seeds: list[int]
    planted_seeds: list[int]
    null_variants: list[str]
    null_false_positive_definition: str
    planted_effect: dict[str, Any]
    statistic: str
    estimand: str
    resampling: dict[str, Any]
    nominal_alpha_one_sided: float
    fpr_upper_bound_max: float
    power_lower_bound_min: float
    pass_rule: str
    leakage_controls: list[str]
    scale_sizes: list[int]
    scale_start: date
    scale_end: date
    scale_warmup_sessions: int
    minimum_universe: int
    per_size_ceiling_seconds: float
    aggregate_ceiling_seconds: float
    rss_ceiling_rule: str
    promoted: bool = False

    @field_validator("null_seeds", "planted_seeds", "scale_sizes", "features", "control_universe")
    @classmethod
    def _non_empty(cls, value: list[Any]) -> list[Any]:
        if not value:
            raise ValueError("a frozen prespecification needs at least one entry")
        return value

    @field_validator("null_seeds", "planted_seeds")
    @classmethod
    def _unique_seeds(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("predeclared seeds must be unique")
        if any(seed < 0 for seed in value):
            raise ValueError("predeclared seeds must be non-negative")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> R01Spec:
        if self.control_end <= self.control_start or self.scale_end <= self.scale_start:
            raise ValueError("frozen windows must be ordered")
        if not 0.0 < self.train_fraction < 1.0:
            raise ValueError("the train fraction must lie strictly between 0 and 1")
        if self.promoted:
            raise ValueError("R01 promotes nothing: 'promoted' must be false in the prespecification")
        return self

    def canonical_json(self) -> str:
        """Compact canonical JSON: the prespecification digest preimage."""
        return json.dumps(self.model_dump(mode="json"), separators=(",", ":"), sort_keys=True, ensure_ascii=True, allow_nan=False)

    def digest(self) -> str:
        """SHA-256 of the prespecification: seeds, statistic, bounds and ceilings only."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def r01_spec() -> R01Spec:
    """The frozen R01 prespecification (never chosen after seeing a result)."""
    return R01Spec(
        prespecification_version=R01_PREREGISTRATION_VERSION,
        experiment=R01_EXPERIMENT,
        control_universe=list(CONTROL_UNIVERSE),
        control_start=CONTROL_START,
        control_end=CONTROL_END,
        holdout_sessions=HOLDOUT_SESSIONS,
        warmup_sessions=WARMUP_SESSIONS,
        train_fraction=TRAIN_FRACTION,
        features=list(CONTROL_FEATURES),
        label="sign of the 5-session forward close-to-close return (1 when strictly positive)",
        label_horizon_sessions=HOLDING_SESSIONS,
        label_maturity_sessions=HOLDING_SESSIONS,
        purge_sessions=HOLDING_SESSIONS,
        embargo_sessions=1,
        entry_clock="next_session_open",
        entry_price="open",
        model=CONTROL_MODEL,
        model_params=dict(CONTROL_MODEL_PARAMS),
        decision_threshold=DECISION_THRESHOLD,
        holding_sessions=HOLDING_SESSIONS,
        cost_regime=R01_COST_REGIME,
        fee_ratio=FEE_RATIO,
        tax_ratio=TAX_RATIO,
        round_trip_cost=ROUND_TRIP_COST,
        generator=R01_GENERATOR,
        null_seeds=list(NULL_SEEDS),
        planted_seeds=list(PLANTED_SEEDS),
        null_variants=["unrelated_noise_labels", "permuted_labels_within_instrument"],
        null_false_positive_definition=(
            "a null seed is a false positive when the frozen decision rule fires on either null variant; "
            "the false-positive rate is the count of such seeds over the predeclared null set"
        ),
        planted_effect=dict(PLANTED_EFFECT),
        statistic=PRIMARY_STATISTIC,
        estimand=PRIMARY_ESTIMAND,
        resampling=dict(RESAMPLING_RULE),
        nominal_alpha_one_sided=NOMINAL_ALPHA_ONE_SIDED,
        fpr_upper_bound_max=FPR_UPPER_BOUND_MAX,
        power_lower_bound_min=POWER_LOWER_BOUND_MIN,
        pass_rule=(
            "pass only when the one-sided 95% Clopper-Pearson upper bound on the null false-positive rate is <= 10% "
            "AND the one-sided 95% Clopper-Pearson lower bound on the planted-signal power is >= 80% "
            "AND all six leakage controls pass on their named regressions; "
            "no individual null seed is required to lose, and a censored set may not be read as passing"
        ),
        leakage_controls=[name for name, _ in LEAKAGE_CONTROLS],
        scale_sizes=list(SCALE_SIZES),
        scale_start=SCALE_START,
        scale_end=SCALE_END,
        scale_warmup_sessions=SCALE_WARMUP_SESSIONS,
        minimum_universe=5,
        per_size_ceiling_seconds=PER_SIZE_CEILING_SECONDS,
        aggregate_ceiling_seconds=AGGREGATE_CEILING_SECONDS,
        rss_ceiling_rule="peak RSS <= min(8 GiB, 50% of host RAM); abort when RSS cannot be monitored",
        promoted=False,
    )


def r01_protocol() -> ResearchProtocol:
    """The frozen R00 protocol R01 runs under (fixture, window, label, costs, budget)."""
    sessions = control_sessions()
    return ResearchProtocol(
        experiment=R01_EXPERIMENT,
        universe=list(CONTROL_UNIVERSE),
        start_date=sessions[0],
        end_date=sessions[-1],
        holdout_start=sessions[-HOLDOUT_SESSIONS],
        holdout_end=sessions[-1],
        entry_clock="next_session_open",
        entry_price="open",
        label_horizon_days=HOLDING_SESSIONS,
        label_maturity_days=HOLDING_SESSIONS,
        purge_days=HOLDING_SESSIONS,
        embargo_days=1,
        costs=[CostApplication(regime=R01_COST_REGIME, fee_ratio=FEE_RATIO, tax_ratio=TAX_RATIO)],
        metrics=list(PRIMARY_METRICS),
        baselines=list(BASELINES),
        acceptance_tolerances={
            "null_false_positive_rate_upper_bound": FPR_UPPER_BOUND_MAX,
            "planted_power_lower_bound": POWER_LOWER_BOUND_MIN,
            "nominal_one_sided_alpha": NOMINAL_ALPHA_ONE_SIDED,
        },
        generator=R01_GENERATOR,
        seeds=sorted([*NULL_SEEDS, *PLANTED_SEEDS]),
        resource_budget=ResourceBudget(
            max_runtime_seconds=AGGREGATE_CEILING_SECONDS,
            max_symbols=max(SCALE_SIZES),
            max_sessions=len(business_days(SCALE_START, SCALE_END)),
        ),
    )


def freeze_r01_protocol(
    *,
    recorded_at: datetime,
    root: Path | None = None,
    config: Mapping[str, Any] | None = None,
) -> tuple[FrozenProtocol, Path]:
    """Freeze the R01 protocol, binding the prespecification digest into it.

    ``recorded_at`` is mandatory: the manifest bytes are a pure function of the inputs
    (including that one timestamp), so the freeze is byte-reproducible and a reviewer
    can prove the preregistration predates the run.
    """
    return freeze_protocol(
        r01_protocol(),
        recorded_at=recorded_at,
        root=root,
        config=dict(config) if config is not None else None,
        input_digests={"r01_preregistration": r01_spec().digest()},
    )


def results_root(root: Path | None = None) -> Path:
    """``data/research/r01`` — auxiliary research storage, never a lake table."""
    return (Path(root) if root is not None else DATA_DIR) / "research" / R01_RESULTS_DIRNAME


# ---------------------------------------------------------------------------
# The frozen control fixture (reused generator, deterministic per seed)
# ---------------------------------------------------------------------------


def control_sessions() -> list[date]:
    """Mon-Fri control-fixture sessions (the untouchable holdout is the last 20)."""
    return business_days(CONTROL_START, CONTROL_END)


def run_sessions() -> list[date]:
    """Sessions a control run may use: the fixture minus its untouched holdout."""
    return control_sessions()[:-HOLDOUT_SESSIONS]


def control_instruments() -> list[InstrumentKey]:
    """The five canonical control instruments, parsed from the frozen display keys."""
    instruments: list[InstrumentKey] = []
    for display_key in CONTROL_UNIVERSE:
        market, mic, symbol = display_key.split("/")
        instruments.append(InstrumentKey(market=market, mic=mic, symbol=symbol))
    return instruments


def _control_profile() -> OhlcvProfile:
    return OhlcvProfile(
        price_range=(10.0, 500.0),
        volume_range=(1_000_000, 50_000_000),
        drift=0.0001,
        volatility=0.02,
        include_adj_close=False,
    )


def _identity_frame() -> pl.DataFrame:
    instruments = control_instruments()
    return pl.DataFrame(
        {
            "ticker": [instrument.symbol for instrument in instruments],
            "market": [instrument.market for instrument in instruments],
            "mic": [instrument.mic for instrument in instruments],
            "symbol": [instrument.symbol for instrument in instruments],
        }
    )


def control_panel(
    seed: int,
    *,
    sessions: Sequence[date] | None = None,
    planted: bool = False,
) -> pl.DataFrame:
    """A deterministic OHLCV control panel for one seed (no new generator).

    The bars come from :func:`~equity_lake.devtools.seeding.synthetic_ohlcv`; the planted
    variant adds :data:`PLANTED_EFFECT`'s signed drift to those generated returns and
    rebuilds the close path from them, so the plant is a fixture transform rather than a
    second generator.
    """
    days = list(sessions) if sessions is not None else run_sessions()
    base = synthetic_ohlcv([instrument.symbol for instrument in control_instruments()], days, profile=_control_profile(), seed=seed)
    panel = base.select("ticker", "date", "open", "close", "volume").join(_identity_frame(), on="ticker", how="left")
    panel = panel.select(*_IDENTITY_KEYS, "date", "open", "close", "volume").sort(*_SORT_KEYS)
    if planted:
        panel = _plant_signal(panel)
    return panel


def _plant_signal(panel: pl.DataFrame, *, shift: float = PLANTED_DAILY_SHIFT) -> pl.DataFrame:
    """Add the preregistered long-side drift to the generator's own daily returns."""
    returns = (pl.col("close") / pl.col("close").shift(1) - 1.0).over(_IDENTITY_KEYS)
    momentum = (pl.col("close") / pl.col("close").shift(MOMENTUM_SESSIONS) - 1.0).over(_IDENTITY_KEYS)
    bump = pl.when(momentum > 0.0).then(shift).otherwise(0.0)
    planted_return = returns.fill_null(0.0) + bump
    growth = (1.0 + planted_return).cum_prod().over(_IDENTITY_KEYS)
    anchored = pl.col("close").first().over(_IDENTITY_KEYS) * growth
    planted = panel.sort(*_SORT_KEYS).with_columns(anchored.alias("planted_close"))
    planted = planted.with_columns(
        pl.col("planted_close").alias("close"),
        pl.col("planted_close").shift(1).over(_IDENTITY_KEYS).alias("previous_close"),
    )
    return planted.with_columns(pl.col("previous_close").fill_null(pl.col("close")).alias("open")).drop("planted_close", "previous_close")


def control_features(panel: pl.DataFrame) -> pl.DataFrame:
    """Trailing-only features plus the forward label and its maturity date.

    Every feature uses only sessions on or before its own decision date, so appending
    later data cannot change it. The label looks forward by construction, which is why
    :func:`_fit_sample` admits a row only once its own label has matured.
    """
    featured = panel.sort(*_SORT_KEYS).with_columns(
        (pl.col("close") / pl.col("close").shift(1) - 1.0).over(_IDENTITY_KEYS).alias("return_1d"),
        (pl.col("close") / pl.col("close").shift(MOMENTUM_SESSIONS) - 1.0).over(_IDENTITY_KEYS).alias("roc_20"),
        (pl.col("close").shift(-HOLDING_SESSIONS) / pl.col("close") - 1.0).over(_IDENTITY_KEYS).alias("forward_return"),
        pl.col("date").shift(-HOLDING_SESSIONS).over(_IDENTITY_KEYS).alias("label_end_date"),
    )
    featured = featured.with_columns(
        (pl.col("volume") / pl.col("volume").rolling_mean(MOMENTUM_SESSIONS)).over(_IDENTITY_KEYS).alias("volume_ratio"),
        pl.col("return_1d").rolling_std(MOMENTUM_SESSIONS).over(_IDENTITY_KEYS).alias("volatility_20"),
    )
    return featured.with_columns(
        pl.when(pl.col("forward_return") > 0.0).then(1.0).when(pl.col("forward_return") < 0.0).then(0.0).otherwise(None).alias("label"),
    )


def _featured_rows(featured: pl.DataFrame) -> pl.DataFrame:
    """Rows whose own features are complete (a label may still be pending)."""
    complete = pl.all_horizontal([pl.col(column).is_not_null() for column in CONTROL_FEATURES])
    return featured.filter(complete).sort(*_SORT_KEYS)


def _fit_sample(featured: pl.DataFrame, cutoff: date) -> pl.DataFrame:
    """Rows whose own label matured on or before *cutoff* — never a pending label."""
    mature = pl.col("label").is_not_null() & pl.col("label_end_date").is_not_null() & (pl.col("label_end_date") <= pl.lit(cutoff))
    return featured.filter(mature).sort(*_SORT_KEYS)


@dataclass(frozen=True)
class ControlWeights:
    """The fitted control model, kept inspectable so a leak shows up as a changed weight."""

    means: tuple[float, ...]
    stds: tuple[float, ...]
    coefficients: tuple[float, ...]
    intercept: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "means": list(self.means),
            "stds": list(self.stds),
            "coefficients": list(self.coefficients),
            "intercept": self.intercept,
        }


def fit_control_weights(sample: pl.DataFrame) -> ControlWeights:
    matrix = sample.select(*CONTROL_FEATURES).to_numpy().astype(np.float64)
    labels = sample["label"].cast(pl.Float64).to_numpy()
    if matrix.shape[0] < 2 or np.unique(labels).size < 2:
        raise R01Error(f"a control fold with {matrix.shape[0]} rows and one class cannot be fitted")
    means = matrix.mean(axis=0)
    stds = matrix.std(axis=0)
    stds = np.where(stds == 0.0, 1.0, stds)
    model = LogisticRegression(**CONTROL_MODEL_PARAMS)
    model.fit((matrix - means) / stds, labels.astype(np.int64))
    return ControlWeights(
        means=tuple(float(value) for value in means),
        stds=tuple(float(value) for value in stds),
        coefficients=tuple(float(value) for value in np.ravel(model.coef_)),
        intercept=float(np.ravel(model.intercept_)[0]),
    )


def predict_probabilities(weights: ControlWeights, frame: pl.DataFrame) -> np.ndarray:
    matrix = frame.select(*CONTROL_FEATURES).to_numpy().astype(np.float64)
    scaled = (matrix - np.asarray(weights.means)) / np.asarray(weights.stds)
    scores = scaled @ np.asarray(weights.coefficients) + weights.intercept
    return np.asarray(1.0 / (1.0 + np.exp(-scores)), dtype=np.float64)


def scored_outputs(panel: pl.DataFrame, cutoff: date) -> dict[str, Any]:
    """Fit on matured rows up to *cutoff* only, then score every row dated on/before it.

    The two rules that make an append-after-cutoff invariant are visible here: the fit
    sample is restricted to labels that matured by the cutoff, and the features are
    trailing-only, so a later row can never reach an earlier output.
    """
    featured = _featured_rows(control_features(panel))
    weights = fit_control_weights(_fit_sample(featured, cutoff))
    window = featured.filter(pl.col("date") <= pl.lit(cutoff))
    probabilities = predict_probabilities(weights, window)
    scored = (
        window.select(*_IDENTITY_KEYS, "date", *CONTROL_FEATURES)
        .with_columns(pl.Series("probability", probabilities, dtype=pl.Float64))
        .with_columns(
            pl.when(pl.col("probability") >= DECISION_THRESHOLD).then(pl.lit(1, dtype=pl.Int8)).otherwise(pl.lit(0, dtype=pl.Int8)).alias("decision"),
        )
    )
    return {"weights": weights, "scored": scored, "rows": scored.height}


# ---------------------------------------------------------------------------
# The six leakage controls (residual plan §5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ControlResult:
    """One measured control: what it expected, what it observed, and its named regression."""

    name: str
    regression_test: str
    expected: str
    observed: dict[str, Any]
    passed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "regression_test": self.regression_test,
            "expected": self.expected,
            "observed": self.observed,
            "passed": self.passed,
        }


def leakage_control_append_after_cutoff() -> ControlResult:
    """(1) Append after the cutoff: features, weights, predictions and decisions hold."""
    sessions = run_sessions()
    panel = control_panel(FIXTURE_SEED, sessions=sessions)
    cutoff = sessions[len(sessions) // 2]
    truncated = panel.filter(pl.col("date") <= pl.lit(cutoff))
    before = scored_outputs(truncated, cutoff)
    after = scored_outputs(panel, cutoff)
    weight_delta = max(
        [abs(left - right) for left, right in zip(before["weights"].coefficients, after["weights"].coefficients, strict=True)]
        + [abs(before["weights"].intercept - after["weights"].intercept)]
        + [abs(left - right) for left, right in zip(before["weights"].means, after["weights"].means, strict=True)]
        + [abs(left - right) for left, right in zip(before["weights"].stds, after["weights"].stds, strict=True)]
    )
    observed = {
        "cutoff": cutoff.isoformat(),
        "appended_rows": panel.height - truncated.height,
        "rows_on_or_before_cutoff": before["rows"],
        "features_and_predictions_equal": before["scored"].equals(after["scored"]),
        "max_abs_weight_difference": weight_delta,
        "decisions_before": int(before["scored"]["decision"].sum()),
        "decisions_after": int(after["scored"]["decision"].sum()),
    }
    passed = bool(observed["features_and_predictions_equal"]) and weight_delta == 0.0 and observed["decisions_before"] == observed["decisions_after"]
    return ControlResult(
        name="append_after_cutoff_invariance",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_1_append_after_cutoff_is_invariant",
        expected="identity/date-keyed features, weights, predictions and decisions on/before the cutoff are equal after appending later rows",
        observed=observed,
        passed=passed,
    )


def leakage_control_delayed_publication() -> ControlResult:
    """(2) Delay publication: unavailable before its observable session, earlier outputs equal."""
    sessions = run_sessions()
    panel = control_panel(FIXTURE_SEED, sessions=sessions)
    featured = _featured_rows(control_features(panel))
    instrument = control_instruments()[0]
    delayed_date = sessions[100]
    publication_close = decision_session_close(instrument, sessions[109]) + timedelta(minutes=1)
    observable = source_observable_session(instrument, publication_close)
    available = pl.when(pl.col("label_end_date").is_null()).then(None).otherwise(pl.col("label_end_date"))
    on_time = featured.with_columns(available.alias(AVAILABILITY_COLUMN))
    delayed = featured.with_columns(
        pl.when((pl.col("symbol") == instrument.symbol) & (pl.col("date") == pl.lit(delayed_date)))
        .then(pl.lit(observable))
        .otherwise(available)
        .alias(AVAILABILITY_COLUMN),
    )
    dropped = delayed.filter(~((pl.col("symbol") == instrument.symbol) & (pl.col("date") == pl.lit(delayed_date))))
    early_cutoff = sessions[107]
    late_cutoff = sessions[112]
    eligible_early = filter_as_of_available(delayed, early_cutoff)
    eligible_late = filter_as_of_available(delayed, late_cutoff)
    key = (instrument.symbol, delayed_date)
    delayed_row = pl.col("symbol") == key[0]
    delayed_present_early = eligible_early.filter(delayed_row & (pl.col("date") == pl.lit(key[1]))).height > 0
    delayed_present_late = eligible_late.filter(delayed_row & (pl.col("date") == pl.lit(key[1]))).height > 0
    delayed_outputs = scored_outputs(filter_as_of_available(delayed, early_cutoff), early_cutoff)
    dropped_outputs = scored_outputs(filter_as_of_available(dropped, early_cutoff), early_cutoff)
    on_time_outputs = scored_outputs(filter_as_of_available(on_time, early_cutoff), early_cutoff)
    observed = {
        "delayed_row_date": delayed_date.isoformat(),
        "publication_after_close": publication_close.isoformat(),
        "observable_session": observable.isoformat(),
        "cutoff_before_publication": early_cutoff.isoformat(),
        "delayed_row_eligible_before_publication": bool(delayed_present_early),
        "delayed_row_eligible_after_publication": bool(delayed_present_late),
        "outputs_equal_to_dropped_row": delayed_outputs["scored"].equals(dropped_outputs["scored"]),
        "outputs_differ_from_on_time_row": not delayed_outputs["scored"].equals(on_time_outputs["scored"]),
        "available_before_observable_session": observable > early_cutoff,
    }
    passed = (
        not observed["delayed_row_eligible_before_publication"]
        and observed["delayed_row_eligible_after_publication"]
        and observed["outputs_equal_to_dropped_row"]
        and observed["available_before_observable_session"]
    )
    return ControlResult(
        name="delayed_publication_invariance",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_2_delayed_publication_cannot_reach_earlier_outputs",
        expected="the delayed observation is unavailable before its observable session and its absence from earlier outputs matches a dropped row",
        observed=observed,
        passed=bool(passed),
    )


def strict_proof(instrument: InstrumentKey, start_date: date, end_date: date, *, valid_until: datetime | None = None) -> ActionCoverageProof:
    """Issue a synthetic source-issued strict coverage proof for one instrument."""
    return ActionCoverageProof(
        market=instrument.market,
        mic=instrument.mic,
        symbol=instrument.symbol,
        source=_ACTION_SOURCE,
        coverage_start=start_date,
        coverage_end=end_date,
        source_available_at=decision_session_close(instrument, start_date - timedelta(days=1)),
        source_valid_until=valid_until if valid_until is not None else decision_session_close(instrument, end_date),
    )


def strict_feature_row(
    instrument: InstrumentKey,
    decision_date: date,
    *,
    coverage_start: date,
    coverage_end: date,
    valid_until: datetime | None = None,
    coverage_flag: int = 1,
    proof: ActionCoverageProof | None = None,
) -> dict[str, Any]:
    """One persisted-looking strict Gold feature row, with its (possibly foreign) proof columns.

    ``proof`` lets a control attach another instrument's proof to this row, which is exactly
    the substitution the identity refusal must catch before any fit.
    """
    owned = proof if proof is not None else strict_proof(instrument, coverage_start, coverage_end, valid_until=valid_until)
    row: dict[str, Any] = {
        "market": instrument.market,
        "mic": instrument.mic,
        "symbol": instrument.symbol,
        "date": decision_date,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "action_coverage": coverage_flag,
    }
    row.update(action_coverage_feature_fields(owned))
    row["action_source_age_days"] = (decision_date - source_observable_session(instrument, owned.source_available_at)).days
    return row


def guarded_control_run(
    panel: pl.DataFrame,
    *,
    proofs: Mapping[tuple[str, str, str], ActionCoverageProof],
    cutoff: date,
    write_dir: Path | None = None,
) -> dict[str, Any]:
    """Validate every instrument's proof *before* fitting, so a foreign proof never reaches a fit."""
    instruments = control_instruments()
    for instrument in instruments:
        proof = proofs.get(instrument.tuple)
        if proof is None:
            raise R01Error(f"no coverage proof for {instrument.display_key}: a control run never proceeds on missing coverage")
        latest = max(panel["date"].to_list())
        for decision_date in (min(panel["date"].to_list()), latest):
            validate_feature_action_coverage(
                strict_feature_row(
                    instrument,
                    decision_date,
                    coverage_start=proof.coverage_start,
                    coverage_end=proof.coverage_end,
                    valid_until=proof.source_valid_until,
                    proof=proof,
                )
            )
    scored = scored_outputs(panel, cutoff)
    written: list[str] = []
    if write_dir is not None:
        target = Path(write_dir) / "r01-guarded-run.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"rows": scored["rows"], "cutoff": cutoff.isoformat()}, indent=2) + "\n", encoding="utf-8")
        written.append(target.name)
    return {"fitted": True, "written": written, "rows": scored["rows"]}


def leakage_control_foreign_identity_proof() -> ControlResult:
    """(3) Another instrument's proof: an explicit identity refusal before fit/write."""
    sessions = run_sessions()
    panel = control_panel(FIXTURE_SEED, sessions=sessions)
    instruments = control_instruments()
    aapl, jpm = instruments[0], instruments[3]
    proofs = {instrument.tuple: strict_proof(instrument, sessions[0], sessions[-1]) for instrument in instruments}
    proofs[aapl.tuple] = strict_proof(jpm, sessions[0], sessions[-1])
    refused: str | None = None
    written: list[str] = []
    fitted = False
    try:
        outcome = guarded_control_run(panel, proofs=proofs, cutoff=sessions[-10])
        fitted = bool(outcome["fitted"])
        written = list(outcome["written"])
    except (ValueError, R01Error) as exc:
        refused = str(exc)
    observed = {
        "instrument": aapl.display_key,
        "proof_instrument": jpm.display_key,
        "refused": refused is not None,
        "refusal_reason": refused,
        "identity_refusal": bool(refused is not None and "identity" in (refused or "")),
        "fitted_before_refusal": fitted,
        "files_written": written,
    }
    passed = (
        bool(observed["refused"]) and bool(observed["identity_refusal"]) and not observed["fitted_before_refusal"] and observed["files_written"] == []
    )
    return ControlResult(
        name="foreign_identity_proof_refusal",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_3_foreign_identity_proof_is_refused_before_fit",
        expected="a proof issued for another instrument refuses on identity before any fit and before any write",
        observed=observed,
        passed=passed,
    )


def pending_label_fixture(*, rows: int = 24, horizon: int = 3) -> tuple[list[date], list[date | None], list[date]]:
    """Decision dates and label maturity dates for the pending-label control."""
    sessions = business_days(date(2024, 1, 1), date(2024, 3, 29))[: rows + horizon]
    decisions = sessions[:rows]
    events: list[date | None] = [sessions[index + horizon] for index in range(rows)]
    return sessions, events, decisions


def leakage_control_pending_labels() -> ControlResult:
    """(4) Pending labels: no row whose maturity exceeds the cutoff enters the sample."""
    sessions, events, decisions = pending_label_fixture()
    cutoff = sessions[13]
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=4,
        test_window=2,
        embargo_window=1,
        label_horizon=3,
        event_end_dates=events,
        decision_dates=decisions,
        decision_cutoff=cutoff,
    )
    admitted: list[int] = []
    splits = 0
    for train_index, test_index in splitter.split(np.zeros(len(events)), np.zeros(len(events))):
        splits += 1
        admitted.extend(int(index) for index in train_index)
        admitted.extend(int(index) for index in test_index)
    admitted_ends: list[date] = [end for index in admitted if (end := events[index]) is not None]
    pending = [index for index, end in enumerate(events) if end is not None and end > cutoff]
    mature = [index for index, end in enumerate(events) if end is not None and end <= cutoff]
    observed: dict[str, Any] = {
        "cutoff": cutoff.isoformat(),
        "rows": len(events),
        "splits": splits,
        "admitted_rows": len(admitted),
        "admitted_max_label_end": str(max(admitted_ends)),
        "mature_rows": len(mature),
        "pending_rows": len(pending),
        "pending_rows_admitted": len(set(pending) & set(admitted)),
        "hand_computed_admitted": 3,
    }
    passed = observed["pending_rows"] > 0 and observed["pending_rows_admitted"] == 0 and all(end <= cutoff for end in admitted_ends)
    return ControlResult(
        name="pending_labels_excluded",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_4_pending_labels_never_enter_the_sample",
        expected="no admitted row's label maturity lies after the decision cutoff, while pending rows exist in the fixture",
        observed=observed,
        passed=bool(passed),
    )


def leakage_control_missing_or_expired_coverage() -> ControlResult:
    """(5) Missing/expired coverage: strict refuses, never falls back to raw marks."""
    sessions = run_sessions()
    instrument = control_instruments()[0]
    decision_date = sessions[40]
    expired_validity = decision_session_close(instrument, decision_date - timedelta(days=1))
    scenarios: dict[str, str] = {}
    rows_returned = 0
    for label, row in (
        (
            "expired",
            strict_feature_row(instrument, decision_date, coverage_start=sessions[0], coverage_end=sessions[-1], valid_until=expired_validity),
        ),
        (
            "missing_flag",
            strict_feature_row(instrument, decision_date, coverage_start=sessions[0], coverage_end=sessions[-1], coverage_flag=0),
        ),
    ):
        try:
            validate_feature_action_coverage(row)
            scenarios[label] = "accepted"
            rows_returned += 1
        except ValueError as exc:
            scenarios[label] = str(exc)
    observed = {
        "decision_date": decision_date.isoformat(),
        "expired_source_valid_until": expired_validity.isoformat(),
        "outcomes": scenarios,
        "expired_refused": scenarios["expired"] != "accepted",
        "missing_refused": scenarios["missing_flag"] != "accepted",
        "rows_returned_by_strict_gate": rows_returned,
        "fallback_to_raw_marks": rows_returned > 0,
    }
    passed = observed["expired_refused"] and observed["missing_refused"] and not observed["fallback_to_raw_marks"]
    return ControlResult(
        name="missing_or_expired_coverage_refusal",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_5_expired_or_missing_coverage_is_refused",
        expected="strict coverage validation refuses an expired or unavailable proof and returns no rows, never a raw mark",
        observed=observed,
        passed=bool(passed),
    )


def executable_entry(opens: Sequence[float], index: int) -> tuple[int, float]:
    """The frozen executable bar for a decision taken at ``opens[index]``'s session.

    ``entry_clock = next_session_open`` and ``entry_price = open``: a decision made on
    session *index* can only trade on the following session's open. Entering on the
    decision session's own bar would be look-ahead, so this is the rule the control pins.
    """
    entry_session = index + 1
    if entry_session >= len(opens):
        raise R01Error("a next-session-open entry needs a following session")
    return entry_session, float(opens[entry_session])


def execution_fixture() -> dict[str, Any]:
    """The hand-computed two-session fixture: O0=100, O1=110, O2=121, horizon 2."""
    opens = [100.0, 110.0, 121.0]
    entry_session, entry_price = executable_entry(opens, 0)
    exit_session = entry_session + 1
    gross = float(opens[exit_session]) / entry_price - 1.0
    return {
        "opens": opens,
        "decision_session": 0,
        "entry_session": entry_session,
        "entry_price": entry_price,
        "exit_session": exit_session,
        "exit_price": float(opens[exit_session]),
        "gross_return": gross,
        "hand_computed_return": 0.1,
    }


def leakage_control_execution_lag_horizon() -> ControlResult:
    """(6) Execution lag/horizon: only the frozen executable bar, plus interval purging."""
    fixture = execution_fixture()
    sessions, events, decisions = pending_label_fixture()
    cutoff = sessions[8]
    splitter = PurgedEmbargoedWalkForwardSplitter(
        train_window=4,
        test_window=2,
        embargo_window=1,
        label_horizon=3,
        event_end_dates=events,
        decision_dates=decisions,
        decision_cutoff=cutoff,
    )
    admitted: list[int] = []
    for train_index, test_index in splitter.split(np.zeros(len(events)), np.zeros(len(events))):
        admitted.extend(int(index) for index in train_index)
        admitted.extend(int(index) for index in test_index)
    pending = [index for index, end in enumerate(events) if end is not None and end > cutoff]
    observed = {
        "entry_session": fixture["entry_session"],
        "entry_price": fixture["entry_price"],
        "exit_session": fixture["exit_session"],
        "gross_return": fixture["gross_return"],
        "hand_computed_return": fixture["hand_computed_return"],
        "same_bar_entry_price": fixture["opens"][fixture["decision_session"]],
        "decision_session_purged_from_fit": sorted(set(pending) & set(admitted)) == [],
        "pending_intervals_after_cutoff": len(pending),
        "admitted_rows": len(admitted),
    }
    passed = (
        fixture["entry_session"] == 1
        and fixture["entry_price"] == 110.0
        and abs(fixture["gross_return"] - fixture["hand_computed_return"]) < 1e-12
        and bool(observed["decision_session_purged_from_fit"])
    )
    return ControlResult(
        name="execution_lag_and_label_purge",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_6_execution_lag_and_purge_match_hand_math",
        expected=(
            "a decision enters only on the frozen next-session open (110.0 -> 121.0 = +10.0%) "
            "and label intervals crossing the fit cutoff stay out of the sample"
        ),
        observed=observed,
        passed=bool(passed),
    )


LEAKAGE_CONTROLS: tuple[tuple[str, Callable[[], ControlResult]], ...] = (
    ("append_after_cutoff_invariance", leakage_control_append_after_cutoff),
    ("delayed_publication_invariance", leakage_control_delayed_publication),
    ("foreign_identity_proof_refusal", leakage_control_foreign_identity_proof),
    ("pending_labels_excluded", leakage_control_pending_labels),
    ("missing_or_expired_coverage_refusal", leakage_control_missing_or_expired_coverage),
    ("execution_lag_and_label_purge", leakage_control_execution_lag_horizon),
)


def run_leakage_controls() -> list[dict[str, Any]]:
    """Measure every leakage control; a raising control is recorded as a failure, not dropped."""
    records: list[dict[str, Any]] = []
    for name, control in LEAKAGE_CONTROLS:
        try:
            records.append(control().as_dict())
        except Exception as exc:  # a broken control is evidence of a failing control
            records.append(
                {
                    "name": name,
                    "regression_test": f"tests/unit/test_r01_controls.py::{name}",
                    "expected": "the control to run and hold",
                    "observed": {"error": f"{type(exc).__name__}: {exc}"},
                    "passed": False,
                }
            )
    return records


# ---------------------------------------------------------------------------
# Planted/null controls over the predeclared seeds
# ---------------------------------------------------------------------------


def _permuted_labels(sample: pl.DataFrame, seed: int) -> pl.DataFrame:
    """Permute labels within each instrument: marginals kept, the temporal link broken."""
    rng = np.random.default_rng(seed + 7919)
    frames: list[pl.DataFrame] = []
    for _key, group in sample.group_by(_IDENTITY_KEYS, maintain_order=True):
        labels = group["label"].to_numpy().copy()
        rng.shuffle(labels)
        frames.append(group.with_columns(pl.Series("label", labels, dtype=pl.Float64)))
    return pl.concat(frames).sort(*_SORT_KEYS)


def _mean_lower_bound(values: np.ndarray, seed: int) -> float:
    """One-sided 95% lower bound on the mean per-session value (moving-block bootstrap)."""
    if values.size < BOOTSTRAP_BLOCK_SESSIONS:
        return float("nan")
    positions = _block_positions(values.size, BOOTSTRAP_BLOCK_SESSIONS, BOOTSTRAP_RESAMPLES, seed)
    return float(np.quantile(values[positions].mean(axis=1), NOMINAL_ALPHA_ONE_SIDED))


_NULL_VARIANTS: tuple[Literal["null_noise"], Literal["null_permuted"]] = ("null_noise", "null_permuted")


def control_outcome(
    seed: int,
    *,
    kind: Literal["planted", "null_noise", "null_permuted"],
    panel: pl.DataFrame | None = None,
) -> dict[str, Any]:
    """One predeclared seed's outcome: fit, predict, decide, then the frozen rule.

    The statistic is the mean per-session net return (never the compounded net return);
    the estimate is charged the frozen round-trip cost through R02's own
    :func:`_strategy_returns`, so the estimand is the one the interval describes.
    """
    sessions = run_sessions()
    fixture = panel if panel is not None else control_panel(seed, sessions=sessions, planted=kind == "planted")
    featured = _featured_rows(control_features(fixture))
    usable = [session for session in sessions if session >= sessions[WARMUP_SESSIONS]]
    train_end = usable[max(1, int(len(usable) * TRAIN_FRACTION)) - 1]
    sample = _fit_sample(featured.filter(pl.col("date") <= pl.lit(train_end)), train_end)
    if kind == "null_permuted":
        sample = _permuted_labels(sample, seed)
    weights = fit_control_weights(sample)
    evaluation = featured.filter(pl.col("date") > pl.lit(train_end)).filter(pl.col("date").is_in(usable))
    probabilities = predict_probabilities(weights, evaluation)
    scored = evaluation.select(*_IDENTITY_KEYS, "date").with_columns(pl.Series("probability", probabilities, dtype=pl.Float64))
    picks: dict[date, list[tuple[str, str, str]]] = {}
    for row in scored.filter(pl.col("probability") >= DECISION_THRESHOLD).select(*_IDENTITY_KEYS, "date").to_dicts():
        picks.setdefault(row["date"], []).append((str(row["market"]), str(row["mic"]), str(row["symbol"])))
    closes: dict[tuple[str, str, str], dict[date, float]] = {}
    for row in fixture.select(*_IDENTITY_KEYS, "date", "close").to_dicts():
        closes.setdefault((str(row["market"]), str(row["mic"]), str(row["symbol"])), {})[row["date"]] = float(row["close"])
    returns = _strategy_returns(picks=picks, sessions=sessions, closes=closes, cost=ROUND_TRIP_COST, holding=HOLDING_SESSIONS)
    evaluation_sessions = sorted(set(evaluation["date"].to_list()))
    returns = returns.filter(pl.col("date").is_in(evaluation_sessions))
    values = returns["net_return"].to_numpy().astype(np.float64, copy=False)
    economics = _economic_summary(returns)
    lower = _mean_lower_bound(values, seed)
    return {
        "seed": seed,
        "kind": kind,
        "evaluation_sessions": int(values.size),
        "decisions": int(scored["probability"].ge(DECISION_THRESHOLD).sum()),
        "statistic": PRIMARY_STATISTIC,
        "estimand": "mean_per_session_net_return",
        "mean_per_session_net_return": float(economics["mean_daily_return"]),
        "compounded_net_return": float(economics["net_return"]),
        "sharp_ratio": float(economics["sharpe_ratio"]),
        "lower_bound_one_sided_95": lower,
        "detected": bool(lower > 0.0),
    }


def _seed_set_summary(outcomes: Sequence[Mapping[str, Any]], *, positives: int) -> dict[str, Any]:
    """Aggregate a seed set into the preregistered counts and bounds."""
    seeds = len(outcomes)
    return {"seeds": seeds, "positive_seeds": positives}


def run_seed_set(
    seeds: Sequence[int],
    *,
    kind: Literal["planted", "null_noise", "null_permuted"],
    budget: Budget,
) -> dict[str, Any]:
    """Run one predeclared seed set inside the remaining ceiling; never widen it.

    If the measured per-seed cost makes the remaining seeds unaffordable, the set stops
    and is recorded ``censored`` with the measured cost — an honest partial record, never
    a widened budget and never a passing claim.
    """
    outcomes: list[dict[str, Any]] = []
    durations: list[float] = []
    censored_reason: str | None = None
    started = time.monotonic()
    for index, seed in enumerate(seeds):
        if index > 0:
            elapsed = time.monotonic() - started
            median = float(np.median(durations))
            projected = elapsed + median * (len(seeds) - index)
            if budget.would_exceed(projected):
                censored_reason = (
                    f"stopped after {index} of {len(seeds)} {kind} seeds: measured median {median:.4f}s/seed projects "
                    f"{projected:.1f}s for the full set, beyond the remaining frozen ceiling "
                    f"{budget.remaining():.1f}s of {budget.ceiling_seconds:.0f}s aggregate"
                )
                break
        clock = time.monotonic()
        outcome = control_outcome(seed, kind=kind)
        durations.append(time.monotonic() - clock)
        outcomes.append(outcome)
    elapsed = time.monotonic() - started
    budget.spend(elapsed)
    positives = sum(1 for outcome in outcomes if outcome["detected"])
    return {
        "kind": kind,
        "requested_seeds": list(seeds),
        "seeds": len(outcomes),
        "seconds": elapsed,
        "median_seconds_per_seed": float(np.median(durations)) if durations else float("nan"),
        "positive_seeds": positives,
        "censored": censored_reason is not None,
        "censored_reason": censored_reason,
        "outcomes": outcomes,
    }


def _binomial_cdf(successes: int, trials: int, probability: float) -> float:
    """P(X <= successes) for X ~ Binomial(trials, probability), exactly in floats."""
    total = 0.0
    for count in range(successes + 1):
        total += math.comb(trials, count) * probability**count * (1.0 - probability) ** (trials - count)
    return total


def _bisect_probability(reaches_alpha: Callable[[float], bool]) -> float:
    """The probability at which the monotone predicate flips, by bisection."""
    low, high = 0.0, 1.0
    for _ in range(200):
        middle = (low + high) / 2.0
        if reaches_alpha(middle):
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


def binomial_bounds(successes: int, trials: int, *, alpha: float = NOMINAL_ALPHA_ONE_SIDED) -> dict[str, float]:
    """Exact one-sided 95% binomial bounds (the Clopper-Pearson one-sided pair).

    The upper bound solves ``P(X <= successes; n, p) = alpha`` and the lower bound solves
    ``P(X >= successes; n, p) = alpha``; both are computed by bisection on the exact
    binomial CDF, so the gate carries no new dependency and the arithmetic is reviewable.
    """
    if trials <= 0:
        raise R01Error("a binomial bound needs at least one trial")
    if successes < 0 or successes > trials:
        raise R01Error(f"{successes} successes out of {trials} trials is impossible")
    upper = 1.0 if successes >= trials else _bisect_probability(lambda probability: _binomial_cdf(successes, trials, probability) > alpha)
    lower = 0.0 if successes <= 0 else _bisect_probability(lambda probability: 1.0 - _binomial_cdf(successes - 1, trials, probability) < alpha)
    return {"lower": lower, "upper": upper}


class Budget:
    """The remaining aggregate ceiling shared by controls and scale."""

    def __init__(self, ceiling_seconds: float) -> None:
        self.ceiling_seconds = ceiling_seconds
        self.spent_seconds = 0.0

    def remaining(self) -> float:
        return max(0.0, self.ceiling_seconds - self.spent_seconds)

    def spend(self, seconds: float) -> None:
        self.spent_seconds += seconds

    def would_exceed(self, projected_seconds: float) -> bool:
        return projected_seconds > self.remaining()


# ---------------------------------------------------------------------------
# The controls run
# ---------------------------------------------------------------------------


def require_frozen_r01(*, protocol_root: Path | None = None, protocol_digest: str | None = None) -> tuple[FrozenProtocol, ResearchProtocol, R01Spec]:
    """Refuse to run unless this exact protocol and prespecification are already frozen."""
    protocol = r01_protocol()
    spec = r01_spec()
    frozen = require_frozen_protocol(protocol, root=protocol_root)
    if protocol_digest is not None and frozen.protocol_digest != protocol_digest:
        raise ProtocolError(
            f"the frozen R01 manifest carries digest {frozen.protocol_digest[:12]}, not the claimed {protocol_digest[:12]}; "
            "a control artifact is never bound to a protocol the run did not use"
        )
    bound = frozen.input_digests.get("r01_preregistration")
    if bound != spec.digest():
        raise ProtocolError(
            f"the frozen manifest does not bind this prespecification (frozen {bound}, candidate {spec.digest()[:12]}); "
            "freeze the preregistration before running (R00)"
        )
    return frozen, protocol, spec


def run_controls(
    *,
    protocol_root: Path | None = None,
    protocol_digest: str | None = None,
    budget: Budget | None = None,
    null_seed_limit: int | None = None,
    planted_seed_limit: int | None = None,
) -> dict[str, Any]:
    """Run the leakage controls and the planted/null sets under the frozen protocol.

    A seed limit is a *bounded demonstration*: it runs a prefix of the predeclared set and
    records the set ``censored``, because a censored set cannot establish a power claim.
    """
    frozen, _protocol, spec = require_frozen_r01(protocol_root=protocol_root, protocol_digest=protocol_digest)
    allocation = budget if budget is not None else Budget(spec.aggregate_ceiling_seconds)
    started = time.monotonic()
    leakage = run_leakage_controls()
    null_rows: list[dict[str, Any]] = []
    null_seeds = list(NULL_SEEDS)[: null_seed_limit if null_seed_limit is not None else len(NULL_SEEDS)]
    planted_seeds = list(PLANTED_SEEDS)[: planted_seed_limit if planted_seed_limit is not None else len(PLANTED_SEEDS)]
    for variant in _NULL_VARIANTS:
        null_rows.append(run_seed_set(null_seeds, kind=variant, budget=allocation))
    planted = run_seed_set(planted_seeds, kind="planted", budget=allocation)

    false_positive_seeds = sorted({outcome["seed"] for row in null_rows for outcome in row["outcomes"] if outcome["detected"]})
    null_trials = len(null_seeds)
    null_bounds = binomial_bounds(len(false_positive_seeds), null_trials)
    planted_bounds = binomial_bounds(planted["positive_seeds"], max(1, planted["seeds"]))
    censored_reasons = [row["censored_reason"] for row in null_rows if row["censored"]]
    if planted["censored"]:
        censored_reasons.append(planted["censored_reason"])
    seed_sets_complete = all(row["seeds"] == len(null_seeds) for row in null_rows) and planted["seeds"] == len(planted_seeds)
    complete_set_sizes = len(null_seeds) == len(NULL_SEEDS) and len(planted_seeds) == len(PLANTED_SEEDS)
    leakage_passed = all(record["passed"] for record in leakage)
    fpr_clears = null_bounds["upper"] <= spec.fpr_upper_bound_max
    power_clears = planted_bounds["lower"] >= spec.power_lower_bound_min
    censored = not seed_sets_complete or not complete_set_sizes
    passed = leakage_passed and fpr_clears and power_clears and not censored
    elapsed = time.monotonic() - started
    return {
        "protocol": {
            "experiment": frozen.protocol.experiment,
            "digest": frozen.protocol_digest,
            "recorded_at": frozen.recorded_at.isoformat(),
            "preregistration_digest": spec.digest(),
        },
        "preregistration": spec.model_dump(mode="json"),
        "leakage_controls": leakage,
        "planted_null": {
            "statistic": PRIMARY_STATISTIC,
            "estimand": PRIMARY_ESTIMAND,
            "resampling": RESAMPLING_RULE,
            "null": {"variants": [row["kind"] for row in null_rows], "sets": null_rows, "false_positive_seeds": false_positive_seeds},
            "planted": planted,
            "bounds": {
                "null_false_positive_rate": {
                    "successes": len(false_positive_seeds),
                    "trials": null_trials,
                    **null_bounds,
                    "ceiling": spec.fpr_upper_bound_max,
                },
                "planted_power": {
                    "successes": planted["positive_seeds"],
                    "trials": planted["seeds"],
                    **planted_bounds,
                    "floor": spec.power_lower_bound_min,
                },
            },
            "seed_sets_complete": seed_sets_complete,
            "complete_set_sizes": complete_set_sizes,
            "censored": censored,
            "censored_reasons": [reason for reason in censored_reasons if reason],
        },
        "verdict": {
            "leakage_controls_passed": leakage_passed,
            "false_positive_rate_upper_bound_clears": fpr_clears,
            "power_lower_bound_clears": power_clears,
            "passed": passed,
            "censored": censored,
        },
        "seconds": elapsed,
        "budget_spent_seconds": allocation.spent_seconds,
    }


# ---------------------------------------------------------------------------
# One-rule mutation proofs (each mutant must fail its own named regression)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationSpec:
    """One rule removed from source, and the regression that must notice."""

    name: str
    protected: str
    path: str
    find: str
    replace: str
    regression_test: str


MUTATIONS: tuple[MutationSpec, ...] = (
    MutationSpec(
        name="leakage_1_label_maturity_rule",
        protected="append_after_cutoff_invariance",
        path="src/equity_lake/ml/r01_controls.py",
        find='    mature = pl.col("label").is_not_null() & pl.col("label_end_date").is_not_null() & (pl.col("label_end_date") <= pl.lit(cutoff))\n'
        "    return featured.filter(mature)",
        replace='    mature = pl.col("label").is_not_null() & pl.col("label_end_date").is_not_null()\n    return featured.filter(mature)',
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_1_append_after_cutoff_is_invariant",
    ),
    MutationSpec(
        name="leakage_2_as_of_availability_rule",
        protected="delayed_publication_invariance",
        path="src/equity_lake/features/labels.py",
        find="return frame.filter(available.is_not_null() & (available <= pl.lit(cutoff)))",
        replace="return frame.filter(available.is_not_null())",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_2_delayed_publication_cannot_reach_earlier_outputs",
    ),
    MutationSpec(
        name="leakage_3_proof_identity_rule",
        protected="foreign_identity_proof_refusal",
        path="src/equity_lake/core/availability.py",
        find=(
            "    if proof.identity != instrument.tuple:\n"
            '        raise AvailabilityContractError(f"coverage proof identity does not match {instrument.display_key}")\n'
        ),
        replace="",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_3_foreign_identity_proof_is_refused_before_fit",
    ),
    MutationSpec(
        name="leakage_4_decision_cutoff_rule",
        protected="pending_labels_excluded",
        path="src/equity_lake/ml/validation.py",
        find=(
            "[index for index, raw_end in enumerate(self.event_end_dates) if raw_end is not None and (cutoff is None or _as_date(raw_end) <= cutoff)]"
        ),
        replace="[index for index, raw_end in enumerate(self.event_end_dates) if raw_end is not None]",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_4_pending_labels_never_enter_the_sample",
    ),
    MutationSpec(
        name="leakage_5_source_expiry_rule",
        protected="missing_or_expired_coverage_refusal",
        path="src/equity_lake/core/availability.py",
        find="    if proof.source_valid_until < decision_session_close(instrument, decision_date):\n"
        '        raise AvailabilityContractError(f"source validity does not cover {instrument.display_key} through decision-session close")\n',
        replace="",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_5_expired_or_missing_coverage_is_refused",
    ),
    MutationSpec(
        name="leakage_6_executable_bar_rule",
        protected="execution_lag_and_label_purge",
        path="src/equity_lake/ml/r01_controls.py",
        find="    entry_session = index + 1\n    if entry_session >= len(opens):",
        replace="    entry_session = index\n    if entry_session >= len(opens):",
        regression_test="tests/unit/test_r01_controls.py::test_leakage_control_6_execution_lag_and_purge_match_hand_math",
    ),
    MutationSpec(
        name="refusal_unfrozen_protocol_rule",
        protected="unfrozen_protocol_refusal",
        path="src/equity_lake/backtesting/protocol.py",
        find="records = [record for record in load_frozen_protocols(root) if record.protocol.experiment == protocol.experiment]",
        replace="records = list(load_frozen_protocols(root))",
        regression_test="tests/unit/test_r01_controls.py::test_a_foreign_frozen_protocol_cannot_stand_in_for_this_one",
    ),
    MutationSpec(
        name="refusal_over_budget_rule",
        protected="over_budget_refusal",
        path="src/equity_lake/ml/r01_scale.py",
        find="    if elapsed > ceilings.per_size_seconds:\n        raise ScaleError(",
        replace="    if False:\n        raise ScaleError(",
        regression_test="tests/unit/test_r01_scale.py::test_an_over_budget_scale_point_refuses",
    ),
)


def repo_root() -> Path:
    """The repository root this module lives in."""
    return Path(__file__).resolve().parents[3]


def run_mutation_proofs(*, root: Path | None = None, python: str | None = None) -> list[dict[str, Any]]:
    """Apply each mutation alone, run its named regression, and restore from a /tmp copy.

    A mutant is *killed* when its named regression fails (a non-zero pytest exit). The
    original bytes are restored in a ``finally`` block and verified with ``diff -q``, so a
    failed proof can never leave a mutated tree behind.
    """
    base = root if root is not None else repo_root()
    interpreter = python if python is not None else sys.executable
    scratch = Path("/tmp") / "r01-mutations"
    scratch.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for spec in MUTATIONS:
        target = base / spec.path
        backup = scratch / f"{spec.name}-{target.name}"
        original = target.read_text(encoding="utf-8")
        if original.count(spec.find) != 1:
            records.append({"name": spec.name, "protected": spec.protected, "applied": False, "error": "mutation anchor is not unique"})
            continue
        shutil.copy2(target, backup)
        record: dict[str, Any] = {
            "name": spec.name,
            "protected": spec.protected,
            "path": spec.path,
            "regression_test": spec.regression_test,
            "mutation": {"find": spec.find, "replace": spec.replace},
        }
        try:
            target.write_text(original.replace(spec.find, spec.replace, 1), encoding="utf-8")
            completed = subprocess.run(
                [interpreter, "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", spec.regression_test],
                cwd=base,
                capture_output=True,
                text=True,
                timeout=1800,
            )
            record["mutated_regression_exit_code"] = completed.returncode
            record["mutant_killed"] = completed.returncode != 0
            record["tail"] = (completed.stdout or completed.stderr).strip().splitlines()[-1:] or [""]
        finally:
            shutil.copy2(backup, target)
        restored = subprocess.run(["diff", "-q", str(backup), str(target)], capture_output=True, text=True)
        record["restore_diff_exit_code"] = restored.returncode
        record["restored_identical"] = restored.returncode == 0
        records.append(record)
    return records


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """Replace a non-finite float with ``null``: an unestimable value is absent, never a fake number."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _write_results(path: Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_json_safe(dict(payload)), indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")
    return target


def _status(payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    """Compute the verification status; partial evidence is never called verified."""
    controls = payload.get("controls") or {}
    scale = payload.get("scale") or {}
    reasons: list[str] = []
    controls_verdict = (controls.get("verdict") or {}) if isinstance(controls, Mapping) else {}
    leakage = [record for record in (controls.get("leakage_controls") or []) if isinstance(record, Mapping)]
    failed = [str(record.get("name")) for record in leakage if not record.get("passed")]
    if len(leakage) < len(LEAKAGE_CONTROLS) or failed:
        reasons.append(f"leakage controls incomplete or failing: {failed or 'missing records'}")
        return "unverified", reasons
    if controls_verdict.get("censored"):
        reasons.append("seed sets censored: a censored set cannot establish a power claim")
    if not scale:
        reasons.append("no scale measurement recorded")
    scale_status = str(scale.get("verification_status", "absent")) if isinstance(scale, Mapping) else "absent"
    if scale_status != "verified":
        reasons.append(f"scale evidence is {scale_status}")
    proofs = [record for record in (payload.get("mutation_proofs") or []) if isinstance(record, Mapping)]
    surviving = [str(record.get("name")) for record in proofs if not record.get("mutant_killed") or not record.get("restored_identical")]
    if len(proofs) < len(MUTATIONS) or surviving:
        reasons.append(f"mutation proofs incomplete or surviving: {surviving or 'missing records'}")
        return "unverified", reasons
    if controls_verdict.get("censored") or scale_status != "verified":
        return "partially-verified", reasons
    if not controls_verdict.get("passed"):
        reasons.append("planted/null criteria did not clear")
        return "unverified", reasons
    return "verified", reasons


def main(argv: Sequence[str] | None = None) -> int:
    """``python -m equity_lake.ml.r01_controls``: freeze, measure controls, or prove mutations."""
    import argparse

    parser = argparse.ArgumentParser(description="R01 controls harness (measured R00 controls)")
    parser.add_argument("--mode", choices=("freeze", "controls", "mutations", "scale", "all"), default="controls")
    parser.add_argument("--recorded-at", default=None, help="ISO-8601 UTC freeze timestamp (mandatory for --mode freeze)")
    parser.add_argument("--protocol-root", default=None, help="root whose research/protocols directory holds frozen manifests")
    parser.add_argument("--results", default=None, help="results JSON target")
    parser.add_argument("--null-seed-limit", type=int, default=None, help="bounded demonstration: run only a prefix of the null set (censors it)")
    parser.add_argument(
        "--planted-seed-limit", type=int, default=None, help="bounded demonstration: run only a prefix of the planted set (censors it)"
    )
    arguments = parser.parse_args(list(argv) if argv is not None else None)

    protocol_root = Path(arguments.protocol_root) if arguments.protocol_root else None
    if arguments.mode == "freeze":
        if not arguments.recorded_at:
            print("--recorded-at is mandatory: the freeze timestamp is the preregistration's proof of precedence", file=sys.stderr)
            return 2
        recorded_at = datetime.fromisoformat(arguments.recorded_at)
        if recorded_at.tzinfo is None:
            recorded_at = recorded_at.replace(tzinfo=UTC)
        frozen, path = freeze_r01_protocol(recorded_at=recorded_at, root=protocol_root)
        print(json.dumps({"manifest": str(path), "protocol_digest": frozen.protocol_digest, "preregistration_digest": r01_spec().digest()}))
        return 0

    if arguments.mode == "mutations":
        records = run_mutation_proofs()
        print(json.dumps(records, indent=2))
        return 0 if all(record.get("mutant_killed") and record.get("restored_identical") for record in records) else 1

    budget = Budget(AGGREGATE_CEILING_SECONDS)
    payload: dict[str, Any] = {
        "stage": "RS-R01",
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": arguments.mode,
    }
    if arguments.mode in ("controls", "all"):
        payload["controls"] = run_controls(
            protocol_root=protocol_root,
            budget=budget,
            null_seed_limit=arguments.null_seed_limit,
            planted_seed_limit=arguments.planted_seed_limit,
        )
    if arguments.mode in ("scale", "all"):
        from equity_lake.ml import r01_scale

        payload["scale"] = r01_scale.run_scale(protocol_root=protocol_root, budget=budget)
    if arguments.mode == "all":
        payload["mutation_proofs"] = run_mutation_proofs()
    status, reasons = _status(payload)
    payload["verification_status"] = status
    payload["status_reasons"] = reasons
    if arguments.results:
        _write_results(Path(arguments.results), payload)
    print(json.dumps({"verification_status": status, "status_reasons": reasons, "results": arguments.results}, indent=2))
    return 0 if status == "verified" else 1


__all__ = [
    "AGGREGATE_CEILING_SECONDS",
    "CONTROL_UNIVERSE",
    "ControlResult",
    "LEAKAGE_CONTROLS",
    "MUTATIONS",
    "NULL_SEEDS",
    "PLANTED_SEEDS",
    "PLANTED_EFFECT",
    "PRIMARY_ESTIMAND",
    "PRIMARY_STATISTIC",
    "R01Error",
    "R01Spec",
    "RESAMPLING_RULE",
    "R01_EXPERIMENT",
    "binomial_bounds",
    "control_features",
    "control_instruments",
    "control_outcome",
    "control_panel",
    "control_sessions",
    "execution_fixture",
    "freeze_r01_protocol",
    "guarded_control_run",
    "leakage_control_append_after_cutoff",
    "leakage_control_delayed_publication",
    "leakage_control_execution_lag_horizon",
    "leakage_control_foreign_identity_proof",
    "leakage_control_missing_or_expired_coverage",
    "leakage_control_pending_labels",
    "protocols_root",
    "r01_protocol",
    "r01_spec",
    "require_frozen_r01",
    "results_root",
    "run_controls",
    "run_leakage_controls",
    "run_mutation_proofs",
    "run_sessions",
    "scored_outputs",
    "strict_feature_row",
    "strict_proof",
]


if __name__ == "__main__":
    raise SystemExit(main())
