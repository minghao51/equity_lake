"""Tests for sentiment-aware feature engineering helpers."""

from __future__ import annotations

import pandas as pd

from equity_lake.features.engineering import FeatureEngineer


def test_add_cross_modal_sentiment_features_adds_expected_columns() -> None:
    engineer = FeatureEngineer.__new__(FeatureEngineer)
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL"] * 6,
            "date": pd.date_range("2024-01-01", periods=6, freq="B"),
            "volume": [100, 120, 140, 160, 180, 200],
            "avg_daily_sentiment": [0.1, 0.0, -0.2, 0.3, 0.4, 0.5],
            "social_sentiment_score": [0.0, -0.1, -0.1, 0.2, 0.3, 0.6],
            "news_count": [1, 0, 2, 1, 3, 4],
            "social_mention_count": [0, 3, 2, 4, 5, 6],
        }
    )

    enriched = engineer.add_cross_modal_sentiment_features(frame)

    expected = {
        "news_social_sentiment_gap",
        "sentiment_x_log_volume",
        "social_sentiment_x_log_volume",
        "news_sentiment_momentum_5d",
        "social_sentiment_momentum_5d",
        "news_social_mentions_gap",
    }
    assert expected.issubset(enriched.columns)
    assert enriched[list(expected)].isna().sum().sum() == 0
