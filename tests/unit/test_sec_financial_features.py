"""Tests for SEC financial feature merge."""

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from equity_lake.features.sec_financial_features import (
    SEC_FINANCIAL_FEATURE_COLUMNS,
    _add_empty_sec_financial_columns,
    merge_sec_financial_features,
)


@pytest.fixture
def price_df():
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT", "MSFT"],
            "date": [date(2024, 1, 10), date(2024, 1, 20), date(2024, 1, 10), date(2024, 1, 20)],
            "close": [185.0, 190.0, 380.0, 385.0],
        }
    )


@pytest.fixture
def sec_financials_df():
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": [date(2024, 1, 15), date(2024, 1, 15)],
            "revenue": [383285.0, 245122.0],
            "roe": [1.56, 0.45],
            "roa": [0.27, 0.22],
            "debt_to_equity": [1.78, 0.35],
            "net_margin": [0.25, 0.37],
            "operating_margin": [0.30, 0.45],
            "eps": [6.24, 10.50],
        }
    )


class TestMergeSecFinancialFeatures:
    def test_merges_financial_data(self, price_df, sec_financials_df):
        mock_conn = MagicMock()

        with (
            patch("equity_lake.features.sec_financial_features.SEC_FINANCIALS_DIR") as mock_path,
            patch("equity_lake.features.sec_financial_features.duckdb_scan_for", return_value="mock_scan"),
        ):
            mock_path.exists.return_value = True
            mock_conn.execute.return_value.pl.return_value = sec_financials_df

            result = merge_sec_financial_features(mock_conn, price_df, date(2024, 1, 1), date(2024, 1, 31))

        for col in SEC_FINANCIAL_FEATURE_COLUMNS:
            assert col in result.columns

        aapl_rows = result.filter(pl.col("ticker") == "AAPL").sort("date")
        jan10 = aapl_rows.row(0, named=True)
        jan20 = aapl_rows.row(1, named=True)

        assert jan10["sec_revenue"] == 0.0  # before filing date
        assert jan20["sec_revenue"] == 383285.0  # after filing date
        assert jan20["sec_roe"] == pytest.approx(1.56)

    def test_no_data_adds_empty_columns(self, price_df):
        mock_conn = MagicMock()

        with (
            patch("equity_lake.features.sec_financial_features.SEC_FINANCIALS_DIR") as mock_path,
            patch("equity_lake.features.sec_financial_features.duckdb_scan_for", return_value="mock_scan"),
        ):
            mock_path.exists.return_value = True
            mock_conn.execute.return_value.pl.return_value = pl.DataFrame()

            result = merge_sec_financial_features(mock_conn, price_df, date(2024, 1, 1), date(2024, 1, 31))

        for col in SEC_FINANCIAL_FEATURE_COLUMNS:
            assert col in result.columns
            assert result[col].sum() == 0.0

    def test_directory_not_found_adds_empty(self, price_df):
        mock_conn = MagicMock()

        with patch("equity_lake.features.sec_financial_features.SEC_FINANCIALS_DIR") as mock_path:
            mock_path.exists.return_value = False

            result = merge_sec_financial_features(mock_conn, price_df, date(2024, 1, 1), date(2024, 1, 31))

        assert "sec_revenue" in result.columns
        assert "sec_roe" in result.columns

    def test_no_look_ahead_bias(self, price_df, sec_financials_df):
        """Filing on Jan 15 must NOT influence Jan 10 prices."""
        mock_conn = MagicMock()

        with (
            patch("equity_lake.features.sec_financial_features.SEC_FINANCIALS_DIR") as mock_path,
            patch("equity_lake.features.sec_financial_features.duckdb_scan_for", return_value="mock_scan"),
        ):
            mock_path.exists.return_value = True
            mock_conn.execute.return_value.pl.return_value = sec_financials_df

            result = merge_sec_financial_features(mock_conn, price_df, date(2024, 1, 1), date(2024, 1, 31))

        jan10 = result.filter((pl.col("ticker") == "AAPL") & (pl.col("date") == date(2024, 1, 10)))
        assert jan10.row(0, named=True)["sec_revenue"] == 0.0


class TestAddEmptyColumns:
    def test_adds_all_columns(self):
        df = pl.DataFrame({"ticker": ["AAPL"], "date": [date(2024, 1, 1)]})
        result = _add_empty_sec_financial_columns(df)

        for col in SEC_FINANCIAL_FEATURE_COLUMNS:
            assert col in result.columns
            assert result[col][0] == 0.0
