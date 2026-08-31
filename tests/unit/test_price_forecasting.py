"""Regression tests for price forecasting model selection."""

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl
import pytest

from equity_lake.ml.candidates import build_candidate_frame
from equity_lake.ml.forecasting import PriceForecaster
from equity_lake.ml.labeling import apply_triple_barrier_labels


def _noop_feature_loader_init(self) -> None:
    return None


def _make_training_frame(ticker: str, periods: int = 80) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=periods, freq="B")
    close = pd.Series(range(100, 100 + periods), dtype=float)
    return pd.DataFrame(
        {
            "ticker": [ticker] * len(dates),
            "date": dates,
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": [1_000_000] * len(dates),
            "next_day_return": [0.01 if i % 2 == 0 else -0.01 for i in range(len(dates))],
            "rsi_14": [50.0 + (i % 5) for i in range(len(dates))],
            "macd": [0.1 * (i % 3) for i in range(len(dates))],
        }
    )


def test_backtest_handles_null_target_on_final_row(monkeypatch, tmp_path) -> None:
    """A1 (handoff 08): the DAG target (``next_day_return > 0`` via shift(-1)) is
    null on the final features row; ``backtest()`` must drop null-target rows
    (mirroring ``_prepare_training_frame``'s consumers) instead of raising
    ``TypeError`` on ``int(null)`` at the last loop index."""
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    frame = _make_training_frame("AAPL", periods=40)
    frame.loc[frame.index[-1], "next_day_return"] = None

    def _fake_load_features(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load_features)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        result = forecaster.backtest("AAPL", date(2024, 1, 1), date(2024, 2, 28), train_window=10)

        assert not result.is_empty()
        # The null-target final row must be excluded, not scored.
        last_frame_date = pd.Timestamp(frame["date"].iloc[-1])
        assert result["date"].max() < last_frame_date
        assert result["actual"].null_count() == 0
        assert set(result["prediction"].unique()).issubset({0, 1})
    finally:
        forecaster.close()


def test_backtest_fits_with_training_scale_pos_weight(monkeypatch, tmp_path) -> None:
    """A3 (handoff 08): ``backtest()`` retrains must use the same
    ``scale_pos_weight`` semantics as ``train_model`` (class-weighted fit),
    not an unweighted estimator."""
    from equity_lake.ml.backends import build_estimator as real_build_estimator

    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    frame = _make_training_frame("AAPL", periods=40)
    # Imbalanced: positive only every 4th day -> fit slice (rows 0..6) has
    # 2 positives / 5 negatives -> scale_pos_weight = 5 / 2 = 2.5.
    frame["next_day_return"] = [0.01 if i % 4 == 0 else -0.01 for i in range(len(frame))]

    def _fake_load_features(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load_features)

    recorded: list[float | None] = []

    def _recording_build_estimator(backend, params=None, **kwargs):
        recorded.append(kwargs.get("scale_pos_weight"))
        return real_build_estimator(backend, params, **kwargs)

    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", _recording_build_estimator)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        forecaster.backtest("AAPL", date(2024, 1, 1), date(2024, 2, 28), train_window=10)

        assert recorded, "backtest() must construct its estimator via build_estimator"
        assert recorded[0] == pytest.approx(2.5)
    finally:
        forecaster.close()


def test_backtest_predicts_with_optimized_threshold(monkeypatch, tmp_path) -> None:
    """A3 (handoff 08): backtest predictions must apply the optimized decision
    threshold (the backtest analog of live ``predict()``'s stored
    ``optimized_threshold``), not a fixed 0.5 cut."""
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    frame = _make_training_frame("AAPL", periods=40)

    def _fake_load_features(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        return frame

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load_features)
    monkeypatch.setattr("equity_lake.ml.forecasting.optimize_threshold", lambda y_true, y_proba: 0.7)

    # A deterministic stub estimator: real XGBoost on this tiny synthetic
    # frame produces near-uniform probabilities, which cannot discriminate
    # a 0.7 threshold from a 0.5 one. The stub cycles probabilities that
    # straddle both cuts (0.6 differs between >=0.7 and >0.5).
    stub_probabilities = [0.2, 0.6, 0.8, 0.4]

    class _StubModel:
        def __init__(self) -> None:
            self.calls = 0

        def predict_proba(self, X: Any) -> np.ndarray:
            n = int(np.asarray(X).shape[0])
            if n == 1:
                p = stub_probabilities[self.calls % len(stub_probabilities)]
                self.calls += 1
                return np.array([[1 - p, p]])
            return np.array(
                [[1 - stub_probabilities[i % len(stub_probabilities)], stub_probabilities[i % len(stub_probabilities)]] for i in range(n)]
            )

    monkeypatch.setattr("equity_lake.ml.forecasting.build_estimator", lambda *args, **kwargs: _StubModel())
    monkeypatch.setattr("equity_lake.ml.forecasting.fit_estimator", lambda *args, **kwargs: None)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        result = forecaster.backtest("AAPL", date(2024, 1, 1), date(2024, 2, 28), train_window=10)

        expected = [int(p >= 0.7) for p in result["probability"].to_list()]
        assert result["prediction"].to_list() == expected
        # A fixed-0.5 threshold would disagree on the 0.6 probabilities.
        fixed = [int(p > 0.5) for p in result["probability"].to_list()]
        assert expected != fixed
        assert 0.6 in result["probability"].to_list()
    finally:
        forecaster.close()


def test_tune_hyperparameters_raises_when_no_purged_fold_fits(monkeypatch, tmp_path) -> None:
    """A2 (handoff 08): short history must raise a clear error advising a longer
    window — the old code silently fell back to unpurged ``KFold(2)``, leaking
    test data into hyperparameter tuning exactly when history is shortest."""
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        x_train = pl.DataFrame({"f1": [float(i) for i in range(50)], "f2": [float(i % 7) for i in range(50)]})
        y_train = pl.Series("target", [i % 2 for i in range(50)], dtype=pl.Int64)

        with pytest.raises(ValueError, match="purged walk-forward fold"):
            forecaster._tune_hyperparameters(
                x_train,
                y_train,
                train_window=252,
                test_window=21,
                embargo_window=1,
                label_horizon_days=1,
            )
    finally:
        forecaster.close()


def test_get_feature_columns_excludes_labels_and_barriers() -> None:
    """Feature columns must never include the label or triple-barrier bookkeeping.

    Regression test for a label-leakage bug where ``NON_FEATURE_COLUMNS`` omitted
    ``target`` / ``barrier_start_idx`` / ``barrier_end_idx``, causing the model to
    train on its own labels.
    """
    frame = _make_training_frame("AAPL")
    frame["target"] = 1
    frame["barrier_start_idx"] = 0
    frame["barrier_end_idx"] = 5

    forecaster = PriceForecaster(model_dir="/tmp")
    features = forecaster._get_feature_columns(frame)

    assert "target" not in features
    assert "barrier_start_idx" not in features
    assert "barrier_end_idx" not in features
    # Genuine features must still be selected.
    assert "rsi_14" in features
    assert "macd" in features


def test_resolve_model_path_uses_latest_model_not_after_target_date(tmp_path) -> None:
    """Historical inference should not pick a model trained in the future."""
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        (tmp_path / "AAPL_xgboost_v1_direction_2026-05-01.pkl").write_bytes(b"old")
        (tmp_path / "AAPL_xgboost_v1_direction_2026-06-01.pkl").write_bytes(b"new")
        (tmp_path / "AAPL_xgboost_v1_direction_2026-07-01.pkl").write_bytes(b"future")

        resolved = forecaster._resolve_model_path("AAPL", date(2026, 6, 15))

        assert resolved is not None
        assert resolved.name == "AAPL_xgboost_v1_direction_2026-06-01.pkl"
    finally:
        forecaster.close()


def test_resolve_model_path_supports_legacy_v1_artifacts(tmp_path) -> None:
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        (tmp_path / "AAPL_xgboost_2026-06-01.pkl").write_bytes(b"legacy")

        resolved = forecaster._resolve_model_path("AAPL", date(2026, 6, 15))

        assert resolved is not None
        assert resolved.name == "AAPL_xgboost_2026-06-01.pkl"
    finally:
        forecaster.close()


def test_train_model_writes_validation_metadata(monkeypatch, tmp_path) -> None:
    def _noop_setup(self) -> None:
        return None

    def _fake_load_features(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        return _make_training_frame(ticker)

    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)
    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load_features)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        forecaster.train_model(
            "AAPL",
            date(2024, 1, 1),
            date(2024, 4, 30),
            validate=True,
            max_model_age_days=0,
            train_window=30,
            test_window=10,
            embargo_window=2,
        )
        metadata = forecaster.load_training_metadata("AAPL", date(2024, 4, 30))

        assert metadata is not None
        assert metadata["model_mode"] == "v1_direction"
        assert metadata["validation"]["validation_mode"] == "purged_walk_forward"
        assert metadata["validation"]["embargo_window"] == 2

        summary = forecaster.load_training_summary("AAPL", date(2024, 4, 30))
        assert summary is not None
        assert summary["validation_fold_count"] >= 0
        assert Path(tmp_path / "AAPL_xgboost_v1_direction_2024-04-30.training_summary.json").exists()
    finally:
        forecaster.close()


def test_v2_meta_label_training_frame_uses_candidate_events(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)
    forecaster = PriceForecaster(
        model_dir=str(tmp_path),
        model_mode="v2_meta_label",
        ml_config={"candidate_strategies": [{"name": "momentum", "lookback_days": 3, "buy_threshold": 0.0, "sell_threshold": -1.0}]},
    )
    try:
        dates = pd.date_range("2024-01-01", periods=10, freq="B")
        df = pd.DataFrame(
            {
                "ticker": ["AAPL"] * len(dates),
                "date": dates,
                "open": [100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
                "high": [101, 102, 103, 104, 105, 108, 109, 110, 111, 112],
                "low": [99, 100, 101, 102, 103, 104, 105, 106, 107, 108],
                "close": [100, 101, 102, 103, 104, 107, 108, 109, 110, 111],
                "volume": [1_000_000] * len(dates),
                "volatility_20": [0.02] * len(dates),
                "next_day_return": [0.01] * len(dates),
                "rsi_14": [55.0] * len(dates),
                "macd": [0.2] * len(dates),
            }
        )

        training_df = forecaster._prepare_training_frame(df)

        assert not training_df.is_empty()
        assert "candidate_source" in training_df.columns
        assert "meta_label" in training_df.columns
        assert set(training_df["candidate_source"].to_list()) == {"momentum"}
    finally:
        forecaster.close()


def test_candidate_generation_deduplicates_mixed_strategy_events() -> None:
    dates = pd.date_range("2024-01-01", periods=6, freq="B")
    df = pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": dates,
            "close": [100.0, 101.0, 102.0, 110.0, 112.0, 113.0],
        }
    )

    candidates = build_candidate_frame(
        df,
        [
            {"name": "fast", "lookback_days": 2, "buy_threshold": 0.01, "sell_threshold": -1.0},
            {"name": "slow", "lookback_days": 3, "buy_threshold": 0.01, "sell_threshold": -1.0},
        ],
    )

    assert not candidates.is_empty()
    assert candidates["date"].n_unique() == candidates.height


def test_candidate_generation_returns_empty_when_no_candidates() -> None:
    dates = pd.date_range("2024-01-01", periods=5, freq="B")
    df = pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": dates,
            "close": [100.0] * len(dates),
        }
    )

    candidates = build_candidate_frame(
        df,
        [{"name": "momentum", "lookback_days": 2, "buy_threshold": 0.5, "sell_threshold": -0.5}],
    )

    assert candidates.is_empty()


def test_triple_barrier_uses_low_volatility_floor() -> None:
    dates = pd.date_range("2024-01-01", periods=4, freq="B")
    full_df = pd.DataFrame(
        {
            "ticker": ["AAPL"] * len(dates),
            "date": dates,
            "open": [100.0, 100.5, 101.0, 101.5],
            "high": [100.4, 100.9, 101.3, 101.7],
            "low": [99.8, 100.1, 100.6, 101.0],
            "close": [100.0, 100.6, 101.1, 101.5],
            "volatility_20": [0.0, 0.0, 0.0, 0.0],
        }
    )
    candidates = pd.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [dates[0]],
            "candidate_action": ["BUY"],
            "candidate_source": ["momentum"],
            "candidate_score": [0.2],
        }
    )

    labeled = apply_triple_barrier_labels(
        candidates,
        full_df,
        vertical_barrier_days=2,
        pt_mult=1.5,
        sl_mult=1.0,
    )

    assert labeled["upper_barrier_return"][0] == 0.0075
    assert labeled["lower_barrier_return"][0] == 0.005


def test_train_model_handles_sparse_optional_feature_columns(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    def _fake_load_features(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        frame = _make_training_frame(ticker)
        frame["social_sentiment_score"] = [None] * len(frame)
        return frame

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load_features)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        forecaster.train_model(
            "AAPL",
            date(2024, 1, 1),
            date(2024, 4, 30),
            max_model_age_days=0,
        )
        assert forecaster.last_training_summary() is not None
    finally:
        forecaster.close()


def test_v2_training_persists_audit_artifact(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    def _fake_load_features(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        close = [100.0, 102.0, 101.0, 103.0, 102.0, 104.0, 103.0, 105.0, 104.0, 106.0] * 3
        frame = pd.DataFrame(
            {
                "ticker": [ticker] * len(dates),
                "date": dates,
                "open": close,
                "high": [value + 1.2 for value in close],
                "low": [value - 1.2 for value in close],
                "close": close,
                "volume": [1_000_000] * len(dates),
                "next_day_return": [0.01 if i % 2 == 0 else -0.01 for i in range(len(dates))],
                "rsi_14": [50.0 + (i % 5) for i in range(len(dates))],
                "macd": [0.1 * ((i % 4) - 1) for i in range(len(dates))],
                "volatility_20": [0.02] * len(dates),
            }
        )
        return frame

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load_features)

    forecaster = PriceForecaster(
        model_dir=str(tmp_path),
        model_mode="v2_meta_label",
        ml_config={"candidate_strategies": [{"name": "momentum", "lookback_days": 3, "buy_threshold": 0.0, "sell_threshold": -1.0}]},
    )
    try:
        forecaster.train_model(
            "AAPL",
            date(2024, 1, 1),
            date(2024, 2, 15),
            max_model_age_days=0,
        )

        audit_path = tmp_path / "AAPL_xgboost_v2_meta_label_2024-02-15.training_audit.parquet"
        assert audit_path.exists()

        audit_df = pl.read_parquet(audit_path)
        assert {
            "ticker",
            "date",
            "candidate_action",
            "candidate_source",
            "candidate_score",
            "meta_label",
            "barrier_outcome",
            "upper_barrier_return",
            "lower_barrier_return",
            "vertical_barrier_days",
        }.issubset(audit_df.columns)
    finally:
        forecaster.close()


def test_predict_uses_trained_feature_set_when_scoring_columns_evolve(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    training_frame = _make_training_frame("AAPL")

    def _load_training(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        return training_frame

    monkeypatch.setattr(PriceForecaster, "load_features", _load_training)

    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        forecaster.train_model(
            "AAPL",
            date(2024, 1, 1),
            date(2024, 4, 19),
            max_model_age_days=0,
        )

        prediction_date = date(2024, 4, 19)

        def _load_scoring(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
            scoring = training_frame[training_frame["date"] <= pd.Timestamp(prediction_date)].copy()
            return scoring.drop(columns=["macd"]).assign(extra_feature=1.0)

        monkeypatch.setattr(PriceForecaster, "load_features", _load_scoring)

        prediction = forecaster.predict("AAPL", prediction_date)

        assert prediction["ticker"] == "AAPL"
        assert prediction["model_mode"] == "v1_direction"
    finally:
        forecaster.close()


def test_class_balance_recorded_in_metadata(monkeypatch, tmp_path) -> None:
    """Imbalanced training set should record class_balance and scale_pos_weight."""
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    def _fake_load(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        frame = _make_training_frame(ticker, periods=120)
        frame["next_day_return"] = [0.01 if i % 5 == 0 else -0.01 for i in range(len(frame))]
        return frame

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load)
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        forecaster.train_model("AAPL", date(2024, 1, 1), date(2024, 6, 30), max_model_age_days=0)
        metadata = forecaster.load_training_metadata("AAPL", date(2024, 6, 30))
        assert metadata is not None
        assert "class_balance" in metadata
        cb = metadata["class_balance"]
        assert cb["scale_pos_weight"] > 1.0
        assert cb["positive_count"] + cb["negative_count"] > 0
    finally:
        forecaster.close()


def test_optimized_threshold_recorded_in_metadata(monkeypatch, tmp_path) -> None:
    """Validation set with clear class separation should produce a non-default threshold."""
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    def _fake_load(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        frame = _make_training_frame(ticker, periods=120)
        return frame

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load)
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        forecaster.train_model("AAPL", date(2024, 1, 1), date(2024, 6, 30), max_model_age_days=0)
        metadata = forecaster.load_training_metadata("AAPL", date(2024, 6, 30))
        assert metadata is not None
        assert "optimized_threshold" in metadata
        assert 0.1 <= metadata["optimized_threshold"] <= 0.9
    finally:
        forecaster.close()


def test_shap_feature_importance_recorded_when_shap_available(monkeypatch, tmp_path) -> None:
    """If SHAP is installed, training metadata should include top features."""
    monkeypatch.setattr("equity_lake.ml.feature_loader.FeatureLoader.__init__", _noop_feature_loader_init)

    def _fake_load(self, ticker: str, start_date: date, end_date: date) -> pd.DataFrame:
        return _make_training_frame(ticker, periods=120)

    monkeypatch.setattr(PriceForecaster, "load_features", _fake_load)
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        forecaster.train_model("AAPL", date(2024, 1, 1), date(2024, 6, 30), max_model_age_days=0)
        metadata = forecaster.load_training_metadata("AAPL", date(2024, 6, 30))
        assert metadata is not None
        if metadata.get("shap_feature_importance") is not None:
            importance = metadata["shap_feature_importance"]
            assert isinstance(importance, dict)
            assert all(isinstance(v, float) for v in importance.values())
            values = list(importance.values())
            assert values == sorted(values, reverse=True)
    finally:
        forecaster.close()
