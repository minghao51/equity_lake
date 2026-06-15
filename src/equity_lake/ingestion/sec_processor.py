"""LLM batch processor for SEC filing section analysis.

Uses ``AsyncOpenAI`` with DeepSeek's OpenAI-compatible API to extract
risk sentiment, guidance direction, management tone, and key risks from
10-K/10-Q filing sections. Same tenacity retry + VADER fallback pattern
as ``llm_processor.py``.

Process flow:
    bronze (source_type="sec_filing") → this processor → silver/sec_extractions
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from datetime import date
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

from equity_lake.core.paths import BRONZE_RAW_ARTICLES_DIR, SEC_EXTRACTIONS_DIR
from equity_lake.core.schemas import SEC_EXTRACTION_COLUMNS
from equity_lake.storage.delta import merge_delta

logger = structlog.get_logger()


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Safely parse source_metadata JSON, returning empty dict on failure."""
    if not raw:
        return {}
    with contextlib.suppress(json.JSONDecodeError, TypeError):
        return json.loads(raw) if isinstance(raw, str) else raw if isinstance(raw, dict) else {}
    return {}


SEC_SYSTEM_PROMPT = """You are a financial SEC filing analyst. Analyze the provided filing sections and return results as JSON.

You MUST respond with a JSON object matching this exact schema:
{
  "items": [
    {
      "id": "the section id provided in the input",
      "ticker": "AAPL",
      "section_type": "risk_factors",
      "risk_sentiment": -0.5,
      "key_risks": ["supply chain disruption", "regulatory changes"],
      "guidance_direction": "positive",
      "forward_statements": ["We expect revenue growth..."],
      "management_tone": 0.3,
      "new_vs_repeated": "new",
      "summary": "2-3 sentence summary of key insights"
    }
  ]
}

Rules:
- risk_sentiment: float from -1.0 (confident/low risk) to 1.0 (highly concerned)
- key_risks: list of top risk factors mentioned (max 5)
- guidance_direction: "positive", "negative", "neutral", or "none"
- forward_statements: forward-looking statements extracted (max 3)
- management_tone: float from -1.0 (pessimistic) to 1.0 (optimistic)
- new_vs_repeated: "new" (new risk), "repeated" (same as prior), or "modified" (changed)
- summary: concise 2-3 sentence summary of the section's key insights
- ticker: the stock ticker for this filing (from the input)
- section_type: from the input (risk_factors, mda)
- Each item's "id" MUST match the id provided in the corresponding input section"""


class SECSectionExtraction(BaseModel):
    id: str
    ticker: str = ""
    section_type: str = Field(description="risk_factors | mda")
    risk_sentiment: float = Field(ge=-1.0, le=1.0, default=0.0)
    key_risks: list[str] = Field(default_factory=list)
    guidance_direction: str = Field(default="none", description="positive | negative | neutral | none")
    forward_statements: list[str] = Field(default_factory=list)
    management_tone: float = Field(ge=-1.0, le=1.0, default=0.0)
    new_vs_repeated: str = Field(default="repeated", description="new | repeated | modified")
    summary: str = ""


class SECBatchExtraction(BaseModel):
    items: list[SECSectionExtraction]


class SECRetryableError(Exception):
    """Raised when DeepSeek returns empty content or invalid JSON."""


class SECSectionProcessor:
    """Batch-process SEC filing sections via DeepSeek for structured extraction.

    Args:
        batch_size: Number of sections per API call (default 5 — SEC text is dense).
        max_concurrency: Maximum parallel API calls (default 5).
        model: DeepSeek model name.
        max_body_chars: Max input body length per section.
    """

    def __init__(
        self,
        batch_size: int = 5,
        max_concurrency: int = 5,
        model: str = "deepseek-v4-flash",
        max_body_chars: int = 5000,
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
            retry=retry_if_exception_type(SECRetryableError),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=2, max=30),
            before_sleep=before_sleep_log(logger, 30),
            reraise=True,
        )

    async def process_batch(self, batch: list[dict[str, Any]]) -> SECBatchExtraction:
        @self._retry_decorator  # type: ignore[misc]
        async def _call() -> SECBatchExtraction:
            async with self.semaphore:
                user_content = self._format_batch(batch)
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SEC_SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=8192,
                )

                content = response.choices[0].message.content
                if not content or not content.strip():
                    raise SECRetryableError("DeepSeek returned empty content")

                try:
                    parsed = json.loads(content)
                except json.JSONDecodeError as e:
                    raise SECRetryableError(f"Invalid JSON: {e}") from e

                try:
                    return SECBatchExtraction.model_validate(parsed)
                except ValidationError as e:
                    raise SECRetryableError(f"Validation failed: {e}") from e

        result: SECBatchExtraction = await _call()
        return result

    async def process_all(self, bronze_df: pl.DataFrame) -> pl.DataFrame:
        if bronze_df.is_empty():
            logger.warning("Empty SEC bronze DataFrame, nothing to process")
            return pl.DataFrame()

        rows = bronze_df.to_dicts()
        batches = [rows[i : i + self.batch_size] for i in range(0, len(rows), self.batch_size)]

        logger.info("SEC LLM batch processing starting", total_sections=len(rows), batch_count=len(batches))

        tasks = [self.process_batch(b) for b in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        extracted: list[SECSectionExtraction] = []
        failed_count = 0

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                failed_count += len(batches[i])
                logger.warning("SEC LLM batch failed, using neutral fallback", error=str(result), batch_size=len(batches[i]))
                extracted.extend(self._neutral_fallback(batches[i]))
            else:
                extracted.extend(result.items)

        logger.info("SEC LLM processing complete", extracted=len(extracted), failed=failed_count)
        return self._to_silver_df(extracted, bronze_df)

    def _format_batch(self, batch: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for item in batch:
            body = (item.get("body") or item.get("title") or "")[: self.max_body_chars]
            metadata = _parse_metadata(item.get("source_metadata"))

            parts.append(
                f"---\n"
                f"id: {item['article_id']}\n"
                f"ticker: {metadata.get('ticker', '')}\n"
                f"section_type: {metadata.get('section', 'unknown')}\n"
                f"title: {item.get('title', '')}\n"
                f"body: {body}\n"
            )
        return "\n".join(parts)

    def _neutral_fallback(self, batch: list[dict[str, Any]]) -> list[SECSectionExtraction]:
        results: list[SECSectionExtraction] = []
        for item in batch:
            metadata = _parse_metadata(item.get("source_metadata"))

            results.append(
                SECSectionExtraction(
                    id=item["article_id"],
                    ticker=metadata.get("ticker", ""),
                    section_type=metadata.get("section", "unknown"),
                    risk_sentiment=0.0,
                    key_risks=[],
                    guidance_direction="none",
                    forward_statements=[],
                    management_tone=0.0,
                    new_vs_repeated="repeated",
                    summary=item.get("title", "")[:200],
                )
            )
        return results

    def _to_silver_df(self, extractions: list[SECSectionExtraction], bronze_df: pl.DataFrame) -> pl.DataFrame:
        if not extractions:
            return pl.DataFrame()

        bronze_lookup = {row["article_id"]: row for row in bronze_df.to_dicts()}

        silver_rows: list[dict[str, Any]] = []
        for ext in extractions:
            bronze_row = bronze_lookup.get(ext.id, {})
            metadata = _parse_metadata(bronze_row.get("source_metadata"))

            filing_date = bronze_row.get("date") or bronze_row.get("published_at", date.today())
            if hasattr(filing_date, "date"):
                filing_date = filing_date.date()

            silver_rows.append(
                {
                    "article_id": ext.id,
                    "ticker": ext.ticker or metadata.get("ticker", ""),
                    "filing_type": metadata.get("filing_type", ""),
                    "section_type": ext.section_type,
                    "filing_date": filing_date,
                    "date": filing_date,
                    "risk_sentiment": ext.risk_sentiment,
                    "key_risks": json.dumps(ext.key_risks),
                    "guidance_direction": ext.guidance_direction,
                    "forward_statements": json.dumps(ext.forward_statements),
                    "management_tone": ext.management_tone,
                    "new_vs_repeated": ext.new_vs_repeated,
                    "summary": ext.summary,
                    "fetched_at": bronze_row.get("fetched_at"),
                }
            )

        df = pl.DataFrame(silver_rows) if silver_rows else pl.DataFrame()

        for col in ["risk_sentiment", "management_tone"]:
            if col in df.columns:
                df = df.with_columns(pl.col(col).cast(pl.Float64))

        return df


def run_sec_processing(bronze_df: pl.DataFrame, batch_size: int = 5) -> pl.DataFrame:
    """Synchronous wrapper for SEC section batch processing."""
    processor = SECSectionProcessor(batch_size=batch_size)
    return asyncio.run(processor.process_all(bronze_df))


def process_sec_bronze_to_silver(trading_date: date) -> bool:
    """Process unprocessed SEC filing sections from bronze to silver.

    Reads bronze articles with source_type="sec_filing", filters out
    already-processed sections, runs SEC LLM processing, and writes to
    the dedicated ``silver/sec_extractions`` Delta table.

    Args:
        trading_date: The trading date to process sections for.

    Returns:
        True if silver write succeeded, False otherwise.
    """
    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan = duckdb_scan_for(BRONZE_RAW_ARTICLES_DIR)
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL delta; LOAD delta;")
            bronze_df = con.execute(
                f"SELECT * FROM {scan} WHERE date = ? AND source_type = 'sec_filing'",
                [trading_date],
            ).pl()
        finally:
            con.close()
    except Exception as exc:
        logger.warning("sec_bronze_read_failed", error=str(exc))
        return False

    if bronze_df.is_empty():
        logger.info("No SEC filing sections to process", trading_date=str(trading_date))
        return False

    processed_ids = _get_processed_sec_ids(trading_date)
    if processed_ids:
        before = bronze_df.height
        bronze_df = bronze_df.filter(~pl.col("article_id").is_in(list(processed_ids)))
        skipped = before - bronze_df.height
        if skipped:
            logger.info("Skipping already-processed SEC sections", skipped=skipped, remaining=bronze_df.height)

    if bronze_df.is_empty():
        logger.info("All SEC sections already processed", trading_date=str(trading_date))
        return True

    logger.info("Processing SEC bronze to silver", section_count=bronze_df.height, trading_date=str(trading_date))

    try:
        silver_df = run_sec_processing(bronze_df)
    except Exception as exc:
        logger.error("sec_llm_processing_failed", error=str(exc))
        return False

    if silver_df.is_empty():
        logger.warning("SEC LLM processing produced no silver rows")
        return False

    ticker_filter = _load_known_tickers()
    if ticker_filter:
        silver_df = silver_df.filter(pl.col("ticker").is_null() | pl.col("ticker").is_in(ticker_filter))
        logger.info("Filtered SEC silver by known tickers", remaining=silver_df.height, known_tickers=len(ticker_filter))

    return _write_sec_silver(silver_df)


def _load_known_tickers() -> list[str]:
    try:
        from equity_lake.core.config import TickerConfig

        config = TickerConfig()
        return config.get_tickers_for_market("us", active_only=True)
    except Exception:
        return []


def _write_sec_silver(df: pl.DataFrame) -> bool:
    if df.is_empty():
        logger.warning("Empty DataFrame, skipping SEC silver write")
        return False

    for col in SEC_EXTRACTION_COLUMNS:
        if col not in df.columns:
            df = df.with_columns(pl.lit(None).alias(col))

    df = df.select(SEC_EXTRACTION_COLUMNS)
    SEC_EXTRACTIONS_DIR.mkdir(parents=True, exist_ok=True)
    return merge_delta(df, "silver/sec_extractions", key_columns=["article_id"])


def _get_processed_sec_ids(trading_date: date) -> set[str]:
    try:
        import duckdb

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan = duckdb_scan_for(SEC_EXTRACTIONS_DIR)
        con = duckdb.connect(":memory:")
        try:
            con.execute("INSTALL delta; LOAD delta;")
            rows = con.execute(
                f"SELECT DISTINCT article_id FROM {scan} WHERE date = ?",
                [trading_date],
            ).fetchall()
        finally:
            con.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


__all__ = [
    "SECBatchExtraction",
    "SECSectionExtraction",
    "SECSectionProcessor",
    "process_sec_bronze_to_silver",
    "run_sec_processing",
]
