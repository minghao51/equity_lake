"""Base class for LLM batch processors.

Provides the shared async infrastructure (DeepSeek client init, tenacity
retry, concurrent batch processing, fallback handling) used by both
``DeepSeekBatchProcessor`` (articles) and ``SECSectionProcessor`` (SEC filings).

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
import json
import os
from abc import ABC, abstractmethod
from typing import Any

import polars as pl
import structlog
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger()


class RetryableError(Exception):
    """Raised when DeepSeek returns empty content or invalid JSON."""


class BaseLLMBatchProcessor[BatchT: BaseModel, ItemT: BaseModel](ABC):
    """Generic async batch processor for LLM-based extraction.

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

    async def process_batch(self, batch: list[dict[str, Any]]) -> BatchT:
        @self._retry_decorator  # type: ignore[misc]
        async def _call() -> BatchT:
            async with self.semaphore:
                user_content = self._format_batch(batch)
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
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
                    return self.batch_model.model_validate(parsed)  # type: ignore[return-value]
                except ValidationError as e:
                    raise RetryableError(f"Validation failed: {e}") from e

        result: BatchT = await _call()
        return result

    async def process_all(self, bronze_df: pl.DataFrame) -> pl.DataFrame:
        if bronze_df.is_empty():
            logger.warning(f"Empty {self.log_label} bronze DataFrame, nothing to process")
            return pl.DataFrame()

        rows = bronze_df.to_dicts()
        batches = [rows[i : i + self.batch_size] for i in range(0, len(rows), self.batch_size)]

        logger.info(
            f"{self.log_label} batch processing starting",
            total_items=len(rows),
            batch_count=len(batches),
        )

        tasks = [self.process_batch(b) for b in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        extracted: list[ItemT] = []
        failed_count = 0

        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                failed_count += len(batches[i])
                logger.warning(
                    f"{self.log_label} batch failed, using fallback",
                    error=str(result),
                    batch_size=len(batches[i]),
                )
                extracted.extend(self._fallback(batches[i]))
            else:
                extracted.extend(result.items)  # type: ignore[attr-defined]

        logger.info(f"{self.log_label} processing complete", extracted=len(extracted), failed=failed_count)
        return self._to_silver_df(extracted, bronze_df)

    @abstractmethod
    def _format_batch(self, batch: list[dict[str, Any]]) -> str:
        """Format a list of bronze row dicts into LLM user content."""

    @abstractmethod
    def _fallback(self, batch: list[dict[str, Any]]) -> list[ItemT]:
        """Return fallback extractions when LLM processing fails."""

    @abstractmethod
    def _to_silver_df(self, extractions: list[ItemT], bronze_df: pl.DataFrame) -> pl.DataFrame:
        """Convert extractions to a silver Polars DataFrame."""


__all__ = ["BaseLLMBatchProcessor", "RetryableError"]
