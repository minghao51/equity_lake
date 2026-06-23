"""
Integration tests for news ingestion module.

These tests can optionally make real API calls to Finnhub.
Most tests use mocking to avoid API quota usage.
"""

import os
from datetime import date, datetime
from unittest.mock import patch

import polars as pl
import pytest
from deltalake import DeltaTable

from equity_lake.core.schemas import NEWS_COLUMNS
from equity_lake.ingestion.writers import validate_schema, write_to_partitioned_parquet
from equity_lake.sources.news import FinnhubNewsFetcher
from equity_lake.storage.delta import delta_table_path


def _make_news_df(n: int = 2, base_url: str = "https://example.com/") -> pl.DataFrame:
    rows = {
        "ticker": ["AAPL", "GOOGL"][:n],
        "date": [date(2024, 1, 1)] * n,
        "datetime": [datetime(2024, 1, 1, 10, 0 + i) for i in range(n)],
        "source": ["Reuters", "Bloomberg"][:n],
        "headline": [f"Test headline {i}" for i in range(1, n + 1)],
        "summary": [f"Test summary {i}" for i in range(1, n + 1)],
        "url": [f"{base_url}{i}" for i in range(1, n + 1)],
        "category": ["general"] * n,
        "sentiment_score": [0.5, -0.3][:n],
        "sentiment_label": ["positive", "negative"][:n],
        "relevance_score": [1.0, 0.9][:n],
    }
    return pl.DataFrame(rows)


# =============================================================================
# Schema Validation Tests
# =============================================================================


class TestNewsSchemaValidation:
    """Test schema validation for news data."""

    def test_valid_news_schema_passes(self):
        """Test that valid news DataFrame passes validation."""
        df = pl.DataFrame(
            {
                "ticker": ["AAPL", "GOOGL"],
                "date": [date(2024, 1, 1), date(2024, 1, 1)],
                "datetime": [datetime(2024, 1, 1, 10, 0), datetime(2024, 1, 1, 11, 0)],
                "source": ["Reuters", "Bloomberg"],
                "headline": ["AAPL stock rises", "GOOGL falls"],
                "summary": ["Strong earnings", "Weak guidance"],
                "url": ["https://example.com/1", "https://example.com/2"],
                "category": ["earnings", "guidance"],
                "sentiment_score": [0.5, -0.3],
                "sentiment_label": ["positive", "negative"],
                "relevance_score": [1.0, 0.9],
            }
        )

        result = validate_schema(df, "us_news")

        assert result is True

    def test_missing_columns_fails_validation(self):
        """Test that missing required columns fails validation."""
        df = pl.DataFrame(
            {
                "ticker": ["AAPL"],
            }
        )

        result = validate_schema(df, "us_news")

        assert result is False

    def test_all_null_column_warns(self):
        """Test that all-null required columns fail validation."""
        data = {col: [None] for col in NEWS_COLUMNS}
        data["ticker"] = ["AAPL"]
        df = pl.DataFrame(data)

        result = validate_schema(df, "us_news")

        assert result is False


# =============================================================================
# Delta Lake Write Tests
# =============================================================================


class TestNewsDeltaWrite:
    """Test writing news data to Delta Lake."""

    def test_write_creates_delta_table(self, tmp_path):
        """Test writing news data creates a valid Delta table."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            df = _make_news_df()

            success = write_to_partitioned_parquet(
                df,
                "us_news",
                date(2024, 1, 1),
                dry_run=False,
            )

            assert success is True

            table_path = delta_table_path("us_news", lake_dir=tmp_path)
            assert DeltaTable.is_deltatable(str(table_path))

            dt = DeltaTable(str(table_path))
            result = pl.from_arrow(dt.to_pyarrow_table())
            assert result.height == 2
            assert "ticker" in result.columns

    def test_dry_run_skips_write(self, tmp_path):
        """Test that dry run mode skips actual write."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            df = _make_news_df(n=1)

            success = write_to_partitioned_parquet(
                df,
                "us_news",
                date(2024, 1, 1),
                dry_run=True,
            )

            assert success is True

            table_path = delta_table_path("us_news", lake_dir=tmp_path)
            assert not DeltaTable.is_deltatable(str(table_path))

    def test_deduplication_by_url(self, tmp_path):
        """Test that duplicate articles (by URL) are deduplicated via Delta merge."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            df1 = _make_news_df()
            write_to_partitioned_parquet(df1, "us_news", date(2024, 1, 1), dry_run=False)

            df2 = _make_news_df()
            success = write_to_partitioned_parquet(df2, "us_news", date(2024, 1, 1), dry_run=False)

            assert success is True

            table_path = delta_table_path("us_news", lake_dir=tmp_path)
            dt = DeltaTable(str(table_path))
            result = pl.from_arrow(dt.to_pyarrow_table())
            assert result.height == 2


# =============================================================================
# End-to-End Tests (with mocking)
# =============================================================================


class TestNewsIngestionE2E:
    """End-to-end tests for news ingestion with mocked API."""

    @patch("time.sleep")
    def test_full_ingestion_flow(
        self,
        mock_sleep,
        tmp_path,
    ):
        """Test full flow from API fetch to Delta write."""
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
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
                fetcher = FinnhubNewsFetcher(
                    api_key="test_key",
                    tickers=["AAPL"],
                )
                df = fetcher.fetch(date(2024, 1, 1))

                success = write_to_partitioned_parquet(
                    df,
                    "us_news",
                    date(2024, 1, 1),
                    dry_run=False,
                )

                assert success is True
                assert not df.is_empty()

                table_path = delta_table_path("us_news", lake_dir=tmp_path)
                assert DeltaTable.is_deltatable(str(table_path))

                dt = DeltaTable(str(table_path))
                result = pl.from_arrow(dt.to_pyarrow_table())
                assert result.height >= 1
                assert "sentiment_score" in result.columns


# =============================================================================
# Optional Real API Tests
# =============================================================================


@pytest.mark.skipif(not os.getenv("FINNHUB_API_KEY"), reason="FINNHUB_API_KEY not set")
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

        from datetime import timedelta

        trading_date = date.today() - timedelta(days=2)

        result = fetcher.fetch(trading_date)

        assert isinstance(result, pl.DataFrame)

        if not result.is_empty():
            assert "ticker" in result.columns
            assert "headline" in result.columns
            assert "sentiment_score" in result.columns


@pytest.mark.skipif(not os.getenv("FINNHUB_API_KEY"), reason="FINNHUB_API_KEY not set")
class TestSentimentAccuracy:
    """Test sentiment accuracy on real headlines."""

    def test_sentiment_on_real_headlines(self):
        """Test sentiment analyzer on real financial headlines."""
        from equity_lake.sentiment import SentimentAnalyzer

        analyzer = SentimentAnalyzer(method="vader")

        positive_headlines = [
            "Apple beats earnings estimates by 15%",
            "Stock surges on strong revenue growth",
            "Company announces dividend increase",
        ]

        negative_headlines = [
            "Tech giant misses earnings expectations",
            "Stock plunges on weak guidance",
            "Company cuts workforce by 10%",
        ]

        for headline in positive_headlines:
            result = analyzer.analyze(headline)
            assert result["compound"] > 0, f"Failed for: {headline}"
            assert result["label"] == "positive", f"Failed for: {headline}"

        for headline in negative_headlines:
            result = analyzer.analyze(headline)
            assert result["compound"] < 0, f"Failed for: {headline}"
            assert result["label"] == "negative", f"Failed for: {headline}"
