"""Tests for RSS news fetcher."""

from datetime import date, datetime
from unittest.mock import Mock, patch

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
        mock_response.raise_for_status = Mock()
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
