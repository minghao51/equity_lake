"""Unit tests for ``ml/ablation.run_ablation`` (pure, synthetic data)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from equity_lake.findings.writer import load_finding_cards
from equity_lake.ml.ablation import run_ablation


def _make_features_frame(ticker: str = "AAPL", periods: int = 300, *, enriched: bool = False) -> pl.DataFrame:
    """Synthetic per-ticker frame; ``enriched`` adds macro/sentiment proxy columns."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(periods)]
    close = [100.0 + i * 0.5 for i in range(periods)]
    frame = pl.DataFrame(
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
    if enriched:
        # Columns that only exist in the enriched arm (include_macro=True).
        frame = frame.with_columns(
            pl.Series("gdp_growth", [0.02 + (i % 3) * 0.001 for i in range(periods)]),
            pl.Series("sentiment_score", [0.1 * ((i % 7) - 3) for i in range(periods)]),
        )
    return frame


def test_run_ablation_returns_enrichment_ablation_card(tmp_path) -> None:
    """run_ablation emits the single enrichment-ablation FindingCard."""
    enriched = _make_features_frame(enriched=True)
    technical = _make_features_frame(enriched=False)

    card = run_ablation(
        enriched_features=enriched,
        technical_features=technical,
        base=tmp_path,
    )

    assert card.id == "enrichment-ablation"
    assert card.axis == "ablation"
    assert card.verdict in {"positive", "negative", "inconclusive"}
    assert "accuracy_delta" in card.metrics
    assert "enriched_feature_count" in card.metrics
    assert "technical_feature_count" in card.metrics


def test_ablation_card_round_trips_via_load(tmp_path) -> None:
    """Written card persists under base and reloads via load_finding_cards."""
    enriched = _make_features_frame(enriched=True)
    technical = _make_features_frame(enriched=False)

    card = run_ablation(
        enriched_features=enriched,
        technical_features=technical,
        base=tmp_path,
    )

    assert (tmp_path / "enrichment-ablation.json").exists()

    loaded = {c.id: c for c in load_finding_cards(base=tmp_path)}
    assert "enrichment-ablation" in loaded
    assert loaded["enrichment-ablation"].axis == card.axis
    assert loaded["enrichment-ablation"].metrics == card.metrics
