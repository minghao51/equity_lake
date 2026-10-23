"""
Unit tests for new data fetchers with batch improvements and multi-source support.

Tests cover:
- USEquityFetcher with batch downloading
- CNEfinanceFetcher (new efinance integration)
- CNHybridFetcher (multi-source fallback system)
"""

import urllib.error
from datetime import date
from email.message import Message
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import polars as pl
import pytest
import requests

from equity_lake.core.config import MarketConfig, TickerConfig, TickerMetadata  # type: ignore[import-untyped]
from equity_lake.sources import (  # type: ignore[import-untyped]
    CNAshareFetcher,
    CNEfinanceFetcher,
    CNHybridFetcher,
    USEquityFetcher,
)
from equity_lake.sources.base import TransientError  # type: ignore[import-untyped]

US_FIXTURE_EXCHANGES = {
    "AAPL": "NASDAQ",
    "MSFT": "NASDAQ",
    "GOOGL": "NASDAQ",
    "AMZN": "NASDAQ",
    "TSLA": "NASDAQ",
    "META": "NASDAQ",
    "NVDA": "NASDAQ",
    "JPM": "NYSE",
}
CN_FIXTURE_EXCHANGES = {
    "000001": "SZSE",
    "600000": "SSE",
    "600036": "SSE",
    "600519": "SSE",
    "000858": "SZSE",
}


def _typed_ticker_config(market: str, exchanges: dict[str, str]) -> TickerConfig:
    """Build a real ticker config with exact, typed test metadata."""
    metadata = [TickerMetadata(symbol=symbol, name=symbol, exchange=exchange, sector="Test") for symbol, exchange in exchanges.items()]
    currency = "USD" if market == "us" else "CNY"
    return TickerConfig(markets={market: MarketConfig(currency=currency, tickers=metadata)})


def _assert_source_identity(
    frame: pl.DataFrame,
    *,
    market: str,
    mic_by_ticker: dict[str, str],
    provenance: str,
) -> None:
    """Assert each output row carries the expected canonical identity and provenance."""
    assert frame["market"].unique().to_list() == [market]
    assert dict(frame.select("ticker", "mic").unique().iter_rows()) == mic_by_ticker
    assert frame["symbol"].to_list() == frame["ticker"].to_list()
    assert frame["identity_schema_version"].unique().to_list() == [1]
    assert frame["identity_provenance"].unique().to_list() == [provenance]


def _cn_provider_frame(tickers: list[str], exchanges: dict[str, str]) -> pd.DataFrame:
    """Synthetic CN source rows with explicit provider exchange labels."""
    return pd.DataFrame(
        {
            "ticker": tickers,
            "exchange": [exchanges[ticker] for ticker in tickers],
            "date": [date(2024, 1, 1)] * len(tickers),
            "open": [10.0] * len(tickers),
            "high": [10.8] * len(tickers),
            "low": [9.8] * len(tickers),
            "close": [10.5] * len(tickers),
            "volume": [1_000_000] * len(tickers),
            "adj_close": [10.5] * len(tickers),
        }
    )


def _yf_flat_frame() -> pd.DataFrame:
    """A flat (non-MultiIndex) OHLCV frame as returned for a single ticker."""
    return pd.DataFrame(
        {
            "Open": [150.0],
            "High": [155.0],
            "Low": [148.0],
            "Close": [152.0],
            "Adj Close": [152.0],
            "Volume": [1000000],
            "Exchange": ["NASDAQ"],
        },
        index=pd.DatetimeIndex(["2024-01-01"]),
    )


def _yf_multi_ticker_frame(tickers: list[str], *, exchanges: dict[str, str], periods: int = 1) -> pd.DataFrame:
    """A yfinance-shaped fixture with explicit per-symbol provider venues."""
    per_ticker = {
        ticker: pd.DataFrame(
            {
                "Open": [150.0] * periods,
                "High": [155.0] * periods,
                "Low": [148.0] * periods,
                "Close": [152.0] * periods,
                "Adj Close": [152.0] * periods,
                "Volume": [1000000] * periods,
                "Exchange": [exchanges[ticker]] * periods,
            },
            index=pd.date_range("2024-01-01", periods=periods, freq="D"),
        )
        for ticker in tickers
    }
    return pd.concat(per_ticker, axis=1, names=["Ticker", "Field"])


# =============================================================================
# USEquityFetcher Tests (Batch Download Improvements)
# =============================================================================


class TestUSEquityFetcherBatching:
    """Test suite for USEquityFetcher with batch downloading."""

    def test_initialization_with_default_batch_size(self, sample_us_tickers: list[str]) -> None:
        """Test that fetcher initializes with default batch size."""
        fetcher = USEquityFetcher(tickers=sample_us_tickers)
        assert fetcher.batch_size == 500  # Default value
        assert fetcher.tickers == sample_us_tickers

    def test_initialization_with_custom_batch_size(self, sample_us_tickers: list[str]) -> None:
        """Test that fetcher accepts custom batch size."""
        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=200)
        assert fetcher.batch_size == 200

    def test_chunked_splits_tickers_correctly(self, sample_large_ticker_list: list[str]) -> None:
        """Test that _chunked method splits tickers into correct batches."""
        fetcher = USEquityFetcher(tickers=sample_large_ticker_list, batch_size=500)
        chunks = fetcher._chunked(sample_large_ticker_list, 500)

        assert len(chunks) == 3  # 1200 / 500 = 2.4, so 3 batches
        assert len(chunks[0]) == 500
        assert len(chunks[1]) == 500
        assert len(chunks[2]) == 200

    def test_chunked_handles_small_lists(self, sample_us_tickers: list[str]) -> None:
        """Test that _chunked handles lists smaller than batch size."""
        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=500)
        chunks = fetcher._chunked(sample_us_tickers, 500)

        assert len(chunks) == 1
        assert len(chunks[0]) == len(sample_us_tickers)

    def test_chunked_handles_empty_list(self) -> None:
        """Test that _chunked handles empty lists."""
        fetcher = USEquityFetcher(tickers=[], batch_size=500)
        chunks = fetcher._chunked([], 500)

        assert len(chunks) == 0

    @patch("equity_lake.sources.base.yf.download")
    def test_fetch_with_batching(self, mock_download: MagicMock, sample_us_tickers: list[str]) -> None:
        """Test that fetch processes data in batches."""
        mock_download.return_value = _yf_multi_ticker_frame(sample_us_tickers, exchanges=US_FIXTURE_EXCHANGES)

        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=3)
        result = fetcher.fetch(date(2024, 1, 1))

        # Should be called 3 times for 8 tickers with batch_size=3
        assert mock_download.call_count == 3
        assert isinstance(result, pl.DataFrame)
        assert not result.is_empty()
        assert "ticker" in result.columns
        _assert_source_identity(
            result,
            market="us_equity",
            mic_by_ticker={symbol: "XNAS" if exchange == "NASDAQ" else "XNYS" for symbol, exchange in US_FIXTURE_EXCHANGES.items()},
            provenance="provider_exchange",
        )

    @patch("equity_lake.sources.base.yf.download")
    def test_fetch_handles_partial_failures(self, mock_download: MagicMock, sample_us_tickers: list[str]) -> None:
        """Test that fetch continues even if one batch fails."""
        call_count = [0]

        def side_effect(*args: Any, **kwargs: Any) -> pd.DataFrame:
            call_count[0] += 1
            if call_count[0] == 2:
                # Second batch fails
                return pd.DataFrame()
            # Other batches succeed
            return _yf_multi_ticker_frame(sample_us_tickers, exchanges=US_FIXTURE_EXCHANGES)

        mock_download.side_effect = side_effect

        fetcher = USEquityFetcher(tickers=sample_us_tickers, batch_size=2)
        result = fetcher.fetch(date(2024, 1, 1))

        # Should continue despite one batch failing
        assert mock_download.call_count == 4
        assert isinstance(result, pl.DataFrame)
        assert not result.is_empty()

    @patch("equity_lake.sources.base.yf.download")
    def test_fetch_standardizes_columns(self, mock_download: MagicMock, sample_us_tickers: list[str]) -> None:
        """Test that fetch standardizes column names."""
        mock_download.return_value = _yf_multi_ticker_frame(sample_us_tickers[:2], exchanges=US_FIXTURE_EXCHANGES)

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
        assert {"market", "mic", "symbol", "identity_schema_version", "identity_provenance"}.issubset(result.columns)
        _assert_source_identity(
            result,
            market="us_equity",
            mic_by_ticker={symbol: "XNAS" if US_FIXTURE_EXCHANGES[symbol] == "NASDAQ" else "XNYS" for symbol in sample_us_tickers[:2]},
            provenance="provider_exchange",
        )

    @patch("equity_lake.sources.base.yf.download")
    def test_fetch_with_single_ticker(self, mock_download: MagicMock) -> None:
        """Test that fetch handles single ticker correctly."""
        mock_download.return_value = pd.DataFrame(
            {
                "Open": [150.0],
                "High": [155.0],
                "Low": [148.0],
                "Close": [152.0],
                "Adj Close": [152.0],
                "Volume": [1000000],
                "Exchange": ["NASDAQ"],
            },
            index=pd.DatetimeIndex(["2024-01-01"]),
        )

        fetcher = USEquityFetcher(tickers=["AAPL"], batch_size=500)
        result = fetcher.fetch(date(2024, 1, 1))

        assert not result.is_empty()
        assert result["ticker"][0] == "AAPL"
        _assert_source_identity(result, market="us_equity", mic_by_ticker={"AAPL": "XNAS"}, provenance="provider_exchange")


class TestRetryOnTransientErrors:
    """Requests/urllib-based fetchers must retry per the tenacity contract."""

    @patch("equity_lake.sources.base.yf.download")
    def test_requests_connection_error_retried_three_times_then_fails(self, mock_download: MagicMock) -> None:
        mock_download.side_effect = requests.exceptions.ConnectionError("connection reset")

        fetcher = USEquityFetcher(tickers=["AAPL"], retry_delay=0.01)

        with pytest.raises(TransientError):
            fetcher.fetch(date(2024, 1, 1))
        assert mock_download.call_count == 3

    @patch("equity_lake.sources.base.yf.download")
    def test_requests_timeout_retried_three_times_then_fails(self, mock_download: MagicMock) -> None:
        mock_download.side_effect = requests.exceptions.Timeout("read timed out")

        fetcher = USEquityFetcher(tickers=["AAPL"], retry_delay=0.01)

        with pytest.raises(TransientError):
            fetcher.fetch(date(2024, 1, 1))
        assert mock_download.call_count == 3

    @patch("equity_lake.sources.base.yf.download")
    def test_urllib_500_retried_three_times_then_fails(self, mock_download: MagicMock) -> None:
        mock_download.side_effect = urllib.error.HTTPError("https://query1.finance.yahoo.com", 500, "Internal Server Error", Message(), None)

        fetcher = USEquityFetcher(tickers=["AAPL"], retry_delay=0.01)

        with pytest.raises(TransientError):
            fetcher.fetch(date(2024, 1, 1))
        assert mock_download.call_count == 3

    @patch("equity_lake.sources.base.yf.download")
    def test_http_404_not_retried(self, mock_download: MagicMock) -> None:
        mock_download.side_effect = urllib.error.HTTPError("https://query1.finance.yahoo.com", 404, "Not Found", Message(), None)

        fetcher = USEquityFetcher(tickers=["AAPL"], retry_delay=0.01)

        with pytest.raises(urllib.error.HTTPError):
            fetcher.fetch(date(2024, 1, 1))
        assert mock_download.call_count == 1


class TestMultiTickerFlatFrame:
    """A flat (non-MultiIndex) frame from a multi-ticker batch is a failed download."""

    @patch("equity_lake.sources.base.yf.download")
    def test_multi_ticker_flat_frame_returns_no_rows(self, mock_download: MagicMock) -> None:
        """Regression: a flat frame must not be copied onto every ticker in the batch."""
        mock_download.return_value = _yf_flat_frame()

        fetcher = USEquityFetcher(tickers=["AAPL", "MSFT"], batch_size=2)
        result = fetcher.fetch(date(2024, 1, 1))

        assert result.is_empty()

    @patch("equity_lake.sources.base.yf.download")
    def test_single_ticker_batch_flat_frame_still_parsed(self, mock_download: MagicMock) -> None:
        """Flat-frame handling is kept for single-ticker batches."""
        mock_download.return_value = _yf_flat_frame()

        fetcher = USEquityFetcher(tickers=["AAPL"], batch_size=1)
        result = fetcher.fetch(date(2024, 1, 1))

        assert not result.is_empty()
        assert result["ticker"][0] == "AAPL"
        _assert_source_identity(result, market="us_equity", mic_by_ticker={"AAPL": "XNAS"}, provenance="provider_exchange")


# =============================================================================
# CNEfinanceFetcher Tests (New efinance Integration)
# =============================================================================


class TestCNEfinanceFetcher:
    """Test suite for CNEfinanceFetcher."""

    def test_initialization_requires_efinance(self) -> None:
        """Test that fetcher raises ImportError if efinance not available."""
        with (
            patch("equity_lake.sources.cn_efinance.efinance", None),
            pytest.raises(ImportError, match="efinance is not installed"),
        ):
            CNEfinanceFetcher()

    def test_initialization_with_params(self) -> None:
        """Test fetcher initialization with parameters."""
        with patch("equity_lake.sources.cn_efinance.efinance", MagicMock()):
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

    def test_standardize_history_frame(self) -> None:
        """Test standardizing a single efinance history frame."""
        with patch("equity_lake.sources.cn_efinance.efinance", MagicMock()):
            fetcher = CNEfinanceFetcher(max_workers=1, stock_limit=1)

        result = fetcher._standardize_history_frame(
            pd.DataFrame(
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
            ),
            "000001",
        )

        assert result is not None
        assert not result.is_empty()
        assert "ticker" in result.columns
        assert result["ticker"][0] == "000001"

    @patch("equity_lake.sources.cn_efinance.efinance")
    def test_fetch_history_batch_handles_failure(self, mock_efinance: MagicMock) -> None:
        """Test batch fetch handles provider failures gracefully."""
        mock_efinance.stock.get_quote_history.side_effect = Exception("Network error")

        fetcher = CNEfinanceFetcher(max_workers=1, stock_limit=1)
        result = fetcher._fetch_history_batch(["000001"], date(2024, 1, 1))

        assert result == []

    @patch("equity_lake.sources.cn_efinance.efinance")
    def test_fetch_standardizes_columns(self, mock_efinance: MagicMock) -> None:
        """Test that fetch standardizes Chinese column names."""
        mock_efinance.stock.get_quote_history.return_value = {
            "000001": pd.DataFrame(
                {
                    "股票代码": ["000001"],
                    "日期": ["2024-01-01"],
                    "开盘": [10.5],
                    "收盘": [10.7],
                    "最高": [10.8],
                    "最低": [10.4],
                    "成交量": [1000000],
                }
            ),
            "600000": pd.DataFrame(
                {
                    "股票代码": ["600000"],
                    "日期": ["2024-01-01"],
                    "开盘": [8.1],
                    "收盘": [8.3],
                    "最高": [8.4],
                    "最低": [8.0],
                    "成交量": [800000],
                }
            ),
        }

        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE", "600000": "SSE"})

        fetcher = CNEfinanceFetcher(
            max_workers=2,
            stock_limit=2,
            ticker_config=ticker_config,
        )
        result = fetcher.fetch(date(2024, 1, 1))

        # Check for standardized column names
        assert "open" in result.columns
        assert "close" in result.columns
        assert "high" in result.columns
        assert "low" in result.columns
        assert "volume" in result.columns
        assert "ticker" in result.columns
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG"},
            provenance="current_ticker_config",
        )

    def test_fetch_with_empty_configured_tickers(self) -> None:
        """Test fetch behavior when no configured CN tickers exist."""
        ticker_config = _typed_ticker_config("cn", {})

        with patch("equity_lake.sources.cn_efinance.efinance", MagicMock()):
            fetcher = CNEfinanceFetcher(ticker_config=ticker_config)
            result = fetcher.fetch(date(2024, 1, 1))

        assert result.is_empty()

    @patch("equity_lake.sources.cn_efinance.efinance")
    def test_fetch_uses_configured_tickers_without_live_stock_list(self, mock_efinance: MagicMock) -> None:
        """Test fetch uses configured tickers and skips live universe discovery."""
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE", "600000": "SSE"})

        mock_efinance.stock.get_quote_history.return_value = {
            "000001": pd.DataFrame(
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
            ),
            "600000": pd.DataFrame(
                {
                    "股票代码": ["600000"],
                    "日期": ["2024-01-01"],
                    "开盘": [8.1],
                    "收盘": [8.3],
                    "最高": [8.4],
                    "最低": [8.0],
                    "成交量": [800000],
                    "成交额": [6640000],
                }
            ),
        }

        fetcher = CNEfinanceFetcher(
            max_workers=2,
            stock_limit=2,
            ticker_config=ticker_config,
        )
        result = fetcher.fetch(date(2024, 1, 1))

        mock_efinance.stock.get_realtime_quotes.assert_not_called()
        assert not result.is_empty()
        mock_efinance.stock.get_quote_history.assert_called_once()
        assert mock_efinance.stock.get_quote_history.call_args.kwargs["stock_codes"] == [
            "000001",
            "600000",
        ]
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG"},
            provenance="current_ticker_config",
        )

    @patch("equity_lake.sources.cn_efinance.efinance")
    def test_fetch_respects_stock_limit_on_configured_tickers(self, mock_efinance: MagicMock) -> None:
        """Test configured CN tickers are trimmed by stock_limit."""
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE", "600036": "SSE", "600000": "SSE"})

        mock_efinance.stock.get_quote_history.return_value = {
            "000001": pd.DataFrame(
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
            ),
            "600036": pd.DataFrame(
                {
                    "股票代码": ["600036"],
                    "日期": ["2024-01-01"],
                    "开盘": [8.1],
                    "收盘": [8.3],
                    "最高": [8.4],
                    "最低": [8.0],
                    "成交量": [800000],
                    "成交额": [6640000],
                }
            ),
        }

        fetcher = CNEfinanceFetcher(
            max_workers=1,
            stock_limit=2,
            ticker_config=ticker_config,
        )
        result = fetcher.fetch(date(2024, 1, 1))

        mock_efinance.stock.get_quote_history.assert_called_once()
        assert mock_efinance.stock.get_quote_history.call_args.kwargs["stock_codes"] == [
            "000001",
            "600036",
        ]
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600036": "XSHG"},
            provenance="current_ticker_config",
        )


class TestCNAshareFetcher:
    """Test suite for CNAshareFetcher with configured CN universe."""

    @patch("equity_lake.sources.cn.ak.stock_zh_a_hist")
    @patch("equity_lake.sources.cn.ak.stock_info_a_code_name")
    def test_fetch_uses_configured_tickers_without_live_stock_list(
        self,
        mock_stock_list: MagicMock,
        mock_hist: MagicMock,
    ) -> None:
        """Test fetch uses configured CN tickers and skips live stock list calls."""
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE", "600000": "SSE"})
        mock_hist.return_value = pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "最高": [10.8],
                "最低": [10.4],
                "收盘": [10.7],
                "成交量": [1000000],
            }
        )
        # Concurrent workers must receive isolated provider frames before the fetcher adds each ticker.
        mock_hist.side_effect = lambda *args, **kwargs: mock_hist.return_value.copy()

        fetcher = CNAshareFetcher(
            max_workers=2,
            stock_limit=2,
            ticker_config=ticker_config,
        )
        result = fetcher.fetch(date(2024, 1, 1))

        mock_stock_list.assert_not_called()
        assert not result.is_empty()
        assert mock_hist.call_count == 2
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG"},
            provenance="current_ticker_config",
        )

    @patch("equity_lake.sources.cn.ak.stock_zh_a_hist")
    def test_fetch_with_empty_configured_tickers(self, mock_hist: MagicMock) -> None:
        """Test fetch returns empty data when no configured CN tickers exist."""
        ticker_config = _typed_ticker_config("cn", {})

        fetcher = CNAshareFetcher(ticker_config=ticker_config)
        result = fetcher.fetch(date(2024, 1, 1))

        mock_hist.assert_not_called()
        assert result.is_empty()

    @patch("equity_lake.sources.cn.ak.stock_zh_a_hist")
    def test_fetch_respects_stock_limit_on_configured_tickers(self, mock_hist: MagicMock) -> None:
        """Test configured CN tickers are trimmed by stock_limit."""
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE", "600036": "SSE", "600000": "SSE"})
        mock_hist.return_value = pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "最高": [10.8],
                "最低": [10.4],
                "收盘": [10.7],
                "成交量": [1000000],
            }
        )
        mock_hist.side_effect = lambda *args, **kwargs: mock_hist.return_value.copy()

        fetcher = CNAshareFetcher(
            max_workers=1,
            stock_limit=2,
            ticker_config=ticker_config,
        )
        result = fetcher.fetch(date(2024, 1, 1))

        called_codes = [call.kwargs["symbol"] for call in mock_hist.call_args_list]
        assert called_codes == ["000001", "600036"]
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600036": "XSHG"},
            provenance="current_ticker_config",
        )

    @patch("equity_lake.sources.cn.ak.stock_zh_a_hist")
    def test_fetch_all_stocks_fail_returns_empty_without_raising(self, mock_hist: MagicMock) -> None:
        """Per-stock failures degrade to failure_count, not an outer exception."""
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE"})
        mock_hist.side_effect = Exception("network down")

        fetcher = CNAshareFetcher(ticker_config=ticker_config)
        result = fetcher.fetch(date(2024, 1, 1))

        assert result.is_empty()

    @patch("equity_lake.sources.cn.ak.stock_zh_a_hist")
    def test_fetch_outer_errors_propagate_not_swallowed(self, mock_hist: MagicMock) -> None:
        """Errors that escape the per-stock loop must propagate (tenacity contract), not return an empty frame."""
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE"})

        fetcher = CNAshareFetcher(ticker_config=ticker_config, retry_delay=0.01)

        with (
            patch.object(fetcher, "_fetch_single_stock", side_effect=RuntimeError("akshare exploded")),
            pytest.raises(RuntimeError, match="akshare exploded"),
        ):
            fetcher.fetch(date(2024, 1, 1))


# =============================================================================
# CNHybridFetcher Tests (Multi-Source Fallback)
# =============================================================================


class TestCNHybridFetcher:
    """Test suite for CNHybridFetcher multi-source fallback system."""

    def test_initialization_with_both_sources(self) -> None:
        """Test fetcher initialization with both sources enabled."""
        with patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher") as mock_efinance:
            fetcher = CNHybridFetcher(
                enable_efinance=True,
                enable_akshare=True,
            )

            assert fetcher.enable_efinance is True
            assert fetcher.enable_akshare is True
            assert mock_efinance.call_args.kwargs["retry_attempts"] == 4
            assert mock_efinance.call_args.kwargs["retry_delay"] == 2.0
            assert fetcher.configured_ticker_count > 0

    def test_initialization_efinance_only(self) -> None:
        """Test fetcher initialization with only efinance."""
        with patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher", MagicMock):
            fetcher = CNHybridFetcher(
                enable_efinance=True,
                enable_akshare=False,
            )

            assert fetcher.enable_efinance is True
            assert fetcher.enable_akshare is False

    def test_initialization_akshare_only(self) -> None:
        """Test fetcher initialization with only akshare."""
        fetcher = CNHybridFetcher(
            enable_efinance=False,
            enable_akshare=True,
        )

        assert fetcher.enable_efinance is False
        assert fetcher.enable_akshare is True

    def test_initialization_defaults_to_akshare_primary(self) -> None:
        """Test daily default keeps efinance off the hot path."""
        fetcher = CNHybridFetcher()

        assert fetcher.enable_efinance is False
        assert fetcher.enable_akshare is True

    def test_initialization_fails_when_no_sources(self) -> None:
        """Test that fetcher raises error when both sources disabled."""
        with pytest.raises(RuntimeError, match="No China data source available"):
            CNHybridFetcher(
                enable_efinance=False,
                enable_akshare=False,
            )

    @patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_uses_efinance_first(self, mock_akshare: MagicMock, mock_efinance: MagicMock) -> None:
        """Test that fetch tries efinance first."""
        # Mock efinance with explicit CN provider venue data.
        mock_efinance_instance = MagicMock()
        mock_efinance_instance.fetch.return_value = _cn_provider_frame(["000001", "600000", "600036"], CN_FIXTURE_EXCHANGES)
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
        assert not result.is_empty()
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG", "600036": "XSHG"},
            provenance="provider_exchange",
        )

    @patch("equity_lake.sources.cn_hybrid.TickerConfig")
    @patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_uses_configured_ticker_threshold(
        self,
        mock_akshare: MagicMock,
        mock_efinance: MagicMock,
        mock_ticker_config: MagicMock,
    ) -> None:
        """Test sufficiency threshold uses configured ticker count."""
        mock_ticker_config.return_value = _typed_ticker_config(
            "cn",
            {"000001": "SZSE", "600000": "SSE", "600036": "SSE", "600519": "SSE"},
        )
        mock_efinance_instance = MagicMock()
        mock_efinance_instance.fetch.return_value = _cn_provider_frame(["000001"], CN_FIXTURE_EXCHANGES)
        mock_efinance.return_value = mock_efinance_instance

        fetcher = CNHybridFetcher(
            enable_efinance=True,
            enable_akshare=True,
            stock_limit=10,
            ticker_config=None,
        )

        result = fetcher.fetch(date(2024, 1, 1))

        mock_akshare.return_value.fetch.assert_not_called()
        assert not result.is_empty()
        _assert_source_identity(result, market="cn_ashare", mic_by_ticker={"000001": "XSHE"}, provenance="provider_exchange")

    @patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_falls_back_to_akshare(self, mock_akshare: MagicMock, mock_efinance: MagicMock) -> None:
        """Test that fetch falls back to akshare when efinance fails."""
        # Mock efinance to fail
        mock_efinance_instance = MagicMock()
        mock_efinance_instance.fetch.side_effect = Exception("efinance failed")
        mock_efinance.return_value = mock_efinance_instance

        # Mock akshare to succeed
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = _cn_provider_frame(["000001", "600000", "600036"], CN_FIXTURE_EXCHANGES)
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
        assert not result.is_empty()
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG", "600036": "XSHG"},
            provenance="provider_exchange",
        )

    @patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_falls_back_to_akshare_on_timeout(
        self,
        mock_akshare: MagicMock,
        mock_efinance: MagicMock,
    ) -> None:
        """Test that efinance timeout triggers the akshare fallback."""
        mock_efinance.return_value = MagicMock()

        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = _cn_provider_frame(["000001", "600000", "600036"], CN_FIXTURE_EXCHANGES)
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
        assert not result.is_empty()
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG", "600036": "XSHG"},
            provenance="provider_exchange",
        )

    @patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_returns_best_result(self, mock_akshare: MagicMock, mock_efinance: MagicMock) -> None:
        """Test that fetch returns the result with most data."""
        # Mock efinance to return fewer rows
        mock_efinance_instance = MagicMock()
        mock_efinance_instance.fetch.return_value = _cn_provider_frame(["000001", "600000"], CN_FIXTURE_EXCHANGES)
        mock_efinance.return_value = mock_efinance_instance

        # Mock akshare to return more rows with explicit CN provider venues.
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = _cn_provider_frame(["000001", "600000", "600036"], CN_FIXTURE_EXCHANGES)
        mock_akshare.return_value = mock_akshare_instance

        fetcher = CNHybridFetcher(
            enable_efinance=True,
            enable_akshare=True,
            stock_limit=10,
        )

        result = fetcher.fetch(date(2024, 1, 1))

        # Should return akshare result (more rows)
        assert result.height == 3
        assert result["ticker"].n_unique() == 3
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG", "600036": "XSHG"},
            provenance="provider_exchange",
        )

    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_fetch_akshare_only(self, mock_akshare: MagicMock) -> None:
        """Test fetch when only akshare is enabled."""
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = _cn_provider_frame(["000001", "600000", "600036"], CN_FIXTURE_EXCHANGES)
        mock_akshare.return_value = mock_akshare_instance

        fetcher = CNHybridFetcher(
            enable_efinance=False,
            enable_akshare=True,
        )

        result = fetcher.fetch(date(2024, 1, 1))

        mock_akshare_instance.fetch.assert_called_once()
        assert not result.is_empty()
        _assert_source_identity(
            result,
            market="cn_ashare",
            mic_by_ticker={"000001": "XSHE", "600000": "XSHG", "600036": "XSHG"},
            provenance="provider_exchange",
        )

    def test_standardize_output(self) -> None:
        """Test _standardize_output method."""
        fetcher = CNHybridFetcher(enable_akshare=True)

        # Create DataFrame with extra columns
        input_df = _cn_provider_frame(["600000"], CN_FIXTURE_EXCHANGES)
        input_df["extra_column"] = "should_be_removed"

        result = fetcher._standardize_output(input_df)

        # Should only have standard columns
        assert "ticker" in result.columns
        assert "date" in result.columns
        assert "close" in result.columns
        assert "extra_column" not in result.columns
        _assert_source_identity(result, market="cn_ashare", mic_by_ticker={"600000": "XSHG"}, provenance="provider_exchange")

    def test_standardize_output_empty_dataframe(self) -> None:
        """Test _standardize_output with empty DataFrame."""
        fetcher = CNHybridFetcher(enable_akshare=True)

        result = fetcher._standardize_output(pd.DataFrame())

        assert result.is_empty()


# =============================================================================
# Integration Tests
# =============================================================================


class TestFetcherIntegration:
    """Integration tests for fetcher interactions."""

    @patch("equity_lake.sources.base.yf.download")
    def test_us_fetcher_with_large_dataset(self, mock_download: MagicMock) -> None:
        """Test USEquityFetcher with large ticker list."""
        # Test with 1200 tickers (should create 3 batches)
        large_ticker_list = [f"TICKER{i:04d}" for i in range(1200)]
        synthetic_exchange_map = dict.fromkeys(large_ticker_list, "NASDAQ")
        mock_download.return_value = _yf_multi_ticker_frame(large_ticker_list, exchanges=synthetic_exchange_map, periods=100)

        fetcher = USEquityFetcher(tickers=large_ticker_list, batch_size=500)

        result = fetcher.fetch(date(2024, 1, 1))

        # Should be called 3 times
        assert mock_download.call_count == 3
        assert not result.is_empty()

    @patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_hybrid_fetcher_reliability(self, mock_akshare: MagicMock, mock_efinance: MagicMock) -> None:
        """Test that hybrid fetcher improves reliability."""
        # Simulate efinance failing 30% of time
        mock_efinance_instance = MagicMock()

        def efinance_side_effect(*args: Any) -> pd.DataFrame:
            import random

            if random.random() < 0.3:
                raise Exception("efinance random failure")
            return _cn_provider_frame(["000001", "600000"], CN_FIXTURE_EXCHANGES)

        mock_efinance_instance.fetch.side_effect = efinance_side_effect
        mock_efinance.return_value = mock_efinance_instance

        # akshare always succeeds with explicit CN provider venues.
        mock_akshare_instance = MagicMock()
        mock_akshare_instance.fetch.return_value = _cn_provider_frame(["000001", "600000", "600036"], CN_FIXTURE_EXCHANGES)
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
            if not result.is_empty():
                successes += 1

        # Hybrid should have 100% success rate
        assert successes == 10


# =============================================================================
# W03/F04: CN explicit-ticker seam (primary + fallback receive the same scope)
# =============================================================================


class TestCNExplicitTickerSeam:
    """An explicit CN scope overrides the configured universe in every provider."""

    @patch("equity_lake.sources.cn_hybrid.CNEfinanceFetcher")
    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_hybrid_forwards_same_explicit_list_to_primary_and_fallback(self, mock_akshare: MagicMock, mock_efinance: MagicMock) -> None:
        CNHybridFetcher(enable_efinance=True, enable_akshare=True, tickers=["600000", "000001"])
        assert mock_efinance.call_args.kwargs["tickers"] == ["600000", "000001"]
        assert mock_akshare.call_args.kwargs["tickers"] == ["600000", "000001"]

    def test_hybrid_explicit_list_not_truncated_by_stock_limit(self) -> None:
        fetcher = CNHybridFetcher(enable_akshare=True, stock_limit=1, tickers=["a", "b", "c"])
        assert fetcher._explicit_tickers == ["a", "b", "c"]
        assert fetcher.configured_ticker_count == 3

    def test_hybrid_empty_explicit_list_is_preserved(self) -> None:
        fetcher = CNHybridFetcher(enable_akshare=True, tickers=[])
        assert fetcher._explicit_tickers == []
        assert fetcher.configured_ticker_count == 0

    @patch("equity_lake.sources.cn.ak.stock_zh_a_hist")
    def test_cn_ashare_explicit_tickers_override_config(self, mock_hist: MagicMock) -> None:
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE", "600000": "SSE"})
        mock_hist.return_value = pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "最高": [10.8],
                "最低": [10.4],
                "收盘": [10.7],
                "成交量": [1000000],
            }
        )
        fetcher = CNAshareFetcher(max_workers=1, ticker_config=ticker_config, tickers=["600000"])
        result = fetcher.fetch(date(2024, 1, 1))
        called = [call.kwargs["symbol"] for call in mock_hist.call_args_list]
        assert called == ["600000"]
        _assert_source_identity(result, market="cn_ashare", mic_by_ticker={"600000": "XSHG"}, provenance="current_ticker_config")

    def test_cn_efinance_explicit_tickers_override_config(self) -> None:
        ticker_config = _typed_ticker_config("cn", {"000001": "SZSE", "600000": "SSE"})
        fake_efinance = MagicMock()
        fake_efinance.stock.get_quote_history.return_value = pd.DataFrame(
            {
                "股票代码": ["600000"],
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "收盘": [10.7],
                "最高": [10.8],
                "最低": [10.4],
                "成交量": [1000000],
            }
        )
        with patch("equity_lake.sources.cn_efinance.efinance", fake_efinance):
            fetcher = CNEfinanceFetcher(max_workers=1, ticker_config=ticker_config, tickers=["600000"])
            result = fetcher.fetch(date(2024, 1, 1))

        assert not result.is_empty()
        assert fake_efinance.stock.get_quote_history.call_args.kwargs["stock_codes"] == ["600000"]
        _assert_source_identity(result, market="cn_ashare", mic_by_ticker={"600000": "XSHG"}, provenance="current_ticker_config")

    @patch("equity_lake.sources.cn_hybrid.CNAshareFetcher")
    def test_hybrid_yfinance_fallback_uses_explicit_list(self, mock_akshare: MagicMock) -> None:
        """The yfinance fallback uses the same effective explicit list."""
        fetcher = CNHybridFetcher(enable_akshare=True, tickers=["600000", "000001"])
        captured: list[list[str]] = []

        def fake_download(symbols: list[str], **kwargs: Any) -> pd.DataFrame:
            captured.append(list(symbols))
            return pd.DataFrame()

        with patch("equity_lake.sources.cn_hybrid.yf.download", side_effect=fake_download):
            result = fetcher._fetch_yfinance_fallback(date(2024, 1, 1))

        assert result.is_empty()
        assert captured == [["600000.SS", "000001.SZ"]]
