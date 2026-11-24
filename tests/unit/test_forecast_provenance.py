"""Row-bound forecast provenance (ADR-0021) writer and validator contracts.

The prediction row must carry the evidence ``PriceForecaster.predict()`` already
verifies (E1-E4 plus the envelope version), and the write boundary must accept
either a complete, self-consistent version-1 proof or an explicit null version.
Nothing is defaulted, inferred, or fabricated.
"""

from __future__ import annotations

import sys
from datetime import date
from types import ModuleType
from typing import Any

import polars as pl
import pytest
from structlog.testing import capture_logs

from equity_lake.core.availability import (
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
)
from equity_lake.core.instruments import (  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_SCHEMA_VERSION,
    InstrumentKey,
)
from equity_lake.core.schemas import (
    FEATURE_SCHEMA_VERSION,
    FORECAST_PROVENANCE_COLUMNS,
    FORECAST_PROVENANCE_VERSION,
    PREDICTION_SEMANTICS_VERSION,
    PREDICTION_TARGET_CANDIDATE_EXECUTE,
    PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE,
)
from equity_lake.ml import run_prediction_job, validate_predictions
from equity_lake.ml.forecasting import (
    DEFAULT_MAX_MODEL_AGE_DAYS,
    MISSING_VALUE_POLICY,
    MODEL_PROVENANCE_VERSION,
)

_TRADING_DATE = date(2024, 1, 2)
_MODEL_TRAINED_AT = date(2023, 12, 29)
_ARTIFACT_SHA256 = "0123456789abcdef" * 4


def _v1_payload(ticker: str, trading_date: date, **overrides: Any) -> dict[str, Any]:
    """Inference payload shaped like a verified ``v1_direction`` ``predict()``."""
    identity = InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker)
    payload: dict[str, Any] = {
        **identity.as_columns(),
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "date": trading_date,
        "prediction": 1,
        "probability": 0.62,
        "model_mode": "v1_direction",
        "model_version": "instrument-v1-xgboost_v1_direction_2023-12-29",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_provenance_version": MODEL_PROVENANCE_VERSION,
        "model_artifact_sha256": _ARTIFACT_SHA256,
        "model_trained_at": _MODEL_TRAINED_AT,
        "model_age_days": (trading_date - _MODEL_TRAINED_AT).days,
        "model_max_age_days": DEFAULT_MAX_MODEL_AGE_DAYS,
        "training_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "inference_feature_schema_version": FEATURE_SCHEMA_VERSION,
        "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "missing_value_policy": MISSING_VALUE_POLICY,
        "candidate_strategy_fingerprint": None,
        "horizon_days": 1,
        "threshold_used": 0.55,
        "threshold_source": "stored_optimized",
    }
    payload.update(overrides)
    return payload


class _StubForecaster:
    """Stub exposing the verified payload without touching model artifacts."""

    payload_overrides: dict[str, Any] | None = None

    def __init__(self, model_dir: str | None = None, candidate_strategies: list[dict[str, Any]] | None = None) -> None:
        del model_dir, candidate_strategies
        self.closed = False

    def predict(self, *, ticker: str | InstrumentKey, date: date) -> dict[str, Any]:
        symbol = ticker if isinstance(ticker, str) else ticker.symbol
        return _v1_payload(symbol, date, **(self.payload_overrides or {}))

    def close(self) -> None:
        self.closed = True


class _LegacyForecaster(_StubForecaster):
    """Inference path that cannot supply a proof: no evidence fields at all."""

    def predict(self, *, ticker: str | InstrumentKey, date: date) -> dict[str, Any]:
        identity = InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker) if isinstance(ticker, str) else ticker
        return {
            **identity.as_columns(),
            "identity_schema_version": IDENTITY_SCHEMA_VERSION,
            "date": date,
            "prediction": 1,
            "probability": 0.62,
            "model_version": "legacy",
        }


def _install_forecaster(monkeypatch: pytest.MonkeyPatch, forecaster: type[_StubForecaster]) -> None:
    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.__dict__["PriceForecaster"] = forecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)


def _run_job(monkeypatch: pytest.MonkeyPatch, *, payload_overrides: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any], pl.DataFrame | None]:
    """Run the writer with a stubbed payload and capture what it would persist."""
    if payload_overrides is None:
        _install_forecaster(monkeypatch, _StubForecaster)
    else:
        stub = type("_OverriddenForecaster", (_StubForecaster,), {"payload_overrides": payload_overrides})
        _install_forecaster(monkeypatch, stub)
    captured: dict[str, pl.DataFrame] = {}

    def _capture_merge(df: pl.DataFrame, **_kwargs: Any) -> bool:
        captured["predictions"] = df
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)
    success, results = run_prediction_job(trading_date=_TRADING_DATE, tickers=["AAPL"])
    return success, results, captured.get("predictions")


def _mutate(frame: pl.DataFrame, **values: Any) -> pl.DataFrame:
    """Overwrite single cells while keeping the persisted column dtypes."""
    return frame.with_columns(*[pl.lit(value).cast(frame.schema[column], strict=False).alias(column) for column, value in values.items()])


def test_run_prediction_job_persists_all_forecast_provenance_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    """P2: a verified ``v1_direction`` payload persists all 17 §2.2 columns."""
    assert len(FORECAST_PROVENANCE_COLUMNS) == 17
    success, _results, persisted = _run_job(monkeypatch)

    assert success is True
    assert persisted is not None
    assert set(FORECAST_PROVENANCE_COLUMNS).issubset(persisted.columns)
    row = persisted.row(0, named=True)
    assert row["forecast_provenance_version"] == FORECAST_PROVENANCE_VERSION == 1
    assert row["model_provenance_version"] == MODEL_PROVENANCE_VERSION
    assert row["model_artifact_sha256"] == _ARTIFACT_SHA256
    assert row["model_trained_at"] == _MODEL_TRAINED_AT
    assert row["model_age_days"] == (_TRADING_DATE - _MODEL_TRAINED_AT).days
    assert row["model_max_age_days"] == DEFAULT_MAX_MODEL_AGE_DAYS
    assert row["training_feature_schema_version"] == row["inference_feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert row["price_adjustment_version"] == PRICE_ADJUSTMENT_VERSION
    assert row["availability_schema_version"] == AVAILABILITY_SCHEMA_VERSION
    assert row["missing_value_policy"] == MISSING_VALUE_POLICY
    assert row["candidate_strategy_fingerprint"] is None
    assert row["horizon_days"] == 1
    assert row["threshold_used"] == 0.55
    assert row["threshold_source"] == "stored_optimized"
    assert row["prediction_target"] == PREDICTION_TARGET_NEXT_SESSION_RETURN_POSITIVE
    assert row["prediction_semantics_version"] == PREDICTION_SEMANTICS_VERSION
    # Nullable additive columns stay typed so legacy rows can evolve in place.
    assert persisted.schema["model_trained_at"] == pl.Date
    assert persisted.schema["threshold_used"] == pl.Float64
    assert persisted.schema["forecast_provenance_version"] == pl.Int16
    # The written row is what the write boundary accepts.
    assert validate_predictions(persisted) is True


def test_run_prediction_job_persists_v2_candidate_semantics(monkeypatch: pytest.MonkeyPatch) -> None:
    """E4 for ``v2_meta_label``: candidate_execute plus the strategy fingerprint."""
    overrides = {
        "model_mode": "v2_meta_label",
        "candidate_strategy_fingerprint": "candidate-strategy-fingerprint",
        "horizon_days": 5,
    }
    success, _results, persisted = _run_job(monkeypatch, payload_overrides=overrides)

    assert success is True
    assert persisted is not None
    row = persisted.row(0, named=True)
    assert row["prediction_target"] == PREDICTION_TARGET_CANDIDATE_EXECUTE
    assert row["candidate_strategy_fingerprint"] == "candidate-strategy-fingerprint"
    assert row["prediction_semantics_version"] == PREDICTION_SEMANTICS_VERSION
    assert validate_predictions(persisted) is True


def test_run_prediction_job_writes_null_envelope_without_a_proof(monkeypatch: pytest.MonkeyPatch) -> None:
    """An inference path that cannot supply a proof writes a null version."""
    _install_forecaster(monkeypatch, _LegacyForecaster)
    captured: dict[str, pl.DataFrame] = {}

    def _capture_merge(df: pl.DataFrame, **_kwargs: Any) -> bool:
        captured["predictions"] = df
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)
    success, _results = run_prediction_job(trading_date=_TRADING_DATE, tickers=["AAPL"])

    assert success is True
    persisted = captured["predictions"]
    assert persisted["forecast_provenance_version"].null_count() == 1
    assert persisted["prediction_target"].null_count() == 1
    assert persisted["prediction_semantics_version"].null_count() == 1
    assert persisted["model_artifact_sha256"].null_count() == 1
    assert validate_predictions(persisted) is True


def test_validate_predictions_accepts_null_provenance_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2.3: a null ``forecast_provenance_version`` is a documented no-proof row."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    no_proof = persisted.with_columns(
        *[pl.lit(None).cast(persisted.schema[column], strict=False).alias(column) for column in FORECAST_PROVENANCE_COLUMNS]
    )

    assert validate_predictions(no_proof) is True


def test_validate_predictions_rejects_claimed_proof_without_artifact_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """A row claiming version 1 with a missing digest rejects the batch (fail closed)."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    claimed_without_digest = _mutate(persisted, model_artifact_sha256=None)

    assert validate_predictions(claimed_without_digest) is False


def test_validate_predictions_rejects_age_beyond_applied_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """``model_age_days`` beyond the applied ``model_max_age_days`` is contradictory."""
    success, _results, persisted = _run_job(monkeypatch, payload_overrides={"model_age_days": DEFAULT_MAX_MODEL_AGE_DAYS + 1})

    assert success is False
    assert persisted is None


def test_validate_predictions_rejects_training_inference_version_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 1 in isolation: training and inference feature-schema versions differ."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    only_training_differs = _mutate(persisted, training_feature_schema_version=FEATURE_SCHEMA_VERSION - 1)

    assert validate_predictions(only_training_differs) is False


def test_validate_predictions_rejects_inference_stored_version_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rule 2 in isolation: both proof versions agree but differ from the stored one."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    proof_agrees_stored_differs = _mutate(
        persisted,
        training_feature_schema_version=FEATURE_SCHEMA_VERSION - 1,
        inference_feature_schema_version=FEATURE_SCHEMA_VERSION - 1,
    )

    assert validate_predictions(proof_agrees_stored_differs) is False


def test_validate_predictions_failure_blocks_the_batch_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rejected batch is never merged and is logged (fail closed)."""
    frame_batch: dict[str, pl.DataFrame] = {}

    def _capture_merge(df: pl.DataFrame, **_kwargs: Any) -> bool:
        frame_batch["predictions"] = df
        return True

    _install_forecaster(
        monkeypatch,
        type("_BadThresholdForecaster", (_StubForecaster,), {"payload_overrides": {"threshold_used": 1.5}}),
    )
    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)
    with capture_logs() as logs:
        success, _results = run_prediction_job(trading_date=_TRADING_DATE, tickers=["AAPL"])

    assert success is False
    assert any(log.get("event") == "predictions_validation_failed" for log in logs)
    assert frame_batch == {}


def test_validate_predictions_rejects_threshold_outside_unit_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    """``threshold_used`` must be the probability-scale value actually applied."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    assert validate_predictions(_mutate(persisted, threshold_used=1.5)) is False
    assert validate_predictions(_mutate(persisted, threshold_used=-0.1)) is False


def test_validate_predictions_rejects_target_inconsistent_with_model_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """``prediction_target`` must state the semantics of the row's ``model_mode``."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    wrong_target = _mutate(persisted, prediction_target=PREDICTION_TARGET_CANDIDATE_EXECUTE)

    assert validate_predictions(wrong_target) is False


def test_run_prediction_job_force_nulls_the_fingerprint_for_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    """§2.2: the fingerprint belongs to the meta-label contract only."""
    success, _results, persisted = _run_job(
        monkeypatch,
        payload_overrides={"candidate_strategy_fingerprint": "leaked-from-payload"},
    )

    assert success is True
    assert persisted is not None
    assert persisted["candidate_strategy_fingerprint"].to_list() == [None]
    assert validate_predictions(persisted) is True


def test_validate_predictions_rejects_fingerprint_on_a_v1_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The iff rule: a directional row must not claim a meta-label fingerprint."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    assert validate_predictions(_mutate(persisted, candidate_strategy_fingerprint="leaked")) is False


@pytest.mark.parametrize("digest", ["0" * 63, "A" * 64, "0" * 64 + "0", "not-a-digest"])
def test_validate_predictions_rejects_a_malformed_digest(monkeypatch: pytest.MonkeyPatch, digest: str) -> None:
    """The digest format rule is enforced on its own, not only via non-nullness."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    assert validate_predictions(_mutate(persisted, model_artifact_sha256=digest)) is False


@pytest.mark.parametrize("trained_at", [date(2024, 1, 3), date(2024, 6, 1)])
def test_validate_predictions_rejects_a_model_trained_after_the_prediction(monkeypatch: pytest.MonkeyPatch, trained_at: date) -> None:
    """A forecast cannot be produced by a model trained after the prediction date."""
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    assert validate_predictions(_mutate(persisted, model_trained_at=trained_at)) is False


def test_validate_predictions_rejects_negative_age(monkeypatch: pytest.MonkeyPatch) -> None:
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    assert validate_predictions(_mutate(persisted, model_age_days=-1)) is False


def test_validate_predictions_rejects_a_horizon_below_one(monkeypatch: pytest.MonkeyPatch) -> None:
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    assert validate_predictions(_mutate(persisted, horizon_days=0)) is False


def test_validate_predictions_rejects_a_target_that_contradicts_the_model_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _success, _results, persisted = _run_job(monkeypatch)
    assert persisted is not None

    contradicting = _mutate(persisted, prediction_target=PREDICTION_TARGET_CANDIDATE_EXECUTE)

    assert validate_predictions(contradicting) is False


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("threshold_used", True),
        ("model_age_days", "4"),
        ("model_age_days", 4.9),
        ("threshold_source", 5),
    ],
)
def test_run_prediction_job_never_launders_a_mistyped_proof_value(
    monkeypatch: pytest.MonkeyPatch,
    column: str,
    value: Any,
) -> None:
    """A proof field with the wrong type becomes the documented no-proof row."""
    success, _results, persisted = _run_job(monkeypatch, payload_overrides={column: value})

    assert success is True
    assert persisted is not None
    assert persisted["forecast_provenance_version"].to_list() == [None]
    assert validate_predictions(persisted) is True
