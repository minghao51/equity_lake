"""Unit tests for JPX and KRX market fetchers."""

# Legacy test methods are not in the strict mypy application target.
# mypy: disable-error-code="no-untyped-def,import-untyped"

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import polars as pl

from equity_lake.core.config import TickerConfig
from equity_lake.core.config_models import MarketConfig, TickerMetadata
from equity_lake.sources import JPXEquityFetcher, KRXEquityFetcher
from equity_lake.sources.hk_sg import HKSGEquityFetcher
from equity_lake.sources.us import USEquityFetcher


def _ticker_config(market: str, exchange: str, symbols: list[str]) -> TickerConfig:
    return TickerConfig(
        markets={
            market: MarketConfig(
                currency="JPY",
                tickers=[TickerMetadata(symbol=symbol, name=symbol, exchange=exchange, sector="Tech") for symbol in symbols],
            )
        }
    )


def test_yfinance_fetch_paths_attach_venue_identity_and_keep_hk_sg_collision(monkeypatch):
    date_index = pd.DatetimeIndex(["2024-01-02"], name="Date")
    provider_frame = pd.DataFrame(
        {"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [100]},
        index=date_index,
    )
    monkeypatch.setattr("equity_lake.sources.base.yf.download", lambda *args, **kwargs: provider_frame)
    cases = [
        (USEquityFetcher(tickers=["US1"], ticker_config=_ticker_config("us", "NASDAQ", ["US1"])), ("us_equity", "XNAS", "US1")),
        (JPXEquityFetcher(tickers=["JP1"], ticker_config=_ticker_config("jpx", "TSE", ["JP1"])), ("jpx_equity", "XTKS", "JP1")),
        (HKSGEquityFetcher(hk_tickers=["HK1"], sg_tickers=[], ticker_config=_ticker_config("hk", "HKEX", ["HK1"])), ("hk_sg_equity", "XHKG", "HK1")),
    ]
    for fetcher, expected in cases:
        result = fetcher.fetch(date(2024, 1, 2))
        assert result.select("market", "mic", "symbol").rows() == [expected]
        assert result["identity_provenance"].to_list() == ["current_ticker_config"]

    dual_config = TickerConfig(
        markets={
            "hk": MarketConfig(currency="HKD", tickers=[TickerMetadata(symbol="DUP", name="HK", exchange="HKEX", sector="Tech")]),
            "sg": MarketConfig(currency="SGD", tickers=[TickerMetadata(symbol="DUP", name="SG", exchange="SGX", sector="Tech")]),
        }
    )
    dual_fetcher = HKSGEquityFetcher(hk_tickers=["DUP"], sg_tickers=["DUP"], ticker_config=dual_config)

    def fetch_dual_venue(_tickers, _start, _end):
        return [
            pd.DataFrame(
                {
                    "ticker": ["DUP"],
                    "date": [date(2024, 1, 2)],
                    "open": [10.0],
                    "high": [11.0],
                    "low": [9.0],
                    "close": [10.5],
                    "volume": [100],
                    "exchange": ["HKEX"],
                }
            ),
            pd.DataFrame(
                {
                    "ticker": ["DUP"],
                    "date": [date(2024, 1, 2)],
                    "open": [20.0],
                    "high": [21.0],
                    "low": [19.0],
                    "close": [20.5],
                    "volume": [200],
                    "exchange": ["SGX"],
                }
            ),
        ]

    monkeypatch.setattr(dual_fetcher, "_download_batch", fetch_dual_venue)
    dual_result = dual_fetcher.fetch(date(2024, 1, 2))
    assert dual_result.select("market", "mic", "symbol").unique().sort("mic").rows() == [
        ("hk_sg_equity", "XHKG", "DUP"),
        ("hk_sg_equity", "XSES", "DUP"),
    ]


def _yf_multi_ticker_frame(tickers: list[str]) -> pd.DataFrame:
    """A frame shaped like yf.download(group_by="ticker") output for N tickers."""
    per_ticker = {
        ticker: pd.DataFrame(
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
        for ticker in tickers
    }
    return pd.concat(per_ticker, axis=1, names=["Ticker", "Field"])


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
        from equity_lake.sources.jpx import _FALLBACK_TICKERS

        assert "7203.T" in _FALLBACK_TICKERS
        assert "6758.T" in _FALLBACK_TICKERS
        assert len(_FALLBACK_TICKERS) == 10

    @patch("equity_lake.sources.base.yf.download")
    def test_fetch_with_batching(self, mock_download):
        """Test that fetch processes data in batches."""
        mock_download.return_value = _yf_multi_ticker_frame(["7203.T", "6758.T", "9984.T"])

        tickers = ["7203.T", "6758.T", "9984.T"]
        fetcher = JPXEquityFetcher(tickers=tickers, batch_size=2, ticker_config=_ticker_config("jpx", "TSE", tickers))
        result = fetcher.fetch(date(2024, 1, 1))

        assert mock_download.call_count == 2
        assert not result.is_empty()
        assert "ticker" in result.columns

    @patch("equity_lake.sources.base.yf.download")
    def test_fetch_handles_no_data(self, mock_download):
        """Test that fetch handles empty response."""
        mock_download.return_value = pd.DataFrame()

        fetcher = JPXEquityFetcher(tickers=["7203.T"], batch_size=500)
        result = fetcher.fetch(date(2024, 1, 1))

        assert result.is_empty()

    @patch("equity_lake.sources.base.yf.download")
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

        fetcher = JPXEquityFetcher(tickers=["7203.T"], batch_size=500, ticker_config=_ticker_config("jpx", "TSE", ["7203.T"]))
        result = fetcher.fetch(date(2024, 1, 1))

        assert "open" in result.columns
        assert "high" in result.columns
        assert "low" in result.columns
        assert "close" in result.columns
        assert "adj_close" in result.columns
        assert "volume" in result.columns
        assert "ticker" in result.columns
        assert "date" in result.columns

    @patch("equity_lake.sources.base.yf.download")
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

        fetcher = JPXEquityFetcher(tickers=["7203.T"], batch_size=500, ticker_config=_ticker_config("jpx", "TSE", ["7203.T"]))
        result = fetcher.fetch(date(2024, 1, 1))

        assert not result.is_empty()
        assert result["ticker"][0] == "7203.T"


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
            fetcher = KRXEquityFetcher(tickers=["005930"], ticker_config=_ticker_config("krx", "KRX", ["005930"]))
            result = fetcher.fetch(date(2024, 1, 1))

        mock_module.DataReader.assert_called_once_with("005930", "2024-01-01", "2024-01-02")
        assert not result.is_empty()
        assert isinstance(result, pl.DataFrame)
        assert result["ticker"][0] == "005930"
