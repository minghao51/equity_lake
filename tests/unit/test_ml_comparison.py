"""Unit tests for ``ml/comparison.run_comparison`` (pure, synthetic data)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from equity_lake.findings.writer import load_finding_cards
from equity_lake.ml.comparison import run_comparison


def _make_features_frame(ticker: str = "AAPL", periods: int = 300) -> pl.DataFrame:
    """Synthetic per-ticker frame (~300 rows so walk-forward folds exist).

    Mirrors the ``_make_training_frame`` pattern in ``test_price_forecasting``;
    ``close`` rises monotonically so v2 momentum candidates are generated.
    """
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(periods)]
    close = [100.0 + i * 0.5 for i in range(periods)]
    return pl.DataFrame(
        {
            "ticker": [ticker] * periods,
            "date": dates,
            "open": close,
            "high": [c + 1.0 for c in close],
            "low": [c - 1.0 for c in close],
            "close": close,
            "volume": [1_000_000] * periods,
            "next_day_return": [0.01 if i % 2 == 0 else -0.01 for i in range(periods)],
            "rsi_14": [50.0 + (i % 5) for i in range(periods)],
            "macd": [0.1 * (i % 3) for i in range(periods)],
        }
    )


@pytest.fixture
def features_frame() -> pl.DataFrame:
    return _make_features_frame()


def test_run_comparison_returns_two_expected_cards(tmp_path, features_frame: pl.DataFrame) -> None:
    """run_comparison emits exactly the labeling + model FindingCards."""
    pytest.importorskip("lightgbm")  # backend pair requires LightGBM (ml group)

    cards = run_comparison(features=features_frame, base=tmp_path)

    assert len(cards) == 2
    by_id = {card.id: card for card in cards}
    assert set(by_id) == {"meta-label-vs-direction", "xgb-vs-lgbm"}

    labeling = by_id["meta-label-vs-direction"]
    assert labeling.axis == "labeling"
    assert labeling.verdict in {"positive", "negative", "inconclusive"}
    assert labeling.metrics["v1_folds"] >= 0
    assert labeling.metrics["v2_folds"] >= 0
    assert "precision_delta" in labeling.metrics

    model = by_id["xgb-vs-lgbm"]
    assert model.axis == "model"
    assert model.verdict in {"positive", "negative", "inconclusive"}
    assert "accuracy_delta" in model.metrics
    assert "feature_importance_agreement" in model.metrics

    # P1: the per-ticker harness must stamp the ticker into the card scope.
    for card in cards:
        assert card.scope["tickers"] == ["AAPL"]


def test_comparison_cards_round_trip_via_load(tmp_path, features_frame: pl.DataFrame) -> None:
    """Written cards persist under base and reload via load_finding_cards."""
    pytest.importorskip("lightgbm")

    cards = run_comparison(features=features_frame, base=tmp_path)

    assert (tmp_path / "meta-label-vs-direction.json").exists()
    assert (tmp_path / "xgb-vs-lgbm.json").exists()

    loaded = {card.id: card for card in load_finding_cards(base=tmp_path)}
    assert set(loaded) == {card.id for card in cards}
    for card in cards:
        assert loaded[card.id].axis == card.axis
        assert loaded[card.id].verdict == card.verdict
        assert loaded[card.id].metrics == card.metrics
