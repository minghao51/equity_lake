"""Unit tests for JPX and KRX market fetchers."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from equity_lake.sources import JPXEquityFetcher, KRXEquityFetcher


class TestJPXEquityFetcher:
    """Test suite for JPXEquityFetcher."""

    def test_initialization_with_default_batch_size(self):
        """Test that fetcher initializes with default batch size."""
        fetcher = JPXEquityFetcher(tickers=["7203.T", "6758.T"])
        assert fetcher.batch_size == 500
        assert fetcher.tickers == ["7203.T", "6758.T"]

    def test_initialization_with_custom_batch_size(self):
        """Test that fetcher accepts custom batch size."""
        fetcher = JPXEquityFetcher(tickers=["7203.T"], batch_size=200)
        assert fetcher.batch_size == 200

    def test_chunked_splits_tickers_correctly(self):
        """Test that _chunked method splits tickers into correct batches."""
        tickers = [f"TICKER{i:04d}.T" for i in range(1200)]
        fetcher = JPXEquityFetcher(tickers=tickers, batch_size=500)
        chunks = fetcher._chunked(tickers, 500)

        assert len(chunks) == 3
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 500
        assert len(chunks[2]) == 200

    def test_chunked_handles_small_lists(self):
        """Test that _chunked handles lists smaller than batch size."""
        tickers = ["7203.T", "6758.T"]
        fetcher = JPXEquityFetcher(tickers=tickers, batch_size=500)
        chunks = fetcher._chunked(tickers, 500)

        assert len(chunks) == 1
        assert len(chunks[0]) == 2

    def test_chunked_handles_empty_list(self):
        """Test that _chunked handles empty lists."""
        fetcher = JPXEquityFetcher(tickers=[], batch_size=500)
        chunks = fetcher._chunked([], 500)

        assert len(chunks) == 0

    def test_get_fallback_list(self):
        """Test that fallback ticker list contains major JPX stocks."""
        fetcher = JPXEquityFetcher(tickers=[])
        fallback = fetcher._get_fallback_list()

        assert "7203.T" in fallback
        assert "6758.T" in fallback
        assert len(fallback) == 10

    @patch("equity_lake.sources.jpx.yf.download")
    def test_fetch_with_batching(self, mock_download):
        """Test that fetch processes data in batches."""
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

        tickers = ["7203.T", "6758.T", "9984.T"]
        fetcher = JPXEquityFetcher(tickers=tickers, batch_size=2)
        result = fetcher.fetch(date(2024, 1, 1))

        assert mock_download.call_count == 2
        assert not result.empty
        assert "ticker" in result.columns

    @patch("equity_lake.sources.jpx.yf.download")
    def test_fetch_handles_no_data(self, mock_download):
        """Test that fetch handles empty response."""
        mock_download.return_value = pd.DataFrame()

        fetcher = JPXEquityFetcher(tickers=["7203.T"], batch_size=500)
        result = fetcher.fetch(date(2024, 1, 1))

        assert result.empty

    @patch("equity_lake.sources.jpx.yf.download")
    def test_fetch_standardizes_columns(self, mock_download):
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

        fetcher = JPXEquityFetcher(tickers=["7203.T"], batch_size=500)
        result = fetcher.fetch(date(2024, 1, 1))

        assert "open" in result.columns
        assert "high" in result.columns
        assert "low" in result.columns
        assert "close" in result.columns
        assert "adj_close" in result.columns
        assert "volume" in result.columns
        assert "ticker" in result.columns
        assert "date" in result.columns

    @patch("equity_lake.sources.jpx.yf.download")
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

        fetcher = JPXEquityFetcher(tickers=["7203.T"], batch_size=500)
        result = fetcher.fetch(date(2024, 1, 1))

        assert not result.empty
        assert result["ticker"].iloc[0] == "7203.T"


class TestKRXEquityFetcher:
    """Test suite for KRXEquityFetcher."""

    def test_initialization_with_tickers(self):
        """Test that fetcher initializes with provided tickers."""
        tickers = ["005930", "000660"]
        fetcher = KRXEquityFetcher(tickers=tickers)
        assert fetcher.tickers == tickers

    def test_initialization_with_default_retry_delay(self):
        """Test that fetcher uses higher retry delay for KRX."""
        fetcher = KRXEquityFetcher(tickers=["005930"])
        assert fetcher.retry_delay == 2.0

    def test_get_fallback_tickers(self):
        """Test that fallback ticker list contains major KRX stocks."""
        fetcher = KRXEquityFetcher(tickers=[])
        fallback = fetcher._get_fallback_list()

        assert "005930" in fallback
        assert "000660" in fallback
        assert len(fallback) == 10

    def test_fallback_tickers_when_config_empty(self):
        """Test that fallback tickers are used when config returns empty."""
        ticker_config = MagicMock()
        ticker_config.get_tickers_for_market.return_value = []

        fetcher = KRXEquityFetcher(ticker_config=ticker_config)
        assert fetcher.tickers == [
            "005930",
            "000660",
            "035420",
            "005380",
            "051910",
            "035720",
            "068270",
            "207940",
            "006400",
            "028260",
        ]

    def test_apply_filters_by_tags(self):
        """Test applying ticker filters by tags."""
        ticker_config = MagicMock()
        ticker_config.get_tickers_by_tags.return_value = ["005930", "000660"]

        KRXEquityFetcher(
            tickers=None,
            ticker_config=ticker_config,
            filters={"tags": ["tech"]},
        )
        ticker_config.get_tickers_by_tags.assert_called_once()

    def test_fetch_uses_finance_data_reader_data_reader_api(self):
        """Test KRX fetch uses the installed FinanceDataReader API surface."""
        mock_df = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [110.0],
                "Low": [95.0],
                "Close": [105.0],
                "Volume": [1000],
            },
            index=pd.DatetimeIndex(["2024-01-01"], name="Date"),
        )
        mock_module = SimpleNamespace(DataReader=MagicMock(return_value=mock_df))

        with patch.dict("sys.modules", {"FinanceDataReader": mock_module}):
            fetcher = KRXEquityFetcher(tickers=["005930"])
            result = fetcher.fetch(date(2024, 1, 1))

        mock_module.DataReader.assert_called_once_with("005930", "2024-01-01", "2024-01-02")
        assert not result.empty
        assert not result.empty
        assert result["ticker"].iloc[0] == "005930"
