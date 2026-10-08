"""Tests for unified bronze→silver pipeline (process_unstructured_to_silver)."""

from datetime import date
from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.ingestion.bronze_silver import process_unstructured_to_silver


@pytest.fixture
def bronze_df():
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
            "source_metadata": ["{}", "{}", "{}"],
            "date": [date(2026, 6, 14)] * 3,
        }
    )


class TestSourceFiltering:
    def test_sec_filter_excludes_non_sec(self, bronze_df):
        """Verify source_type_filter='sec_filing' only processes SEC rows."""
        captured_df = pl.DataFrame()

        def capture_process_fn(df):
            nonlocal captured_df
            captured_df = df
            return pl.DataFrame({"article_id": ["sec-1"], "ticker": ["AAPL"], "date": [date(2026, 6, 14)]})

        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze_df),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", return_value=set()),
            patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True),
            patch("equity_lake.ingestion.bronze_silver._load_known_tickers", return_value=[]),
        ):
            result = process_unstructured_to_silver(
                date(2026, 6, 14),
                source_type_filter="sec_filing",
                process_fn=capture_process_fn,
                silver_path=pl.DataFrame(),  # not used due to mocks
                silver_table_name="02_silver/sec_extractions",
                silver_key_columns=["article_id"],
                log_label="SEC",
            )

        assert result is True
        assert captured_df.height == 1
        assert captured_df["article_id"].to_list() == ["sec-1"]

    def test_no_filter_processes_all(self, bronze_df):
        captured_df = pl.DataFrame()

        def capture_process_fn(df):
            nonlocal captured_df
            captured_df = df
            return pl.DataFrame({"article_id": ["art-1", "art-2", "sec-1"], "ticker": [None, None, None], "date": [date(2026, 6, 14)] * 3})

        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze_df),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", return_value=set()),
            patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True),
            patch("equity_lake.ingestion.bronze_silver._load_known_tickers", return_value=[]),
        ):
            result = process_unstructured_to_silver(
                date(2026, 6, 14),
                source_type_filter=None,
                process_fn=capture_process_fn,
                silver_path=pl.DataFrame(),
                silver_table_name="02_silver/processed_articles",
                silver_key_columns=["article_id", "ticker"],
                log_label="article",
            )

        assert result is True
        assert captured_df.height == 3


class TestTickerFiltering:
    def test_filters_unknown_tickers(self, bronze_df):
        process_result = pl.DataFrame(
            {
                "article_id": ["art-1", "art-2"],
                "ticker": ["AAPL", "PENNY"],
                "date": [date(2026, 6, 14)] * 2,
            }
        )

        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze_df),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", return_value=set()),
            patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True),
            patch("equity_lake.ingestion.bronze_silver._load_known_tickers", return_value=["AAPL", "MSFT"]),
        ):
            result = process_unstructured_to_silver(
                date(2026, 6, 14),
                source_type_filter=None,
                process_fn=lambda df: process_result,
                silver_path=pl.DataFrame(),
                silver_table_name="02_silver/processed_articles",
                silver_key_columns=["article_id", "ticker"],
            )

        assert result is True


class TestProcessingFailure:
    def test_process_fn_exception_returns_false(self, bronze_df):
        def failing_fn(_):
            raise RuntimeError("LLM API down")

        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze_df),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", return_value=set()),
        ):
            result = process_unstructured_to_silver(
                date(2026, 6, 14),
                source_type_filter=None,
                process_fn=failing_fn,
                silver_path=pl.DataFrame(),
                silver_table_name="02_silver/processed_articles",
                silver_key_columns=["article_id", "ticker"],
            )

        assert result is False


class TestW02Diagnostics:
    """W02/F25: no-work is distinguishable from failure at the reporting seam."""

    def _call(self, bronze, process_fn, diagnostics, *, processed_ids=None, merge_result=True):
        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", return_value=processed_ids or set()),
            patch("equity_lake.ingestion.bronze_silver._load_known_tickers", return_value=[]),
            patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=merge_result),
        ):
            return process_unstructured_to_silver(
                date(2026, 6, 14),
                source_type_filter=None,
                process_fn=process_fn,
                silver_path=pl.DataFrame(),
                silver_table_name="02_silver/processed_articles",
                silver_key_columns=["article_id", "ticker"],
                diagnostics=diagnostics,
            )

    def test_empty_bronze_reports_no_work(self):
        diagnostics: dict = {}
        result = self._call(pl.DataFrame(), lambda df: pl.DataFrame(), diagnostics)
        assert result is False
        assert diagnostics["mode"] == "no_bronze_data"

    def test_all_already_processed_reports_no_work_success(self, bronze_df):
        diagnostics: dict = {}
        result = self._call(
            bronze_df,
            lambda df: pl.DataFrame(),
            diagnostics,
            processed_ids={"art-1", "art-2", "sec-1"},
        )
        assert result is True
        assert diagnostics["mode"] == "already_processed"

    def test_processed_reports_row_count(self, bronze_df):
        diagnostics: dict = {}

        def process_fn(df):
            return pl.DataFrame({"article_id": ["art-1", "art-2", "sec-1"], "ticker": [None, None, None], "date": [date(2026, 6, 14)] * 3})

        result = self._call(bronze_df, process_fn, diagnostics)
        assert result is True
        assert diagnostics["mode"] == "processed"
        assert diagnostics["rows"] == 3

    def test_processing_failure_reports_mode_and_cause(self, bronze_df):
        diagnostics: dict = {}

        def failing_fn(_):
            raise RuntimeError("LLM API down")

        result = self._call(bronze_df, failing_fn, diagnostics)
        assert result is False
        assert diagnostics["mode"] == "processing_failed"
        assert "LLM API down" in diagnostics["error"]

    def test_write_failure_reports_mode(self, bronze_df):
        diagnostics: dict = {}

        def process_fn(df):
            return pl.DataFrame({"article_id": ["art-1"], "ticker": ["AAPL"], "date": [date(2026, 6, 14)]})

        result = self._call(bronze_df, process_fn, diagnostics, merge_result=False)
        assert result is False
        assert diagnostics["mode"] == "write_failed"

    def test_diagnostics_is_optional(self, bronze_df):
        """The boolean contract is unchanged for callers that do not pass diagnostics."""
        result = self._call(bronze_df, lambda df: pl.DataFrame(), None, processed_ids={"art-1", "art-2", "sec-1"})
        assert result is True
