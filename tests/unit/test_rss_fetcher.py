"""Tests for RSS news fetcher."""

from datetime import date, datetime
from unittest.mock import Mock, patch

import httpx

from equity_lake.sources.rss import RSSNewsFetcher, _extract_body, _parse_published


class TestParsePublished:
    def test_struct_timestamp(self):
        entry = {"published_parsed": (2026, 6, 14, 12, 0, 0, 0, 0, 0)}
        result = _parse_published(entry)
        assert result.year == 2026
        assert result.month == 6
        assert result.day == 14

    def test_fallback_datetime(self):
        fallback = datetime(2026, 6, 13, 9, 0, 0)
        result = _parse_published({}, fallback=fallback)
        assert result == fallback

    def test_string_parsing(self):
        entry = {"published": "Sat, 14 Jun 2026 12:00:00 +0000"}
        result = _parse_published(entry)
        assert result.year == 2026
        assert result.month == 6


class TestExtractBody:
    def test_content_list(self):
        entry = {"content": [{"value": "Article body text"}]}
        assert _extract_body(entry) == "Article body text"

    def test_summary_string(self):
        entry = {"summary": "Summary text"}
        assert _extract_body(entry) == "Summary text"

    def test_empty(self):
        assert _extract_body({}) == ""


class TestRSSNewsFetcher:
    def test_no_feeds_returns_empty(self):
        with patch("equity_lake.sources.rss._load_feed_config", return_value=[]):
            fetcher = RSSNewsFetcher()
            result = fetcher.fetch(date(2026, 6, 14))
            assert result.is_empty()

    def test_fetch_articles(self, mock_httpx_client):
        mock_feeds = [{"name": "test_feed", "url": "https://example.com/rss", "category": ["stock"]}]

        mock_parsed = Mock()
        mock_parsed.bozo = False
        mock_parsed.entries = [
            {
                "title": "AAPL hits new high",
                "link": "https://example.com/article1",
                "author": "John Doe",
                "published_parsed": (2026, 6, 14, 10, 0, 0, 0, 0, 0),
                "content": [{"value": "Apple stock reached a new all-time high today."}],
            },
            {
                "title": "Old article",
                "link": "https://example.com/article2",
                "published_parsed": (2026, 6, 10, 10, 0, 0, 0, 0, 0),
                "summary": "This is an old article.",
            },
        ]

        mock_response = Mock()
        mock_response.content = b"<rss/>"
        mock_response.status_code = 200
        mock_response.is_error = False
        mock_httpx_client.get.return_value = mock_response

        with (
            patch("equity_lake.sources.rss._load_feed_config", return_value=mock_feeds),
            patch("equity_lake.sources.rss.feedparser.parse", return_value=mock_parsed),
            patch("equity_lake.sources.rss.httpx.Client", return_value=mock_httpx_client),
        ):
            fetcher = RSSNewsFetcher()
            result = fetcher.fetch(date(2026, 6, 14))

        assert not result.is_empty()
        assert result.height == 1
        assert result["title"][0] == "AAPL hits new high"
        assert result["source_type"][0] == "rss"
        assert result["source_name"][0] == "test_feed"

    def test_fetch_error_returns_empty_without_feedparser_url_fallback(self, mock_httpx_client):
        """Fetch errors must not fall back to feedparser's own un-timed HTTP."""
        mock_feeds = [{"name": "test_feed", "url": "https://example.com/rss"}]
        mock_httpx_client.get.side_effect = httpx.ConnectError("connection refused")

        with (
            patch("equity_lake.sources.rss._load_feed_config", return_value=mock_feeds),
            patch("equity_lake.sources.rss.feedparser.parse") as mock_parse,
            patch("equity_lake.sources.rss.httpx.Client", return_value=mock_httpx_client),
        ):
            fetcher = RSSNewsFetcher(retry_delay=0.01)
            result = fetcher.fetch(date(2026, 6, 14))

        assert result.is_empty()
        mock_parse.assert_not_called()


class TestRSSStatusClassification:
    """HTTP status handling must mirror sources/base.py's retry rule."""

    @staticmethod
    def _fetch_with_response(mock_httpx_client, response):  # type: ignore[no-untyped-def]
        mock_feeds = [{"name": "test_feed", "url": "https://example.com/rss"}]
        mock_httpx_client.get.return_value = response
        with (
            patch("equity_lake.sources.rss._load_feed_config", return_value=mock_feeds),
            patch("equity_lake.sources.rss.feedparser.parse") as mock_parse,
            patch("equity_lake.sources.rss.httpx.Client", return_value=mock_httpx_client),
        ):
            fetcher = RSSNewsFetcher(retry_delay=0.01)
            result = fetcher.fetch(date(2026, 6, 14))
        return result, mock_parse

    def test_server_error_is_retried_then_degrades(self, mock_httpx_client):
        """5xx must surface as TransientError so tenacity retries it (not raise_for_status's
        httpx.HTTPStatusError, which the retry wrapper does not convert)."""
        result, mock_parse = self._fetch_with_response(mock_httpx_client, Mock(status_code=503))

        assert result.is_empty()
        assert mock_httpx_client.get.call_count == 3  # default retry_attempts
        mock_parse.assert_not_called()

    def test_rate_limit_and_request_timeout_are_retryable(self, mock_httpx_client):
        for status in (408, 429):
            mock_httpx_client.reset_mock()
            result, mock_parse = self._fetch_with_response(mock_httpx_client, Mock(status_code=status))
            assert result.is_empty()
            assert mock_httpx_client.get.call_count == 3
            mock_parse.assert_not_called()

    def test_client_error_is_not_retried(self, mock_httpx_client):
        """Permanent 4xx propagates as httpx.HTTPStatusError after a single attempt."""
        response = Mock(status_code=404, is_error=True)
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404 Not Found",
            request=httpx.Request("GET", "https://example.com/rss"),
            response=httpx.Response(404),
        )
        result, mock_parse = self._fetch_with_response(mock_httpx_client, response)

        assert result.is_empty()
        assert mock_httpx_client.get.call_count == 1  # no retries for permanent 4xx
        mock_parse.assert_not_called()
