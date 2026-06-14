"""Tests for DeepSeek LLM batch processor."""

from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.ingestion.llm_processor import (
    ArticleExtraction,
    DeepSeekBatchProcessor,
    SentimentResult,
)


class TestPydanticModels:
    def test_sentiment_result_valid(self):
        s = SentimentResult(score=0.5, label="bullish", confidence=0.9)
        assert s.score == 0.5
        assert s.label == "bullish"

    def test_sentiment_result_clamped(self):
        with pytest.raises(ValueError):
            SentimentResult(score=2.0, label="bullish", confidence=0.5)

    def test_article_extraction_defaults(self):
        a = ArticleExtraction(
            id="test-1",
            sentiment=SentimentResult(score=0.0, label="neutral", confidence=0.5),
            event_type="general",
            summary="Test summary",
            impact_horizon="short",
            market_relevance=0.5,
        )
        assert a.mentioned_tickers == []
        assert a.key_entities == []


class TestFormatBatch:
    def test_format_includes_id_title_body(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            batch = [
                {"article_id": "art-1", "source_type": "rss", "title": "AAPL up", "body": "Apple rises 5%"},
            ]
            result = proc._format_batch(batch)
            assert "art-1" in result
            assert "AAPL up" in result
            assert "Apple rises 5%" in result

    def test_body_truncation(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor(max_body_chars=100)
            batch = [
                {"article_id": "art-1", "source_type": "rss", "title": "T", "body": "x" * 500},
            ]
            result = proc._format_batch(batch)
            assert len(result) < 700


class TestToSilverDf:
    def test_single_ticker(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            bronze_df = pl.DataFrame(
                {
                    "article_id": ["art-1"],
                    "source_type": ["rss"],
                    "source_name": ["test"],
                    "source_url": ["https://example.com/1"],
                    "title": ["AAPL up"],
                    "body": ["Apple rises"],
                    "author": ["John"],
                    "published_at": [None],
                    "fetched_at": [None],
                    "source_metadata": ['{"feed": "test"}'],
                    "date": [None],
                }
            )
            extractions = [
                ArticleExtraction(
                    id="art-1",
                    mentioned_tickers=["AAPL"],
                    sentiment=SentimentResult(score=0.7, label="bullish", confidence=0.9),
                    event_type="product",
                    key_entities=["Tim Cook"],
                    summary="Apple up on new product",
                    impact_horizon="short",
                    market_relevance=0.8,
                )
            ]
            result = proc._to_silver_df(extractions, bronze_df)
            assert result.height == 1
            assert result["ticker"][0] == "AAPL"
            assert result["sentiment_score"][0] == 0.7
            assert result["sentiment_label"][0] == "bullish"

    def test_no_tickers_creates_null_row(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            bronze_df = pl.DataFrame({"article_id": ["art-1"], "source_type": ["rss"], "source_name": ["test"]})
            extractions = [
                ArticleExtraction(
                    id="art-1",
                    mentioned_tickers=[],
                    sentiment=SentimentResult(score=0.0, label="neutral", confidence=0.3),
                    event_type="general",
                    key_entities=[],
                    summary="Macro news",
                    impact_horizon="long",
                    market_relevance=0.3,
                )
            ]
            result = proc._to_silver_df(extractions, bronze_df)
            assert result.height == 1
            assert result["ticker"][0] is None

    def test_multiple_tickers_explodes(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            bronze_df = pl.DataFrame({"article_id": ["art-1"], "source_type": ["rss"], "source_name": ["test"]})
            extractions = [
                ArticleExtraction(
                    id="art-1",
                    mentioned_tickers=["AAPL", "MSFT", "GOOGL"],
                    sentiment=SentimentResult(score=0.5, label="bullish", confidence=0.8),
                    event_type="macro",
                    key_entities=[],
                    summary="Tech rally",
                    impact_horizon="medium",
                    market_relevance=0.7,
                )
            ]
            result = proc._to_silver_df(extractions, bronze_df)
            assert result.height == 3


class TestVaderFallback:
    def test_fallback_returns_neutral_without_vader(self):
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            batch = [{"article_id": "art-1", "title": "Test", "body": "Some body text"}]
            results = proc._vader_fallback(batch)
            assert len(results) == 1
            assert results[0].mentioned_tickers == []
            assert results[0].event_type == "general"
            assert results[0].sentiment.confidence == 0.3


class TestProcessAllEmpty:
    def test_empty_df_returns_empty(self):
        import asyncio

        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            proc = DeepSeekBatchProcessor()
            result = asyncio.run(proc.process_all(pl.DataFrame()))
            assert result.is_empty()
