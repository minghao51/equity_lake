"""Unit tests for ML package helpers."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import polars as pl
import pytest
from structlog.testing import capture_logs

from equity_lake.core.instruments import (  # type: ignore[import-untyped]  # pyright: ignore[reportMissingImports] -- source import verified by uv
    InstrumentKey,
)
from equity_lake.ml import run_prediction_job, validate_predictions  # type: ignore[import-untyped]


class _FakeForecaster:
    def __init__(self, model_dir: str | None = None, candidate_strategies: list[dict[str, Any]] | None = None) -> None:
        self.model_dir = model_dir
        self.candidate_strategies = candidate_strategies
        self.closed = False

    def predict(self, *, ticker: str | InstrumentKey, date: date) -> dict[str, Any]:
        identity = InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker) if isinstance(ticker, str) else InstrumentKey.model_validate(ticker)
        return {
            **identity.as_columns(),
            "identity_schema_version": 1,
            "date": date,
            "prediction": 1,
            "probability": 0.8,
            "model_version": "fake",
        }

    def close(self) -> None:
        self.closed = True


def _install_fake_forecaster(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.__dict__["PriceForecaster"] = _FakeForecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)


def test_validate_predictions_accepts_exact_boundary_probabilities() -> None:
    """A4 (handoff 08): float32 ``predict_proba`` can return exactly 0.0/1.0;
    inclusive bounds must accept them instead of rejecting the whole batch."""
    frame = pl.DataFrame(
        {
            "market": ["us_equity", "us_equity"],
            "mic": ["XNAS", "XNAS"],
            "symbol": ["AAPL", "MSFT"],
            "ticker": ["AAPL", "MSFT"],
            "identity_schema_version": [1, 1],
            "date": [date(2024, 1, 2), date(2024, 1, 2)],
            "direction": ["up", "down"],
            "probability": [0.0, 1.0],
        }
    )

    assert validate_predictions(frame) is True


def test_validate_predictions_still_rejects_out_of_range() -> None:
    """A4: inclusive bounds still reject probabilities outside [0, 1]."""
    frame = pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "ticker": ["AAPL"],
            "identity_schema_version": [1],
            "date": [date(2024, 1, 2)],
            "direction": ["up"],
            "probability": [1.5],
        }
    )

    assert validate_predictions(frame) is False


def test_run_prediction_job_clips_and_logs_out_of_bounds_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    """A4: values infinitesimally outside [0, 1] are clipped to the boundary
    (with a warning log) and still persist instead of vanishing the batch."""

    class _BoundaryForecaster(_FakeForecaster):
        def predict(self, *, ticker: str | InstrumentKey, date: date) -> dict[str, Any]:
            result = super().predict(ticker=ticker, date=date)
            result["probability"] = 1.0000002
            return result

    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.__dict__["PriceForecaster"] = _BoundaryForecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)

    persisted: dict[str, object] = {}

    def _capture_merge(df: pl.DataFrame, *, table: str, key_columns: list[str]) -> bool:
        persisted["df"] = df
        persisted["table"] = table
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)

    with capture_logs() as logs:
        success, _results = run_prediction_job(
            trading_date=date(2024, 1, 2),
            tickers=["AAPL"],
        )

    assert success is True
    assert persisted["table"] == "04_platinum/predictions"
    persisted_df = persisted["df"]
    assert isinstance(persisted_df, pl.DataFrame)
    assert persisted_df["probability"].to_list() == [1.0]
    assert any(log.get("event") == "prediction_probability_clipped" for log in logs)


def test_run_prediction_job_uses_package_forecaster(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Resolve the package forecaster while capturing the synthetic persistence seam.

    ``run_prediction_job`` targets ``04_platinum/predictions`` keyed by the
    canonical instrument identity and date.
    """
    _install_fake_forecaster(monkeypatch)
    persisted: dict[str, object] = {}

    def _capture_merge(df: pl.DataFrame, *, table: str, key_columns: list[str]) -> bool:
        persisted["df"] = df
        persisted["table"] = table
        persisted["key_columns"] = key_columns
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)

    success, results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL", "MSFT"],
        model_dir=str(tmp_path),
    )

    assert success is True
    assert results["AAPL"]["prediction"]["model_version"] == "fake"
    assert results["MSFT"]["prediction"]["date"] == date(2024, 1, 2)
    assert persisted["table"] == "04_platinum/predictions"
    assert persisted["key_columns"] == ["market", "mic", "symbol", "date"]
    persisted_df = persisted["df"]
    assert isinstance(persisted_df, pl.DataFrame)
    assert persisted_df.select("market", "mic", "symbol", "date").rows() == [
        ("us_equity", "XNAS", "AAPL", date(2024, 1, 2)),
        ("us_equity", "XNAS", "MSFT", date(2024, 1, 2)),
    ]


def test_run_prediction_job_keeps_cn_krx_and_hk_sg_predictions_distinct(monkeypatch: pytest.MonkeyPatch) -> None:
    instruments = [
        InstrumentKey(market="cn_ashare", mic="XSHG", symbol="DUP"),
        InstrumentKey(market="krx_equity", mic="XKRX", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XHKG", symbol="DUP"),
        InstrumentKey(market="hk_sg_equity", mic="XSES", symbol="DUP"),
    ]
    _install_fake_forecaster(monkeypatch)
    captured: dict[str, Any] = {}

    def _capture_merge(df: pl.DataFrame, *, table: str, key_columns: list[str]) -> bool:
        captured.update(df=df, table=table, key_columns=key_columns)
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)
    strategies = [{"name": "explicit", "lookback_days": 3}]
    success, results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=instruments,
        candidate_strategies=strategies,
    )

    assert success is True
    assert set(results) == {instrument.display_key for instrument in instruments}
    predictions = captured["df"]
    assert isinstance(predictions, pl.DataFrame)
    assert set(map(tuple, predictions.select("market", "mic", "symbol").rows())) == {instrument.tuple for instrument in instruments}
    assert predictions.height == 4
    assert captured["key_columns"] == ["market", "mic", "symbol", "date"]


def test_run_prediction_job_keeps_legacy_schema_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """A legacy prediction without training schema provenance is not stamped current."""
    _install_fake_forecaster(monkeypatch)
    captured: dict[str, pl.DataFrame] = {}

    def _capture_merge(df: pl.DataFrame, *, table: str, key_columns: list[str]) -> bool:
        captured["predictions"] = df
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)
    success, _results = run_prediction_job(trading_date=date(2024, 1, 2), tickers=["AAPL"])

    assert success is True
    persisted = captured["predictions"]
    assert persisted["feature_schema_version"].null_count() == 1
    assert persisted.schema["feature_schema_version"] == pl.Int8


def test_run_prediction_job_persists_known_training_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    class _KnownSchemaForecaster(_FakeForecaster):
        def predict(self, *, ticker: str | InstrumentKey, date: date) -> dict[str, Any]:
            result = super().predict(ticker=ticker, date=date)
            result["feature_schema_version"] = 7
            return result

    fake_module = ModuleType("equity_lake.ml.forecasting")
    fake_module.__dict__["PriceForecaster"] = _KnownSchemaForecaster
    monkeypatch.setitem(sys.modules, "equity_lake.ml.forecasting", fake_module)
    captured: dict[str, pl.DataFrame] = {}

    def _capture_merge(df: pl.DataFrame, **_kwargs: Any) -> bool:
        captured["predictions"] = df
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_merge)

    success, _results = run_prediction_job(trading_date=date(2024, 1, 2), tickers=["AAPL"])

    assert success is True
    assert captured["predictions"]["feature_schema_version"].to_list() == [7]
    assert captured["predictions"].schema["feature_schema_version"] == pl.Int8


def test_run_prediction_job_fails_when_persistence_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test (P0): a ``False`` merge result must fail the ML stage.

    Previously the merge result was discarded and only a ``logger.warning`` was
    emitted on exception, so the pipeline could report success with unwritten
    predictions.
    """
    _install_fake_forecaster(monkeypatch)
    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", lambda *_, **__: False)

    success, _results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL"],
    )

    assert success is False


def test_run_prediction_job_fails_when_persistence_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test (P0): a persistence exception must fail the ML stage."""
    _install_fake_forecaster(monkeypatch)

    def _boom(*_: Any, **__: Any) -> bool:
        raise RuntimeError("disk full")

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _boom)

    success, _results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL"],
    )

    assert success is False


# =============================================================================
# W02/F25: inference success is not claimed as persistence success.
# =============================================================================


def test_run_prediction_job_marks_persisted_only_after_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """W02/F25: per-ticker results report ``persisted`` only when the batch write landed."""
    _install_fake_forecaster(monkeypatch)
    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", lambda *_, **__: True)

    success, results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL", "MSFT"],
    )

    assert success is True
    assert results["AAPL"]["success"] is True
    assert results["AAPL"]["persisted"] is True
    assert results["MSFT"]["persisted"] is True


def test_run_prediction_job_write_failure_keeps_persisted_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """W02/F25: a failed batch write never leaves per-ticker persisted-success claims."""
    _install_fake_forecaster(monkeypatch)
    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", lambda *_, **__: False)

    success, results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL", "MSFT"],
    )

    assert success is False
    # Predictions are preserved as diagnostics, but durability is not claimed.
    assert results["AAPL"]["prediction"] is not None
    assert results["AAPL"]["persisted"] is False
    assert results["MSFT"]["persisted"] is False


def test_run_prediction_job_validation_failure_keeps_persisted_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """W02/F25: a batch that fails validation is not written and not reported persisted."""
    _install_fake_forecaster(monkeypatch)
    monkeypatch.setattr("equity_lake.ml.validate_predictions", lambda _df: False)

    write_calls: list[object] = []

    def _capture_write(*args: Any, **_kwargs: Any) -> bool:
        write_calls.append(args)
        return True

    monkeypatch.setattr("equity_lake.storage.delta.merge_delta", _capture_write)

    success, results = run_prediction_job(
        trading_date=date(2024, 1, 2),
        tickers=["AAPL"],
    )

    assert success is False
    assert results["AAPL"]["persisted"] is False
    assert write_calls == []
