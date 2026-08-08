"""Unit tests for the pluggable model-backend seam (``ml/backends.py``).

The XGBoost-backed cases run in the base environment (xgboost is a core
dependency); the LightGBM-backed cases are gated behind ``importorskip`` so the
fast suite stays green without the optional ``ml`` dependency group.
"""

from __future__ import annotations

import warnings

import joblib
import numpy as np
import pytest

from equity_lake.ml.backends import (
    DEFAULT_BACKEND,
    SUPPORTED_BACKENDS,
    ModelBackend,
    backend_of,
    build_estimator,
    build_fit_kwargs,
    fit_estimator,
    normalize_params,
    validate_backend,
)

CANONICAL_PARAMS: dict[str, object] = {
    "max_depth": 5,
    "learning_rate": 0.05,
    "n_estimators": 200,
    "subsample": 0.9,
    "colsample_bytree": 0.9,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
    "random_state": 42,
    "n_jobs": -1,
}

# Params that build both backends and fit without extra eval configuration.
FIT_PARAMS: dict[str, object] = {
    "n_estimators": 10,
    "max_depth": 2,
    "learning_rate": 0.1,
    "objective": "binary:logistic",
    "eval_metric": "logloss",
}


def _skip_if_lightgbm_missing(backend: str) -> None:
    """LightGBM cases skip in the base env; xgboost is a core dep."""
    if backend == "lightgbm":
        pytest.importorskip("lightgbm")


def test_validate_backend_accepts_known_and_rejects_unknown() -> None:
    assert validate_backend("xgboost") == "xgboost"
    assert validate_backend("LightGBM") == "lightgbm"  # case-insensitive
    assert validate_backend("  xgboost ") == "xgboost"  # stripped
    with pytest.raises(ValueError):
        validate_backend("catboost")


def test_default_backend_preserves_xgboost_filename_token() -> None:
    assert DEFAULT_BACKEND == "xgboost"
    assert {"xgboost", "lightgbm"} <= SUPPORTED_BACKENDS


def test_normalize_params_xgboost_is_passthrough() -> None:
    assert normalize_params("xgboost", dict(CANONICAL_PARAMS)) == CANONICAL_PARAMS


def test_normalize_params_xgboost_accepts_lightgbm_native_spelling() -> None:
    assert normalize_params("xgboost", {"feature_fraction": 0.7}) == {"colsample_bytree": 0.7}


def test_normalize_params_lightgbm_translates_canonical_form() -> None:
    pytest.importorskip("lightgbm")
    out = normalize_params("lightgbm", dict(CANONICAL_PARAMS))
    assert out == {
        "max_depth": 5,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "subsample": 0.9,
        "subsample_freq": 1,  # D2: injected because subsample < 1.0
        "feature_fraction": 0.9,  # colsample_bytree translated
        "objective": "binary",  # D4: mapped from binary:logistic
        "random_state": 42,
        "n_jobs": -1,
        "verbose": -1,
    }


@pytest.mark.parametrize("backend", ["xgboost", "lightgbm"])
def test_normalize_params_drops_none_and_scale_pos_weight(backend: str) -> None:
    # D3: scale_pos_weight is centralized on build_estimator; normalize strips it.
    _skip_if_lightgbm_missing(backend)
    out = normalize_params(backend, {"max_depth": 5, "learning_rate": None, "scale_pos_weight": 2.0})
    assert out["max_depth"] == 5
    assert "scale_pos_weight" not in out
    assert "learning_rate" not in out


@pytest.mark.parametrize("backend", ["xgboost", "lightgbm"])
def test_normalize_params_rejects_colsample_collision(backend: str) -> None:
    # D5: both spellings present is a silent last-write-wins bug -> raise.
    _skip_if_lightgbm_missing(backend)
    with pytest.raises(ValueError, match="colsample_bytree"):
        normalize_params(backend, {"colsample_bytree": 0.9, "feature_fraction": 0.7})


def test_normalize_params_lightgbm_objective_passes_through_unknown() -> None:
    # D4: unknown objective passes through unchanged (warned via structlog).
    pytest.importorskip("lightgbm")
    assert normalize_params("lightgbm", {"objective": "cross-entropy"})["objective"] == "cross-entropy"


def test_normalize_params_lightgbm_injects_subsample_freq() -> None:
    # D2: without subsample_freq LightGBM silently ignores subsample.
    pytest.importorskip("lightgbm")
    assert normalize_params("lightgbm", {"subsample": 0.8})["subsample_freq"] == 1
    # subsample == 1.0 (or absent) leaves subsample_freq unset.
    assert "subsample_freq" not in normalize_params("lightgbm", {"subsample": 1.0})
    assert "subsample_freq" not in normalize_params("lightgbm", {})


@pytest.mark.parametrize("backend", ["xgboost", "lightgbm"])
def test_build_estimator_applies_params_and_scale_pos_weight(backend: str) -> None:
    _skip_if_lightgbm_missing(backend)
    model = build_estimator(backend, {"max_depth": 3, "learning_rate": 0.1}, scale_pos_weight=2.5)
    assert isinstance(model, ModelBackend)
    params = model.get_params()
    assert params["max_depth"] == 3
    assert params["scale_pos_weight"] == 2.5
    assert backend_of(model) == backend


def test_build_estimator_xgboost_scale_pos_weight_default_is_version_decoupled() -> None:
    # D6: assert against a fresh estimator's own default, not a hard-coded value,
    # so the test is robust across XGBoost versions.
    import xgboost as xgb

    expected = xgb.XGBClassifier().get_params()["scale_pos_weight"]
    model = build_estimator("xgboost", {"max_depth": 3}, scale_pos_weight=None)
    assert isinstance(model, xgb.XGBClassifier)
    assert model.get_params()["scale_pos_weight"] == expected


def test_build_estimator_does_not_inject_scale_pos_weight_of_one() -> None:
    # D3: 1.0 is the neutral value -> never injected even when explicitly passed.
    import xgboost as xgb

    expected = xgb.XGBClassifier().get_params()["scale_pos_weight"]
    model = build_estimator("xgboost", {"max_depth": 3}, scale_pos_weight=1.0)
    assert isinstance(model, xgb.XGBClassifier)
    assert model.get_params()["scale_pos_weight"] == expected


def test_build_estimator_lightgbm_translates_and_injects_scale_pos_weight() -> None:
    lgb = pytest.importorskip("lightgbm")
    model = build_estimator(
        "lightgbm",
        {"colsample_bytree": 0.9, "objective": "binary:logistic", "eval_metric": "logloss"},
        scale_pos_weight=1.5,
    )
    assert isinstance(model, lgb.LGBMClassifier)
    params = model.get_params()
    assert params["feature_fraction"] == 0.9  # colsample_bytree translated
    assert params["scale_pos_weight"] == 1.5  # D6: LGBM injection
    assert params["objective"] == "binary"  # D4: mapped


@pytest.mark.parametrize("backend", ["xgboost", "lightgbm"])
def test_build_estimator_predicts_two_class_probabilities(backend: str) -> None:
    # D13: both backends' predict_proba return (N, 2) for the binary case.
    _skip_if_lightgbm_missing(backend)
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 3))
    y = rng.integers(0, 2, size=40)
    model = build_estimator(backend, dict(FIT_PARAMS))
    model.fit(X, y)
    assert model.predict_proba(X[:5]).shape == (5, 2)


def test_build_estimator_joblib_roundtrip_preserves_backend() -> None:
    # D6: dump -> load -> backend_of recovers the original backend identity.
    import io

    rng = np.random.default_rng(3)
    X = rng.normal(size=(30, 3))
    y = rng.integers(0, 2, size=30)
    model = build_estimator("xgboost", {"n_estimators": 5, "max_depth": 2, "eval_metric": "logloss"})
    model.fit(X, y)
    buf = io.BytesIO()
    joblib.dump(model, buf)
    buf.seek(0)
    restored = joblib.load(buf)
    assert backend_of(restored) == "xgboost"
    assert restored.predict_proba(X[:5]).shape == (5, 2)


@pytest.mark.parametrize("backend", ["xgboost", "lightgbm"])
def test_backend_of_detects_built_estimators(backend: str) -> None:
    # D6: backend_of positive tests for both backends.
    _skip_if_lightgbm_missing(backend)
    assert backend_of(build_estimator(backend, {"n_estimators": 1})) == backend


def test_backend_of_rejects_unknown_model() -> None:
    with pytest.raises(TypeError):
        backend_of(object())


def test_build_fit_kwargs_xgboost_with_eval() -> None:
    out = build_fit_kwargs(
        "xgboost",
        sample_weight=[1, 2],
        eval_set=[("Xv", "yv")],
        eval_sample_weight=[3],
        verbose=False,
    )
    assert out == {
        "sample_weight": [1, 2],
        "eval_set": [("Xv", "yv")],
        "sample_weight_eval_set": [3],
        "verbose": False,
    }


def test_build_fit_kwargs_lightgbm_translates_eval_set_to_eval_xy() -> None:
    # D9: LightGBM 4.7 deprecates ``eval_set`` in favor of ``eval_X``/``eval_y``;
    # ``eval_sample_weight`` stays a list (only ``eval_set`` is deprecated).
    pytest.importorskip("lightgbm")
    assert build_fit_kwargs(
        "lightgbm",
        sample_weight=[1, 2],
        eval_set=[("Xv", "yv")],
        eval_sample_weight=[3],
        verbose=False,
    ) == {
        "sample_weight": [1, 2],
        "eval_X": "Xv",
        "eval_y": "yv",
        "eval_sample_weight": [3],
    }  # verbose intentionally omitted (constructor arg for LightGBM)


@pytest.mark.parametrize("backend", ["xgboost", "lightgbm"])
def test_build_fit_kwargs_without_eval(backend: str) -> None:
    _skip_if_lightgbm_missing(backend)
    if backend == "xgboost":
        assert build_fit_kwargs("xgboost", verbose=False) == {"verbose": False}
    else:
        assert build_fit_kwargs("lightgbm", verbose=False) == {}


def test_fit_estimator_xgboost_roundtrip_with_eval() -> None:
    rng = np.random.default_rng(0)
    X = rng.normal(size=(40, 3))
    y = rng.integers(0, 2, size=40)
    model = build_estimator(
        "xgboost",
        {"n_estimators": 10, "max_depth": 2, "learning_rate": 0.1, "eval_metric": "logloss"},
    )
    fit_estimator(
        model,
        X[:30],
        y[:30],
        eval_set=[(X[30:], y[30:])],
        eval_sample_weight=[np.ones(10)],
        verbose=False,
    )
    assert isinstance(model, ModelBackend)
    assert model.predict_proba(X[:5]).shape == (5, 2)
    assert backend_of(model) == "xgboost"


def test_fit_estimator_lightgbm_uses_eval_xy_without_deprecation() -> None:
    # D9 + D13: a real LightGBM fit via ``eval_set`` must not emit the 4.7
    # deprecation (translated to ``eval_X``/``eval_y``), and ``predict_proba``
    # must return (N, 2).
    pytest.importorskip("lightgbm")
    rng = np.random.default_rng(2)
    X = rng.normal(size=(60, 3))
    y = rng.integers(0, 2, size=60)
    model = build_estimator(
        "lightgbm",
        {"n_estimators": 10, "max_depth": 3, "learning_rate": 0.1, "objective": "binary:logistic", "eval_metric": "logloss"},
        scale_pos_weight=1.5,
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        fit_estimator(
            model,
            X[:40],
            y[:40],
            eval_set=[(X[40:], y[40:])],
            eval_sample_weight=[np.ones(20)],
        )
    deprecated = [w for w in caught if "eval_set" in str(w.message) and "deprecated" in str(w.message)]
    assert not deprecated, f"LightGBM emitted eval_set deprecation: {[str(w.message) for w in deprecated]}"
    assert backend_of(model) == "lightgbm"
    assert model.predict_proba(X[:5]).shape == (5, 2)
