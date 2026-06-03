"""Regression tests for price forecasting model selection."""

from datetime import date

from equity_lake.ml.forecasting import PriceForecaster


def test_resolve_model_path_uses_latest_model_not_after_target_date(tmp_path) -> None:
    """Historical inference should not pick a model trained in the future."""
    forecaster = PriceForecaster(model_dir=str(tmp_path))
    try:
        (tmp_path / "AAPL_xgboost_2026-05-01.pkl").write_bytes(b"old")
        (tmp_path / "AAPL_xgboost_2026-06-01.pkl").write_bytes(b"new")
        (tmp_path / "AAPL_xgboost_2026-07-01.pkl").write_bytes(b"future")

        resolved = forecaster._resolve_model_path("AAPL", date(2026, 6, 15))

        assert resolved is not None
        assert resolved.name == "AAPL_xgboost_2026-06-01.pkl"
    finally:
        forecaster.close()
