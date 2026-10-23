"""Synthetic regressions for per-refit label maturity and interval purging."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from runpy import run_path
from typing import Any, cast

import numpy as np
import polars as pl
import pytest

from equity_lake.core.instruments import (
    InstrumentKey,  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
)
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION  # type: ignore[import-untyped]
from equity_lake.ml.forecasting import PriceForecaster  # type: ignore[import-untyped]
from equity_lake.ml.labeling import apply_triple_barrier_labels  # type: ignore[import-untyped]

_pit_fixtures = run_path(str(Path(__file__).with_name("_pit_fixtures.py")))
with_synthetic_feature_proof = _pit_fixtures["with_synthetic_feature_proof"]


def _training_features(rows: int = 45) -> pl.DataFrame:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(rows)]
    availability = [dates[index + 1] if index + 1 < rows else None for index in range(rows)]
    # The ninth training event crosses the first refit's decision cutoff and
    # must not enter either its fit or threshold-validation set.
    availability[9] = dates[11]
    return _with_adjustment_contract(
        pl.DataFrame(
            {
                "market": ["us_equity"] * rows,
                "mic": ["XNAS"] * rows,
                "symbol": ["SYNTH"] * rows,
                "identity_schema_version": [1] * rows,
                "ticker": ["SYNTH"] * rows,
                "date": dates,
                "next_day_return": [0.02 if index % 3 == 0 else -0.01 for index in range(rows - 1)] + [None],
                "label_available_date": availability,
                "feature_schema_version": [FEATURE_SCHEMA_VERSION] * rows,
                "feature_value": [float(index) for index in range(rows)],
            }
        )
    )


class _RecordingModel:
    def __init__(self) -> None:
        self.predict_inputs: list[list[float]] = []

    def predict_proba(self, features: pl.DataFrame) -> np.ndarray:
        values = features["feature_value"].to_list()
        self.predict_inputs.append(values)
        return np.tile(np.array([[0.4, 0.6]]), (len(values), 1))


def test_backtest_uses_only_refit_available_labels_and_purges_crossing_intervals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    frame = _training_features()
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)

    model = _RecordingModel()
    fit_inputs: list[float] = []
    threshold_labels: list[list[int]] = []

    def _record_fit(_model: Any, features: pl.DataFrame, _targets: np.ndarray, **_kwargs: Any) -> None:
        fit_inputs.extend(features["feature_value"].to_list())

    def _record_threshold(labels: pl.Series, _probabilities: np.ndarray) -> float:
        threshold_labels.extend(labels.cast(pl.Int64).to_list())
        return 0.71

    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *_args, **_kwargs: model)
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", _record_fit)
    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", _record_threshold)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        results = forecaster.backtest("SYNTH", date(2024, 1, 1), frame["date"].to_list()[-1], train_window=10, retrain_interval=1000)
    finally:
        forecaster.close()

    # Refit at row 10: row 9's label is not mature by this cutoff. Row 6's
    # interval reaches the validation start (row 7) and is purged from fitting.
    dates = frame["date"].to_list()
    first_result = results.row(0, named=True)
    assert fit_inputs == [float(index) for index in range(6)]
    assert model.predict_inputs[0] == [7.0, 8.0]
    assert len(threshold_labels) == 2
    assert first_result["refit_boundary"] == str(dates[10])
    assert first_result["model_trained_at"] == str(dates[10])
    assert first_result["training_start"] == str(dates[0])
    assert first_result["threshold_used"] == 0.71
    assert first_result["threshold_source"] == "refit_validation"
    assert first_result["horizon_days"] == 1
    assert results["actual"].null_count() == 0
    result_end = date.fromisoformat(str(results["date"].max())[:10])
    assert result_end <= dates[-1]


def test_backtest_resets_threshold_when_later_refit_has_no_validation_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    rows = 50
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(rows)]
    availability = [dates[index + 1] if index + 1 < rows else None for index in range(rows)]
    # At the second refit (date 20), only the first row in its rolling window
    # is mature. This leaves a fit row but an empty 20% threshold-validation tail.
    for index in range(11, 20):
        availability[index] = dates[25]
    frame = pl.DataFrame(
        {
            "market": ["us_equity"] * rows,
            "mic": ["XNAS"] * rows,
            "symbol": ["SYNTH"] * rows,
            "identity_schema_version": [1] * rows,
            "ticker": ["SYNTH"] * rows,
            "date": dates,
            "next_day_return": [0.02 if index % 3 == 0 else -0.01 for index in range(rows - 1)] + [None],
            "label_available_date": availability,
            "feature_schema_version": [FEATURE_SCHEMA_VERSION] * rows,
            "feature_value": [float(index) for index in range(rows)],
        }
    )
    frame = _with_adjustment_contract(frame)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    model = _RecordingModel()
    optimized_labels: list[list[int]] = []

    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *_args, **_kwargs: model)
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_class_weights", lambda _y: {"scale_pos_weight": 1.0})

    def _record_threshold(labels: pl.Series, _probabilities: np.ndarray) -> float:
        optimized_labels.append(labels.cast(pl.Int64).to_list())
        return 0.71

    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", _record_threshold)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        results = forecaster.backtest("SYNTH", dates[0], dates[-1], train_window=10, retrain_interval=10)
    finally:
        forecaster.close()

    first_refit = results.filter(pl.col("date") == dates[10]).row(0, named=True)
    second_refit = results.filter(pl.col("date") == dates[20]).row(0, named=True)
    assert first_refit["threshold_used"] == 0.71
    assert first_refit["threshold_source"] == "refit_validation"
    assert second_refit["threshold_used"] == 0.5
    assert second_refit["threshold_source"] == "default"
    # The second refit skips optimization; later mature windows may optimize again.
    assert len(optimized_labels) == 3


@pytest.mark.parametrize("model_mode", ["v1_direction", "v2_meta_label"])
def test_train_model_passes_maturity_metadata_to_validation_and_tuning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, model_mode: str) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    frame = _training_features(rows=21)
    dates = frame["date"].to_list()
    maturity = frame["label_available_date"].to_list()
    maturity[4] = dates[-1] + timedelta(days=1)  # complete target, but immature at the training cutoff
    if model_mode == "v2_meta_label":
        frame = frame.drop("next_day_return", "label_available_date").with_columns(
            pl.Series("meta_label", [index % 2 for index in range(frame.height)]),
            pl.Series("barrier_end_date", maturity),
            pl.Series("candidate_action", ["BUY"] * frame.height),
            pl.Series("candidate_source", ["synthetic"] * frame.height),
            pl.Series("candidate_score", [float(index) for index in range(frame.height)]),
            pl.Series("barrier_outcome", ["time_profit"] * frame.height),
        )
        monkeypatch.setattr(PriceForecaster, "_prepare_training_frame", lambda _self, labeled: labeled)
        monkeypatch.setattr("equity_lake.ml.forecasting.compute_uniqueness_weights", lambda labeled: np.ones(labeled.height))
        monkeypatch.setattr(PriceForecaster, "_save_training_audit_artifact", lambda *_args, **_kwargs: None)
    else:
        frame = frame.with_columns(pl.Series("label_available_date", maturity))
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    captured: dict[str, Any] = {}

    def _capture_validation(self: PriceForecaster, **kwargs: Any) -> dict[str, int]:
        captured["validation"] = kwargs
        return {"folds": 0}

    def _capture_tuning(self: PriceForecaster, X_train: pl.DataFrame, _y_train: pl.Series, **kwargs: Any) -> _RecordingModel:
        captured["tuning"] = kwargs
        captured["tuning_features"] = X_train
        return _RecordingModel()

    monkeypatch.setattr(PriceForecaster, "_validate_model", _capture_validation)
    monkeypatch.setattr(PriceForecaster, "_tune_hyperparameters", _capture_tuning)
    monkeypatch.setattr(PriceForecaster, "_save_training_metadata", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(PriceForecaster, "_save_training_summary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_shap_importance", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", lambda *_args, **_kwargs: 0.65)

    forecaster = PriceForecaster(
        model_dir=str(tmp_path),
        model_mode=model_mode,
        ml_config={"vertical_barrier_days": 3} if model_mode == "v2_meta_label" else None,
    )
    try:
        forecaster.train_model(
            "SYNTH",
            dates[0],
            dates[-1],
            tune_hyperparams=True,
            validate=True,
            max_model_age_days=0,
            train_window=5,
            test_window=2,
            embargo_window=0,
        )
    finally:
        forecaster.close()

    validation_args = captured["validation"]
    tuning_args = captured["tuning"]
    assert validation_args["decision_cutoff"] == dates[-1]
    assert all(value is not None and value <= dates[-1] for value in validation_args["event_end_dates"])
    assert dates[4] not in validation_args["decision_dates"]  # immature label excluded before CV
    assert tuning_args["decision_cutoff"] == dates[-1]
    assert all(value is not None and value <= dates[-1] for value in tuning_args["event_end_dates"])
    assert len(tuning_args["event_end_dates"]) == captured["tuning_features"].height
    assert "date" not in captured["tuning_features"].columns
    assert "label_available_date" not in captured["tuning_features"].columns
    assert "barrier_end_date" not in captured["tuning_features"].columns


def _with_adjustment_contract(frame: pl.DataFrame) -> pl.DataFrame:
    symbol_column = "symbol" if "symbol" in frame.columns else "ticker"
    symbols = frame[symbol_column].drop_nulls().cast(pl.String).unique().to_list()
    if len(symbols) != 1 or frame[symbol_column].null_count():
        raise ValueError("Synthetic adjustment fixture must contain one explicit symbol")
    market = frame["market"].unique().item(0) if "market" in frame.columns else "us_equity"
    mic = frame["mic"].unique().item(0) if "mic" in frame.columns else "XNAS"
    instrument = InstrumentKey(market=market, mic=mic, symbol=symbols[0])
    return cast(pl.DataFrame, with_synthetic_feature_proof(frame, instrument))


def test_triple_barrier_right_edge_without_full_horizon_stays_pending() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(4)]
    full_frame = pl.DataFrame(
        {
            "ticker": ["SYNTH"] * 4,
            "date": dates,
            "high": [100.5] * 4,
            "low": [99.5] * 4,
            "close": [100.0] * 4,
            "volatility_20": [0.02] * 4,
        }
    )
    candidates = pl.DataFrame(
        {
            "ticker": ["SYNTH", "SYNTH", "SYNTH"],
            "date": [dates[0], dates[2], dates[3]],
            "candidate_action": ["BUY"] * 3,
            "candidate_source": ["synthetic"] * 3,
            "candidate_score": [0.1, 0.2, 0.3],
        }
    )
    full_frame = _with_adjustment_contract(full_frame)
    candidates = _with_adjustment_contract(candidates)

    labeled = apply_triple_barrier_labels(candidates, full_frame, vertical_barrier_days=2, pt_mult=1.5, sl_mult=1.0)

    assert labeled["meta_label"][0] is not None
    assert labeled["barrier_end_date"][0] == dates[2]
    assert labeled["meta_label"][1] is None
    assert labeled["barrier_outcome"][1] == "pending"
    assert labeled["barrier_end_date"][1] is None
    assert labeled["meta_label"][2] is None
    assert labeled["barrier_outcome"][2] == "pending"


def test_triple_barrier_right_edge_hit_matures_before_full_vertical_horizon() -> None:
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(4)]
    full_frame = pl.DataFrame(
        {
            "ticker": ["SYNTH"] * 4,
            "date": dates,
            "high": [100.5, 100.5, 100.5, 104.0],
            "low": [99.5] * 4,
            "close": [100.0] * 4,
            "volatility_20": [0.02] * 4,
        }
    )
    candidates = pl.DataFrame(
        {
            "ticker": ["SYNTH"],
            "date": [dates[2]],
            "candidate_action": ["BUY"],
            "candidate_source": ["synthetic"],
            "candidate_score": [0.3],
        }
    )
    full_frame = _with_adjustment_contract(full_frame)
    candidates = _with_adjustment_contract(candidates)

    labeled = apply_triple_barrier_labels(candidates, full_frame, vertical_barrier_days=2, pt_mult=1.5, sl_mult=1.0)

    assert labeled["meta_label"][0] == 1
    assert labeled["barrier_outcome"][0] == "profit"
    assert labeled["barrier_end_idx"][0] == 3
    assert labeled["barrier_end_date"][0] == dates[3]


def test_v2_refit_eligibility_and_purge_use_actual_barrier_interval(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    forecaster = PriceForecaster(model_dir=str(tmp_path), model_mode="v2_meta_label", ml_config={"vertical_barrier_days": 3})
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(4)]
    rows = pl.DataFrame(
        {
            "date": dates[:3],
            "meta_label": [1, 0, 1],
            "barrier_end_date": [dates[2], dates[3], None],
        }
    )
    try:
        eligible = forecaster._filter_complete_labels(rows, "meta_label", dates[3])
        assert eligible["date"].to_list() == dates[:2]
        purged = forecaster._purge_crossing_labels(eligible, dates[3])
        assert purged["date"].to_list() == [dates[0]]
        assert forecaster._label_horizon_days() == 3
    finally:
        forecaster.close()


def test_v2_backtest_selects_only_matured_and_purged_validation_events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    rows = 100
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(rows)]
    closes = [100.0 + (index % 4) * 0.4 for index in range(rows)]
    frame = pl.DataFrame(
        {
            "market": ["us_equity"] * rows,
            "mic": ["XNAS"] * rows,
            "symbol": ["SYNTH"] * rows,
            "identity_schema_version": [1] * rows,
            "ticker": ["SYNTH"] * rows,
            "date": dates,
            "open": closes,
            "high": [value + 0.1 for value in closes],
            "low": [value - 0.1 for value in closes],
            "close": closes,
            "volume": [1000.0] * rows,
            "volatility_20": [0.01] * rows,
            "feature_schema_version": [FEATURE_SCHEMA_VERSION] * rows,
            "feature_value": [float(index) for index in range(rows)],
        }
    )
    frame = _with_adjustment_contract(frame)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)

    model = _RecordingModel()
    fit_inputs: list[float] = []
    threshold_labels: list[int] = []

    def _record_fit(_model: Any, features: pl.DataFrame, _targets: np.ndarray, **_kwargs: Any) -> None:
        fit_inputs.extend(features["feature_value"].to_list())

    def _record_threshold(labels: pl.Series, _probabilities: np.ndarray) -> float:
        threshold_labels.extend(labels.cast(pl.Int64).to_list())
        return 0.63

    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *_args, **_kwargs: model)
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", _record_fit)
    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", _record_threshold)
    config = {
        "vertical_barrier_days": 2,
        "pt_mult": 100.0,
        "sl_mult": 100.0,
        "meta_label_threshold": 0.55,
        "candidate_strategies": [{"name": "synthetic", "lookback_days": 2, "buy_threshold": -1.0, "sell_threshold": -2.0}],
    }
    forecaster = PriceForecaster(model_dir=str(tmp_path), model_mode="v2_meta_label", ml_config=config)
    try:
        results = forecaster.backtest("SYNTH", dates[0], dates[-1], train_window=20, retrain_interval=1000)
    finally:
        forecaster.close()

    validation_inputs = model.predict_inputs[0]
    first_result = results.row(0, named=True)
    assert fit_inputs
    assert validation_inputs
    assert max(fit_inputs) + 2 < min(validation_inputs)  # exact barrier interval purge
    assert all(dates[int(index)] <= first_result["date"] for index in validation_inputs)
    assert len(threshold_labels) == len(validation_inputs)
    assert first_result["threshold_source"] == "refit_validation"
    assert first_result["horizon_days"] == 2
    assert results["actual"].null_count() == 0
    assert "pending" not in results["barrier_outcome"].to_list()


def test_v2_backtest_uses_configured_threshold_when_later_refit_has_no_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", lambda _self: None)
    rows = 50
    dates = [date(2024, 1, 1) + timedelta(days=index) for index in range(rows)]
    barrier_end_dates = [dates[index + 1] if index + 1 < rows else None for index in range(rows)]
    # At the second refit (date 20), only row 10 is mature in its rolling
    # window, leaving no threshold-validation tail.
    for index in range(11, 20):
        barrier_end_dates[index] = dates[25]
    frame = pl.DataFrame(
        {
            "market": ["us_equity"] * rows,
            "mic": ["XNAS"] * rows,
            "symbol": ["SYNTH"] * rows,
            "identity_schema_version": [1] * rows,
            "ticker": ["SYNTH"] * rows,
            "date": dates,
            "meta_label": [index % 2 for index in range(rows - 1)] + [None],
            "barrier_end_date": barrier_end_dates,
            "candidate_action": ["BUY"] * rows,
            "candidate_source": ["synthetic"] * rows,
            "candidate_score": [float(index) for index in range(rows)],
            "barrier_outcome": ["time_profit"] * rows,
            "feature_schema_version": [FEATURE_SCHEMA_VERSION] * rows,
            "feature_value": [float(index) for index in range(rows)],
        }
    )
    frame = _with_adjustment_contract(frame)
    monkeypatch.setattr(PriceForecaster, "load_features", lambda *_args, **_kwargs: frame)
    monkeypatch.setattr(PriceForecaster, "_prepare_training_frame", lambda _self, features: features)
    model = _RecordingModel()
    optimized_labels: list[list[int]] = []

    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *_args, **_kwargs: model)
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("equity_lake.ml.forecasting.compute_class_weights", lambda _y: {"scale_pos_weight": 1.0})

    def _record_threshold(labels: pl.Series, _probabilities: np.ndarray) -> float:
        optimized_labels.append(labels.cast(pl.Int64).to_list())
        return 0.71

    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", _record_threshold)

    forecaster = PriceForecaster(model_dir=str(tmp_path), model_mode="v2_meta_label", ml_config={"meta_label_threshold": 0.62})
    try:
        results = forecaster.backtest("SYNTH", dates[0], dates[-1], train_window=10, retrain_interval=10)
    finally:
        forecaster.close()

    first_refit = results.filter(pl.col("date") == dates[10]).row(0, named=True)
    second_refit = results.filter(pl.col("date") == dates[20]).row(0, named=True)
    assert first_refit["threshold_used"] == 0.71
    assert first_refit["threshold_source"] == "refit_validation"
    assert second_refit["threshold_used"] == 0.62
    assert second_refit["threshold_source"] == "configured_fallback"
    assert second_refit["threshold_used"] != first_refit["threshold_used"]
    assert second_refit["threshold_source"] != first_refit["threshold_source"]
    assert len(optimized_labels) == 3
