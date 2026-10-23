"""Fail-closed tests for unbounded DeepSeek processor entrypoints."""

import asyncio
from unittest.mock import AsyncMock, patch

import polars as pl
import pytest

from equity_lake.ingestion.llm_processor import DeepSeekBatchProcessor, run_llm_processing  # type: ignore[import-untyped]
from equity_lake.ingestion.replay import ReplayAuthorizationError  # type: ignore[import-untyped]


def _bronze_frame() -> pl.DataFrame:
    return pl.DataFrame({"article_id": ["art-1"], "source_type": ["rss"], "title": ["T"], "body": ["B"]})


class TestUnboundedProcessorGates:
    def test_direct_batch_call_fails_before_provider_request(self) -> None:
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            processor = DeepSeekBatchProcessor()
        processor.client.chat.completions.create = AsyncMock()

        with pytest.raises(ReplayAuthorizationError, match="Direct LLM batch calls are disabled"):
            asyncio.run(processor.process_batch([{"article_id": "art-1", "title": "T", "body": "B"}]))
        processor.client.chat.completions.create.assert_not_called()

    def test_corpus_process_all_fails_before_provider_request(self) -> None:
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            processor = DeepSeekBatchProcessor()
        processor.client.chat.completions.create = AsyncMock()

        with pytest.raises(ReplayAuthorizationError, match="Unbounded LLM corpus processing"):
            asyncio.run(processor.process_all(_bronze_frame()))
        processor.client.chat.completions.create.assert_not_called()

    def test_empty_process_all_is_a_free_noop(self) -> None:
        with patch.dict("os.environ", {"DEEPSEEK_API_KEY": "test-key"}):
            processor = DeepSeekBatchProcessor()

        assert asyncio.run(processor.process_all(pl.DataFrame())).is_empty()

    def test_legacy_sync_helper_fails_before_processor_construction(self) -> None:
        with (
            patch("equity_lake.ingestion.llm_processor.DeepSeekBatchProcessor") as processor,
            pytest.raises(ReplayAuthorizationError, match="run_llm_processing is disabled"),
        ):
            run_llm_processing(_bronze_frame())
        processor.assert_not_called()

    def test_missing_api_key_still_fails_at_client_seam(self) -> None:
        with patch.dict("os.environ", {}, clear=True), pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
            DeepSeekBatchProcessor()
