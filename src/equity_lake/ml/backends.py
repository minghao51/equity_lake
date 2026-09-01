"""Pluggable model backends (XGBoost, LightGBM) for the ML pipeline.

This is the single seam that owns *all* backend divergence (handoff B4). It
exists so the four XGBoost construction sites in ``forecasting.py`` and
``validation.py`` can be replaced by a swappable factory instead of forking each
call site per backend:

* ``forecasting.PriceForecaster.train_model`` default fit
* ``forecasting.PriceForecaster._tune_hyperparameters`` (GridSearchCV estimator)
* ``forecasting.PriceForecaster.backtest`` periodic retrain
* ``validation.run_purged_walk_forward_validation`` per-fold estimator

Callers keep one *canonical* (XGBoost-style) parameter dict and let
``build_estimator`` / ``normalize_params`` translate per backend, so the XGBoost
path is behaviorally identical to the previous direct construction and existing
``_xgboost_`` model files keep loading (``DEFAULT_BACKEND == "xgboost"``).

Because every fit path already imports this module, it is also the single home
for the canonical parameter base (:data:`DEFAULT_XGB_PARAMS`,
:func:`canonical_training_params`) and the class-imbalance ratio
(:func:`scale_pos_weight`), which used to be copied per call site.
"""

from __future__ import annotations

from typing import Any, Final, Literal, Protocol, cast, runtime_checkable

import structlog

from equity_lake.ml._intel import configure_intel_runtime, intel_thread_count

# Preset the Intel runtime (OMP/MKL env + optional sklearnex patch) BEFORE the
# heavy imports below read their thread settings. No-op on non-Intel CPUs.
_INTEL_INFO = configure_intel_runtime()

import numpy as np  # noqa: E402  (must follow the Intel env preset)
import xgboost as xgb  # noqa: E402  (must follow the Intel env preset)  # core dependency; LightGBM is imported lazily below.

logger = structlog.get_logger(__name__)

#: Canonical backend identifiers. These strings are also the model-filename token.
SUPPORTED_BACKENDS: Final[frozenset[str]] = frozenset({"xgboost", "lightgbm"})

#: Default backend (kept as ``"xgboost"`` to preserve the existing ``_xgboost_``
#: filename token and pre-Phase-2 model artifacts).
DEFAULT_BACKEND: Final[BackendName] = "xgboost"

BackendName = Literal["xgboost", "lightgbm"]

# Canonical (XGBoost-style) -> LightGBM native parameter names.
_LGBM_PARAM_MAP: Final[dict[str, str]] = {
    "colsample_bytree": "feature_fraction",
}

# LightGBM native -> XGBoost parameter names (lets a LightGBM-native caller build XGBoost).
_XGB_PARAM_MAP: Final[dict[str, str]] = {
    "feature_fraction": "colsample_bytree",
}

# Params owned centrally by :func:`build_estimator`, never passed through
# ``normalize_params`` (D3): ``scale_pos_weight`` is injected solely via the
# ``build_estimator`` kwarg so imbalance handling has exactly one code path.
_CENTRAL_PARAMS: Final[frozenset[str]] = frozenset({"scale_pos_weight"})

# Constructor-only params that LightGBM does not accept (it infers the metric from
# the objective; ``eval_metric`` is a ``.fit()`` argument for LightGBM, not a
# constructor argument, and the binary objective already defaults to logloss).
_LGBM_DROP_KEYS: Final[frozenset[str]] = frozenset({"eval_metric"})

# XGBoost objective strings -> LightGBM objective enums.
_LGBM_OBJECTIVE_MAP: Final[dict[str, str]] = {
    "binary:logistic": "binary",
}

#: Canonical (XGBoost-style) params shared by *every* fit path in the package.
#: The two parameter homes derive from this base and restate only their
#: intentional divergence in tree depth / learning rate / boosting rounds:
#: :func:`canonical_training_params` (production fits, tuned) and
#: ``_metrics.DEFAULT_FIT_PARAMS`` (comparison/ablation harness, deliberately
#: cheap). Lives here because ``backends`` is the seam every fit path imports.
DEFAULT_XGB_PARAMS: Final[dict[str, Any]] = {
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "random_state": 42,
    "n_jobs": -1,
}

#: Production-fit divergence from :data:`DEFAULT_XGB_PARAMS`.
_TRAINING_PARAM_OVERRIDES: Final[dict[str, Any]] = {
    "max_depth": 5,
    "learning_rate": 0.05,
    "n_estimators": 200,
}


def canonical_training_params(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Canonical (XGBoost-style) params shared by every production fit.

    ``forecasting.PriceForecaster.train_model`` / ``.backtest`` and
    ``validation.run_purged_walk_forward_validation`` must fit with identical
    parameter and class-weight semantics (handoff 08 A3): :func:`build_estimator`
    normalizes per backend, so callers pass one canonical dict plus explicit
    overrides. An override value of ``None`` *drops* the base key (``None`` is
    stripped by :func:`normalize_params`).
    """
    params: dict[str, Any] = {**_TRAINING_PARAM_OVERRIDES, **DEFAULT_XGB_PARAMS}
    if overrides:
        params.update(overrides)
    return params


def scale_pos_weight(y_train: np.ndarray) -> float:
    """Class-imbalance ratio (negatives / positives) for :func:`build_estimator`.

    Single implementation for every fit path: ``trainer.compute_class_weights``,
    the walk-forward validator, and the comparison/ablation harnesses (which
    reach it via ``_metrics``). Returns ``1.0`` for an empty or single-class
    split so the estimator keeps its unweighted default.
    """
    pos = int((y_train == 1).sum())
    neg = int((y_train == 0).sum())
    if pos <= 0 or neg <= 0:
        return 1.0
    return float(neg) / float(pos)


@runtime_checkable
class ModelBackend(Protocol):
    """Structural surface every backend estimator must satisfy.

    Both ``xgboost.XGBClassifier`` and ``lightgbm.LGBMClassifier`` conform, so no
    wrapper class is needed. Fitted models additionally expose
    ``feature_importances_`` (consumed by ``trainer.compute_shap_importance`` via
    ``shap.TreeExplainer``, which supports both backends).
    """

    def fit(self, X: Any, y: Any, **fit_kwargs: Any) -> Any: ...

    def predict(self, X: Any) -> Any: ...

    def predict_proba(self, X: Any) -> Any: ...

    def get_params(self, deep: bool = True) -> dict[str, Any]: ...


def validate_backend(name: str) -> BackendName:
    """Return the canonical backend identifier or raise ``ValueError``."""
    normalized = str(name).strip().lower()
    if normalized not in SUPPORTED_BACKENDS:
        raise ValueError(
            f"Unsupported backend {name!r}; expected one of {sorted(SUPPORTED_BACKENDS)}.",
        )
    return cast("BackendName", normalized)


def normalize_params(backend: BackendName, params: dict[str, Any]) -> dict[str, Any]:
    """Translate canonical (XGBoost-style) params to the backend's native spelling.

    Drops ``None`` values on both backends so callers can pass sparse override
    dicts. ``scale_pos_weight`` is intentionally NOT handled here (it is injected
    by :func:`build_estimator`) so callers control imbalance handling centrally.
    """
    backend = validate_backend(backend)
    if backend == "xgboost":
        return _normalize_xgboost(params)
    return _normalize_lightgbm(params)


def _reject_param_collision(params: dict[str, Any]) -> None:
    """Reject ambiguous colsample spelling (D5).

    ``colsample_bytree`` (XGBoost) and ``feature_fraction`` (LightGBM) are the
    same knob; passing both is a silent last-write-wins bug, so fail fast.
    """
    if "colsample_bytree" in params and "feature_fraction" in params:
        raise ValueError(
            "Conflicting params 'colsample_bytree' and 'feature_fraction': pass only the canonical 'colsample_bytree' (normalized per backend).",
        )


def _normalize_xgboost(params: dict[str, Any]) -> dict[str, Any]:
    _reject_param_collision(params)
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or key in _CENTRAL_PARAMS:
            continue
        normalized[_XGB_PARAM_MAP.get(key, key)] = value
    return normalized


def _normalize_lightgbm(params: dict[str, Any]) -> dict[str, Any]:
    _reject_param_collision(params)
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        if value is None or key in _CENTRAL_PARAMS or key in _LGBM_DROP_KEYS:
            continue
        normalized[_LGBM_PARAM_MAP.get(key, key)] = value
    objective = normalized.get("objective")
    if isinstance(objective, str):
        mapped = _LGBM_OBJECTIVE_MAP.get(objective)
        if mapped is not None:
            normalized["objective"] = mapped
        elif objective not in _LGBM_OBJECTIVE_MAP.values():
            # D4: unknown objective passes through, but surface it so a typo does
            # not silently pick LightGBM's default metric.
            logger.debug(
                "lightgbm objective not in canonical map; passing through",
                objective=objective,
            )
    # D2: LightGBM silently ignores ``subsample`` unless ``subsample_freq`` is set
    # (sklearn alias for native ``bagging_freq``); inject it for any < 1.0 value.
    subsample = normalized.get("subsample")
    if isinstance(subsample, (int, float)) and float(subsample) < 1.0:
        normalized.setdefault("subsample_freq", 1)
    # XGBoost silences training output via ``verbose=False`` in ``fit``; LightGBM
    # has no ``verbose`` fit argument, so silence via the constructor instead.
    normalized.setdefault("verbose", -1)
    return normalized


def build_estimator(
    backend: str,
    params: dict[str, Any] | None = None,
    *,
    scale_pos_weight: float | None = None,
) -> ModelBackend:
    """Construct a native backend estimator from canonical params.

    ``scale_pos_weight`` (identical name on both backends) is injected only when
    provided and not equal to 1.0, matching the pre-Phase-2 conditional set in
    the four call sites.
    """
    backend = validate_backend(backend)
    normalized = normalize_params(backend, params or {})
    if scale_pos_weight is not None and float(scale_pos_weight) != 1.0:
        normalized["scale_pos_weight"] = scale_pos_weight

    # Intel runtime: pin backend thread pools explicitly (user-passed values win).
    # XGBoost uses ``nthread``; LightGBM uses ``num_threads``. No-op otherwise.
    threads = intel_thread_count(_INTEL_INFO)
    if threads is not None:
        if backend == "xgboost":
            normalized.setdefault("nthread", threads)
        else:
            normalized.setdefault("num_threads", threads)

    if backend == "xgboost":
        return cast("ModelBackend", xgb.XGBClassifier(**normalized))

    # Lazily imported: LightGBM lives in the optional ``ml`` dependency group.
    import lightgbm as lgb

    return cast("ModelBackend", lgb.LGBMClassifier(**normalized))


def backend_of(model: Any) -> BackendName:
    """Detect the backend that produced ``model`` from its class module."""
    module = type(model).__module__ or ""
    if module.startswith("lightgbm"):
        return "lightgbm"
    if module.startswith("xgboost"):
        return "xgboost"
    raise TypeError(f"Unsupported model backend: {type(model).__name__}")


def build_fit_kwargs(
    backend: str,
    *,
    sample_weight: Any = None,
    eval_set: Any = None,
    eval_sample_weight: Any = None,
    verbose: bool = False,
) -> dict[str, Any]:
    """Build backend-correct ``.fit`` keyword arguments from a common form.

    Callers pass the sklearn-style ``eval_set=[(Xv, yv)]`` and a list-aligned
    ``eval_sample_weight=[w]``; this function translates them per backend so the
    four call sites in ``forecasting.py``/``validation.py`` stay backend-neutral:

    * **XGBoost** keeps ``eval_set`` / ``sample_weight_eval_set`` and adds
      ``verbose`` (silenced in ``fit``).
    * **LightGBM 4.7+** deprecated ``eval_set`` (D9); translate to the native
      ``eval_X`` / ``eval_y`` pair (``eval_sample_weight`` stays a list).
      ``verbose`` is a constructor arg for LightGBM (set via ``normalize_params``),
      so it is intentionally omitted here.
    """
    backend = validate_backend(backend)
    kwargs: dict[str, Any] = {}
    if sample_weight is not None:
        kwargs["sample_weight"] = sample_weight
    if eval_set is not None:
        if backend == "xgboost":
            kwargs["eval_set"] = eval_set
            if eval_sample_weight is not None:
                kwargs["sample_weight_eval_set"] = eval_sample_weight
        else:
            # D9: LightGBM 4.7 deprecates ``eval_set`` in favor of ``eval_X``/``eval_y``.
            first_eval = eval_set[0]
            kwargs["eval_X"] = first_eval[0]
            kwargs["eval_y"] = first_eval[1]
            if eval_sample_weight is not None:
                # LightGBM still expects eval_sample_weight as a list aligned with
                # the eval sets (only ``eval_set`` itself is deprecated).
                kwargs["eval_sample_weight"] = eval_sample_weight
    if backend == "xgboost":
        kwargs["verbose"] = verbose
    return kwargs


def fit_estimator(
    model: ModelBackend,
    X: Any,
    y: Any,
    *,
    sample_weight: Any = None,
    eval_set: Any = None,
    eval_sample_weight: Any = None,
    verbose: bool = False,
) -> ModelBackend:
    """Fit ``model`` with backend-correct eval/verbose kwargs and return it."""
    kwargs = build_fit_kwargs(
        backend_of(model),
        sample_weight=sample_weight,
        eval_set=eval_set,
        eval_sample_weight=eval_sample_weight,
        verbose=verbose,
    )
    model.fit(X, y, **kwargs)
    return model


__all__ = [
    "DEFAULT_BACKEND",
    "DEFAULT_XGB_PARAMS",
    "SUPPORTED_BACKENDS",
    "BackendName",
    "ModelBackend",
    "backend_of",
    "build_estimator",
    "build_fit_kwargs",
    "canonical_training_params",
    "fit_estimator",
    "normalize_params",
    "scale_pos_weight",
    "validate_backend",
]
