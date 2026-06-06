"""Unit tests for FinnhubSocialSentimentFetcher."""

import os
from datetime import date
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from equity_lake.core.schemas import SOCIAL_COLUMNS
from equity_lake.sources.sentiment import FinnhubSocialSentimentFetcher


@pytest.fixture
def mock_api_key():
    """Fixture providing a mock API key."""
    return "test_api_key_12345"


@pytest.fixture
def mock_response_reddit():
    """Fixture providing a mock Finnhub API response with Reddit sentiment."""
    return {
        "sentiment": {
            "reddit": {
                "mention": 1250,
                "positive": 800,
                "negative": 150,
            },
            "twitter": {
                "mention": 3400,
                "positive": 2100,
                "negative": 400,
            },
        }
    }


@pytest.fixture
def mock_response_no_data():
    """Fixture providing a mock Finnhub API response with no sentiment data."""
    return {"sentiment": {}}


@pytest.fixture
def sample_parsed_metrics():
    """Fixture providing sample parsed sentiment metrics."""
    return [
        {
            "ticker": "AAPL",
            "date": date(2024, 12, 1),
            "datetime": "2024-12-01 12:00:00",
            "source": "reddit",
            "mention_count": 1250,
            "positive_score": 800.0,
            "negative_score": 150.0,
            "score": 0.6842105263157895,  # (800-150)/(800+150)
            "social_metric": "mention_count",
        },
        {
            "ticker": "AAPL",
            "date": date(2024, 12, 1),
            "datetime": "2024-12-01 12:00:00",
            "source": "twitter",
            "mention_count": 3400,
            "positive_score": 2100.0,
            "negative_score": 400.0,
            "score": 0.6774193548387096,  # (2100-400)/(2100+400)
            "social_metric": "mention_count",
        },
    ]


class TestFinnhubSocialSentimentFetcher:
    """Test suite for FinnhubSocialSentimentFetcher."""

    def test_initialization_with_api_key(self, mock_api_key):
        """Test fetcher initialization with API key parameter."""
        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL", "GOOGL"],
        )

        assert fetcher.api_key == mock_api_key
        assert fetcher.tickers == ["AAPL", "GOOGL"]
        assert fetcher.retry_attempts == 3
        assert fetcher.retry_delay == 1.0
        assert fetcher.max_workers == 1

    def test_initialization_without_api_key(self):
        """Test fetcher initialization fails without API key."""
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ValueError, match="Finnhub API key not found"),
        ):
            FinnhubSocialSentimentFetcher(tickers=["AAPL"])

    def test_initialization_from_env(self, mock_api_key):
        """Test fetcher initialization reads API key from environment."""
        with patch.dict(os.environ, {"FINNHUB_API_KEY": mock_api_key}):
            fetcher = FinnhubSocialSentimentFetcher(tickers=["AAPL"])

            assert fetcher.api_key == mock_api_key

    def test_fetch_with_no_tickers(self, mock_api_key):
        """Test fetch returns empty DataFrame when no tickers configured."""
        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=[],
        )

        result = fetcher.fetch(date(2024, 12, 1))

        assert result.empty
        assert isinstance(result, pd.DataFrame)

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_fetch_sequential_success(
        self,
        mock_get,
        mock_api_key,
        mock_response_reddit,
    ):
        """Test successful sequential fetch of social sentiment."""
        # Setup mock response
        mock_response = Mock()
        mock_response.json.return_value = mock_response_reddit
        mock_get.return_value = mock_response

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
            max_workers=1,  # Sequential
        )

        result = fetcher.fetch(date(2024, 12, 1))

        assert not result.empty
        assert len(result) == 2  # reddit + twitter
        assert list(result.columns) == SOCIAL_COLUMNS
        assert result["ticker"].iloc[0] == "AAPL"
        assert result["date"].iloc[0] == date(2024, 12, 1)

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_fetch_parallel_success(
        self,
        mock_get,
        mock_api_key,
        mock_response_reddit,
    ):
        """Test successful parallel fetch of social sentiment."""
        # Setup mock response
        mock_response = Mock()
        mock_response.json.return_value = mock_response_reddit
        mock_get.return_value = mock_response

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL", "GOOGL", "MSFT"],
            max_workers=3,  # Parallel
        )

        result = fetcher.fetch(date(2024, 12, 1))

        assert not result.empty
        # 3 tickers * 2 sources (reddit + twitter) = 6 records
        assert len(result) == 6
        assert result["ticker"].nunique() == 3

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_fetch_handles_no_data(
        self,
        mock_get,
        mock_api_key,
        mock_response_no_data,
    ):
        """Test fetch handles API response with no sentiment data."""
        # Setup mock response
        mock_response = Mock()
        mock_response.json.return_value = mock_response_no_data
        mock_get.return_value = mock_response

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
        )

        result = fetcher.fetch(date(2024, 12, 1))

        assert result.empty

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_parse_sentiment_metric_reddit(
        self,
        mock_get,
        mock_api_key,
        mock_response_reddit,
    ):
        """Test parsing Reddit sentiment metric."""
        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
        )

        reddit_data = mock_response_reddit["sentiment"]["reddit"]
        result = fetcher._parse_sentiment_metric(
            reddit_data,
            "AAPL",
            date(2024, 12, 1),
            "reddit",
        )

        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["date"] == date(2024, 12, 1)
        assert result["source"] == "reddit"
        assert result["mention_count"] == 1250
        assert result["positive_score"] == 800.0
        assert result["negative_score"] == 150.0
        assert -1.0 <= result["score"] <= 1.0  # Normalized

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_parse_sentiment_metric_twitter(
        self,
        mock_get,
        mock_api_key,
        mock_response_reddit,
    ):
        """Test parsing Twitter sentiment metric."""
        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
        )

        twitter_data = mock_response_reddit["sentiment"]["twitter"]
        result = fetcher._parse_sentiment_metric(
            twitter_data,
            "AAPL",
            date(2024, 12, 1),
            "twitter",
        )

        assert result is not None
        assert result["ticker"] == "AAPL"
        assert result["source"] == "twitter"
        assert result["mention_count"] == 3400
        assert result["positive_score"] == 2100.0
        assert result["negative_score"] == 400.0

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_score_normalization(self, mock_get, mock_api_key):
        """Test that sentiment scores are properly normalized to -1 to 1 range."""
        # Create a response with equal positive and negative mentions
        mock_response_data = {
            "sentiment": {
                "reddit": {
                    "mention": 100,
                    "positive": 50,
                    "negative": 50,
                }
            }
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_get.return_value = mock_response

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
        )

        reddit_data = mock_response_data["sentiment"]["reddit"]
        result = fetcher._parse_sentiment_metric(
            reddit_data,
            "AAPL",
            date(2024, 12, 1),
            "reddit",
        )

        # Score should be 0.0 when positive == negative
        assert result["score"] == 0.0

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_score_all_positive(self, mock_get, mock_api_key):
        """Test score normalization when all mentions are positive."""
        mock_response_data = {
            "sentiment": {
                "reddit": {
                    "mention": 100,
                    "positive": 100,
                    "negative": 0,
                }
            }
        }

        mock_response = Mock()
        mock_response.json.return_value = mock_response_data
        mock_get.return_value = mock_response

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
        )

        reddit_data = mock_response_data["sentiment"]["reddit"]
        result = fetcher._parse_sentiment_metric(
            reddit_data,
            "AAPL",
            date(2024, 12, 1),
            "reddit",
        )

        # Score should be 1.0 when all positive
        assert result["score"] == 1.0

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_schema_compliance(self, mock_get, mock_api_key, mock_response_reddit):
        """Test that returned DataFrame complies with SOCIAL_COLUMNS schema."""
        mock_response = Mock()
        mock_response.json.return_value = mock_response_reddit
        mock_get.return_value = mock_response

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
        )

        result = fetcher.fetch(date(2024, 12, 1))

        # Check all required columns present
        assert all(col in result.columns for col in SOCIAL_COLUMNS)

        # Check no extra columns
        assert set(result.columns) == set(SOCIAL_COLUMNS)

    @patch("equity_lake.sources.sentiment.requests.get")
    def test_handles_api_failure_gracefully(self, mock_get, mock_api_key):
        """Test that fetcher handles API failure gracefully and returns empty DataFrame."""
        # Mock API failure
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = Exception("API Error")
        mock_get.return_value = mock_response

        fetcher = FinnhubSocialSentimentFetcher(
            api_key=mock_api_key,
            tickers=["AAPL"],
        )

        result = fetcher.fetch(date(2024, 12, 1))

        # Should return empty DataFrame on failure
        assert result.empty


class TestSchemaValidation:
    """Test schema validation for social sentiment data."""

    def test_social_columns_defined(self):
        """Test that SOCIAL_COLUMNS is properly defined."""
        expected_columns = [
            "ticker",
            "date",
            "datetime",
            "source",
            "mention_count",
            "positive_score",
            "negative_score",
            "score",
            "social_metric",
        ]

        assert expected_columns == SOCIAL_COLUMNS

    def test_validate_social_sentiment_schema(self):
        """Test schema validation with valid social sentiment data."""
        df = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "date": date(2024, 12, 1),
                    "datetime": "2024-12-01 12:00:00",
                    "source": "reddit",
                    "mention_count": 1000,
                    "positive_score": 600.0,
                    "negative_score": 200.0,
                    "score": 0.5,
                    "social_metric": "mention_count",
                }
            ]
        )

        from equity_lake.ingestion.writers import validate_schema

        assert validate_schema(df, "us_social_sentiment") is True

    def test_validate_social_sentiment_missing_columns(self):
        """Test schema validation with missing required columns."""
        df = pd.DataFrame(
            [
                {
                    "ticker": "AAPL",
                    "date": date(2024, 12, 1),
                    # Missing required columns
                }
            ]
        )

        from equity_lake.ingestion.writers import validate_schema

        assert validate_schema(df, "us_social_sentiment") is False
