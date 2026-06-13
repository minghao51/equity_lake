"""Tests for sentiment-aware feature engineering helpers."""

from __future__ import annotations

import pandas as pd
import polars as pl

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

    assert isinstance(enriched, pl.DataFrame)
    expected = {
        "news_social_sentiment_gap",
        "sentiment_x_log_volume",
        "social_sentiment_x_log_volume",
        "news_sentiment_momentum_5d",
        "social_sentiment_momentum_5d",
        "news_social_mentions_gap",
    }
    assert expected.issubset(enriched.columns)
    assert enriched.select([pl.col(column).is_null().sum().alias(column) for column in expected]).row(0) == (0, 0, 0, 0, 0, 0)


def test_zscore_cross_sectional_normalizes_per_date() -> None:
    """Z-scored features should have ~0 mean and ~1 std within each date group."""
    engineer = FeatureEngineer.__new__(FeatureEngineer)
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "GOOG", "AAPL", "MSFT", "GOOG"],
            "date": pd.to_datetime(["2024-01-01"] * 3 + ["2024-01-02"] * 3),
            "rsi_14": [10.0, 20.0, 30.0, 40.0, 50.0, 60.0],
            "macd": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6],
        }
    )

    enriched = engineer.zscore_cross_sectional(frame)
    assert isinstance(enriched, pl.DataFrame)
    assert "rsi_14_zscore" in enriched.columns
    assert "macd_zscore" in enriched.columns

    for date_value in enriched["date"].unique():
        per_date = enriched.filter(pl.col("date") == date_value)
        mean_z = per_date["rsi_14_zscore"].mean()
        std_z = per_date["rsi_14_zscore"].std()
        assert abs(mean_z) < 1e-6
        assert abs(std_z - 1.0) < 1e-3


def test_zscore_cross_sectional_skips_metadata_columns() -> None:
    """Ticker, date, OHLC, and target columns should not be z-scored."""
    engineer = FeatureEngineer.__new__(FeatureEngineer)
    frame = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "close": [100.0, 200.0],
            "rsi_14": [50.0, 60.0],
        }
    )

    enriched = engineer.zscore_cross_sectional(frame)
    assert "close_zscore" not in enriched.columns
    assert "ticker_zscore" not in enriched.columns
    assert "rsi_14_zscore" in enriched.columns
