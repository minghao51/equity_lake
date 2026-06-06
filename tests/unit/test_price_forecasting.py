"""Regression tests for price forecasting model selection."""

from datetime import date
from pathlib import Path

import pandas as pd

from equity_lake.ml.candidates import build_candidate_frame
from equity_lake.ml.forecasting import PriceForecaster
from equity_lake.ml.labeling import apply_triple_barrier_labels


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

    monkeypatch.setattr(PriceForecaster, "_setup_feature_view", _noop_setup)
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
    monkeypatch.setattr(PriceForecaster, "_setup_feature_view", lambda self: None)
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

        assert not training_df.empty
        assert "candidate_source" in training_df.columns
        assert "meta_label" in training_df.columns
        assert training_df["candidate_source"].eq("momentum").all()
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

    assert not candidates.empty
    assert candidates["date"].is_unique


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

    assert candidates.empty


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

    assert labeled.loc[0, "upper_barrier_return"] == 0.0075
    assert labeled.loc[0, "lower_barrier_return"] == 0.005


def test_train_model_handles_sparse_optional_feature_columns(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(PriceForecaster, "_setup_feature_view", lambda self: None)

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
    monkeypatch.setattr(PriceForecaster, "_setup_feature_view", lambda self: None)

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

        audit_df = pd.read_parquet(audit_path)
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
    monkeypatch.setattr(PriceForecaster, "_setup_feature_view", lambda self: None)

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
