"""
Integration tests for news ingestion module.

These tests can optionally make real API calls to Finnhub.
Most tests use mocking to avoid API quota usage.
"""

import os
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from equity_lake.ingestion.sources.news import FinnhubNewsFetcher
from equity_lake.ingestion.writers import validate_schema, write_to_partitioned_parquet
from equity_lake.core.runtime import NEWS_COLUMNS, US_NEWS_DIR


# =============================================================================
# Schema Validation Tests
# =============================================================================


class TestNewsSchemaValidation:
    """Test schema validation for news data."""

    def test_valid_news_schema_passes(self):
        """Test that valid news DataFrame passes validation."""
        df = pd.DataFrame({
            "ticker": ["AAPL", "GOOGL"],
            "date": [date(2024, 1, 1), date(2024, 1, 1)],
            "datetime": pd.to_datetime(["2024-01-01 10:00", "2024-01-01 11:00"]),
            "source": ["Reuters", "Bloomberg"],
            "headline": ["AAPL stock rises", "GOOGL falls"],
            "summary": ["Strong earnings", "Weak guidance"],
            "url": ["https://example.com/1", "https://example.com/2"],
            "category": ["earnings", "guidance"],
            "sentiment_score": [0.5, -0.3],
            "sentiment_label": ["positive", "negative"],
            "relevance_score": [1.0, 0.9],
        })

        result = validate_schema(df, "us_news")

        assert result is True

    def test_missing_columns_fails_validation(self):
        """Test that missing required columns fails validation."""
        df = pd.DataFrame({
            "ticker": ["AAPL"],
            # Missing required columns
        })

        result = validate_schema(df, "us_news")

        assert result is False

    def test_all_null_column_warns(self):
        """Test that all-null columns are flagged."""
        # Create DataFrame with one ticker and null values for other columns
        data = {col: [None] for col in NEWS_COLUMNS}
        data["ticker"] = ["AAPL"]
        df = pd.DataFrame(data)

        # Should still return True (warning only, not error)
        result = validate_schema(df, "us_news")

        assert result is True


# =============================================================================
# Parquet Write Tests
# =============================================================================


class TestNewsParquetWrite:
    """Test writing news data to partitioned Parquet."""

    def test_write_to_partitioned_parquet(self, tmp_path):
        """Test writing news data to Hive-partitioned Parquet."""
        # Patch the US_NEWS_DIR to use temp path
        with patch("equity_lake.ingestion.writers.US_NEWS_DIR", tmp_path):
            df = pd.DataFrame({
                "ticker": ["AAPL", "GOOGL"],
                "date": [date(2024, 1, 1), date(2024, 1, 1)],
                "datetime": pd.to_datetime(["2024-01-01 10:00", "2024-01-01 11:00"]),
                "source": ["Reuters", "Bloomberg"],
                "headline": ["Test headline 1", "Test headline 2"],
                "summary": ["Test summary 1", "Test summary 2"],
                "url": ["https://example.com/1", "https://example.com/2"],
                "category": ["general", "general"],
                "sentiment_score": [0.5, -0.3],
                "sentiment_label": ["positive", "negative"],
                "relevance_score": [1.0, 0.9],
            })

            success = write_to_partitioned_parquet(
                df,
                "us_news",
                date(2024, 1, 1),
                dry_run=False,
            )

            assert success is True

            # Check file was created
            partition_dir = tmp_path / "date=2024-01-01"
            parquet_file = partition_dir / "2024-01-01.parquet"
            assert parquet_file.exists()

            # Verify data can be read back
            read_df = pd.read_parquet(parquet_file)
            assert len(read_df) == 2
            assert "ticker" in read_df.columns

    def test_dry_run_skips_write(self, tmp_path):
        """Test that dry run mode skips actual write."""
        with patch("equity_lake.ingestion.writers.US_NEWS_DIR", tmp_path):
            df = pd.DataFrame({
                "ticker": ["AAPL"],
                "date": [date(2024, 1, 1)],
                "datetime": pd.to_datetime(["2024-01-01 10:00"]),
                "source": ["Reuters"],
                "headline": ["Test"],
                "summary": ["Test"],
                "url": ["https://example.com"],
                "category": ["general"],
                "sentiment_score": [0.5],
                "sentiment_label": ["positive"],
                "relevance_score": [1.0],
            })

            success = write_to_partitioned_parquet(
                df,
                "us_news",
                date(2024, 1, 1),
                dry_run=True,
            )

            assert success is True

            # File should NOT exist in dry run mode
            partition_dir = tmp_path / "date=2024-01-01"
            assert not partition_dir.exists()

    def test_deduplication_by_url(self, tmp_path):
        """Test that duplicate articles (by URL) are skipped."""
        with patch("equity_lake.ingestion.writers.US_NEWS_DIR", tmp_path):
            df1 = pd.DataFrame({
                "ticker": ["AAPL", "GOOGL"],
                "date": [date(2024, 1, 1), date(2024, 1, 1)],
                "datetime": pd.to_datetime(["2024-01-01 10:00", "2024-01-01 11:00"]),
                "source": ["Reuters", "Bloomberg"],
                "headline": ["Test 1", "Test 2"],
                "summary": ["Summary 1", "Summary 2"],
                "url": ["https://example.com/1", "https://example.com/2"],
                "category": ["general", "general"],
                "sentiment_score": [0.5, -0.3],
                "sentiment_label": ["positive", "negative"],
                "relevance_score": [1.0, 0.9],
            })

            # Write initial data
            write_to_partitioned_parquet(
                df1,
                "us_news",
                date(2024, 1, 1),
                dry_run=False,
            )

            # Try to write duplicate data (same URLs)
            df2 = pd.DataFrame({
                "ticker": ["AAPL", "GOOGL"],
                "date": [date(2024, 1, 1), date(2024, 1, 1)],
                "datetime": pd.to_datetime(["2024-01-01 10:00", "2024-01-01 11:00"]),
                "source": ["Reuters", "Bloomberg"],
                "headline": ["Test 1", "Test 2"],
                "summary": ["Summary 1", "Summary 2"],
                "url": ["https://example.com/1", "https://example.com/2"],
                "category": ["general", "general"],
                "sentiment_score": [0.5, -0.3],
                "sentiment_label": ["positive", "negative"],
                "relevance_score": [1.0, 0.9],
            })

            # Should skip duplicates
            success = write_to_partitioned_parquet(
                df2,
                "us_news",
                date(2024, 1, 1),
                dry_run=False,
            )

            assert success is True

            # Verify only 2 rows (not 4)
            partition_dir = tmp_path / "date=2024-01-01"
            parquet_file = partition_dir / "2024-01-01.parquet"
            read_df = pd.read_parquet(parquet_file)
            assert len(read_df) == 2


# =============================================================================
# End-to-End Tests (with mocking)
# =============================================================================


@pytest.mark.integration
class TestNewsIngestionE2E:
    """End-to-end tests for news ingestion with mocked API."""

    @patch("time.sleep")
    def test_full_ingestion_flow(
        self,
        mock_sleep,
        tmp_path,
    ):
        """Test full flow from API fetch to Parquet write."""
        # Create temp news directory
        news_dir = tmp_path / "us_news"
        news_dir.mkdir()

        # Patch US_NEWS_DIR to return our temp path
        import equity_lake.ingestion.writers as writers_module
        original_dir = writers_module.US_NEWS_DIR
        writers_module.US_NEWS_DIR = news_dir

        try:
            # Mock _fetch_news_for_ticker to return test data
            mock_articles = [
                {
                    "ticker": "AAPL",
                    "date": date(2024, 1, 1),
                    "datetime": datetime(2024, 1, 1, 10, 0),
                    "headline": "AAPL stock surges on earnings",
                    "source": "Reuters",
                    "url": "https://example.com/article1",
                    "summary": "Strong Q4 results",
                    "category": "earnings",
                    "sentiment_score": 0.5,
                    "sentiment_label": "positive",
                    "relevance_score": 1.0,
                },
            ]

            with patch.object(
                FinnhubNewsFetcher,
                "_fetch_news_for_ticker",
                return_value=mock_articles,
            ):
                # Fetch
                fetcher = FinnhubNewsFetcher(
                    api_key="test_key",
                    tickers=["AAPL"],
                )
                df = fetcher.fetch(date(2024, 1, 1))

                # Write
                success = write_to_partitioned_parquet(
                    df,
                    "us_news",
                    date(2024, 1, 1),
                    dry_run=False,
                )

                assert success is True
                assert not df.empty

                # Verify file was created
                parquet_file = news_dir / "date=2024-01-01" / "2024-01-01.parquet"
                assert parquet_file.exists()

                # Verify data
                read_df = pd.read_parquet(parquet_file)
                assert len(read_df) >= 1
                assert "sentiment_score" in read_df.columns
        finally:
            # Restore original
            writers_module.US_NEWS_DIR = original_dir


# =============================================================================
# Optional Real API Tests
# =============================================================================


@pytest.mark.skipif(
    not os.getenv("FINNHUB_API_KEY"),
    reason="FINNHUB_API_KEY not set"
)
@pytest.mark.integration
class TestRealFinnhubAPI:
    """Tests with real Finnhub API (requires API key)."""

    def test_real_api_call(self):
        """Test actual API call with real credentials."""
        api_key = os.getenv("FINNHUB_API_KEY")

        fetcher = FinnhubNewsFetcher(
            api_key=api_key,
            tickers=["AAPL"],
            max_articles_per_ticker=5,
        )

        # Fetch recent news (within last 2 days)
        from datetime import timedelta
        trading_date = date.today() - timedelta(days=2)

        result = fetcher.fetch(trading_date)

        # Should return some data
        assert isinstance(result, pd.DataFrame)
        # May be empty if no news on that date

        if not result.empty:
            assert "ticker" in result.columns
            assert "headline" in result.columns
            assert "sentiment_score" in result.columns


@pytest.mark.skipif(
    not os.getenv("FINNHUB_API_KEY"),
    reason="FINNHUB_API_KEY not set"
)
@pytest.mark.integration
class TestSentimentAccuracy:
    """Test sentiment accuracy on real headlines."""

    def test_sentiment_on_real_headlines(self):
        """Test sentiment analyzer on real financial headlines."""
        from equity_lake.sentiment import SentimentAnalyzer

        analyzer = SentimentAnalyzer(method="vader")

        # Known positive examples
        positive_headlines = [
            "Apple beats earnings estimates by 15%",
            "Stock surges on strong revenue growth",
            "Company announces dividend increase",
        ]

        # Known negative examples
        negative_headlines = [
            "Tech giant misses earnings expectations",
            "Stock plunges on weak guidance",
            "Company cuts workforce by 10%",
        ]

        # Test positive headlines
        for headline in positive_headlines:
            result = analyzer.analyze(headline)
            assert result["compound"] > 0, f"Failed for: {headline}"
            assert result["label"] == "positive", f"Failed for: {headline}"

        # Test negative headlines
        for headline in negative_headlines:
            result = analyzer.analyze(headline)
            assert result["compound"] < 0, f"Failed for: {headline}"
            assert result["label"] == "negative", f"Failed for: {headline}"
