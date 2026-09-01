"""Tests for ingestion.router market-to-fetcher routing."""

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from equity_lake.ingestion.router import fetch_market_data


class TestFetchMarketDataUnknownMarket:
    def test_unknown_market_returns_none(self) -> None:
        result = fetch_market_data("unknown", date(2026, 6, 2))
        assert result is None


class TestFetchMarketDataMacro:
    @patch("equity_lake.sources.macro.MacroFetcher")
    def test_macro_returns_none_on_empty(self, mock_fetcher_cls) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = pl.DataFrame()
        mock_fetcher_cls.return_value = mock_fetcher

        result = fetch_market_data("macro", date(2026, 6, 2))
        assert result is None

    @patch("equity_lake.sources.macro.MacroFetcher")
    def test_macro_returns_df_on_success(self, mock_fetcher_cls) -> None:
        df = pl.DataFrame({"indicator": ["DGS10"], "value": [4.5], "date": [date(2026, 6, 2)], "source": ["yfinance"], "updated_at": ["2026-06-02"]})
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.return_value = df
        mock_fetcher_cls.return_value = mock_fetcher

        result = fetch_market_data("macro", date(2026, 6, 2))
        assert result is not None
        assert len(result) == 1


class TestFetchMarketDataNewsMissingKey:
    def test_news_raises_without_api_key(self, monkeypatch) -> None:
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        with pytest.raises(OSError, match="FINNHUB_API_KEY"):
            fetch_market_data("us_news", date(2026, 6, 2))


class TestFetchMarketDataSentimentMissingKey:
    def test_sentiment_raises_without_api_key(self, monkeypatch) -> None:
        monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
        with pytest.raises(OSError, match="FINNHUB_API_KEY"):
            fetch_market_data("us_social_sentiment", date(2026, 6, 2))


class TestFetchMarketDataWithFetcherError:
    def test_fetcher_exception_returns_none(self) -> None:
        mock_fetcher = MagicMock()
        mock_fetcher.fetch.side_effect = RuntimeError("API down")
        # MARKET_REGISTRY now holds the factory callables directly, so patch the
        # registry entry rather than the module-level function object.
        with patch.dict(
            "equity_lake.ingestion.router.MARKET_REGISTRY",
            {"us_equity": lambda **kw: mock_fetcher},
        ):
            result = fetch_market_data("us_equity", date(2026, 6, 2))
        assert result is None
