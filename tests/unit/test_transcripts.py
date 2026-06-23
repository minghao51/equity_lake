"""Tests for the Finnhub earnings transcript fetcher."""

from datetime import date
from unittest.mock import patch

import pytest

from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS
from equity_lake.sources.transcripts import EarningsTranscriptFetcher


@pytest.fixture
def mock_transcript_data():
    return [
        {
            "id": 12345,
            "title": "Apple Inc (AAPL) Q1 2024 Earnings Call",
            "time": 1706140800,
            "year": 2024,
            "quarter": 1,
            "symbol": "AAPL",
            "transcript": [
                {"name": "Operator", "speaker": "Operator", "text": "Good day everyone."},
                {"name": "Tim Cook - CEO", "speaker": "Tim Cook", "text": "Welcome to our Q1 results."},
            ],
        }
    ]


class TestEarningsTranscriptFetcherInit:
    def test_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="FINNHUB_API_KEY"):
            EarningsTranscriptFetcher()

    def test_init_with_explicit_key(self):
        fetcher = EarningsTranscriptFetcher(api_key="test-key", tickers=["AAPL"], year=2024)
        assert fetcher.api_key == "test-key"
        assert fetcher.tickers == ["AAPL"]
        assert fetcher.year == 2024
        assert fetcher.market == "us_earnings_transcripts"


class TestEarningsTranscriptFetch:
    def test_fetch_returns_empty_when_no_tickers(self):
        fetcher = EarningsTranscriptFetcher(api_key="test-key", tickers=[])
        df = fetcher.fetch(date(2024, 1, 15))
        assert df.is_empty()

    def test_fetch_returns_bronze_schema(self, mock_transcript_data):
        fetcher = EarningsTranscriptFetcher(api_key="test-key", tickers=["AAPL"], year=2024)

        with patch.object(
            fetcher,
            "_fetch_ticker",
            return_value=[
                {
                    "article_id": "test-id",
                    "source_type": "transcript",
                    "source_name": "finnhub",
                    "source_url": "https://finnhub.io/transcript/12345",
                    "title": "Apple Inc (AAPL) Q1 2024 Earnings Call",
                    "body": "Good day everyone.\n\nWelcome to our Q1 results.",
                    "author": "",
                    "published_at": date(2024, 1, 25),
                    "fetched_at": date(2024, 1, 25),
                    "source_metadata": '{"quarter": 1, "fiscal_year": 2024}',
                    "date": date(2024, 1, 25),
                }
            ],
        ):
            df = fetcher.fetch(date(2024, 1, 15))

        assert not df.is_empty()
        assert set(BRONZE_ARTICLE_COLUMNS).issubset(set(df.columns))
        assert df.row(0, named=True)["source_type"] == "transcript"

    def test_fetch_concats_transcript_sections(self, mock_transcript_data):
        fetcher = EarningsTranscriptFetcher(api_key="test-key", tickers=["AAPL"], year=2024)

        import httpx

        mock_resp = httpx.Response(
            200,
            json=mock_transcript_data,
            request=httpx.Request("GET", "https://finnhub.io/api/v1/stock/earnings-call-transcripts"),
        )

        with patch("httpx.Client.get", return_value=mock_resp):
            df = fetcher.fetch(date(2024, 1, 15))

        assert not df.is_empty()
        row = df.row(0, named=True)
        assert "Good day everyone" in row["body"]
        assert "Welcome to our Q1 results" in row["body"]
        assert row["title"] == "Apple Inc (AAPL) Q1 2024 Earnings Call"

    def test_fetch_handles_403_forbidden(self):
        fetcher = EarningsTranscriptFetcher(api_key="test-key", tickers=["AAPL"], year=2024)

        import httpx

        mock_resp = httpx.Response(
            403,
            request=httpx.Request("GET", "https://finnhub.io/api/v1/stock/earnings-call-transcripts"),
        )

        with patch("httpx.Client.get", return_value=mock_resp):
            df = fetcher.fetch(date(2024, 1, 15))

        assert df.is_empty()

    def test_fetch_handles_empty_response(self):
        fetcher = EarningsTranscriptFetcher(api_key="test-key", tickers=["AAPL"], year=2024)

        import httpx

        mock_resp = httpx.Response(
            200,
            json=[],
            request=httpx.Request("GET", "https://finnhub.io/api/v1/stock/earnings-call-transcripts"),
        )

        with patch("httpx.Client.get", return_value=mock_resp):
            df = fetcher.fetch(date(2024, 1, 15))

        assert df.is_empty()

    def test_source_metadata_contains_quarter_and_year(self, mock_transcript_data):
        import json

        fetcher = EarningsTranscriptFetcher(api_key="test-key", tickers=["AAPL"], year=2024)

        import httpx

        mock_resp = httpx.Response(
            200,
            json=mock_transcript_data,
            request=httpx.Request("GET", "https://finnhub.io/api/v1/stock/earnings-call-transcripts"),
        )

        with patch("httpx.Client.get", return_value=mock_resp):
            df = fetcher.fetch(date(2024, 1, 15))

        row = df.row(0, named=True)
        metadata = json.loads(row["source_metadata"])
        assert metadata["quarter"] == 1
        assert metadata["fiscal_year"] == 2024
        assert metadata["ticker"] == "AAPL"
