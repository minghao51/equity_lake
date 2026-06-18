"""Tests for the SEC EDGAR full-text filing fetcher."""

import json
from datetime import date
from unittest.mock import patch

import httpx
import pytest

from equity_lake.core.schemas import BRONZE_ARTICLE_COLUMNS
from equity_lake.sources.sec_fulltext import SECFilingFetcher


@pytest.fixture(autouse=True)
def _set_sec_user_agent(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "TestCompany test@example.com")


TICKER_MAP_PAYLOAD = {
    "0": {"ticker": "AAPL", "cik_str": 320193},
    "1": {"ticker": "MSFT", "cik_str": 789019},
}

SUBMISSIONS_PAYLOAD = {
    "filings": {
        "recent": {
            "filingDate": ["2024-01-15", "2023-10-30"],
            "form": ["10-K", "10-Q"],
            "accessionNumber": ["0000320193-24-000001", "0000320193-23-000050"],
            "primaryDocument": ["aapl-20230930.htm", "aapl-20230930.htm"],
        }
    }
}

MOCK_FILING_HTML = """
<html><head><title>Apple Inc. Form 10-K</title></head>
<body>
<h2>ITEM 1A. RISK FACTORS</h2>
<p>The Company is subject to various risks including supply chain disruptions,
regulatory changes, and cybersecurity threats. These factors could materially
affect business operations and financial results.</p>
<h2>ITEM 7. MANAGEMENT'S DISCUSSION AND ANALYSIS</h2>
<p>Net sales increased during fiscal 2023 compared to fiscal 2022. The growth
was driven primarily by higher Services revenue and strong iPhone demand.</p>
</body></html>
"""


class TestSECFilingFetcherInit:
    def test_init_with_defaults(self):
        fetcher = SECFilingFetcher(tickers=["AAPL"])
        assert fetcher.tickers == ["AAPL"]
        assert fetcher.market == "sec_filings_fulltext"
        assert fetcher.lookback_days > 0

    def test_init_with_custom_agent(self):
        fetcher = SECFilingFetcher(tickers=["AAPL"], user_agent="Test Agent test@test.com")
        assert fetcher.user_agent == "Test Agent test@test.com"


class TestSECFilingFetch:
    def test_fetch_returns_empty_when_no_tickers(self):
        fetcher = SECFilingFetcher(tickers=[])
        df = fetcher.fetch(date(2024, 1, 15))
        assert df.is_empty()

    def test_fetch_returns_bronze_schema(self):
        fetcher = SECFilingFetcher(tickers=["AAPL"])

        article = {
            "article_id": "test-id",
            "source_type": "sec_filing",
            "source_name": "sec_edgar",
            "source_url": "https://www.sec.gov/test",
            "title": "AAPL 10-K — Risk Factors",
            "body": "Risk factor text...",
            "author": "",
            "published_at": date(2024, 1, 15),
            "fetched_at": date(2024, 1, 15),
            "source_metadata": "{}",
            "date": date(2024, 1, 15),
        }

        with patch.object(fetcher, "_fetch_ticker", return_value=[article]):
            df = fetcher.fetch(date(2024, 1, 15))

        assert not df.is_empty()
        assert set(BRONZE_ARTICLE_COLUMNS).issubset(set(df.columns))
        assert df.row(0, named=True)["source_type"] == "sec_filing"

    def test_fetch_handles_ticker_error(self):
        fetcher = SECFilingFetcher(tickers=["AAPL", "MSFT"])

        with patch.object(fetcher, "_fetch_ticker", side_effect=Exception("network error")):
            df = fetcher.fetch(date(2024, 1, 15))

        assert df.is_empty()

    def test_extract_sections_finds_risk_factors_and_mda(self):
        fetcher = SECFilingFetcher(tickers=["AAPL"])
        sections = fetcher._extract_sections(MOCK_FILING_HTML)

        section_names = [s[0] for s in sections]
        assert "risk_factors" in section_names
        assert "mda" in section_names

        risk_text = next(body for name, body in sections if name == "risk_factors")
        assert "supply chain" in risk_text.lower()

    def test_extract_sections_returns_empty_for_no_match(self):
        fetcher = SECFilingFetcher(tickers=["AAPL"])
        html = "<html><body><p>Random content without item headers.</p></body></html>"
        sections = fetcher._extract_sections(html)
        assert sections == []

    def test_source_metadata_contains_filing_info(self):
        fetcher = SECFilingFetcher(tickers=["AAPL"])

        ticker_resp = httpx.Response(
            200,
            json=TICKER_MAP_PAYLOAD,
            request=httpx.Request("GET", "https://www.sec.gov/files/company_tickers.json"),
        )
        sub_resp = httpx.Response(
            200,
            json=SUBMISSIONS_PAYLOAD,
            request=httpx.Request("GET", "https://data.sec.gov/submissions/CIK0000320193.json"),
        )
        filing_resp = httpx.Response(
            200,
            text=MOCK_FILING_HTML,
            request=httpx.Request("GET", "https://www.sec.gov/Archives/edgar/data/320193/000032019324000001/aapl-20230930.htm"),
        )

        responses = [ticker_resp, sub_resp, filing_resp, ticker_resp, sub_resp, filing_resp]
        with patch("httpx.Client.get", side_effect=responses):
            df = fetcher.fetch(date(2024, 6, 15))

        if not df.is_empty():
            row = df.row(0, named=True)
            metadata = json.loads(row["source_metadata"])
            assert "filing_type" in metadata
            assert "accession" in metadata
            assert metadata["ticker"] == "AAPL"

    def test_fetch_filters_non_target_forms(self):
        payload = {
            "filings": {
                "recent": {
                    "filingDate": ["2024-01-15", "2024-01-10"],
                    "form": ["8-K", "4"],
                    "accessionNumber": ["acc-001", "acc-002"],
                    "primaryDocument": ["doc1.htm", "doc2.xml"],
                }
            }
        }

        fetcher = SECFilingFetcher(tickers=["AAPL"])
        fetcher._ticker_cik_cache = {"AAPL": 320193}

        sub_resp = httpx.Response(
            200,
            json=payload,
            request=httpx.Request("GET", "https://data.sec.gov/submissions/CIK0000320193.json"),
        )

        with patch("httpx.Client.get", return_value=sub_resp):
            filings = fetcher._get_recent_filings(320193, date(2024, 1, 1), date(2024, 6, 1))

        assert len(filings) == 0
