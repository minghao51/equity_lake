"""Tests for StockTwits fetcher."""

from datetime import date
from unittest.mock import Mock, patch

from equity_lake.sources.stocktwits import StockTwitsFetcher, _parse_timestamp


class TestParseTimestamp:
    def test_utc_format(self):
        result = _parse_timestamp("2026-06-14T12:00:00Z")
        assert result.year == 2026
        assert result.month == 6

    def test_invalid_returns_now(self):
        result = _parse_timestamp("invalid")
        assert result is not None


class TestStockTwitsFetcher:
    def test_disabled_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            fetcher = StockTwitsFetcher(tickers=["AAPL"])
            assert not fetcher.enabled
            result = fetcher.fetch(date(2026, 6, 14))
            assert result.is_empty()

    def test_no_tickers_returns_empty(self):
        with patch.dict("os.environ", {"STOCKTWITS_ENABLED": "true"}):
            fetcher = StockTwitsFetcher(tickers=[])
            result = fetcher.fetch(date(2026, 6, 14))
            assert result.is_empty()

    def test_fetch_messages_when_enabled(self, mock_httpx_client):
        mock_response_data = {
            "messages": [
                {
                    "id": 12345,
                    "body": "$AAPL looking strong today",
                    "created_at": "2026-06-14T12:00:00Z",
                    "user": {"username": "trader1", "watchlist_count": 100, "followers": 500},
                    "entities": {"sentiment": {"basic": "Bullish"}},
                }
            ]
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()
        mock_httpx_client.get.return_value = mock_response

        with (
            patch.dict("os.environ", {"STOCKTWITS_ENABLED": "true"}),
            patch("equity_lake.sources.stocktwits.httpx.Client", return_value=mock_httpx_client),
        ):
            fetcher = StockTwitsFetcher(tickers=["AAPL"], messages_per_symbol=30)
            result = fetcher.fetch(date(2026, 6, 14))

        assert not result.is_empty()
        assert result["source_type"][0] == "stocktwits"
        assert result["title"][0] == "$AAPL looking strong today"

    def test_old_messages_filtered(self, mock_httpx_client):
        mock_response_data = {
            "messages": [
                {
                    "id": 99999,
                    "body": "Old message",
                    "created_at": "2020-01-01T12:00:00Z",
                    "user": {"username": "old_user"},
                    "entities": {},
                }
            ]
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = Mock()
        mock_httpx_client.get.return_value = mock_response

        with (
            patch.dict("os.environ", {"STOCKTWITS_ENABLED": "true"}),
            patch("equity_lake.sources.stocktwits.httpx.Client", return_value=mock_httpx_client),
        ):
            fetcher = StockTwitsFetcher(tickers=["AAPL"])
            result = fetcher.fetch(date(2026, 6, 14))
            assert result.is_empty()

    def test_access_token_sent_as_query_param(self, mock_httpx_client):
        """STOCKTWITS_ACCESS_TOKEN is sent as the access_token query param."""
        mock_response = Mock()
        mock_response.json.return_value = {"messages": []}
        mock_response.raise_for_status = Mock()
        mock_httpx_client.get.return_value = mock_response

        with (
            patch.dict("os.environ", {"STOCKTWITS_ENABLED": "true", "STOCKTWITS_ACCESS_TOKEN": "token123"}),
            patch("equity_lake.sources.stocktwits.httpx.Client", return_value=mock_httpx_client),
        ):
            fetcher = StockTwitsFetcher(tickers=["AAPL"])
            assert fetcher.access_token == "token123"
            fetcher.fetch(date(2026, 6, 14))

        params = mock_httpx_client.get.call_args.kwargs["params"]
        assert params["access_token"] == "token123"
