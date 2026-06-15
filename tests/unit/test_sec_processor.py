"""Tests for the SEC section LLM processor."""

import json
from datetime import date, datetime
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest

from equity_lake.core.schemas import SEC_EXTRACTION_COLUMNS
from equity_lake.ingestion.sec_processor import SECSectionExtraction, SECSectionProcessor


@pytest.fixture
def mock_bronze_df():
    return pl.DataFrame(
        [
            {
                "article_id": "sec-001",
                "source_type": "sec_filing",
                "source_name": "sec_edgar",
                "source_url": "https://www.sec.gov/test1",
                "title": "AAPL 10-K — Risk Factors",
                "body": "The company faces supply chain risks...",
                "author": "",
                "published_at": datetime(2024, 1, 15),
                "fetched_at": datetime(2024, 1, 16),
                "source_metadata": json.dumps(
                    {
                        "ticker": "AAPL",
                        "cik": 320193,
                        "filing_type": "10-K",
                        "accession": "acc-001",
                        "section": "risk_factors",
                    }
                ),
                "date": date(2024, 1, 15),
            }
        ]
    )


class TestSECSectionProcessorInit:
    def test_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            SECSectionProcessor()

    def test_init_with_key(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = SECSectionProcessor(batch_size=3)
            assert proc.batch_size == 3


class TestSECProcessing:
    def test_to_silver_df_produces_correct_schema(self, mock_bronze_df):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = SECSectionProcessor()

        extractions = [
            SECSectionExtraction(
                id="sec-001",
                ticker="AAPL",
                section_type="risk_factors",
                risk_sentiment=-0.5,
                key_risks=["supply chain", "regulatory"],
                guidance_direction="negative",
                forward_statements=["We expect headwinds"],
                management_tone=-0.2,
                new_vs_repeated="new",
                summary="Increased risk from supply chain.",
            )
        ]

        df = proc._to_silver_df(extractions, mock_bronze_df)
        assert not df.is_empty()
        assert set(SEC_EXTRACTION_COLUMNS).issubset(set(df.columns))
        row = df.row(0, named=True)
        assert row["ticker"] == "AAPL"
        assert row["filing_type"] == "10-K"
        assert row["section_type"] == "risk_factors"
        assert row["risk_sentiment"] == -0.5

    def test_neutral_fallback_returns_extractions(self, mock_bronze_df):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = SECSectionProcessor()

        batch = mock_bronze_df.to_dicts()
        results = proc._neutral_fallback(batch)

        assert len(results) == 1
        assert results[0].ticker == "AAPL"
        assert results[0].risk_sentiment == 0.0
        assert results[0].guidance_direction == "none"

    def test_format_batch_includes_ticker_and_section(self, mock_bronze_df):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = SECSectionProcessor()

        formatted = proc._format_batch(mock_bronze_df.to_dicts())
        assert "sec-001" in formatted
        assert "AAPL" in formatted
        assert "risk_factors" in formatted

    def test_process_all_handles_empty_df(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = SECSectionProcessor()

        result = pytest.importorskip("asyncio").run(proc.process_all(pl.DataFrame()))
        assert result.is_empty()

    def test_process_all_returns_silver_df(self, mock_bronze_df):
        mock_batch_result = json.dumps(
            {
                "items": [
                    {
                        "id": "sec-001",
                        "ticker": "AAPL",
                        "section_type": "risk_factors",
                        "risk_sentiment": -0.6,
                        "key_risks": ["supply chain"],
                        "guidance_direction": "negative",
                        "forward_statements": ["headwinds expected"],
                        "management_tone": -0.3,
                        "new_vs_repeated": "new",
                        "summary": "Increased risk factors identified.",
                    }
                ]
            }
        )

        mock_choice = type(
            "MockChoice",
            (),
            {"message": type("MockMsg", (), {"content": mock_batch_result})},
        )()
        mock_response = type("MockResp", (), {"choices": [mock_choice]})()

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = SECSectionProcessor()
            proc.client.chat.completions.create = AsyncMock(return_value=mock_response)

            import asyncio

            df = asyncio.run(proc.process_all(mock_bronze_df))

        assert not df.is_empty()
        assert df.row(0, named=True)["risk_sentiment"] == -0.6
        assert df.row(0, named=True)["ticker"] == "AAPL"
