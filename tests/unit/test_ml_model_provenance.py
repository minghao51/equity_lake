"""Synthetic model-schema, threshold, age, and sidecar provenance checks."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from equity_lake.core.availability import (  # type: ignore[import-untyped]
    AVAILABILITY_SCHEMA_VERSION,
    PRICE_ADJUSTMENT_VERSION,
    ActionCoverageProof,
    action_coverage_feature_fields,
    source_observable_session,
)
from equity_lake.core.instruments import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    IDENTITY_SCHEMA_VERSION,
    InstrumentKey,
)
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION  # type: ignore[import-untyped]
from equity_lake.ml import run_prediction_job  # type: ignore[import-untyped]
from equity_lake.ml.forecasting import (  # type: ignore[import-untyped]
    DEFAULT_MAX_MODEL_AGE_DAYS,
    DEFAULT_V2_SETTINGS,
    MISSING_VALUE_POLICY,
    MODEL_AGE_POLICY,
    ModelExpiredError,
    ModelFutureError,
    ModelIncompatibleError,
    ModelMissingError,
    ModelProvenanceError,
    NoCandidateConditionError,
    PriceForecaster,
)


class _FixedModel:
    def __init__(self, probability: float = 0.7) -> None:
        self.probability = probability
        self.prediction_calls = 0
        self.load_calls = 0

    def fit(self, X: Any, y: Any, **fit_kwargs: Any) -> _FixedModel:
        del X, y, fit_kwargs
        return self

    def predict_proba(self, X: Any) -> np.ndarray:
        self.prediction_calls += 1
        return np.tile(np.array([[1.0 - self.probability, self.probability]]), (X.height, 1))

    def predict(self, X: Any) -> np.ndarray:
        return np.zeros(X.height, dtype=np.int8)

    def get_params(self, deep: bool = True) -> dict[str, Any]:
        del deep
        return {}


def _action_proof_fields(dates: list[date]) -> dict[str, Any]:
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH")
    source_time = datetime(2023, 12, 1, 12, 0, tzinfo=UTC)
    source_session = source_observable_session(instrument, source_time)
    proof = ActionCoverageProof(
        market=instrument.market,
        mic=instrument.mic,
        symbol=instrument.symbol,
        source="synthetic-source",
        coverage_start=min(dates) - timedelta(days=1),
        coverage_end=max(dates) + timedelta(days=1),
        source_available_at=source_time,
        source_valid_until=datetime(2030, 1, 1, tzinfo=UTC),
    )
    proof_fields = action_coverage_feature_fields(proof)
    return {
        "action_coverage": pl.Series([1] * len(dates), dtype=pl.Int8),
        "action_source_age_days": pl.Series([(day - source_session).days for day in dates], dtype=pl.Int32),
        **{column: [value] * len(dates) for column, value in proof_fields.items()},
    }


def _v1_features(as_of: date, schema_version: int) -> pl.DataFrame:
    dates = [as_of - timedelta(days=2), as_of - timedelta(days=1), as_of]
    return pl.DataFrame(
        {
            "market": ["us_equity"] * 3,
            "mic": ["XNAS"] * 3,
            "symbol": ["SYNTH"] * 3,
            "identity_schema_version": [IDENTITY_SCHEMA_VERSION] * 3,
            "ticker": ["SYNTH"] * 3,
            "date": dates,
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.0, 101.0, 102.0],
            "volume": [1000.0] * 3,
            "next_day_return": [0.01, 0.01, None],
            "label_available_date": [dates[1], dates[2], None],
            "feature_schema_version": pl.Series([schema_version] * 3, dtype=pl.Int8),
            "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION] * 3,
            "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION] * 3,
            **_action_proof_fields(dates),
            "rsi_14": pl.Series([50.0, 51.0, 52.0], dtype=pl.Float64),
            "macd": pl.Series([0.1, 0.2, 0.3], dtype=pl.Float64),
        }
    )


def _install_inference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, as_of: date, schema_version: int, metadata: dict[str, Any] | None
) -> tuple[PriceForecaster, _FixedModel]:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    frame = _v1_features(as_of, schema_version)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    model = _FixedModel()

    def _load_model(_self: PriceForecaster, _path: Path) -> _FixedModel:
        model.load_calls += 1
        return model

    monkeypatch.setattr(PriceForecaster, "_load_model", _load_model)
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH")
    refit_boundary = (
        date.fromisoformat(metadata["refit_boundary"])
        if metadata is not None and isinstance(metadata.get("refit_boundary"), str)
        else as_of - timedelta(days=9)
    )
    model_path = tmp_path / f"instrument-v{IDENTITY_SCHEMA_VERSION}-{instrument.artifact_token}_xgboost_v1_direction_{refit_boundary}.pkl"
    model_path.write_bytes(b"synthetic model placeholder")
    if metadata is not None:
        metadata = dict(metadata)
        metadata.setdefault("model_mode", "v1_direction")
        metadata.setdefault("backend", "xgboost")
        metadata["model_file"] = model_path.name
        metadata["artifact_sha256"] = hashlib.sha256(model_path.read_bytes()).hexdigest()
        model_path.with_suffix(".training_metadata.json").write_text(json.dumps(metadata))
    return PriceForecaster(model_dir=str(tmp_path)), model


def _v1_metadata(version: int = FEATURE_SCHEMA_VERSION, threshold: Any = 0.8, refit_boundary: date = date(2024, 1, 4)) -> dict[str, Any]:
    strategies = [{"name": "momentum", "lookback_days": 20, "buy_threshold": 0.02, "sell_threshold": -0.01}]
    return {
        "provenance_version": 3,
        "ticker": "SYNTH",
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "instrument": InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH").model_dump(mode="json"),
        "trained_on": str(refit_boundary),
        "refit_boundary": str(refit_boundary),
        "training_start": "2024-01-01",
        "data_range": {"start": "2024-01-01", "end": str(refit_boundary)},
        "model_mode": "v1_direction",
        "backend": "xgboost",
        "features": ["action_coverage", "action_source_age_days", "rsi_14", "macd"],
        "feature_dtypes": {"action_coverage": "Int8", "action_source_age_days": "Int32", "rsi_14": "Float64", "macd": "Float64"},
        "training_feature_schema_version": version,
        "training_feature_schema_versions": [version],
        "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "candidate_strategies": strategies,
        "candidate_strategy_fingerprint": PriceForecaster._candidate_strategy_fingerprint(strategies),
        "missing_value_policy": MISSING_VALUE_POLICY,
        "model_age_policy": MODEL_AGE_POLICY,
        "target_settings": {"label_horizon_days": 1},
        "optimized_threshold": threshold,
    }


@pytest.mark.parametrize("incompatible_contract", ["feature_names", "feature_dtype", "identity", "schema", "adjustment", "missingness", "barrier"])
def test_training_reuse_rejects_any_incompatible_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, incompatible_contract: str) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    model_mode = "v2_meta_label" if incompatible_contract == "barrier" else "v1_direction"
    forecaster = PriceForecaster(model_dir=str(tmp_path), model_mode=model_mode)
    frame = _v1_features(date(2024, 1, 10), FEATURE_SCHEMA_VERSION)
    metadata = _v1_metadata(refit_boundary=date(2024, 1, 4))
    metadata["model_mode"] = model_mode
    metadata["training_config"] = {}
    if model_mode == "v2_meta_label":
        metadata["candidate_strategies"] = forecaster.candidate_strategies
        metadata["candidate_strategy_fingerprint"] = forecaster._candidate_strategy_fingerprint(forecaster.candidate_strategies)
        metadata["target_settings"] = dict(DEFAULT_V2_SETTINGS)
    model_path = tmp_path / "synthetic_model.pkl"
    model_path.write_bytes(b"synthetic model placeholder")
    metadata["model_file"] = model_path.name
    metadata["artifact_sha256"] = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if incompatible_contract == "feature_names":
        metadata["features"] = ["rsi_14", "unrecorded_feature"]
        metadata["feature_dtypes"] = {"rsi_14": "Float64", "unrecorded_feature": "Float64"}
    elif incompatible_contract == "feature_dtype":
        metadata["feature_dtypes"]["macd"] = "Float32"
    elif incompatible_contract == "identity":
        metadata["ticker"] = "OTHER"
        metadata["instrument"] = InstrumentKey(market="us_equity", mic="XNAS", symbol="OTHER").model_dump(mode="json")
    elif incompatible_contract == "schema":
        metadata["training_feature_schema_version"] = 4
        metadata["training_feature_schema_versions"] = [4]
    elif incompatible_contract == "adjustment":
        metadata["price_adjustment_version"] = "unknown-adjustment"
    elif incompatible_contract == "missingness":
        metadata["missing_value_policy"] = "unknown-fill-policy"
    else:
        metadata["target_settings"]["pt_mult"] = float(DEFAULT_V2_SETTINGS["pt_mult"]) + 0.5
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH")
    try:
        assert not forecaster._training_model_is_reusable(
            model_path=model_path,
            metadata=metadata,
            frame=frame,
            instrument=instrument,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 10),
            max_model_age_days=DEFAULT_MAX_MODEL_AGE_DAYS,
            expected_training_config={},
        )
    finally:
        forecaster.close()


@pytest.mark.parametrize("invalid_age", [8, 99, -1, True, 1.5])
def test_public_age_bound_is_validated_before_feature_loading(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_age: Any) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    feature_loads: list[bool] = []
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: feature_loads.append(True))
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        with pytest.raises(ValueError, match=r"max_model_age_days must be an integer in \[0, 7\]"):
            forecaster.predict("SYNTH", date(2024, 1, 10), max_model_age_days=invalid_age)
        with pytest.raises(ValueError, match=r"max_model_age_days must be an integer in \[0, 7\]"):
            forecaster.train_model("SYNTH", date(2024, 1, 1), date(2024, 1, 10), max_model_age_days=invalid_age)
    finally:
        forecaster.close()
    assert feature_loads == []


@pytest.mark.parametrize("corruption", ["swapped_bytes", "missing_digest"])
def test_artifact_integrity_failure_precedes_load_and_inference(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, corruption: str) -> None:
    as_of = date(2024, 1, 10)
    forecaster, model = _install_inference(
        monkeypatch,
        tmp_path,
        as_of,
        schema_version=FEATURE_SCHEMA_VERSION,
        metadata=_v1_metadata(refit_boundary=date(2024, 1, 3)),
    )
    model_path = next(tmp_path.glob("*.pkl"))
    if corruption == "swapped_bytes":
        model_path.write_bytes(b"different model bytes")
    else:
        sidecar_path = model_path.with_suffix(".training_metadata.json")
        sidecar = json.loads(sidecar_path.read_text())
        sidecar.pop("artifact_sha256")
        sidecar_path.write_text(json.dumps(sidecar))
    try:
        with pytest.raises(ModelProvenanceError):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_future_sidecar_cutoff_incoherence_fails_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    metadata = _v1_metadata(refit_boundary=date(2024, 1, 3))
    metadata["data_range"]["end"] = "2024-01-02"
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=metadata)
    try:
        with pytest.raises(ModelProvenanceError, match="coherent actual training cutoff"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_prediction_rejects_schema_mismatch_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata(version=4))
    try:
        with pytest.raises(ModelIncompatibleError, match="feature-schema"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()

    assert model.load_calls == 0
    assert model.prediction_calls == 0


def test_prediction_reports_known_matching_schema(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    forecaster, model = _install_inference(
        monkeypatch,
        tmp_path,
        as_of,
        schema_version=FEATURE_SCHEMA_VERSION,
        metadata=_v1_metadata(version=FEATURE_SCHEMA_VERSION, refit_boundary=date(2024, 1, 3)),
    )
    try:
        prediction = forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()

    assert prediction["feature_schema_compatibility"] == "known"
    assert prediction["adjustment_compatibility"] == "known"
    assert prediction["training_feature_schema_version"] == prediction["inference_feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert prediction["threshold_used"] == 0.8
    assert prediction["threshold_source"] == "stored_optimized"
    assert prediction["model_age_days"] == DEFAULT_MAX_MODEL_AGE_DAYS == 7
    assert prediction["model_age_source"] == "training_metadata"
    assert prediction["model_max_age_days"] == DEFAULT_MAX_MODEL_AGE_DAYS
    valid_features = _v1_features(as_of, FEATURE_SCHEMA_VERSION)
    assert valid_features["action_source_age_days"][-1] > 7
    assert valid_features["action_source_valid_until"][-1] == datetime(2030, 1, 1, tzinfo=UTC)
    assert model.load_calls == model.prediction_calls == 1


@pytest.mark.parametrize("proof_failure", ["missing_expiry", "expired", "unknown_publication"])
def test_prediction_rejects_unverifiable_action_proof_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, proof_failure: str) -> None:
    as_of = date(2024, 1, 10)
    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION)
    assert frame["availability_schema_version"].unique().to_list() == [AVAILABILITY_SCHEMA_VERSION]
    assert all(age >= 0 for age in frame["action_source_age_days"].to_list())
    if proof_failure == "missing_expiry":
        frame = frame.drop("action_source_valid_until")
    elif proof_failure == "expired":
        frame = frame.with_columns(pl.lit(datetime(2024, 1, 10, 20, 59, tzinfo=UTC)).alias("action_source_valid_until"))
    else:
        frame = frame.with_columns(pl.Series("action_source_available_at", [None] * frame.height, dtype=pl.Datetime("us", "UTC")))
    forecaster, model = _install_inference(
        monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata(refit_boundary=date(2024, 1, 3))
    )
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ModelIncompatibleError, match="adjustment/availability"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


@pytest.mark.parametrize("proof_failure", ["missing_expiry", "expired", "unknown_publication"])
def test_training_rejects_unverifiable_action_proof_before_estimator_build(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, proof_failure: str
) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    dates = [date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10)]
    frame = _v1_features(dates[-1], FEATURE_SCHEMA_VERSION)
    if proof_failure == "missing_expiry":
        frame = frame.drop("action_source_valid_until")
    elif proof_failure == "expired":
        frame = frame.with_columns(pl.lit(datetime(2024, 1, 10, 20, 59, tzinfo=UTC)).alias("action_source_valid_until"))
    else:
        frame = frame.with_columns(pl.Series("action_source_available_at", [None] * frame.height, dtype=pl.Datetime("us", "UTC")))
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    estimator_builds: list[bool] = []
    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *_args, **_kwargs: estimator_builds.append(True))
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        with pytest.raises(ValueError, match="source-availability proof"):
            forecaster.train_model("SYNTH", dates[0], dates[-1], max_model_age_days=0)
    finally:
        forecaster.close()
    assert estimator_builds == []


def test_gold_feature_writer_rejects_expired_proof_even_when_stamps_and_age_are_current() -> None:
    from equity_lake.ingestion.writers import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
        validate_schema,
    )

    day = date(2024, 1, 10)
    current = _v1_features(day, FEATURE_SCHEMA_VERSION)
    assert validate_schema(current, "03_gold/features")
    expired = current.with_columns(pl.lit(datetime(2024, 1, 10, 20, 59, tzinfo=UTC)).alias("action_source_valid_until"))
    assert not validate_schema(expired, "03_gold/features")
    legacy_without_proof_identity = current.drop("action_coverage_market", "action_coverage_mic", "action_coverage_symbol")
    assert not validate_schema(legacy_without_proof_identity, "03_gold/features")


def test_prediction_rejects_cross_identity_action_proof_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION).with_columns(pl.lit("XNYS").alias("action_coverage_mic"))
    forecaster, model = _install_inference(
        monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata(refit_boundary=date(2024, 1, 3))
    )
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ModelIncompatibleError, match="adjustment/availability"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


@pytest.mark.parametrize("invalid_proof", ["negative_age", "published_after_cutoff", "coverage_ends_early"])
def test_prediction_rejects_invalid_action_proof_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_proof: str) -> None:
    as_of = date(2024, 1, 10)
    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION)
    if invalid_proof == "negative_age":
        frame = frame.with_columns(pl.lit(-1, dtype=pl.Int32).alias("action_source_age_days"))
    elif invalid_proof == "published_after_cutoff":
        frame = frame.with_columns(pl.lit(datetime(2024, 1, 10, 22, 0, tzinfo=UTC)).alias("action_source_available_at"))
    else:
        frame = frame.with_columns(pl.lit(date(2024, 1, 9)).alias("action_coverage_end"))
    forecaster, model = _install_inference(
        monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata(refit_boundary=date(2024, 1, 3))
    )
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ModelIncompatibleError, match="adjustment/availability"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_training_allows_row_valid_source_expiry_before_later_training_cutoff(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    last_feature_date = date(2024, 1, 10)
    training_cutoff = date(2024, 1, 15)
    frame = _v1_features(last_feature_date, FEATURE_SCHEMA_VERSION).with_columns(
        pl.lit(datetime(2024, 1, 10, 22, 0, tzinfo=UTC)).alias("action_source_valid_until")
    )
    assert frame["action_source_valid_until"][0].date() < training_cutoff
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(PriceForecaster, "_prepare_training_frame", lambda _self, data: data.head(0))
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        with pytest.raises(ValueError, match="No training rows available"):
            forecaster.train_model("SYNTH", date(2024, 1, 8), training_cutoff, max_model_age_days=0)
    finally:
        forecaster.close()


def test_prediction_requires_exact_inference_date_without_older_row_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION).filter(pl.col("date") < pl.lit(as_of))
    forecaster, model = _install_inference(
        monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata(refit_boundary=date(2024, 1, 3))
    )
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ValueError, match="No scoring features found"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_prediction_rejects_stale_model_using_refit_boundary_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    metadata = _v1_metadata(refit_boundary=date(2024, 1, 2))
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=metadata)
    try:
        with pytest.raises(ModelExpiredError, match="8 calendar days"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_prediction_job_applies_strict_age_gate_and_reports_typed_outcome(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    forecaster, model = _install_inference(
        monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata(refit_boundary=date(2024, 1, 2))
    )
    forecaster.close()
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH")

    success, results = run_prediction_job(trading_date=as_of, tickers=[instrument], model_dir=str(tmp_path))

    result = results[instrument.display_key]
    assert success is False
    assert result["success"] is False
    assert result["status"] == "model_expired"
    assert result["error_type"] == "ModelExpiredError"
    assert model.load_calls == model.prediction_calls == 0


def test_prediction_rejects_future_refit_boundary_before_model_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    metadata = _v1_metadata(refit_boundary=date(2024, 1, 11))
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=metadata)
    try:
        with pytest.raises(ModelFutureError, match="after inference date"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_prediction_rejects_feature_name_dtype_identity_and_version_mismatches_before_load(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION).with_columns(pl.col("macd").cast(pl.Int64))
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata())
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ModelIncompatibleError, match="feature names/dtypes"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0

    metadata = _v1_metadata()
    metadata["features"] = ["rsi_14", "unrecorded_feature"]
    metadata["feature_dtypes"] = {"rsi_14": "Float64", "unrecorded_feature": "Float64"}
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=metadata)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: _v1_features(as_of, FEATURE_SCHEMA_VERSION))
    try:
        with pytest.raises(ModelIncompatibleError, match="feature names/dtypes"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0

    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION).with_columns(pl.lit(2).alias("identity_schema_version"))
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata())
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ModelIncompatibleError, match="identity schema version"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0

    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION).with_columns(pl.lit("raw-v0").alias("price_adjustment_version"))
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata())
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ModelIncompatibleError, match="adjustment/availability"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0

    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION).with_columns(pl.lit(AVAILABILITY_SCHEMA_VERSION + 1).alias("availability_schema_version"))
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata())
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    try:
        with pytest.raises(ModelIncompatibleError, match="adjustment/availability"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_missing_model_and_unverified_inline_model_are_typed_unavailable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from equity_lake.ml import ModelMissingError as PublicModelMissingError

    assert PublicModelMissingError is ModelMissingError
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    frame = _v1_features(date(2024, 1, 10), FEATURE_SCHEMA_VERSION)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        with pytest.raises(ModelMissingError):
            forecaster.predict("SYNTH", date(2024, 1, 10))
        unverified_model = _FixedModel()
        with pytest.raises(ModelProvenanceError, match="In-memory models cannot be verified"):
            forecaster.predict("SYNTH", date(2024, 1, 10), model=unverified_model)
        assert unverified_model.prediction_calls == 0
        invalid_metadata = _v1_metadata()
        invalid_metadata["identity_schema_version"] = IDENTITY_SCHEMA_VERSION + 1
        incompatible_model = _FixedModel()
        with pytest.raises(ModelProvenanceError, match="In-memory models cannot be verified"):
            forecaster.predict("SYNTH", date(2024, 1, 10), model=incompatible_model, model_metadata=invalid_metadata)
        assert incompatible_model.prediction_calls == 0
    finally:
        forecaster.close()


def test_v2_without_candidate_reports_typed_no_condition(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    as_of = date(2024, 1, 10)
    frame = _v1_features(as_of, FEATURE_SCHEMA_VERSION)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    forecaster = PriceForecaster(model_dir=str(tmp_path), model_mode="v2_meta_label")
    try:
        with pytest.raises(NoCandidateConditionError) as error:
            forecaster.predict("SYNTH", as_of)
        assert error.value.status == "no_condition"
    finally:
        forecaster.close()


def test_prediction_rejects_model_without_adjustment_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    metadata = _v1_metadata()
    metadata.pop("price_adjustment_version")
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=metadata)
    try:
        with pytest.raises(ModelIncompatibleError, match="adjustment/availability contract"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_legacy_metadata_without_actual_refit_boundary_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    as_of = date(2024, 1, 10)
    legacy = {
        "identity_schema_version": IDENTITY_SCHEMA_VERSION,
        "instrument": InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH").model_dump(mode="json"),
        "ticker": "SYNTH",
        "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
        "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
        "features": ["rsi_14", "macd"],
        "feature_dtypes": {"rsi_14": "Float64", "macd": "Float64"},
    }
    forecaster, model = _install_inference(monkeypatch, tmp_path, as_of, schema_version=4, metadata=legacy)
    try:
        with pytest.raises(ModelProvenanceError, match="trusted refit-boundary"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()

    assert model.load_calls == model.prediction_calls == 0


@pytest.mark.parametrize("invalid_threshold", [1.01, -0.01, "0.8", True, float("nan")])
def test_invalid_stored_threshold_fails_visibly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, invalid_threshold: Any) -> None:
    as_of = date(2024, 1, 10)
    forecaster, model = _install_inference(
        monkeypatch, tmp_path, as_of, schema_version=FEATURE_SCHEMA_VERSION, metadata=_v1_metadata(threshold=invalid_threshold)
    )
    try:
        with pytest.raises(ValueError, match="Stored optimized_threshold"):
            forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()
    assert model.load_calls == model.prediction_calls == 0


def test_null_features_remain_missing_as_native_nan_not_neutral_fill(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        matrix = forecaster._prepare_training_matrix(
            pl.DataFrame({"optional_feature": pl.Series([None, 0.0, 1.0], dtype=pl.Float64)}), ["optional_feature"]
        )
    finally:
        forecaster.close()
    assert np.isnan(matrix["optional_feature"][0])
    assert matrix["optional_feature"][1:].to_list() == [0.0, 1.0]


def test_v2_prediction_uses_configured_fallback_and_barrier_horizon(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    as_of = date(2024, 2, 10)
    dates = [as_of - timedelta(days=7 - index) for index in range(8)]
    closes = [100.0 + 2.0 * index for index in range(8)]
    frame = pl.DataFrame(
        {
            "market": ["us_equity"] * 8,
            "mic": ["XNAS"] * 8,
            "symbol": ["SYNTH"] * 8,
            "identity_schema_version": [IDENTITY_SCHEMA_VERSION] * 8,
            "ticker": ["SYNTH"] * 8,
            "date": dates,
            "open": closes,
            "high": [value + 0.2 for value in closes],
            "low": [value - 0.2 for value in closes],
            "close": closes,
            "volume": [1000.0] * 8,
            "volatility_20": [0.01] * 8,
            "feature_schema_version": pl.Series([FEATURE_SCHEMA_VERSION] * 8, dtype=pl.Int8),
            "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION] * 8,
            "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION] * 8,
            **_action_proof_fields(dates),
            "rsi_14": [50.0] * 8,
        }
    )
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    model = _FixedModel(probability=0.7)

    def _load_model(_self: PriceForecaster, _path: Path) -> _FixedModel:
        model.load_calls += 1
        return model

    monkeypatch.setattr(PriceForecaster, "_load_model", _load_model)
    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH")
    model_path = tmp_path / f"instrument-v{IDENTITY_SCHEMA_VERSION}-{instrument.artifact_token}_xgboost_v2_meta_label_2024-02-01.pkl"
    model_path.write_bytes(b"synthetic model placeholder")
    strategies: list[dict[str, Any]] = [{"name": "synthetic", "lookback_days": 2, "buy_threshold": 0.005, "sell_threshold": -1.0}]
    config: dict[str, Any] = {
        "vertical_barrier_days": 3,
        "meta_label_threshold": 0.75,
        "candidate_strategies": strategies,
    }
    model_path.with_suffix(".training_metadata.json").write_text(
        json.dumps(
            {
                "provenance_version": 3,
                "artifact_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
                "identity_schema_version": IDENTITY_SCHEMA_VERSION,
                "instrument": instrument.model_dump(mode="json"),
                "ticker": instrument.symbol,
                "trained_on": "2024-02-04",
                "refit_boundary": "2024-02-04",
                "training_start": "2024-02-01",
                "data_range": {"start": "2024-02-01", "end": "2024-02-04"},
                "model_mode": "v2_meta_label",
                "backend": "xgboost",
                "model_file": model_path.name,
                "features": ["action_coverage", "action_source_age_days", "volatility_20", "rsi_14", "sma", "pct_diff"],
                "feature_dtypes": {
                    "action_coverage": "Int8",
                    "action_source_age_days": "Int32",
                    "volatility_20": "Float64",
                    "rsi_14": "Float64",
                    "sma": "Float64",
                    "pct_diff": "Float64",
                },
                "training_feature_schema_version": FEATURE_SCHEMA_VERSION,
                "training_feature_schema_versions": [FEATURE_SCHEMA_VERSION],
                "price_adjustment_version": PRICE_ADJUSTMENT_VERSION,
                "availability_schema_version": AVAILABILITY_SCHEMA_VERSION,
                "candidate_strategies": strategies,
                "candidate_strategy_fingerprint": PriceForecaster._candidate_strategy_fingerprint(strategies),
                "missing_value_policy": MISSING_VALUE_POLICY,
                "model_age_policy": MODEL_AGE_POLICY,
                "target_settings": {**DEFAULT_V2_SETTINGS, "vertical_barrier_days": 3},
            }
        )
    )
    forecaster = PriceForecaster(model_dir=str(tmp_path), model_mode="v2_meta_label", ml_config=config)
    try:
        prediction = forecaster.predict("SYNTH", as_of)
    finally:
        forecaster.close()

    assert prediction["candidate_action"] == "BUY"
    assert prediction["threshold_used"] == 0.75
    assert prediction["threshold_source"] == "configured_fallback"
    assert prediction["meta_label_threshold"] == 0.75
    assert prediction["horizon_days"] == 3
    assert prediction["should_execute"] is False
    assert prediction["feature_schema_compatibility"] == "known"
    assert prediction["candidate_explanation"]["threshold_used"] == prediction["threshold_used"] == prediction["meta_label_threshold"]
    assert prediction["candidate_explanation"]["threshold_source"] == prediction["threshold_source"] == "configured_fallback"
    assert prediction["candidate_strategy_fingerprint"] == PriceForecaster._candidate_strategy_fingerprint(strategies)
    assert model.load_calls == model.prediction_calls == 1

    threshold_only_change = {**config, "meta_label_threshold": 0.9}
    threshold_forecaster = PriceForecaster(model_dir=str(tmp_path), model_mode="v2_meta_label", ml_config=threshold_only_change)
    try:
        threshold_prediction = threshold_forecaster.predict("SYNTH", as_of)
    finally:
        threshold_forecaster.close()
    assert threshold_prediction["threshold_used"] == threshold_prediction["meta_label_threshold"] == 0.9
    assert threshold_prediction["threshold_source"] == "configured_fallback"
    assert threshold_prediction["candidate_strategy_fingerprint"] == prediction["candidate_strategy_fingerprint"]

    changed_strategy = {**config["candidate_strategies"][0], "buy_threshold": 0.006}
    changed_forecaster = PriceForecaster(
        model_dir=str(tmp_path),
        model_mode="v2_meta_label",
        ml_config={**config, "candidate_strategies": [changed_strategy]},
    )
    prior_load_calls = model.load_calls
    try:
        with pytest.raises(ModelIncompatibleError, match="candidate strategies differ"):
            changed_forecaster.predict("SYNTH", as_of)
    finally:
        changed_forecaster.close()
    assert model.load_calls == prior_load_calls


def test_train_reuses_only_fully_compatible_seven_day_model_and_refits_explicitly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(40)]
    frame = pl.DataFrame(
        {
            "market": ["us_equity"] * len(dates),
            "mic": ["XNAS"] * len(dates),
            "symbol": ["SYNTH"] * len(dates),
            "identity_schema_version": [IDENTITY_SCHEMA_VERSION] * len(dates),
            "ticker": ["SYNTH"] * len(dates),
            "date": dates,
            "next_day_return": [0.02 if index % 2 == 0 else -0.01 for index in range(len(dates) - 1)] + [None],
            "label_available_date": [dates[index + 1] if index + 1 < len(dates) else None for index in range(len(dates))],
            "feature_schema_version": pl.Series([FEATURE_SCHEMA_VERSION] * len(dates), dtype=pl.Int8),
            "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION] * len(dates),
            "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION] * len(dates),
            **_action_proof_fields(dates),
            "rsi_14": [float(index % 10) for index in range(len(dates))],
            "macd": [float(index % 4) for index in range(len(dates))],
        }
    )
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    model = _FixedModel()
    build_calls: list[bool] = []

    def _build_estimator(*_args: Any, **_kwargs: Any) -> _FixedModel:
        build_calls.append(True)
        return model

    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", _build_estimator)
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_class_weights", lambda _y: {"scale_pos_weight": 1.0})
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_shap_importance", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", lambda *_args, **_kwargs: 0.65)
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    start_date = dates[0]
    first_cutoff = dates[29]
    reuse_cutoff = dates[36]
    try:
        forecaster.train_model("SYNTH", start_date, first_cutoff, max_model_age_days=0)
        model_path = tmp_path / forecaster._build_model_filename(InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH"), first_cutoff)

        loaded_paths: list[Path] = []

        def _load_saved_model(_self: PriceForecaster, path: Path) -> _FixedModel:
            loaded_paths.append(path)
            return model

        monkeypatch.setattr(PriceForecaster, "_load_model", _load_saved_model)
        forecaster.train_model("SYNTH", start_date, reuse_cutoff, max_model_age_days=7)
        reuse_summary = forecaster.last_training_summary()
        assert reuse_summary is not None
        assert reuse_summary["status"] == "reused_fresh_model"
        assert loaded_paths == [model_path]
        assert len(build_calls) == 1

        sidecar_path = model_path.with_suffix(".training_metadata.json")
        sidecar = json.loads(sidecar_path.read_text())
        sidecar["missing_value_policy"] = "incompatible-policy"
        sidecar_path.write_text(json.dumps(sidecar))
        forecaster.train_model("SYNTH", start_date, reuse_cutoff, max_model_age_days=7)
        refit_summary = forecaster.last_training_summary()
        assert refit_summary is not None
        assert refit_summary["status"] == "trained"
        assert len(build_calls) == 2
    finally:
        forecaster.close()


def test_training_sidecar_records_actual_schema_config_candidates_and_boundaries(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    rows = 80
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(rows)]
    frame = pl.DataFrame(
        {
            "market": ["us_equity"] * rows,
            "mic": ["XNAS"] * rows,
            "symbol": ["SYNTH"] * rows,
            "identity_schema_version": [IDENTITY_SCHEMA_VERSION] * rows,
            "ticker": ["SYNTH"] * rows,
            "date": dates,
            "next_day_return": [0.02 if index % 2 == 0 else -0.01 for index in range(rows - 1)] + [None],
            "label_available_date": [dates[index + 1] if index + 1 < rows else None for index in range(rows)],
            "feature_schema_version": pl.Series([FEATURE_SCHEMA_VERSION] * rows, dtype=pl.Int8),
            "price_adjustment_version": [PRICE_ADJUSTMENT_VERSION] * rows,
            "availability_schema_version": [AVAILABILITY_SCHEMA_VERSION] * rows,
            **_action_proof_fields(dates),
            "rsi_14": pl.Series([float(index % 17) for index in range(rows)], dtype=pl.Float32),
            "macd": pl.Series([index % 5 for index in range(rows)], dtype=pl.Int16),
        }
    )
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *_args, **_kwargs: _FixedModel())
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_shap_importance", lambda *_args, **_kwargs: {})
    strategies = [{"name": "provenance-test", "lookback_days": 4, "buy_threshold": 0.1, "sell_threshold": -0.1}]
    forecaster = PriceForecaster(model_dir=str(tmp_path), ml_config={"candidate_strategies": strategies})
    end_date = dates[-1]
    try:
        forecaster.train_model("SYNTH", dates[0], end_date, max_model_age_days=0)
    finally:
        forecaster.close()

    instrument = InstrumentKey(market="us_equity", mic="XNAS", symbol="SYNTH")
    metadata_name = f"instrument-v{IDENTITY_SCHEMA_VERSION}-{instrument.artifact_token}_xgboost_v1_direction_{end_date}.training_metadata.json"
    metadata_path = tmp_path / metadata_name
    model_path = tmp_path / (metadata_path.name.removesuffix(".training_metadata.json") + ".pkl")
    metadata = json.loads(metadata_path.read_text())
    assert metadata["provenance_version"] == 3
    assert metadata["artifact_sha256"] == hashlib.sha256(model_path.read_bytes()).hexdigest()
    monkeypatch.setattr("equity_lake.ml.forecasting.backend_of", lambda _model: "xgboost")
    assert isinstance(forecaster._load_model(model_path), _FixedModel)
    assert metadata["training_feature_schema_version"] == FEATURE_SCHEMA_VERSION
    assert metadata["training_feature_schema_versions"] == [FEATURE_SCHEMA_VERSION]
    assert metadata["price_adjustment_version"] == PRICE_ADJUSTMENT_VERSION
    assert metadata["availability_schema_version"] == AVAILABILITY_SCHEMA_VERSION
    assert metadata["features"] == ["action_coverage", "action_source_age_days", "rsi_14", "macd"]
    assert metadata["feature_dtypes"] == {
        "action_coverage": "Int8",
        "action_source_age_days": "Int32",
        "rsi_14": "Float32",
        "macd": "Int16",
    }
    assert metadata["candidate_strategies"] == strategies
    assert metadata["candidate_strategy_fingerprint"] == PriceForecaster._candidate_strategy_fingerprint(strategies)
    assert metadata["missing_value_policy"] == MISSING_VALUE_POLICY
    assert metadata["model_age_policy"] == MODEL_AGE_POLICY
    assert metadata["training_config"]["ml_config"]["candidate_strategies"] == strategies
    assert metadata["refit_boundary"] == str(end_date)
    assert date.fromisoformat(metadata["training_start"]) < end_date
    audit_frame = frame.select("market", "mic", "symbol", "identity_schema_version", "ticker", "date").with_columns(
        pl.lit("BUY").alias("candidate_action"),
        pl.lit("provenance-test").alias("candidate_source"),
        pl.lit(0.5).alias("candidate_score"),
        pl.lit(1).alias("meta_label"),
        pl.lit("profit").alias("barrier_outcome"),
        pl.lit(0.1).alias("upper_barrier_return"),
        pl.lit(-0.1).alias("lower_barrier_return"),
        pl.lit(5).alias("vertical_barrier_days"),
    )
    forecaster._save_training_audit_artifact(model_path, audit_frame)
    audit_path = model_path.with_suffix(".training_audit.parquet")
    audit = pl.read_parquet(audit_path)
    assert {"market", "mic", "symbol", "identity_schema_version"}.issubset(audit.columns)
    assert audit.select("market", "mic", "symbol").unique().rows() == [("us_equity", "XNAS", "SYNTH")]
