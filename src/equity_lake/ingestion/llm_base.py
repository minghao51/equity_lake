"""Base utilities for legacy LLM batch processors.

Client construction remains available for compatibility, but direct batch and
corpus-processing calls fail closed until a bounded provider adapter can report
actual cost. The supported paid-work boundary is an explicitly authorized replay
callback with finite estimates and provider-reported actual costs.

Subclasses provide:
    - ``system_prompt``: LLM system prompt text
    - ``batch_model``: Pydantic model with an ``items: list[...]`` field
    - ``_format_batch``: Format a list of bronze dicts into LLM user content
    - ``_fallback``: Return extractions when LLM fails (VADER/neutral)
    - ``_to_silver_df``: Convert extractions to silver Polars DataFrame
    - ``log_label``: String used in log messages
"""

from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from typing import Any

import polars as pl
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel

from equity_lake.ingestion.replay import ReplayAuthorizationError

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# LLM provider clients — the OpenAI SDK pointed at DeepSeek via base_url. Keys
# stay raw/unprefixed and are read via os.getenv at this seam only (never
# declared in Settings), matching FRED_API_KEY / WANDB_API_KEY.
# ---------------------------------------------------------------------------
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def build_chat_client() -> AsyncOpenAI:
    """DeepSeek chat-completions client (OpenAI-compatible)."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not set")
    return AsyncOpenAI(api_key=api_key, base_url=DEEPSEEK_BASE_URL)


class RetryableError(Exception):
    """Raised when DeepSeek returns empty content or invalid JSON."""


class BaseLLMBatchProcessor[BatchT: BaseModel, ItemT: BaseModel](ABC):
    """Legacy LLM processor whose unbounded execution methods are disabled.

    Args:
        batch_size: Number of items per API call.
        max_concurrency: Maximum parallel API calls.
        model: DeepSeek model name.
        max_body_chars: Max input body length per item.
    """

    system_prompt: str
    batch_model: type[BaseModel]
    log_label: str = "LLM"

    def __init__(
        self,
        batch_size: int,
        max_concurrency: int,
        model: str = "deepseek-v4-flash",
        max_body_chars: int = 2000,
    ):
        self.client = build_chat_client()
        self.model = model
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.max_body_chars = max_body_chars

    async def process_batch(self, batch: list[dict[str, Any]]) -> BatchT:
        """Reject direct provider calls without bounded authorization/cost accounting."""
        del batch
        raise ReplayAuthorizationError(
            "Direct LLM batch calls are disabled until a bounded provider adapter reports actual cost. "
            "Use an explicitly authorized replay callback with estimate and actual-cost reporting."
        )

    async def process_all(self, bronze_df: pl.DataFrame) -> pl.DataFrame:
        """Reject corpus-wide processing, which cannot enforce a scoped spend cap."""
        if bronze_df.is_empty():
            logger.warning("llm_empty_bronze_input", log_label=self.log_label)
            return pl.DataFrame()
        raise ReplayAuthorizationError(
            "Unbounded LLM corpus processing is disabled until a bounded provider adapter reports actual cost. "
            "Use an explicitly authorized replay callback with estimate and actual-cost reporting."
        )

    @abstractmethod
    def _format_batch(self, batch: list[dict[str, Any]]) -> str:
        """Format a list of bronze row dicts into LLM user content."""

    @abstractmethod
    def _fallback(self, batch: list[dict[str, Any]]) -> list[ItemT]:
        """Return fallback extractions when LLM processing fails."""

    @abstractmethod
    def _to_silver_df(self, extractions: list[ItemT], bronze_df: pl.DataFrame) -> pl.DataFrame:
        """Convert extractions to a silver Polars DataFrame."""


__all__ = [
    "BaseLLMBatchProcessor",
    "DEEPSEEK_BASE_URL",
    "RetryableError",
    "build_chat_client",
]
