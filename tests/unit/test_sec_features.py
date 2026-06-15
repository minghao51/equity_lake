"""Tests for SEC filing feature engineering."""

from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.features.sec_features import SEC_FEATURE_COLUMNS, merge_sec_features


@pytest.fixture
def price_df():
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT", "MSFT"],
            "date": [date(2024, 1, 15), date(2024, 1, 16), date(2024, 1, 15), date(2024, 1, 16)],
            "close": [185.0, 186.0, 390.0, 392.0],
        }
    )


class TestMergeSECFeatures:
    def test_adds_empty_columns_when_no_dir(self, price_df):
        import duckdb

        conn = duckdb.connect(":memory:")

        with patch("equity_lake.features.sec_features.SEC_EXTRACTIONS_DIR") as mock_dir:
            mock_dir.exists.return_value = False
            result = merge_sec_features(conn, price_df, date(2024, 1, 1), date(2024, 1, 31))

        for col in SEC_FEATURE_COLUMNS:
            assert col in result.columns
        assert result.height == price_df.height

    def test_merges_sec_data(self, price_df):
        sec_df = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "filing_date": [date(2024, 1, 10)],
                "date": [date(2024, 1, 10)],
                "risk_sentiment": [-0.5],
                "management_tone": [0.3],
                "guidance_direction": ["positive"],
                "new_vs_repeated": ["new"],
            }
        )

        import duckdb

        conn = duckdb.connect(":memory:")

        with (
            patch("equity_lake.features.sec_features.SEC_EXTRACTIONS_DIR") as mock_dir,
            patch("equity_lake.features.sec_features.duckdb_scan_for", return_value="sec_scan"),
        ):
            mock_dir.exists.return_value = True
            conn.register("sec_scan", sec_df.to_arrow())

            result = merge_sec_features(conn, price_df, date(2024, 1, 1), date(2024, 1, 31))

        assert "sec_risk_sentiment" in result.columns
        assert "sec_management_tone" in result.columns
        assert "sec_guidance_positive" in result.columns
        assert "sec_risk_change_flag" in result.columns

        aapl_rows = result.filter(pl.col("ticker") == "AAPL")
        assert aapl_rows.row(0, named=True)["sec_risk_sentiment"] == -0.5
        assert aapl_rows.row(0, named=True)["sec_guidance_positive"] == 1
        assert aapl_rows.row(0, named=True)["sec_risk_change_flag"] == 1

        msft_rows = result.filter(pl.col("ticker") == "MSFT")
        assert msft_rows.row(0, named=True)["sec_risk_sentiment"] == 0.0
        assert msft_rows.row(0, named=True)["sec_guidance_positive"] == 0

    def test_handles_empty_features_df(self):
        import duckdb

        conn = duckdb.connect(":memory:")
        empty_df = pl.DataFrame()
        result = merge_sec_features(conn, empty_df, date(2024, 1, 1), date(2024, 1, 31))
        assert result.is_empty()

    def test_guidance_negative_yields_zero(self, price_df):
        sec_df = pl.DataFrame(
            {
                "ticker": ["AAPL"],
                "filing_date": [date(2024, 1, 10)],
                "date": [date(2024, 1, 10)],
                "risk_sentiment": [0.2],
                "management_tone": [-0.1],
                "guidance_direction": ["negative"],
                "new_vs_repeated": ["repeated"],
            }
        )

        import duckdb

        conn = duckdb.connect(":memory:")

        with (
            patch("equity_lake.features.sec_features.SEC_EXTRACTIONS_DIR") as mock_dir,
            patch("equity_lake.features.sec_features.duckdb_scan_for", return_value="sec_scan"),
        ):
            mock_dir.exists.return_value = True
            conn.register("sec_scan", sec_df.to_arrow())

            result = merge_sec_features(conn, price_df, date(2024, 1, 1), date(2024, 1, 31))

        aapl_row = result.filter(pl.col("ticker") == "AAPL").row(0, named=True)
        assert aapl_row["sec_guidance_positive"] == 0
        assert aapl_row["sec_risk_change_flag"] == 0

    def test_feature_columns_constant(self):
        assert SEC_FEATURE_COLUMNS == [
            "sec_risk_sentiment",
            "sec_management_tone",
            "sec_guidance_positive",
            "sec_risk_change_flag",
        ]
