"""Unit tests for daily EOD data ingestion orchestration."""

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from equity_lake.ingestion import (
    CNAshareFetcher,
    HKSGEquityFetcher,
    USEquityFetcher,
    fetch_market_data,
    run_daily_ingestion,
    validate_schema,
    write_to_partitioned_parquet,
)

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

        assert not df.empty
        assert "ticker" in df.columns
        assert "AAPL" in df["ticker"].values


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
        assert isinstance(df, pd.DataFrame)


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
        """Test writing DataFrame to partitioned Parquet."""
        # Create market directory
        market_dir = tmp_path / "us_equity"
        market_dir.mkdir(parents=True, exist_ok=True)

        # Mock the directory constants
        with patch("equity_lake.ingestion.writers.US_EQUITY_DIR", market_dir):
            success = write_to_partitioned_parquet(
                sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=False
            )

        assert success is True

        # Verify file was created
        partition_dir = market_dir / "date=2024-01-01"
        assert partition_dir.exists()

        parquet_file = partition_dir / "2024-01-01.parquet"
        assert parquet_file.exists()

    def test_write_empty_dataframe(self, tmp_path):
        """Test writing empty DataFrame."""
        market_dir = tmp_path / "us_equity"
        market_dir.mkdir(parents=True, exist_ok=True)

        with patch("equity_lake.ingestion.writers.US_EQUITY_DIR", market_dir):
            success = write_to_partitioned_parquet(
                pd.DataFrame(), "us_equity", date(2024, 1, 1), dry_run=False
            )

        assert success is False

    def test_write_dry_run(self, tmp_path, sample_ohlcv_data):
        """Test dry run mode."""
        market_dir = tmp_path / "us_equity"
        market_dir.mkdir(parents=True, exist_ok=True)

        with patch("equity_lake.ingestion.writers.US_EQUITY_DIR", market_dir):
            success = write_to_partitioned_parquet(
                sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=True
            )

        assert success is True

        # Verify no file was created
        partition_dir = market_dir / "date=2024-01-01"
        assert not partition_dir.exists()


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
        # Should still be valid (warning only)
        assert is_valid is True


# =============================================================================
# Test Pipeline Functions
# =============================================================================


class TestPipelineIntegration:
    """Integration tests for the ingestion pipeline."""

    @pytest.mark.unit
    def test_fetch_market_data_invalid_market(self):
        """Test fetching with invalid market identifier."""
        result = fetch_market_data("invalid_market", date(2024, 1, 1), {})
        assert result is None

    @pytest.mark.unit
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

        with patch(
            "equity_lake.ingestion.orchestrator.CNHybridFetcher"
        ) as mock_fetcher:
            mock_fetcher.return_value.fetch.return_value = sample

            result = fetch_market_data("cn", date(2024, 1, 1), {})

        mock_fetcher.assert_called_once()
        assert result is not None
        assert not result.empty

    @pytest.mark.unit
    def test_run_daily_ingestion_dry_run(self, tmp_path, sample_ohlcv_data):
        """Test daily ingestion in dry-run mode."""
        # Mock fetchers
        with patch(
            "equity_lake.ingestion.orchestrator.fetch_market_data"
        ) as mock_fetch:
            mock_fetch.return_value = sample_ohlcv_data

            # Mock directory constants
            with (
                patch(
                    "equity_lake.ingestion.writers.US_EQUITY_DIR",
                    tmp_path / "us_equity",
                ),
                patch(
                    "equity_lake.ingestion.writers.CN_ASHARE_DIR",
                    tmp_path / "cn_ashare",
                ),
                patch(
                    "equity_lake.ingestion.writers.HK_SG_EQUITY_DIR",
                    tmp_path / "hk_sg_equity",
                ),
            ):
                results = run_daily_ingestion(date(2024, 1, 1), ["us"], dry_run=True)

        assert "us" in results
        # Dry run should succeed
        assert results["us"] is True

    def test_write_partition_structure(self, tmp_path, sample_ohlcv_data):
        """Test that partition structure is correct."""
        market_dir = tmp_path / "us_equity"

        with patch("equity_lake.ingestion.writers.US_EQUITY_DIR", market_dir):
            write_to_partitioned_parquet(
                sample_ohlcv_data, "us_equity", date(2024, 1, 1), dry_run=False
            )

        # Verify Hive partition format
        partition_dir = market_dir / "date=2024-01-01"
        assert partition_dir.exists()
        assert partition_dir.is_dir()

        # Verify Parquet file exists
        parquet_files = list(partition_dir.glob("*.parquet"))
        assert len(parquet_files) == 1


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
        assert isinstance(df, pd.DataFrame)

    def test_future_date(self):
        """Test handling of future dates."""
        fetcher = USEquityFetcher(tickers=["AAPL"])
        future_date = date(2030, 1, 1)

        # Should not crash, will return empty DataFrame
        df = fetcher.fetch(future_date)
        assert isinstance(df, pd.DataFrame)


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
        assert isinstance(df1, pd.DataFrame)
        assert isinstance(df2, pd.DataFrame)
