"""Tests for analyst rating feature engineering."""

from datetime import date
from unittest.mock import Mock, patch

import polars as pl
import pytest

from equity_lake.features.analyst_features import (
    ANALYST_FEATURE_COLUMNS,
    _add_empty_analyst_columns,
    merge_analyst_rating_features,
)


@pytest.fixture
def base_features_df():
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT", "MSFT"],
            "date": [date(2024, 1, 15), date(2024, 1, 16), date(2024, 1, 15), date(2024, 1, 16)],
            "close": [185.0, 186.0, 390.0, 392.0],
        }
    )


class TestMergeAnalystRatingFeatures:
    def test_empty_features_returns_unchanged(self):
        df = pl.DataFrame()
        result = merge_analyst_rating_features(Mock(), df, date(2024, 1, 15), date(2024, 1, 16))
        assert result.is_empty()

    def test_adds_empty_columns_when_no_dir(self, base_features_df):
        with patch("equity_lake.features.analyst_features.ANALYST_RATINGS_DIR") as mock_path:
            mock_path.exists.return_value = False
            result = merge_analyst_rating_features(Mock(), base_features_df, date(2024, 1, 15), date(2024, 1, 16))
        for col in ANALYST_FEATURE_COLUMNS:
            assert col in result.columns

    def test_adds_empty_columns_helper(self):
        df = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 15)], "close": [185.0]})
        result = _add_empty_analyst_columns(df)
        for col in ANALYST_FEATURE_COLUMNS:
            assert col in result.columns
        assert result.height == 1

    def test_merges_rating_data(self, base_features_df):
        ratings_df = pl.DataFrame(
            {
                "ticker": ["AAPL", "MSFT"],
                "date": [date(2024, 1, 15), date(2024, 1, 15)],
                "analyst_consensus_score": [1.2, 0.8],
                "analyst_coverage_count": [30, 25],
                "analyst_price_target_mean": [195.5, 420.0],
            }
        )
        mock_conn = Mock()
        mock_conn.execute.return_value.pl.return_value = ratings_df

        with patch("equity_lake.features.analyst_features.ANALYST_RATINGS_DIR") as mock_path:
            mock_path.exists.return_value = True
            with patch("equity_lake.features.analyst_features.duckdb_scan_for", return_value="delta_scan('fake')"):
                result = merge_analyst_rating_features(mock_conn, base_features_df, date(2024, 1, 15), date(2024, 1, 16))

        aapl_row = result.filter(pl.col("ticker") == "AAPL", pl.col("date") == date(2024, 1, 15)).row(0, named=True)
        assert aapl_row["analyst_consensus_score"] == 1.2
        assert aapl_row["analyst_coverage_count"] == 30
        assert aapl_row["analyst_price_target_mean"] == 195.5

    def test_price_target_upside_calculation(self, base_features_df):
        ratings_df = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 15)],
                "analyst_consensus_score": [1.0],
                "analyst_coverage_count": [20],
                "analyst_price_target_mean": [200.0],
            }
        )
        mock_conn = Mock()
        mock_conn.execute.return_value.pl.return_value = ratings_df

        with patch("equity_lake.features.analyst_features.ANALYST_RATINGS_DIR") as mock_path:
            mock_path.exists.return_value = True
            with patch("equity_lake.features.analyst_features.duckdb_scan_for", return_value="delta_scan('fake')"):
                result = merge_analyst_rating_features(mock_conn, base_features_df, date(2024, 1, 15), date(2024, 1, 16))

        aapl_row = result.filter(pl.col("ticker") == "AAPL", pl.col("date") == date(2024, 1, 15)).row(0, named=True)
        close = 185.0
        expected_upside = (200.0 - close) / close
        assert abs(aapl_row["analyst_price_target_upside"] - expected_upside) < 1e-6
