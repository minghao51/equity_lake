#!/usr/bin/env python3
"""
Tests for macro indicators fetcher.
"""

from datetime import date, timedelta
from unittest.mock import Mock, patch

import pandas as pd
import pytest


class TestYFinanceFetcher:
    """Tests for YFinanceFetcher class."""

    def test_fetch_dxy_index(self):
        """Test fetching USD Index from yfinance."""
        from equity_lake.fetch_macro import YFinanceFetcher

        fetcher = YFinanceFetcher(ticker="^DXY", indicator_name="dxy")

        with patch("equity_lake.fetch_macro.yf.download") as mock_download:
            mock_df = pd.DataFrame(
                {
                    "Open": [102.0],
                    "High": [103.0],
                    "Low": [101.5],
                    "Close": [102.5],
                    "Adj Close": [102.5],
                    "Volume": [0],
                },
                index=pd.to_datetime(["2024-12-01"]),
            )

            mock_download.return_value = mock_df

            result = fetcher.fetch(date(2024, 12, 1))

            assert result is not None
            assert not result.empty
            assert "dxy" in result["indicator"].values
            assert "yfinance" in result["source"].values

    def test_fetch_gld_etf(self):
        """Test fetching GLD ETF price from yfinance."""
        from equity_lake.fetch_macro import YFinanceFetcher

        fetcher = YFinanceFetcher(ticker="GLD", indicator_name="gld")

        with patch("equity_lake.fetch_macro.yf.download") as mock_download:
            mock_df = pd.DataFrame(
                {
                    "Close": [185.50],
                },
                index=pd.to_datetime(["2024-12-01"]),
            )

            mock_download.return_value = mock_df

            result = fetcher.fetch(date(2024, 12, 1))

            assert result is not None
            assert not result.empty
            assert result["indicator"].values[0] == "gld"
            assert abs(result["value"].values[0] - 185.50) < 0.01

    def test_fetch_no_data(self):
        """Test handling empty data response."""
        from equity_lake.fetch_macro import YFinanceFetcher

        fetcher = YFinanceFetcher(ticker="INVALID", indicator_name="test")

        with patch("equity_lake.fetch_macro.yf.download") as mock_download:
            mock_download.return_value = pd.DataFrame()

            result = fetcher.fetch(date(2024, 12, 1))

            assert result is None or result.empty


class TestFredFetcher:
    """Tests for FredFetcher class."""

    def test_fetch_tips_yield(self):
        """Test fetching TIPS yield from FRED."""
        from equity_lake.fetch_macro import FredFetcher

        with patch("equity_lake.fetch_macro.Fred") as MockFred:
            mock_fred_instance = Mock()
            mock_series = pd.Series([2.15], index=pd.to_datetime(["2024-12-01"]))
            mock_fred_instance.get_series.return_value = mock_series
            MockFred.return_value = mock_fred_instance

            fetcher = FredFetcher(
                series_id="DFII10", indicator_name="tips_yield", fred_api_key="test_key"
            )

            result = fetcher.fetch(date(2024, 12, 1))

            assert result is not None
            assert not result.empty
            assert "tips_yield" in result["indicator"].values
            assert abs(result["value"].values[0] - 2.15) < 0.01

    def test_fetch_geopolitical_risk(self):
        """Test fetching GEPUI from FRED."""
        from equity_lake.fetch_macro import FredFetcher

        with patch("equity_lake.fetch_macro.Fred") as MockFred:
            mock_fred_instance = Mock()
            mock_series = pd.Series([125.0], index=pd.to_datetime(["2024-12-01"]))
            mock_fred_instance.get_series.return_value = mock_series
            MockFred.return_value = mock_fred_instance

            fetcher = FredFetcher(
                series_id="GEPUI",
                indicator_name="geopolitical_risk",
                fred_api_key="test_key",
            )

            result = fetcher.fetch(date(2024, 12, 1))

            assert result is not None
            assert not result.empty
            assert "geopolitical_risk" in result["indicator"].values


class TestMacroDataPipeline:
    """Tests for MacroDataPipeline class."""

    def test_pipeline_initialization(self):
        """Test pipeline initializes with correct fetchers."""
        from equity_lake.fetch_macro import MacroDataPipeline

        with patch.dict("os.environ", {"FRED_API_KEY": "test_key"}):
            pipeline = MacroDataPipeline()

            assert len(pipeline.indicators) > 0
            indicator_names = [type(i).__name__ for i in pipeline.indicators]
            assert "YFinanceFetcher" in indicator_names
            assert "FredFetcher" in indicator_names

    def test_pipeline_fetch_all(self):
        """Test fetching all indicators."""
        from equity_lake.fetch_macro import MacroDataPipeline

        with patch.dict("os.environ", {"FRED_API_KEY": "test_key"}):
            pipeline = MacroDataPipeline()

            with patch.object(pipeline.indicators[0], "fetch") as mock_fetch:
                mock_df = pd.DataFrame(
                    {
                        "date": [date(2024, 12, 1)],
                        "indicator": ["test"],
                        "value": [100.0],
                        "source": ["test"],
                        "updated_at": ["2024-12-01 10:00:00"],
                    }
                )
                mock_fetch.return_value = mock_df

                result = pipeline.fetch_all(date(2024, 12, 1))

                assert result is not None


class TestSchemaValidation:
    """Tests for schema validation."""

    def test_validate_macro_schema_valid(self):
        """Test schema validation with valid DataFrame."""
        from equity_lake.fetch_macro import validate_macro_schema

        df = pd.DataFrame(
            {
                "date": [date(2024, 12, 1)],
                "indicator": ["dxy"],
                "value": [102.5],
                "source": ["yfinance"],
                "updated_at": ["2024-12-01 10:00:00"],
            }
        )

        assert validate_macro_schema(df) is True

    def test_validate_macro_schema_missing_columns(self):
        """Test schema validation with missing columns."""
        from equity_lake.fetch_macro import validate_macro_schema

        df = pd.DataFrame(
            {
                "date": [date(2024, 12, 1)],
                "indicator": ["dxy"],
                # Missing 'value', 'source', 'updated_at'
            }
        )

        assert validate_macro_schema(df) is False

    def test_validate_macro_schema_empty(self):
        """Test schema validation with empty DataFrame."""
        from equity_lake.fetch_macro import validate_macro_schema

        df = pd.DataFrame(
            columns=["date", "indicator", "value", "source", "updated_at"]
        )

        assert validate_macro_schema(df) is True


class TestParquetWrite:
    """Tests for parquet writing functionality."""

    def test_write_macro_to_parquet(self, tmp_path):
        """Test writing macro data to parquet."""
        from equity_lake.fetch_macro import write_macro_to_parquet

        df = pd.DataFrame(
            {
                "date": [date(2024, 12, 1)],
                "indicator": ["dxy"],
                "value": [102.5],
                "source": ["yfinance"],
                "updated_at": ["2024-12-01 10:00:00"],
            }
        )

        with patch("equity_lake.fetch_macro.MACRO_INDICATORS_DIR", tmp_path):
            result = write_macro_to_parquet(df, date(2024, 12, 1), dry_run=False)

            assert result is True
            assert (tmp_path / "date=2024-12-01").exists()


@pytest.mark.integration
class TestIntegration:
    """Integration tests (require network access)."""

    def test_fetch_dxy_integration(self):
        """Integration test for DXY fetching."""
        from equity_lake.fetch_macro import YFinanceFetcher

        fetcher = YFinanceFetcher(ticker="^DXY", indicator_name="dxy")
        result = fetcher.fetch(date.today() - timedelta(days=1))

        if result is not None and not result.empty:
            assert "dxy" in result["indicator"].values
            assert 90.0 < result["value"].values[0] < 120.0

    def test_fetch_gld_integration(self):
        """Integration test for GLD fetching."""
        from equity_lake.fetch_macro import YFinanceFetcher

        fetcher = YFinanceFetcher(ticker="GLD", indicator_name="gld")
        result = fetcher.fetch(date.today() - timedelta(days=1))

        if result is not None and not result.empty:
            assert "gld" in result["indicator"].values
            assert (
                100.0 < result["value"].values[0] < 500.0
            )  # GLD has increased in price


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
