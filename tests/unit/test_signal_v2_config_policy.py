"""Regression tests for selected v2 candidates and effective threshold explanations."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

import equity_lake.signals.generators.meta_label as meta_label_module  # type: ignore[import-untyped]
import equity_lake.signals.generators.ml as ml_module  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.ml.forecasting import ModelMissingError, ModelUnavailableError  # type: ignore[import-untyped]
from equity_lake.signals.config import load_signal_config  # type: ignore[import-untyped]
from equity_lake.signals.generators.meta_label import MetaLabelSignalGenerator
from equity_lake.signals.generators.ml import MLPredictionSignalGenerator
from equity_lake.signals.models import SignalConfig, SignalRecord, Watchlist  # type: ignore[import-untyped]
from equity_lake.signals.scanner import SignalScanner  # type: ignore[import-untyped]


def _write_config(path: Path, ml_block: str, backtest_block: str | None = None) -> Path:
    backtest = backtest_block or "backtest: {enabled: false, strategies: [{name: selected, lookback_days: 7}]}"
    path.write_text(f"{backtest}\nsentiment: {{enabled: false}}\nml: {{enabled: true, mode: v2_meta_label, {ml_block}}}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("ml_block", "backtest_block", "expected_name"),
    [
        ("model_dir: unused", None, "selected"),
        (
            "model_dir: unused, candidate_strategies: [{name: explicit, lookback_days: 3}]",
            "backtest: {enabled: false, strategies: [{name: ignored, lookback_days: 9}]}",
            "explicit",
        ),
    ],
)
def test_scanner_passes_selected_v2_candidates_through_ml_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ml_block: str,
    backtest_block: str | None,
    expected_name: str,
) -> None:
    captured: dict[str, Any] = {}

    class FakeForecaster:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(meta_label_module, "PriceForecaster", FakeForecaster)
    config = load_signal_config(_write_config(tmp_path / "signals.yaml", ml_block, backtest_block))
    scanner = SignalScanner(
        config,
        Watchlist(name="Test", tickers=["AAPL"]),
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
    )

    assert captured["model_mode"] == "v2_meta_label"
    assert captured["ml_config"]["candidate_strategies"][0]["name"] == expected_name
    assert isinstance(scanner.generators[0], MetaLabelSignalGenerator)


def test_v2_config_rejects_empty_or_missing_selected_candidate_rules(tmp_path: Path) -> None:
    path = tmp_path / "signals.yaml"
    _write_config(path, "model_dir: unused, candidate_strategies: []")
    with pytest.raises(ValueError, match="candidate_strategies must not be empty"):
        load_signal_config(path)

    _write_config(path, "model_dir: unused", "backtest: {enabled: false, strategies: []}")
    with pytest.raises(ValueError, match="requires non-empty"):
        load_signal_config(path)


def test_direct_v2_scanner_does_not_fall_back_to_repository_rules(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    called = False

    def unexpected_forecaster(**_: Any) -> object:
        nonlocal called
        called = True
        raise AssertionError("no selected candidates must not construct a forecaster")

    monkeypatch.setattr(meta_label_module, "PriceForecaster", unexpected_forecaster)
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": True, "mode": "v2_meta_label", "model_dir": str(tmp_path)},
    )
    scanner = SignalScanner(
        config,
        Watchlist(name="Test", tickers=["AAPL"]),
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
    )

    assert not called
    outcome = scanner.generators[0].generate_outcome(InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"), date(2024, 12, 2))
    assert outcome.status == "error"
    assert "no candidate strategies selected" in (outcome.detail or "")


def _meta_generator(monkeypatch: pytest.MonkeyPatch, prediction: dict[str, Any], configured_threshold: float = 0.99) -> MetaLabelSignalGenerator:
    class FakeForecaster:
        def __init__(self, **_: Any) -> None:
            pass

        def predict(self, **_: Any) -> dict[str, Any]:
            return {"market": "us_equity", "mic": "XNAS", "ticker": "AAPL", **prediction}

    monkeypatch.setattr(meta_label_module, "PriceForecaster", FakeForecaster)
    return MetaLabelSignalGenerator(
        {
            "enabled": True,
            "mode": "v2_meta_label",
            "candidate_strategies": [{"name": "selected", "lookback_days": 3}],
            "meta_label_threshold": configured_threshold,
        }
    )


def test_v2_explanation_and_history_use_effective_threshold_and_source(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = _meta_generator(
        monkeypatch,
        {
            "should_execute": True,
            "execution_probability": 0.70,
            "candidate_action": "BUY",
            "candidate_source": "selected",
            "threshold_used": 0.50,
            "threshold_source": "stored_optimized",
            "meta_label_threshold": 0.99,
            "horizon_days": 1,
            "barrier_settings": {"vertical_barrier_days": 5},
        },
    )
    outcome = generator.generate_outcome(InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"), date(2024, 12, 2))

    assert outcome.status == "signal"
    assert outcome.signal is not None
    assert "threshold=0.50" in outcome.signal.reasoning
    assert "source=stored_optimized" in outcome.signal.reasoning
    assert outcome.signal.metadata["threshold_used"] == 0.50
    assert outcome.signal.metadata["threshold_source"] == "stored_optimized"
    assert outcome.signal.metadata["meta_label_threshold"] == 0.50
    assert outcome.signal.metadata["horizon_days"] == 1
    record = SignalRecord.from_signal(outcome.signal)
    assert record.meta_label_threshold == 0.50
    assert "source=stored_optimized" in record.reasoning


def test_legacy_threshold_output_uses_unknown_source_and_effective_value(monkeypatch: pytest.MonkeyPatch) -> None:
    generator = _meta_generator(
        monkeypatch,
        {
            "should_execute": True,
            "execution_probability": 0.70,
            "candidate_action": "BUY",
            "candidate_source": "legacy",
            "meta_label_threshold": 0.50,
            "barrier_settings": {"vertical_barrier_days": 4},
        },
    )
    outcome = generator.generate_outcome(InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"), date(2024, 12, 2))

    assert outcome.signal is not None
    assert "threshold=0.50" in outcome.signal.reasoning
    assert "source=unknown" in outcome.signal.reasoning
    assert outcome.signal.metadata["threshold_used"] == 0.50
    assert outcome.signal.metadata["threshold_source"] == "unknown"
    assert outcome.signal.metadata["horizon_days"] == 4


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (ModelMissingError("no identity-proven model"), "missing_model"),
        (ModelUnavailableError("feature input unavailable"), "unavailable"),
        (RuntimeError("query execution failed"), "error"),
    ],
)
def test_v1_prediction_failures_have_explicit_outcomes(monkeypatch: pytest.MonkeyPatch, error: Exception, expected_status: str) -> None:
    class FakeForecaster:
        def __init__(self, **_: Any) -> None:
            pass

        def predict(self, **_: Any) -> dict[str, Any]:
            raise error

    monkeypatch.setattr(ml_module, "PriceForecaster", FakeForecaster)
    generator = MLPredictionSignalGenerator({"enabled": True})

    outcome = generator.generate_outcome(InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"), date(2024, 12, 2))

    assert outcome.status == expected_status
    assert str(error) in (outcome.detail or "")


def test_v1_signal_horizon_uses_prediction_output_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeForecaster:
        def __init__(self, **_: Any) -> None:
            pass

        def predict(self, **_: Any) -> dict[str, Any]:
            return {"market": "us_equity", "mic": "XNAS", "ticker": "AAPL", "prediction": 1, "probability": 0.8, "horizon_days": 1}

    monkeypatch.setattr(ml_module, "PriceForecaster", FakeForecaster)
    generator = MLPredictionSignalGenerator({"enabled": True, "horizon_days": 5, "min_confidence": 60})
    signal = generator.generate(InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL"), date(2024, 12, 2))

    assert signal is not None
    assert signal.metadata["horizon_days"] == 1
