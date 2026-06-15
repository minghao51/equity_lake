"""Tests for DeepSeek LLM batch processor — API mocking, retry, fallback."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest

from equity_lake.ingestion.llm_processor import DeepSeekBatchProcessor


def _mock_response(content: str):
    mock_choice = type("MockChoice", (), {"message": type("MockMsg", (), {"content": content})})()
    return type("MockResp", (), {"choices": [mock_choice]})()


@pytest.fixture
def bronze_df():
    return pl.DataFrame(
        {
            "article_id": ["art-1", "art-2"],
            "source_type": ["rss", "rss"],
            "source_name": ["feed1", "feed2"],
            "source_url": ["https://example.com/1", "https://example.com/2"],
            "title": ["AAPL soars", "MSFT drops"],
            "body": ["Apple up 5%", "Microsoft down 3%"],
            "author": ["", ""],
            "published_at": [None, None],
            "fetched_at": [None, None],
            "source_metadata": ["{}", "{}"],
            "date": [None, None],
        }
    )


class TestProcessBatchWithMock:
    def test_successful_batch(self):
        mock_content = json.dumps(
            {
                "items": [
                    {
                        "id": "art-1",
                        "mentioned_tickers": ["AAPL"],
                        "sentiment": {"score": 0.8, "label": "bullish", "confidence": 0.9},
                        "event_type": "product",
                        "key_entities": ["Tim Cook"],
                        "summary": "Apple soars",
                        "impact_horizon": "short",
                        "market_relevance": 0.85,
                    }
                ]
            }
        )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            proc.client.chat.completions.create = AsyncMock(return_value=_mock_response(mock_content))

            result = asyncio.run(proc.process_batch([{"article_id": "art-1", "title": "AAPL soars", "body": "Apple up 5%"}]))

            assert len(result.items) == 1
            assert result.items[0].mentioned_tickers == ["AAPL"]
            assert result.items[0].sentiment.score == 0.8

    def test_empty_content_triggers_retry(self):
        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return _mock_response("")
            return _mock_response(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "art-1",
                                "mentioned_tickers": [],
                                "sentiment": {"score": 0.0, "label": "neutral", "confidence": 0.5},
                                "event_type": "general",
                                "key_entities": [],
                                "summary": "test",
                                "impact_horizon": "short",
                                "market_relevance": 0.3,
                            }
                        ]
                    }
                )
            )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            proc.client.chat.completions.create = mock_create

            result = asyncio.run(proc.process_batch([{"article_id": "art-1", "title": "T", "body": "B"}]))
            assert call_count == 3
            assert len(result.items) == 1

    def test_invalid_json_triggers_retry(self):
        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _mock_response("not valid json {{{")
            return _mock_response(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "art-1",
                                "mentioned_tickers": [],
                                "sentiment": {"score": 0.0, "label": "neutral", "confidence": 0.5},
                                "event_type": "general",
                                "key_entities": [],
                                "summary": "ok",
                                "impact_horizon": "short",
                                "market_relevance": 0.3,
                            }
                        ]
                    }
                )
            )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            proc.client.chat.completions.create = mock_create

            asyncio.run(proc.process_batch([{"article_id": "art-1", "title": "T", "body": "B"}]))
            assert call_count == 2


class TestProcessAllWithMock:
    def test_successful_processing(self, bronze_df):
        mock_content = json.dumps(
            {
                "items": [
                    {
                        "id": "art-1",
                        "mentioned_tickers": ["AAPL"],
                        "sentiment": {"score": 0.7, "label": "bullish", "confidence": 0.8},
                        "event_type": "product",
                        "key_entities": [],
                        "summary": "Apple up",
                        "impact_horizon": "short",
                        "market_relevance": 0.7,
                    },
                    {
                        "id": "art-2",
                        "mentioned_tickers": ["MSFT"],
                        "sentiment": {"score": -0.5, "label": "bearish", "confidence": 0.6},
                        "event_type": "general",
                        "key_entities": [],
                        "summary": "MSFT down",
                        "impact_horizon": "medium",
                        "market_relevance": 0.5,
                    },
                ]
            }
        )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor(batch_size=15)
            proc.client.chat.completions.create = AsyncMock(return_value=_mock_response(mock_content))

            df = asyncio.run(proc.process_all(bronze_df))

        assert not df.is_empty()
        assert df.height == 2
        assert set(df["ticker"].to_list()) == {"AAPL", "MSFT"}

    def test_failed_batch_uses_fallback(self, bronze_df):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor(batch_size=15)
            proc.client.chat.completions.create = AsyncMock(side_effect=Exception("API down"))

            df = asyncio.run(proc.process_all(bronze_df))

        assert not df.is_empty()
        assert df.height == 2
        assert all(df["event_type"] == "general")
        assert all(df["sentiment_label"].is_in(["bullish", "bearish", "neutral"]))

    def test_batch_size_splitting(self, bronze_df):
        call_count = 0

        async def mock_create(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            items = kwargs.get("messages", [{}])[1].get("content", "")
            ids = [line for line in items.split("\n") if line.startswith("id: ")]
            return _mock_response(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": ids[0].split("id: ")[1] if ids else "art-1",
                                "mentioned_tickers": [],
                                "sentiment": {"score": 0.0, "label": "neutral", "confidence": 0.5},
                                "event_type": "general",
                                "key_entities": [],
                                "summary": "test",
                                "impact_horizon": "short",
                                "market_relevance": 0.3,
                            }
                        ]
                    }
                )
            )

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor(batch_size=1)
            proc.client.chat.completions.create = mock_create

            df = asyncio.run(proc.process_all(bronze_df))

        assert call_count == 2
        assert df.height == 2
