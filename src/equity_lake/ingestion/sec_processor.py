"""SEC filing section extraction models and bounded Bronze-to-Silver entrypoint.

Unbounded LLM processor helpers fail closed until a bounded provider adapter
reports actual cost. ``process_sec_bronze_to_silver`` requires explicit replay
scope and an authorized cost-reporting callback for paid work.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import structlog
from pydantic import BaseModel, Field

from equity_lake.ingestion.llm_base import BaseLLMBatchProcessor
from equity_lake.ingestion.replay import EstimateBatchCost, ProcessBatch, ReplayAuthorizationError, ReplayScope

logger = structlog.get_logger()


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Safely parse source_metadata JSON, returning empty dict on failure."""
    if not raw:
        return {}
    try:
        return json.loads(raw) if isinstance(raw, str) else raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, TypeError):
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
    """Legacy SEC extraction formatter; inherited direct provider calls fail closed.

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
    """Fail closed: corpus-wide SEC processing has no bounded actual-cost adapter."""
    del bronze_df, batch_size
    raise ReplayAuthorizationError(
        "run_sec_processing is disabled because it cannot enforce a scoped spend cap or report provider actual cost. "
        "Use process_sec_bronze_to_silver with an explicitly authorized bounded callback."
    )


def process_sec_bronze_to_silver(
    trading_date: date,
    *,
    scope: ReplayScope | None = None,
    process_batch: ProcessBatch | None = None,
    estimate_batch_cost: EstimateBatchCost | None = None,
    paid_processing_authorized: bool = False,
    approved_estimate: Decimal | None = None,
    actual_spend_cap: Decimal = Decimal("0"),
    checkpoint_path: Path | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    """Run SEC extraction through the same explicitly scoped bounded replay.

    Paid work is disabled by default. Authorized processing requires a bounded
    callback with a per-batch estimate and provider-reported actual cost.
    """
    from equity_lake.core.paths import SEC_EXTRACTIONS_DIR
    from equity_lake.ingestion.bronze_silver import process_unstructured_to_silver

    if scope is None:
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update({"mode": "processing_failed", "error": "explicit replay scope is required"})
        return False
    if scope.source != "sec_filing":
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update({"mode": "processing_failed", "error": "SEC replay scope source must be sec_filing"})
        return False

    return process_unstructured_to_silver(
        trading_date,
        scope=scope,
        source_type_filter="sec_filing",
        process_batch=process_batch,
        estimate_batch_cost=estimate_batch_cost,
        paid_processing_authorized=paid_processing_authorized,
        approved_estimate=approved_estimate,
        actual_spend_cap=actual_spend_cap,
        checkpoint_path=checkpoint_path,
        silver_path=SEC_EXTRACTIONS_DIR,
        silver_table_name="02_silver/sec_extractions",
        silver_key_columns=["article_id"],
        log_label="SEC",
        diagnostics=diagnostics,
    )


__all__ = [
    "SECBatchExtraction",
    "SECSectionExtraction",
    "SECSectionProcessor",
    "process_sec_bronze_to_silver",
    "run_sec_processing",
]
