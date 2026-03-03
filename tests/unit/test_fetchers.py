"""
Unit tests for new data fetchers with batch improvements and multi-source support.

Tests cover:
- USEquityFetcher with batch downloading
- CNEfinanceFetcher (new efinance integration)
- CNHybridFetcher (multi-source fallback system)
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from equity_lake.ingestion.sources import (
    CNEfinanceFetcher,
    CNHybridFetcher,
    USEquityFetcher,
)

# =============================================================================
# USEquityFetcher Tests (Batch Download Improvements)
# =============================================================================


class TestUSEquityFetcherBatching:
    """Test suite for USEquityFetcher with batch downloading."""

    def test_initialization_with_default_batch_size(self, sample_us_tickers):
        """Test that fetcher initializes with default batch size."""
        fetcher = USEquityFetcher(tickers=sample_us_tickers)
        assert fetcher.batch_size == 500  # Default value
        assert fetcher.tickers == sample_us_tickers

    def test_initialization_with_custom_batch_size(self, sample_us_tickers):
        """Test that fetcher accepts custom batch size."""
        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=200)
        assert fetcher.batch_size == 200

    def test_chunked_splits_tickers_correctly(self, sample_large_ticker_list):
        """Test that _chunked method splits tickers into correct batches."""
        fetcher = USEquityFetcher(tickers=sample_large_ticker_list, batch_size=500)
        chunks = fetcher._chunked(sample_large_ticker_list, 500)

        assert len(chunks) == 3  # 1200 / 500 = 2.4, so 3 batches
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 500
        assert len(chunks[2]) == 200

    def test_chunked_handles_small_lists(self, sample_us_tickers):
        """Test that _chunked handles lists smaller than batch size."""
        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=500)
        chunks = fetcher._chunked(sample_us_tickers, 500)

        assert len(chunks) == 1
        assert len(chunks[0]) == len(sample_us_tickers)

    def test_chunked_handles_empty_list(self):
        """Test that _chunked handles empty lists."""
        fetcher = USEquityFetcher(tickers=[], batch_size=500)
        chunks = fetcher._chunked([], 500)

        assert len(chunks) == 1
        assert len(chunks[0]) == 0

    @patch("equity_lake.ingestion.sources.us.yf.download")
    def test_fetch_with_batching(self, mock_download, sample_us_tickers):
        """Test that fetch processes data in batches."""
        # Mock yfinance to return data for each batch
        mock_data = pd.DataFrame(
            {
                "Open": [150.0],
                "High": [155.0],
                "Low": [148.0],
                "Close": [152.0],
                "Adj Close": [152.0],
                "Volume": [1000000],
            },
            index=pd.DatetimeIndex(["2024-01-01"]),
        )

        mock_download.return_value = mock_data

        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=3)
        result = fetcher.fetch(date(2024, 1, 1))

        # Should be called 3 times for 8 tickers with batch_size=3
        assert mock_download.call_count == 3
        assert not result.empty
        assert "ticker" in result.columns

    @patch("equity_lake.ingestion.sources.us.yf.download")
    def test_fetch_handles_partial_failures(self, mock_download, sample_us_tickers):
        """Test that fetch continues even if one batch fails."""
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                # Second batch fails
                return pd.DataFrame()
            # Other batches succeed
            return pd.DataFrame(
                {
                    "Open": [150.0],
                    "High": [155.0],
                    "Low": [148.0],
                    "Close": [152.0],
                    "Adj Close": [152.0],
                    "Volume": [1000000],
                },
                index=pd.DatetimeIndex(["2024-01-01"]),
            )

        mock_download.side_effect = side_effect

        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=2)
        result = fetcher.fetch(date(2024, 1, 1))

        # Should continue despite one batch failing
        assert mock_download.call_count == 4
        assert not result.empty

    @patch("equity_lake.ingestion.sources.us.yf.download")
    def test_fetch_standardizes_columns(self, mock_download, sample_us_tickers):
        """Test that fetch standardizes column names."""
        mock_download.return_value = pd.DataFrame(
            {
                "Open": [150.0],
                "High": [155.0],
                "Low": [148.0],
                "Close": [152.0],
                "Adj Close": [152.0],
                "Volume": [1000000],
            },
            index=pd.DatetimeIndex(["2024-01-01"]),
        )

        fetcher = USEquityFetcher(tickers=sample_us_tickers[:2], batch_size=2)
        result = fetcher.fetch(date(2024, 1, 1))

        # Check for standardized column names
        assert "open" in result.columns
        assert "high" in result.columns
        assert "low" in result.columns
        assert "close" in result.columns
        assert "adj_close" in result.columns
        assert "volume" in result.columns
        assert "ticker" in result.columns
        assert "date" in result.columns

    @patch("equity_lake.ingestion.sources.us.yf.download")
    def test_fetch_with_single_ticker(self, mock_download):
        """Test that fetch handles single ticker correctly."""
        mock_download.return_value = pd.DataFrame(
            {
                "Open": [150.0],
                "High": [155.0],
                "Low": [148.0],
                "Close": [152.0],
                "Adj Close": [152.0],
                "Volume": [1000000],
            },
            index=pd.DatetimeIndex(["2024-01-01"]),
        )

        fetcher = USEquityFetcher(tickers=["AAPL"], batch_size=500)
        result = fetcher.fetch(date(2024, 1, 1))

        assert not result.empty
        assert result["ticker"].iloc[0] == "AAPL"


# =============================================================================
# CNEfinanceFetcher Tests (New efinance Integration)
# =============================================================================


class TestCNEfinanceFetcher:
    """Test suite for CNEfinanceFetcher."""

    def test_initialization_requires_efinance(self):
        """Test that fetcher raises ImportError if efinance not available."""
        with (
            patch("equity_lake.ingestion.sources.cn_efinance.efinance", None),
            pytest.raises(ImportError, match="efinance is not installed"),
        ):
            CNEfinanceFetcher()

    def test_initialization_with_params(self):
        """Test fetcher initialization with parameters."""
        with patch("equity_lake.ingestion.sources.cn_efinance.efinance", MagicMock()):
            fetcher = CNEfinanceFetcher(
                max_workers=20,
                stock_limit=200,
                retry_attempts=5,
                retry_delay=2.0,
            )

            assert fetcher.max_workers == 20
            assert fetcher.stock_limit == 200
            assert fetcher.retry_attempts == 5
            assert fetcher.retry_delay == 2.0

    @patch("equity_lake.ingestion.sources.cn_efinance.efinance")
    def test_fetch_single_stock(self, mock_efinance, sample_cn_tickers):
        """Test fetching a single stock."""
        # Mock efinance response
        mock_efinance.stock.get_quote_history.return_value = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "收盘": [10.7],
                "最高": [10.8],
                "最低": [10.4],
                "成交量": [1000000],
                "成交额": [10700000],
            }
        )

        fetcher = CNEfinanceFetcher(max_workers=1, stock_limit=1)
        result = fetcher._fetch_single_stock("000001", date(2024, 1, 1))

        assert not result.empty
        assert "ticker" in result.columns
        assert result["ticker"].iloc[0] == "000001"

    @patch("equity_lake.ingestion.sources.cn_efinance.efinance")
    def test_fetch_single_stock_handles_failure(self, mock_efinance):
        """Test that fetch handles single stock failures gracefully."""
        mock_efinance.stock.get_quote_history.side_effect = Exception("Network error")

        fetcher = CNEfinanceFetcher(max_workers=1, stock_limit=1)
        result = fetcher._fetch_single_stock("000001", date(2024, 1, 1))

        assert result is None

    @patch("equity_lake.ingestion.sources.cn_efinance.efinance")
    def test_fetch_standardizes_columns(self, mock_efinance):
        """Test that fetch standardizes Chinese column names."""
        mock_efinance.stock.get_realtime_quotes.return_value = pd.DataFrame(
            {
                "股票代码": ["000001", "000002"],
                "股票名称": ["平安银行", "万科A"],
                "最新价": [10.5, 8.3],
            }
        )
        mock_efinance.stock.get_quote_history.return_value = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "收盘": [10.7],
                "最高": [10.8],
                "最低": [10.4],
                "成交量": [1000000],
            }
        )

        fetcher = CNEfinanceFetcher(max_workers=2, stock_limit=2)
        result = fetcher.fetch(date(2024, 1, 1))

        # Check for standardized column names
        assert "open" in result.columns
        assert "close" in result.columns
        assert "high" in result.columns
        assert "low" in result.columns
        assert "volume" in result.columns
        assert "ticker" in result.columns

    @patch("equity_lake.ingestion.sources.cn_efinance.efinance")
    def test_fetch_with_empty_stock_list(self, mock_efinance):
        """Test fetch behavior when stock list is empty."""
        mock_efinance.stock.get_realtime_quotes.return_value = pd.DataFrame()

        fetcher = CNEfinanceFetcher()
        result = fetcher.fetch(date(2024, 1, 1))

        assert result.empty

    @patch("equity_lake.ingestion.sources.cn_efinance.efinance")
    def test_fetch_retries_realtime_quote_failures(self, mock_efinance):
        """Test that realtime quote failures use the shared retry backoff."""
        mock_efinance.stock.get_realtime_quotes.side_effect = [
            Exception("temporary network failure"),
            pd.DataFrame({"股票代码": ["000001"]}),
        ]
        mock_efinance.stock.get_quote_history.return_value = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "收盘": [10.7],
                "最高": [10.8],
                "最低": [10.4],
                "成交量": [1000000],
                "成交额": [10700000],
            }
        )

        fetcher = CNEfinanceFetcher(max_workers=1, stock_limit=1, retry_delay=0.0)
        result = fetcher.fetch(date(2024, 1, 1))

        assert mock_efinance.stock.get_realtime_quotes.call_count == 2
        assert not result.empty


# =============================================================================
# CNHybridFetcher Tests (Multi-Source Fallback)
# =============================================================================


class TestCNHybridFetcher:
    """Test suite for CNHybridFetcher multi-source fallback system."""

    def test_initialization_with_both_sources(self):
        """Test fetcher initialization with both sources enabled."""
        with patch(
            "equity_lake.ingestion.sources.cn_hybrid.CNEfinanceFetcher"
        ) as mock_efinance:
            fetcher = CNHybridFetcher(
                enable_efinance=True,
                enable_akshare=True,
            )

            status = fetcher.get_source_status()
            assert status["efinance"] is True
            assert status["akshare"] is True
            assert mock_efinance.call_args.kwargs["retry_attempts"] == 4
            assert mock_efinance.call_args.kwargs["retry_delay"] == 2.0

    def test_initialization_efinance_only(self):
        """Test fetcher initialization with only efinance."""
        with patch(
            "equity_lake.ingestion.sources.cn_hybrid.CNEfinanceFetcher", MagicMock
        ):
            fetcher = CNHybridFetcher(
                enable_efinance=True,
                enable_akshare=False,
            )

            status = fetcher.get_source_status()
            assert status["efinance"] is True
            assert status["akshare"] is False

    def test_initialization_akshare_only(self):
        """Test fetcher initialization with only akshare."""
        fetcher = CNHybridFetcher(
            enable_efinance=False,
            enable_akshare=True,
        )

        status = fetcher.get_source_status()
        assert status["efinance"] is False
        assert status["akshare"] is True

    def test_initialization_fails_when_no_sources(self):
        """Test that fetcher raises error when both sources disabled."""
        with pytest.raises(RuntimeError, match="No China data source available"):
            CNHybridFetcher(
                enable_efinance=False,
                enable_akshare=False,
            )

    @patch("equity_lake.ingestion.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.ingestion.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_uses_efinance_first(
        self, mock_akshare, mock_efinance, sample_ohlcv_data
    ):
        """Test that fetch tries efinance first."""
        # Mock efinance to return data
        mock_efinance_instance = MagicMock()
        mock_efinance_instance.fetch.return_value = sample_ohlcv_data
        mock_efinance.return_value = mock_efinance_instance

        fetcher = CNHybridFetcher(
            enable_efinance=True,
            enable_akshare=True,
            stock_limit=10,
        )

        result = fetcher.fetch(date(2024, 1, 1))

        # Should use efinance and not call akshare
        mock_efinance_instance.fetch.assert_called_once()
        mock_akshare.return_value.fetch.assert_not_called()
        assert not result.empty

    @patch("equity_lake.ingestion.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.ingestion.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_falls_back_to_akshare(
        self, mock_akshare, mock_efinance, sample_ohlcv_data
    ):
        """Test that fetch falls back to akshare when efinance fails."""
        # Mock efinance to fail
        mock_efinance_instance = MagicMock()
        mock_efinance_instance.fetch.side_effect = Exception("efinance failed")
        mock_efinance.return_value = mock_efinance_instance

        # Mock akshare to succeed
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = sample_ohlcv_data
        mock_akshare.return_value = mock_akshare_instance

        fetcher = CNHybridFetcher(
            enable_efinance=True,
            enable_akshare=True,
            stock_limit=10,
        )

        result = fetcher.fetch(date(2024, 1, 1))

        # Should try both and return akshare result
        mock_efinance_instance.fetch.assert_called_once()
        mock_akshare_instance.fetch.assert_called_once()
        assert not result.empty

    @patch("equity_lake.ingestion.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.ingestion.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_falls_back_to_akshare_on_timeout(
        self,
        mock_akshare,
        mock_efinance,
        sample_ohlcv_data,
    ):
        """Test that efinance timeout triggers the akshare fallback."""
        mock_efinance.return_value = MagicMock()

        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = sample_ohlcv_data
        mock_akshare.return_value = mock_akshare_instance

        fetcher = CNHybridFetcher(
            enable_efinance=True,
            enable_akshare=True,
            stock_limit=10,
            efinance_timeout_seconds=0.01,
        )

        with patch.object(
            fetcher,
            "_fetch_efinance_with_timeout",
            side_effect=TimeoutError("efinance timed out"),
        ) as mock_timeout:
            result = fetcher.fetch(date(2024, 1, 1))

        mock_timeout.assert_called_once()
        mock_akshare_instance.fetch.assert_called_once()
        assert not result.empty

    @patch("equity_lake.ingestion.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.ingestion.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_returns_best_result(self, mock_akshare, mock_efinance):
        """Test that fetch returns the result with most data."""
        # Mock efinance to return fewer rows
        mock_efinance_instance = MagicMock()
        mock_efinance_instance.fetch.return_value = pd.DataFrame(
            {
                "ticker": ["000001", "000002"],
                "date": [date(2024, 1, 1)] * 2,
                "close": [10.5, 8.3],
            }
        )
        mock_efinance.return_value = mock_efinance_instance

        # Mock akshare to return more rows
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = pd.DataFrame(
            {
                "ticker": ["000001", "000002", "600000"],
                "date": [date(2024, 1, 1)] * 3,
                "close": [10.5, 8.3, 12.7],
            }
        )
        mock_akshare.return_value = mock_akshare_instance

        fetcher = CNHybridFetcher(
            enable_efinance=True,
            enable_akshare=True,
            stock_limit=10,
        )

        result = fetcher.fetch(date(2024, 1, 1))

        # Should return akshare result (more rows)
        assert len(result) == 3
        assert result["ticker"].nunique() == 3

    @patch("equity_lake.ingestion.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_akshare_only(self, mock_akshare, sample_ohlcv_data):
        """Test fetch when only akshare is enabled."""
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = sample_ohlcv_data
        mock_akshare.return_value = mock_akshare_instance

        fetcher = CNHybridFetcher(
            enable_efinance=False,
            enable_akshare=True,
        )

        result = fetcher.fetch(date(2024, 1, 1))

        mock_akshare_instance.fetch.assert_called_once()
        assert not result.empty

    def test_standardize_output(self):
        """Test _standardize_output method."""
        fetcher = CNHybridFetcher(enable_akshare=True)

        # Create DataFrame with extra columns
        input_df = pd.DataFrame(
            {
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "open": [150.0],
                "close": [152.0],
                "extra_column": ["should_be_removed"],
            }
        )

        result = fetcher._standardize_output(input_df)

        # Should only have standard columns
        assert "ticker" in result.columns
        assert "date" in result.columns
        assert "close" in result.columns
        assert "extra_column" not in result.columns

    def test_standardize_output_empty_dataframe(self):
        """Test _standardize_output with empty DataFrame."""
        fetcher = CNHybridFetcher(enable_akshare=True)

        result = fetcher._standardize_output(pd.DataFrame())

        assert result.empty


# =============================================================================
# Integration Tests
# =============================================================================


class TestFetcherIntegration:
    """Integration tests for fetcher interactions."""

    @patch("equity_lake.ingestion.sources.us.yf.download")
    def test_us_fetcher_with_large_dataset(self, mock_download):
        """Test USEquityFetcher with large ticker list."""
        # Create mock data
        mock_data = pd.DataFrame(
            {
                "Open": [150.0] * 100,
                "High": [155.0] * 100,
                "Low": [148.0] * 100,
                "Close": [152.0] * 100,
                "Adj Close": [152.0] * 100,
                "Volume": [1000000] * 100,
            },
            index=pd.date_range("2024-01-01", periods=100),
        )

        mock_download.return_value = mock_data

        # Test with 1200 tickers (should create 3 batches)
        large_ticker_list = [f"TICKER{i:04d}" for i in range(1200)]
        fetcher = USEquityFetcher(tickers=large_ticker_list, batch_size=500)

        result = fetcher.fetch(date(2024, 1, 1))

        # Should be called 3 times
        assert mock_download.call_count == 3
        assert not result.empty

    @patch("equity_lake.ingestion.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.ingestion.sources.cn_hybrid.CNAshareFetcher")
    def test_hybrid_fetcher_reliability(self, mock_akshare, mock_efinance):
        """Test that hybrid fetcher improves reliability."""
        # Simulate efinance failing 30% of time
        mock_efinance_instance = MagicMock()

        def efinance_side_effect(*args):
            import random

            if random.random() < 0.3:
                raise Exception("efinance random failure")
            return pd.DataFrame(
                {
                    "ticker": ["000001", "000002"],
                    "date": [date(2024, 1, 1)] * 2,
                    "close": [10.5, 8.3],
                }
            )

        mock_efinance_instance.fetch.side_effect = efinance_side_effect
        mock_efinance.return_value = mock_efinance_instance

        # akshare always succeeds
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = pd.DataFrame(
            {
                "ticker": ["000001", "000002", "600000"],
                "date": [date(2024, 1, 1)] * 3,
                "close": [10.5, 8.3, 12.7],
            }
        )
        mock_akshare.return_value = mock_akshare_instance

        fetcher = CNHybridFetcher(
            enable_efinance=True,
            enable_akshare=True,
            stock_limit=10,
        )

        # Run 10 times, should always succeed
        successes = 0
        for _ in range(10):
            result = fetcher.fetch(date(2024, 1, 1))
            if not result.empty:
                successes += 1

        # Hybrid should have 100% success rate
        assert successes == 10
