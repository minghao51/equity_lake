"""Tests for enriched sentiment feature engineering."""

from datetime import date
from unittest.mock import Mock, patch

import polars as pl

from equity_lake.features.enriched_sentiment import (
    ENRICHED_FEATURE_COLUMNS,
    _add_empty_enriched_columns,
    merge_enriched_sentiment_features,
)


class TestEnrichedFeatureColumns:
    def test_all_columns_defined(self):
        assert "enriched_article_count" in ENRICHED_FEATURE_COLUMNS
        assert "enriched_sentiment_mean" in ENRICHED_FEATURE_COLUMNS
        assert "enriched_sentiment_ewma_3d" in ENRICHED_FEATURE_COLUMNS
        assert "enriched_sentiment_ewma_7d" in ENRICHED_FEATURE_COLUMNS
        assert "bullish_ratio" in ENRICHED_FEATURE_COLUMNS
        assert "breaking_news_flag" in ENRICHED_FEATURE_COLUMNS
        assert len(ENRICHED_FEATURE_COLUMNS) == 10


class TestAddEmptyEnrichedColumns:
    def test_adds_all_columns(self):
        df = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2026, 6, 14)],
                "close": [150.0],
            }
        )
        result = _add_empty_enriched_columns(df)
        for col in ENRICHED_FEATURE_COLUMNS:
            assert col in result.columns
        assert result.height == 1
        assert result["enriched_article_count"][0] == 0
        assert result["enriched_sentiment_mean"][0] == 0.0


class TestMergeEnrichedSentimentFeatures:
    def test_empty_features_returns_unchanged(self):
        df = pl.DataFrame()
        mock_conn = Mock()
        result = merge_enriched_sentiment_features(mock_conn, df, date(2026, 6, 1), date(2026, 6, 14))
        assert result.is_empty()

    def test_no_silver_dir_returns_empty_columns(self):
        df = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2026, 6, 14)],
                "close": [150.0],
            }
        )
        mock_conn = Mock()
        with patch("equity_lake.features.enriched_sentiment.SILVER_PROCESSED_ARTICLES_DIR") as mock_path:
            mock_path.exists.return_value = False
            result = merge_enriched_sentiment_features(mock_conn, df, date(2026, 6, 1), date(2026, 6, 14))
        assert "enriched_article_count" in result.columns
        assert result["enriched_article_count"][0] == 0

    def test_successful_merge(self):
        features_df = pl.DataFrame(
            {
                "ticker": ["AAPL", "AAPL", "MSFT"],
                "date": [date(2026, 6, 13), date(2026, 6, 14), date(2026, 6, 14)],
                "close": [149.0, 150.0, 380.0],
            }
        )

        sentiment_df = pl.DataFrame(
            {
                "ticker": ["AAPL", "AAPL", "MSFT"],
                "date": [date(2026, 6, 13), date(2026, 6, 14), date(2026, 6, 14)],
                "enriched_article_count": [3, 5, 2],
                "enriched_sentiment_mean": [0.5, 0.7, -0.3],
                "enriched_confidence_mean": [0.8, 0.9, 0.6],
                "enriched_relevance_mean": [0.6, 0.8, 0.4],
                "bullish_ratio": [0.67, 0.8, 0.0],
                "social_volume": [1, 2, 0],
                "social_sentiment_mean": [0.4, 0.6, None],
                "breaking_news_flag": [0, 1, 0],
            }
        )

        mock_conn = Mock()
        mock_conn.execute.return_value.pl.return_value = sentiment_df

        with patch("equity_lake.features.enriched_sentiment.SILVER_PROCESSED_ARTICLES_DIR") as mock_path:
            mock_path.exists.return_value = True
            with patch("equity_lake.features.enriched_sentiment.duckdb_scan_for", return_value="delta_scan('fake')"):
                result = merge_enriched_sentiment_features(mock_conn, features_df, date(2026, 6, 13), date(2026, 6, 14))

        assert result.height == 3
        assert "enriched_sentiment_ewma_3d" in result.columns
        assert "enriched_sentiment_ewma_7d" in result.columns
        aapl_rows = result.filter(pl.col("ticker") == "AAPL").sort("date")
        assert aapl_rows["enriched_article_count"].to_list() == [3, 5]
