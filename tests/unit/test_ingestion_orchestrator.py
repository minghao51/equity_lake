"""Unit tests for daily EOD data ingestion orchestration."""

from datetime import date
from unittest.mock import patch

import pandas as pd
import polars as pl
import pytest

from equity_lake.ingestion.orchestrator import fetch_market_data, run_daily_ingestion
from equity_lake.ingestion.writers import validate_schema, write_to_partitioned_parquet
from equity_lake.sources import CNAshareFetcher, HKSGEquityFetcher, USEquityFetcher

# =============================================================================
# Test Market Data Fetchers
# =============================================================================


class TestUSEquityFetcher:
    """Tests for US equity data fetcher."""

    def test_initialization(self):
        """Test fetcher initialization."""
        fetcher = USEquityFetcher()
        assert fetcher.tickers is not None
        assert len(fetcher.tickers) > 0
        assert "AAPL" in fetcher.tickers

    def test_initialization_custom_tickers(self):
        """Test fetcher with custom tickers."""
        custom_tickers = ["AAPL", "GOOGL"]
        fetcher = USEquityFetcher(tickers=custom_tickers)
        assert fetcher.tickers == custom_tickers

    @pytest.mark.slow
    @pytest.mark.integration
    def test_fetch_real_data(self):
        """Test fetching real data from yfinance (integration test)."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        trading_date = date(2024, 1, 2)  # Known trading day

        df = fetcher.fetch(trading_date)

        assert not df.is_empty()
        assert "ticker" in df.columns
        assert "AAPL" in df["ticker"].to_list()


class TestCNAshareFetcher:
    """Tests for China A-share data fetcher."""

    def test_initialization(self):
        """Test fetcher initialization."""
        fetcher = CNAshareFetcher()
        assert fetcher is not None

    @pytest.mark.slow
    @pytest.mark.integration
    def test_fetch_real_data(self):
        """Test fetching real data from akshare (integration test)."""
        fetcher = CNAshareFetcher()
        trading_date = date(2024, 1, 2)  # Known trading day

        df = fetcher.fetch(trading_date)

        # Note: akshare may return empty data for dates without trading
        # We're mainly testing it doesn't crash
        assert isinstance(df, pl.DataFrame)


class TestHKSGEquityFetcher:
    """Tests for HK/SG equity data fetcher."""

    def test_initialization(self):
        """Test fetcher initialization."""
        fetcher = HKSGEquityFetcher()
        assert fetcher.hk_tickers is not None
        assert fetcher.sg_tickers is not None
        assert "0700.HK" in fetcher.hk_tickers


# =============================================================================
# Test Data Writers
# =============================================================================


class TestPartitionedParquetWriter:
    """Tests for Parquet writer."""

    def test_write_to_partitioned_parquet(self, tmp_path, sample_ohlcv_data):
        """Test writing DataFrame to Delta storage."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            success = write_to_partitioned_parquet(sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=False)

        assert success is True

        from deltalake import DeltaTable

        market_dir = tmp_path / "us_equity"
        dt = DeltaTable(str(market_dir))
        assert dt.version() >= 0

    def test_write_empty_dataframe(self, tmp_path):
        """Test writing empty DataFrame."""
        success = write_to_partitioned_parquet(pd.DataFrame(), "us_equity", date(2024, 1, 1), dry_run=False)

        assert success is False

    def test_write_dry_run(self, tmp_path, sample_ohlcv_data):
        """Test dry run mode."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            success = write_to_partitioned_parquet(sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=True)

        assert success is True


# =============================================================================
# Test Schema Validation
# =============================================================================


class TestSchemaValidation:
    """Tests for schema validation."""

    def test_valid_schema(self, sample_ohlcv_data):
        """Test validation of valid schema."""
        is_valid = validate_schema(sample_ohlcv_data, "test")
        assert is_valid is True

    def test_missing_required_columns(self):
        """Test validation with missing columns."""
        invalid_data = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 1)],
                # Missing: open, high, low, close, volume
            }
        )

        is_valid = validate_schema(invalid_data, "test")
        assert is_valid is False

    def test_all_null_column(self):
        """Test validation with all-null column."""
        data_with_nulls = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "open": [150.0],
                "high": [155.0],
                "low": [148.0],
                "close": [152.0],
                "volume": [None],  # All null
            }
        )

        is_valid = validate_schema(data_with_nulls, "test")
        # All-null required column should now fail validation (not just warn)
        assert is_valid is False


# =============================================================================
# Test Pipeline Functions
# =============================================================================


class TestPipelineIntegration:
    """Integration tests for the ingestion pipeline."""

    def test_fetch_market_data_invalid_market(self):
        """Test fetching with invalid market identifier."""
        result = fetch_market_data("invalid_market", date(2024, 1, 1), {})
        assert result is None

    def test_fetch_market_data_cn_uses_hybrid_fetcher(self):
        """CN ingestion should use the hybrid fetcher with built-in fallback."""
        sample = pd.DataFrame(
            {
                "ticker": ["000001"],
                "date": [date(2024, 1, 1)],
                "open": [10.0],
                "high": [11.0],
                "low": [9.5],
                "close": [10.5],
                "volume": [1000],
                "adj_close": [10.5],
            }
        )

        with patch("equity_lake.sources.cn_hybrid.CNHybridFetcher") as mock_fetcher:
            mock_fetcher.return_value.fetch.return_value = sample

            result = fetch_market_data("cn", date(2024, 1, 1), {})

        mock_fetcher.assert_called_once()
        assert result is not None
        assert not result.is_empty()

    def test_run_daily_ingestion_dry_run(self, tmp_path, sample_ohlcv_data):
        """Test daily ingestion in dry-run mode."""
        with patch("equity_lake.ingestion.orchestrator.fetch_market_data") as mock_fetch:
            mock_fetch.return_value = sample_ohlcv_data

            with (
                patch("equity_lake.storage.delta.LAKE_DIR", tmp_path),
                patch("equity_lake.ingestion.orchestrator.LAKE_DIR", tmp_path),
            ):
                results = run_daily_ingestion(date(2024, 1, 1), ["us"], dry_run=True)

        assert "us" in results
        assert results["us"] is True

    def test_write_partition_structure(self, tmp_path, sample_ohlcv_data):
        """Test that Delta table is created on write."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            write_to_partitioned_parquet(sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=False)

        from deltalake import DeltaTable

        market_dir = tmp_path / "us_equity"
        dt = DeltaTable(str(market_dir))
        assert dt.version() >= 0
        pdf = dt.to_pandas()
        assert len(pdf) > 0


# =============================================================================
# Test Date Handling
# =============================================================================


class TestDateHandling:
    """Tests for date handling and edge cases."""

    def test_weekend_date(self):
        """Test handling of weekend dates."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        weekend_date = date(2024, 1, 6)  # Saturday

        # Should not crash, may return empty DataFrame
        df = fetcher.fetch(weekend_date)
        assert isinstance(df, pl.DataFrame)

    def test_future_date(self):
        """Test handling of future dates."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        future_date = date(2030, 1, 1)

        # Should not crash, will return empty DataFrame
        df = fetcher.fetch(future_date)
        assert isinstance(df, pl.DataFrame)


# =============================================================================
# Performance Tests
# =============================================================================


class TestPerformance:
    """Performance tests for data ingestion."""

    @pytest.mark.slow
    def test_large_ticker_list(self):
        """Test fetching with large ticker list."""
        large_ticker_list = [f"TICKER{i}" for i in range(100)]
        fetcher = USEquityFetcher(tickers=large_ticker_list)

        # Should initialize without issues
        assert len(fetcher.tickers) == 100

    def test_concurrent_fetches(self):
        """Test that multiple fetches don't interfere."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        trading_date = date(2024, 1, 2)

        # Fetch multiple times
        df1 = fetcher.fetch(trading_date)
        df2 = fetcher.fetch(trading_date)

        # Both should return DataFrames
        assert isinstance(df1, pl.DataFrame)
        assert isinstance(df2, pl.DataFrame)
