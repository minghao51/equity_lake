"""Tests for bounded, scoped Bronze→Silver replay orchestration."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.ingestion.bronze_silver import MARKET_WIDE_INSTRUMENT, process_unstructured_to_silver  # type: ignore[import-untyped]
from equity_lake.ingestion.replay import ReplayBatchResult, ReplayScope  # type: ignore[import-untyped]

_TRADING_DATE = date(2026, 6, 14)


@pytest.fixture
def bronze_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "article_id": ["art-1", "art-2", "sec-1"],
            "source_type": ["rss", "reddit", "sec_filing"],
            "source_name": ["feed1", "r/wallstreetbets", "sec_edgar"],
            "source_url": ["u1", "u2", "u3"],
            "title": ["T1", "T2", "T3"],
            "body": ["B1", "B2", "B3"],
            "author": ["", "", ""],
            "published_at": [None, None, None],
            "fetched_at": [None, None, None],
            "source_metadata": ["{}", "{}", '{"ticker":"AAPL"}'],
            "date": [_TRADING_DATE] * 3,
        }
    )


def _scope(source: str = "rss") -> ReplayScope:
    instruments = ("AAPL",) if source == "sec_filing" else ("AAPL", MARKET_WIDE_INSTRUMENT)
    return ReplayScope(source=source, trading_date=_TRADING_DATE, market="us_equity", instruments=instruments)


def _call(
    bronze: pl.DataFrame,
    process_fn: Any,
    diagnostics: dict[str, Any] | None,
    tmp_path: Path,
    *,
    processed_ids: set[str] | None = None,
    merge_result: bool = True,
    source_type_filter: str | None = "rss",
    scope: ReplayScope | None = None,
) -> bool:
    durable_ids = set(processed_ids or ())
    chosen_scope = scope or _scope(source_type_filter or "rss")

    def process_batch(batch: Any, _budget: Any) -> ReplayBatchResult:
        batch_frame = pl.DataFrame([item.payload for item in batch])
        processed = process_fn(batch_frame)
        return ReplayBatchResult(rows=processed.to_dicts(), actual_cost=Decimal("0.01") * len(batch))

    def write_silver(frame: pl.DataFrame, _table: str, _keys: list[str]) -> bool:
        if merge_result:
            durable_ids.update(str(item_id) for item_id in frame["article_id"].to_list())
        return merge_result

    with (
        patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze),
        patch("equity_lake.ingestion.bronze_silver._get_processed_ids", side_effect=lambda *_: set(durable_ids)),
        patch("equity_lake.ingestion.bronze_silver._write_silver_generic", side_effect=write_silver),
    ):
        result = process_unstructured_to_silver(
            _TRADING_DATE,
            scope=chosen_scope,
            source_type_filter=source_type_filter,
            exclude_source_types=None,
            process_batch=process_batch,
            estimate_batch_cost=lambda batch: Decimal("0.10") * len(batch),
            paid_processing_authorized=True,
            approved_estimate=Decimal("1.00"),
            actual_spend_cap=Decimal("2.00"),
            checkpoint_path=tmp_path / "checkpoint.json",
            silver_path=tmp_path / "silver",
            silver_table_name="02_silver/processed_articles",
            silver_key_columns=["article_id", "ticker"],
            log_label="article",
            diagnostics=diagnostics,
        )
        return bool(result)


class TestSourceFiltering:
    def test_sec_filter_excludes_non_sec(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        """The SEC replay scope submits only rows with SEC source type and instrument."""
        captured: list[pl.DataFrame] = []

        def capture_process_fn(frame: pl.DataFrame) -> pl.DataFrame:
            captured.append(frame)
            return pl.DataFrame({"article_id": ["sec-1"], "ticker": ["AAPL"], "date": [_TRADING_DATE]})

        result = _call(
            bronze_df,
            capture_process_fn,
            None,
            tmp_path,
            source_type_filter="sec_filing",
            scope=_scope("sec_filing"),
        )

        assert result is True
        assert len(captured) == 1
        assert captured[0].height == 1
        assert captured[0]["article_id"].to_list() == ["sec-1"]

    def test_article_source_filter_processes_only_requested_source(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        captured: list[pl.DataFrame] = []

        def capture_process_fn(frame: pl.DataFrame) -> pl.DataFrame:
            captured.append(frame)
            ids = frame["article_id"].to_list()
            return pl.DataFrame({"article_id": ids, "ticker": [None] * len(ids), "date": [_TRADING_DATE] * len(ids)})

        result = _call(bronze_df, capture_process_fn, None, tmp_path)

        assert result is True
        assert len(captured) == 1
        assert captured[0]["article_id"].to_list() == ["art-1"]


class TestScopeValidation:
    def test_out_of_scope_output_is_rejected_before_silver_write(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        diagnostics: dict[str, Any] = {}
        calls: list[pl.DataFrame] = []

        def make_out_of_scope(frame: pl.DataFrame) -> pl.DataFrame:
            calls.append(frame)
            return pl.DataFrame(
                {
                    "article_id": ["art-1"],
                    "ticker": ["PENNY"],
                    "date": [_TRADING_DATE],
                }
            )

        with patch("equity_lake.ingestion.bronze_silver._write_silver_generic") as writer:
            result = _call(bronze_df, make_out_of_scope, diagnostics, tmp_path)

        assert result is False
        assert diagnostics["mode"] == "processing_failed"
        assert "out-of-scope instrument" in str(diagnostics["error"])
        assert calls
        writer.assert_not_called()


class TestProcessingFailure:
    def test_process_fn_exception_returns_false(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        def failing_fn(_frame: pl.DataFrame) -> pl.DataFrame:
            raise RuntimeError("LLM API down")

        diagnostics: dict[str, Any] = {}
        result = _call(bronze_df, failing_fn, diagnostics, tmp_path)

        assert result is False
        assert diagnostics["mode"] == "processing_failed"
        assert "LLM API down" in diagnostics["error"]


class TestReplayDiagnostics:
    """No-work, failure, and success outcomes remain distinguishable."""

    def test_empty_bronze_reports_no_work(self, tmp_path: Path) -> None:
        diagnostics: dict[str, Any] = {}
        result = _call(pl.DataFrame(), lambda _frame: pl.DataFrame(), diagnostics, tmp_path)
        assert result is False
        assert diagnostics["mode"] == "no_bronze_data"

    def test_all_already_processed_reports_planned_skip(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        diagnostics: dict[str, Any] = {}
        result = _call(
            bronze_df,
            lambda _frame: pl.DataFrame(),
            diagnostics,
            tmp_path,
            processed_ids={"art-1", "art-2", "sec-1"},
        )
        assert result is True
        assert diagnostics["mode"] == "already_processed"

    def test_processed_reports_row_count_and_actual_cost(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        diagnostics: dict[str, Any] = {}

        def process_fn(frame: pl.DataFrame) -> pl.DataFrame:
            ids = frame["article_id"].to_list()
            return pl.DataFrame({"article_id": ids, "ticker": [None] * len(ids), "date": [_TRADING_DATE] * len(ids)})

        result = _call(bronze_df, process_fn, diagnostics, tmp_path)
        assert result is True
        assert diagnostics["mode"] == "processed"
        assert diagnostics["rows"] == 1
        assert diagnostics["actual_spend"] == "0.01"
        assert diagnostics["remaining_allowance"] == "1.99"

    def test_write_failure_reports_mode_without_checkpoint(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        diagnostics: dict[str, Any] = {}

        def process_fn(_frame: pl.DataFrame) -> pl.DataFrame:
            return pl.DataFrame({"article_id": ["art-1"], "ticker": [None], "date": [_TRADING_DATE]})

        result = _call(bronze_df, process_fn, diagnostics, tmp_path, merge_result=False)
        assert result is False
        assert diagnostics["mode"] == "write_failed"
        assert not (tmp_path / "checkpoint.json").exists()

    def test_diagnostics_is_optional_for_already_processed_items(self, bronze_df: pl.DataFrame, tmp_path: Path) -> None:
        result = _call(
            bronze_df,
            lambda _frame: pl.DataFrame(),
            None,
            tmp_path,
            processed_ids={"art-1", "art-2", "sec-1"},
        )
        assert result is True
