"""DeepSeek LLM batch processor for unstructured article extraction.

Uses ``AsyncOpenAI`` with DeepSeek's OpenAI-compatible API endpoint.
Structured output via ``response_format={'type': 'json_object'}`` and
Pydantic validation. Retries via tenacity (handles DeepSeek's known
empty-content bug and JSON parse errors). VADER fallback on persistent failure.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import polars as pl
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger()

SYSTEM_PROMPT = """You are a financial news analyst. Analyze the provided articles and return results as JSON.

You MUST respond with a JSON object matching this exact schema:
{
  "items": [
    {
      "id": "the article id provided in the input",
      "mentioned_tickers": ["AAPL", "MSFT"],
      "sentiment": {
        "score": 0.72,
        "label": "bullish",
        "confidence": 0.85
      },
      "event_type": "earnings",
      "key_entities": ["Tim Cook", "Apple Intelligence"],
      "summary": "1-2 sentence concise summary of the key financial point",
      "impact_horizon": "short",
      "market_relevance": 0.82
    }
  ]
}

Rules:
- sentiment.score: float from -1.0 (very bearish) to 1.0 (very bullish)
- sentiment.label: "bullish", "bearish", or "neutral"
- sentiment.confidence: float from 0.0 to 1.0
- event_type: one of "earnings", "m&a", "product", "regulatory", "analyst", "macro", "general"
- key_entities: people, companies, products mentioned (exclude the ticker itself)
- impact_horizon: "short" (days), "medium" (weeks), or "long" (months)
- market_relevance: float from 0.0 (no market impact) to 1.0 (major catalyst)
- mentioned_tickers: uppercase US stock symbols only (e.g. AAPL, TSLA, NVDA)
- For articles with no clear tickers, return empty list: []
- Each item's "id" MUST match the id provided in the corresponding input article"""


class SentimentResult(BaseModel):
    score: float = Field(ge=-1.0, le=1.0)
    label: str = Field(description="bullish | bearish | neutral")
    confidence: float = Field(ge=0.0, le=1.0)


class ArticleExtraction(BaseModel):
    id: str
    mentioned_tickers: list[str] = Field(default_factory=list)
    sentiment: SentimentResult
    event_type: str = Field(description="earnings | m&a | product | regulatory | analyst | macro | general")
    key_entities: list[str] = Field(default_factory=list)
    summary: str
    impact_horizon: str = Field(description="short | medium | long")
    market_relevance: float = Field(ge=0.0, le=1.0)


class BatchExtraction(BaseModel):
    items: list[ArticleExtraction]


class RetryableError(Exception):
    """Raised when DeepSeek returns empty content or invalid JSON."""


class DeepSeekBatchProcessor:
    """Batch-process raw bronze articles via DeepSeek for structured extraction.

    Articles are grouped into batches by source type. Each batch is a single
    API call. Failed batches fall back to VADER sentiment.

    Args:
        batch_size: Number of articles per API call (default 15).
        max_concurrency: Maximum parallel API calls (default 10).
        model: DeepSeek model name (default: deepseek-v4-flash).
        max_tokens_per_article: Max input body length in characters (default 2000).
    """

    def __init__(
        self,
        batch_size: int = 15,
        max_concurrency: int = 10,
        model: str = "deepseek-v4-flash",
        max_body_chars: int = 2000,
    ):
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY not set")

        self.client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
        self.model = model
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.max_body_chars = max_body_chars

        self._retry_decorator = retry(
            retry=retry_if_exception_type(RetryableError),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            before_sleep=before_sleep_log(logger, 30),
            reraise=True,
        )

    async def process_batch(self, batch: list[dict[str, Any]]) -> BatchExtraction:
        @self._retry_decorator  # type: ignore[misc]
        async def _call() -> BatchExtraction:
            async with self.semaphore:
                user_content = self._format_batch(batch)
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=8192,
                )

                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise RetryableError("DeepSeek returned empty content")

                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as e:
                    raise RetryableError(f"Invalid JSON: {e}") from e

                try:
                    return BatchExtraction.model_validate(parsed)
                except ValidationError as e:
                    raise RetryableError(f"Validation failed: {e}") from e

        result: BatchExtraction = await _call()
        return result

    async def process_all(self, bronze_df: pl.DataFrame) -> pl.DataFrame:
        if bronze_df.is_empty():
            logger.warning("Empty bronze DataFrame, nothing to process")
            return pl.DataFrame()

        rows = bronze_df.to_dicts()
        batches = [rows[i : i + self.batch_size] for i in range(0, len(rows), self.batch_size)]

        logger.info("LLM batch processing starting", total_articles=len(rows), batch_count=len(batches))

        tasks = [self.process_batch(b) for b in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        extracted: list[ArticleExtraction] = []
        failed_count = 0

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                failed_count += len(batches[i])
                logger.warning("LLM batch failed, using VADER fallback", error=str(result), batch_size=len(batches[i]))
                extracted.extend(self._vader_fallback(batches[i]))
            else:
                extracted.extend(result.items)

        logger.info("LLM processing complete", extracted=len(extracted), failed=failed_count)
        return self._to_silver_df(extracted, bronze_df)

    def _format_batch(self, batch: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for item in batch:
            body = (item.get("body") or item.get("title") or "")[: self.max_body_chars]
            parts.append(
                f"---\nid: {item['article_id']}\nsource: {item.get('source_type', 'unknown')}\ntitle: {item.get('title', '')}\nbody: {body}\n"
            )
        return "\n".join(parts)

    def _vader_fallback(self, batch: list[dict[str, Any]]) -> list[ArticleExtraction]:
        try:
            from equity_lake.sentiment.analyzer import SentimentAnalyzer

            analyzer = SentimentAnalyzer(method="vader")
        except ImportError:
            logger.warning("VADER not available, returning neutral sentiment for failed batch")
            analyzer = None

        results: list[ArticleExtraction] = []
        for item in batch:
            text = f"{item.get('title', '')} {item.get('body', '')}"[:500]
            if analyzer:
                result = analyzer.analyze(text)
                raw_score: object = result.get("compound", 0.0)
                raw_label: object = result.get("label", "neutral")
                score: float = float(raw_score) if isinstance(raw_score, int | float) else 0.0
                label: str = str(raw_label) if raw_label else "neutral"
            else:
                score, label = 0.0, "neutral"

            label = "bullish" if label == "positive" else ("bearish" if label == "negative" else "neutral")

            results.append(
                ArticleExtraction(
                    id=item["article_id"],
                    mentioned_tickers=[],
                    sentiment=SentimentResult(score=score, label=label, confidence=0.3),
                    event_type="general",
                    key_entities=[],
                    summary=item.get("title", "")[:200],
                    impact_horizon="short",
                    market_relevance=0.3,
                )
            )
        return results

    def _to_silver_df(self, extractions: list[ArticleExtraction], bronze_df: pl.DataFrame) -> pl.DataFrame:
        if not extractions:
            return pl.DataFrame()

        bronze_lookup = {row["article_id"]: row for row in bronze_df.to_dicts()}

        silver_rows: list[dict[str, Any]] = []
        for ext in extractions:
            bronze_row = bronze_lookup.get(ext.id, {})

            tickers: list[str | None] = list(ext.mentioned_tickers) if ext.mentioned_tickers else [None]
            for ticker in tickers:
                silver_rows.append(
                    {
                        "article_id": ext.id,
                        "ticker": ticker,
                        "source_type": bronze_row.get("source_type"),
                        "source_name": bronze_row.get("source_name"),
                        "published_at": bronze_row.get("published_at"),
                        "date": bronze_row.get("date"),
                        "sentiment_score": ext.sentiment.score,
                        "sentiment_label": ext.sentiment.label,
                        "confidence": ext.sentiment.confidence,
                        "event_type": ext.event_type,
                        "summary": ext.summary,
                        "impact_horizon": ext.impact_horizon,
                        "market_relevance": ext.market_relevance,
                        "key_entities": json.dumps(ext.key_entities),
                        "source_metadata": bronze_row.get("source_metadata"),
                    }
                )

        df = pl.DataFrame(silver_rows) if silver_rows else pl.DataFrame()

        for col in ["sentiment_score", "confidence", "market_relevance"]:
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64))

        return df


def run_llm_processing(bronze_df: pl.DataFrame, batch_size: int = 15) -> pl.DataFrame:
    """Synchronous wrapper for async batch processing.

    Convenience function for use in non-async contexts (e.g. CLI commands).
    """
    processor = DeepSeekBatchProcessor(batch_size=batch_size)
    return asyncio.run(processor.process_all(bronze_df))


__all__ = [
    "ArticleExtraction",
    "BatchExtraction",
    "DeepSeekBatchProcessor",
    "RetryableError",
    "SentimentResult",
    "run_llm_processing",
]
