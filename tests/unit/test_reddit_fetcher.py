"""Tests for Reddit JSON API fetcher."""

import json
from datetime import date
from unittest.mock import Mock, patch

import pytest

from equity_lake.sources.reddit import RedditFetcher, _build_user_agent


class TestBuildUserAgent:
    def test_custom_user_agent(self):
        with patch.dict("os.environ", {"REDDIT_USER_AGENT": "macos:myapp:1.0 (by u/testuser)"}):
            ua = _build_user_agent()
            assert "macos" in ua
            assert "testuser" in ua

    def test_missing_user_agent_raises(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="REDDIT_USER_AGENT"):
            _build_user_agent()

    def test_invalid_platform_raises(self):
        with patch.dict("os.environ", {"REDDIT_USER_AGENT": "web:myapp:1.0 (by u/testuser)"}), pytest.raises(ValueError, match="must start with"):
            _build_user_agent()


@pytest.fixture(autouse=True)
def _set_reddit_user_agent(monkeypatch):
    monkeypatch.setenv("REDDIT_USER_AGENT", "macos:equity-test:1.0 (by u/testuser)")


class TestRedditFetcher:
    def test_no_subreddits_returns_empty(self):
        with patch("equity_lake.sources.reddit._load_social_config", return_value={}):
            fetcher = RedditFetcher()
            result = fetcher.fetch(date(2026, 6, 14))
            assert result.is_empty()

    def test_fetch_posts(self, mock_httpx_client):
        mock_config = {
            "reddit": {
                "subreddits": [
                    {"name": "wallstreetbets", "post_limit": 5},
                ]
            }
        }

        mock_response_data = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "AAPL to the moon",
                            "selftext": "Bullish on Apple",
                            "author": "trader1",
                            "created_utc": 1718352000,
                            "score": 500,
                            "upvote_ratio": 0.9,
                            "num_comments": 200,
                            "permalink": "/r/wallstreetbets/comments/abc/aapl_to_the_moon/",
                            "link_flair_text": "DD",
                        }
                    }
                ]
            }
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_response.headers = {}
        mock_response.raise_for_status = Mock()
        mock_httpx_client.get.return_value = mock_response

        with (
            patch("equity_lake.sources.reddit._load_social_config", return_value=mock_config),
            patch("equity_lake.sources.reddit.httpx.Client", return_value=mock_httpx_client),
            patch("equity_lake.sources.reddit.time.sleep"),
        ):
            fetcher = RedditFetcher()
            result = fetcher.fetch(date(2024, 6, 14))

        assert not result.is_empty()
        assert result["source_type"][0] == "reddit"
        assert result["source_name"][0] == "r/wallstreetbets"
        assert result["title"][0] == "AAPL to the moon"

        metadata = json.loads(result["source_metadata"][0])
        assert metadata["score"] == 500
        assert metadata["subreddit"] == "wallstreetbets"
