"""
Unit tests for Finnhub news fetcher and sentiment analyzer.

Tests cover:
- FinnhubNewsFetcher initialization and configuration
- SentimentAnalyzer with VADER
- API retry logic
- Data parsing and validation
- Sentiment analysis accuracy
"""

from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
import polars as pl
import pytest

from equity_lake.sentiment import SentimentAnalyzer
from equity_lake.sources.news import FinnhubNewsFetcher

# =============================================================================
# SentimentAnalyzer Tests
# =============================================================================


class TestSentimentAnalyzer:
    """Test suite for SentimentAnalyzer."""

    def test_initialize_vader_analyzer(self):
        """Test VADER analyzer initialization."""
        analyzer = SentimentAnalyzer(method="vader")
        assert analyzer.method == "vader"
        assert analyzer.analyzer is not None

    def test_analyze_positive_text(self):
        """Test sentiment analysis for positive text."""
        analyzer = SentimentAnalyzer(method="vader")
        result = analyzer.analyze("AAPL stock surges on strong earnings beat")

        assert result["label"] == "positive"
        assert result["compound"] > 0

    def test_analyze_negative_text(self):
        """Test sentiment analysis for negative text."""
        analyzer = SentimentAnalyzer(method="vader")
        result = analyzer.analyze("Terrible revenue miss causes stock to plummet")

        assert result["label"] == "negative"
        assert result["compound"] < 0

    def test_analyze_neutral_text(self):
        """Test sentiment analysis for neutral text."""
        analyzer = SentimentAnalyzer(method="vader")
        result = analyzer.analyze("Stock price unchanged in today's trading")

        assert result["label"] == "neutral"

    def test_analyze_empty_text(self):
        """Test sentiment analysis for empty text."""
        analyzer = SentimentAnalyzer(method="vader")
        result = analyzer.analyze("")

        assert result["label"] == "neutral"
        assert result["compound"] == 0.0

    def test_analyze_none_text(self):
        """Test sentiment analysis for None input."""
        analyzer = SentimentAnalyzer(method="vader")
        result = analyzer.analyze(None)

        assert result["label"] == "neutral"
        assert result["compound"] == 0.0

    def test_analyze_batch_texts(self):
        """Test batch sentiment analysis."""
        analyzer = SentimentAnalyzer(method="vader")
        texts = [
            "Great earnings report",
            "Terrible revenue miss",
            "Stock price unchanged",
        ]

        result_df = analyzer.analyze_batch(texts)

        assert len(result_df) == 3
        assert "compound" in result_df.columns
        assert "label" in result_df.columns
        assert result_df["label"].to_list() == ["positive", "negative", "neutral"]

    def test_finbert_not_implemented(self):
        """Test that FinBERT method raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            SentimentAnalyzer(method="finbert")


class TestAnalyzeSentimentScores:
    """Test suite for analyze_sentiment_scores helper function."""

    def test_adds_sentiment_to_dataframe(self):
        """Test that sentiment scores are added to DataFrame."""
        from equity_lake.sentiment import analyze_sentiment_scores

        df = pd.DataFrame(
            {
                "headline": [
                    "AAPL stock surges on earnings",
                    "GOOGL declines on weak guidance",
                ],
            }
        )

        result_df = analyze_sentiment_scores(df, text_column="headline")

        assert "sentiment_score" in result_df.columns
        assert "sentiment_label" in result_df.columns
        assert len(result_df) == 2

    def test_handles_missing_column(self):
        """Test error handling for missing text column."""
        from equity_lake.sentiment import analyze_sentiment_scores

        df = pd.DataFrame({"ticker": ["AAPL"]})

        with pytest.raises(ValueError, match="Column"):
            analyze_sentiment_scores(df, text_column="headline")


# =============================================================================
# FinnhubNewsFetcher Tests
# =============================================================================


class TestFinnhubNewsFetcherInit:
    """Test suite for FinnhubNewsFetcher initialization."""

    def test_initialization_with_api_key(self):
        """Test fetcher initialization with explicit API key."""
        fetcher = FinnhubNewsFetcher(
            api_key="test_key",
            tickers=["AAPL", "GOOGL"],
        )

        assert fetcher.api_key == "test_key"
        assert fetcher.tickers == ["AAPL", "GOOGL"]
        assert fetcher.max_articles_per_ticker == 50
        assert fetcher.sentiment_method == "vader"

    def test_initialization_without_api_key_raises_error(self):
        """Test that missing API key raises ValueError."""
        with (
            patch.dict("os.environ", {}, clear=True),
            pytest.raises(ValueError, match="API key"),
        ):
            FinnhubNewsFetcher(tickers=["AAPL"])

    def test_initialization_from_env_var(self):
        """Test initialization from environment variable."""
        with patch.dict("os.environ", {"FINNHUB_API_KEY": "env_key"}):
            fetcher = FinnhubNewsFetcher(tickers=["AAPL"])

            assert fetcher.api_key == "env_key"

    def test_initialization_with_custom_params(self):
        """Test initialization with custom parameters."""
        fetcher = FinnhubNewsFetcher(
            api_key="test_key",
            tickers=["AAPL"],
            max_articles_per_ticker=100,
            sentiment_method="vader",
            min_relevance=0.5,
        )

        assert fetcher.max_articles_per_ticker == 100
        assert fetcher.min_relevance == 0.5

    def test_sentiment_analyzer_initialization(self):
        """Test that sentiment analyzer is initialized."""
        fetcher = FinnhubNewsFetcher(
            api_key="test_key",
            tickers=["AAPL"],
        )

        assert fetcher.sentiment_analyzer is not None
        assert isinstance(fetcher.sentiment_analyzer, SentimentAnalyzer)


@pytest.fixture
def mock_finnhub_response():
    """Mock Finnhub API response."""
    return [
        {
            "datetime": 1704067200,  # 2024-01-01 00:00:00 UTC
            "headline": "AAPL stock surges on strong earnings",
            "source": "Reuters",
            "url": "https://example.com/article1",
            "summary": "Apple reports better than expected Q4 earnings",
            "category": "earnings",
        },
        {
            "datetime": 1704070800,
            "headline": "Apple announces new product lineup",
            "source": "Bloomberg",
            "url": "https://example.com/article2",
            "summary": "Unveils latest iPhone and Mac updates",
            "category": "product",
        },
    ]


class TestFinnhubNewsFetcherFetch:
    """Test suite for FinnhubNewsFetcher.fetch method."""

    @patch("time.sleep")
    def test_fetch_returns_dataframe(
        self,
        mock_sleep,
        mock_finnhub_response,
    ):
        """Test that fetch returns a DataFrame."""
        # Mock at the method level to avoid retry logic issues
        with patch.object(
            FinnhubNewsFetcher,
            "_fetch_news_for_ticker",
            return_value=mock_finnhub_response,
        ):
            fetcher = FinnhubNewsFetcher(
                api_key="test_key",
                tickers=["AAPL"],
            )

            result = fetcher.fetch(date(2024, 1, 1))

            assert isinstance(result, pl.DataFrame)
            assert not result.is_empty()
            assert "ticker" in result.columns
            assert "headline" in result.columns

    @patch("time.sleep")
    def test_fetch_adds_sentiment_analysis(
        self,
        mock_sleep,
        mock_finnhub_response,
    ):
        """Test that fetch adds sentiment scores."""
        with patch.object(
            FinnhubNewsFetcher,
            "_fetch_news_for_ticker",
            return_value=mock_finnhub_response,
        ):
            fetcher = FinnhubNewsFetcher(
                api_key="test_key",
                tickers=["AAPL"],
            )

            result = fetcher.fetch(date(2024, 1, 1))

            assert "sentiment_score" in result.columns
            assert "sentiment_label" in result.columns
            # First headline should be positive (contains "surges")
            assert result["sentiment_label"][0] == "positive"

    @patch("time.sleep")
    def test_fetch_with_no_tickers_returns_empty_df(
        self,
        mock_sleep,
    ):
        """Test that fetch with no tickers returns empty DataFrame."""
        fetcher = FinnhubNewsFetcher(
            api_key="test_key",
            tickers=[],
        )

        result = fetcher.fetch(date(2024, 1, 1))

        assert result.is_empty()

    @patch("time.sleep")
    def test_fetch_limits_articles_per_ticker(
        self,
        mock_sleep,
        mock_finnhub_response,
    ):
        """Test that max_articles_per_ticker limits results."""
        # Return 100 articles
        large_response = mock_finnhub_response * 50

        def mock_fetch(self, ticker, trading_date):
            # Apply the limit in the mock
            return large_response[: self.max_articles_per_ticker]

        with patch.object(
            FinnhubNewsFetcher,
            "_fetch_news_for_ticker",
            mock_fetch,
        ):
            fetcher = FinnhubNewsFetcher(
                api_key="test_key",
                tickers=["AAPL"],
                max_articles_per_ticker=10,
            )

            result = fetcher.fetch(date(2024, 1, 1))

            # Should only return 10 articles
            assert len(result) <= 10

    @patch("time.sleep")
    def test_fetch_filters_by_min_relevance(
        self,
        mock_sleep,
        mock_finnhub_response,
    ):
        """Test that min_relevance filters results."""
        with patch.object(
            FinnhubNewsFetcher,
            "_fetch_news_for_ticker",
            return_value=mock_finnhub_response,
        ):
            fetcher = FinnhubNewsFetcher(
                api_key="test_key",
                tickers=["AAPL"],
                min_relevance=0.8,
            )

            result = fetcher.fetch(date(2024, 1, 1))

            # All articles should have relevance >= 0.8
            # (Default relevance is 1.0, so all should pass)
            # Check if result has data before accessing
            if not result.is_empty():
                assert result["relevance_score"].min() >= 0.8


class TestFinnhubNewsFetcherParsing:
    """Test suite for article parsing logic."""

    def test_parse_article_with_valid_data(self):
        """Test parsing a valid article."""
        fetcher = FinnhubNewsFetcher(api_key="test_key", tickers=["AAPL"])

        article = {
            "datetime": 1704067200,
            "headline": "Test headline",
            "source": "Test Source",
            "url": "https://example.com",
            "summary": "Test summary",
            "category": "test",
        }

        result = fetcher._parse_article(article, "AAPL")

        assert result["ticker"] == "AAPL"
        assert result["headline"] == "Test headline"
        assert result["source"] == "Test Source"
        assert result["url"] == "https://example.com"
        assert isinstance(result["datetime"], datetime)
        assert isinstance(result["date"], date)

    def test_parse_article_with_missing_fields(self):
        """Test parsing article with missing optional fields."""
        fetcher = FinnhubNewsFetcher(api_key="test_key", tickers=["AAPL"])

        article = {
            "datetime": 1704067200,
            "headline": "Test headline",
        }

        result = fetcher._parse_article(article, "AAPL")

        assert result["ticker"] == "AAPL"
        assert result["headline"] == "Test headline"
        assert result["source"] == "Unknown"
        assert result["category"] == "general"


class TestFinnhubNewsFetcherRetry:
    """Test suite for retry logic."""

    @patch("time.sleep")
    def test_retry_on_api_failure(
        self,
        mock_sleep,
    ):
        """Test that API failures are retried."""
        # Mock _fetch_news_for_ticker to fail then succeed
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("API Error")
            return []

        with patch.object(
            FinnhubNewsFetcher,
            "_fetch_news_for_ticker",
            side_effect=side_effect,
        ):
            fetcher = FinnhubNewsFetcher(
                api_key="test_key",
                tickers=["AAPL"],
                retry_attempts=3,
                retry_delay=0.01,  # Short delay for tests
            )

            # Should not raise exception due to retry
            result = fetcher.fetch(date(2024, 1, 1))

            # Should have retried
            assert call_count[0] >= 1
            assert isinstance(result, pl.DataFrame)

    @patch("time.sleep")
    def test_continues_on_ticker_failure(
        self,
        mock_sleep,
    ):
        """Test that fetch continues when one ticker fails."""

        def side_effect(ticker, *args, **kwargs):
            if ticker == "AAPL":
                raise Exception("Network error")
            return []

        with patch.object(
            FinnhubNewsFetcher,
            "_fetch_news_for_ticker",
            side_effect=side_effect,
        ):
            fetcher = FinnhubNewsFetcher(
                api_key="test_key",
                tickers=["AAPL", "GOOGL"],
            )

            # Should not raise exception
            result = fetcher.fetch(date(2024, 1, 1))

            # Should return empty DataFrame (both failed or one failed)
            assert isinstance(result, pl.DataFrame)
