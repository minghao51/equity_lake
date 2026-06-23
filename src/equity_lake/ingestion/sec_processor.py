"""LLM batch processor for SEC filing section analysis.

Uses ``AsyncOpenAI`` with DeepSeek's OpenAI-compatible API to extract
risk sentiment, guidance direction, management tone, and key risks from
10-K/10-Q filing sections. Same tenacity retry + fallback pattern
as ``llm_processor.py``, via shared ``BaseLLMBatchProcessor``.

Process flow:
    bronze (source_type="sec_filing") → this processor → silver/sec_extractions
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import date
from typing import Any

import polars as pl
import structlog
from pydantic import BaseModel, Field

from equity_lake.ingestion.llm_base import BaseLLMBatchProcessor

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


class SECSectionProcessor(BaseLLMBatchProcessor[SECBatchExtraction, SECSectionExtraction]):
    """Batch-process SEC filing sections via DeepSeek for structured extraction.

    Args:
        batch_size: Number of sections per API call (default 5 — SEC text is dense).
        max_concurrency: Maximum parallel API calls (default 5).
        model: DeepSeek model name.
        max_body_chars: Max input body length per section.
    """

    system_prompt = SEC_SYSTEM_PROMPT
    batch_model = SECBatchExtraction
    log_label = "SEC"

    def __init__(
        self,
        batch_size: int = 5,
        max_concurrency: int = 5,
        model: str = "deepseek-v4-flash",
        max_body_chars: int = 5000,
    ):
        super().__init__(batch_size=batch_size, max_concurrency=max_concurrency, model=model, max_body_chars=max_body_chars)

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

    def _fallback(self, batch: list[dict[str, Any]]) -> list[SECSectionExtraction]:
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

    Delegates to :func:`process_unstructured_to_silver` with SEC-specific
    parameters (``source_type="sec_filing"``, dedicated silver table).

    Args:
        trading_date: The trading date to process sections for.

    Returns:
        True if silver write succeeded, False otherwise.
    """
    from equity_lake.core.paths import SEC_EXTRACTIONS_DIR
    from equity_lake.ingestion.bronze_silver import process_unstructured_to_silver

    return process_unstructured_to_silver(
        trading_date,
        source_type_filter="sec_filing",
        process_fn=run_sec_processing,
        silver_path=SEC_EXTRACTIONS_DIR,
        silver_table_name="silver/sec_extractions",
        silver_key_columns=["article_id"],
        log_label="SEC",
    )


__all__ = [
    "SECBatchExtraction",
    "SECSectionExtraction",
    "SECSectionProcessor",
    "process_sec_bronze_to_silver",
    "run_sec_processing",
]
