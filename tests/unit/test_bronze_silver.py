"""Tests for bronze-to-silver transform."""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.ingestion.bronze_silver import MARKET_WIDE_INSTRUMENT, _get_processed_ids, _write_silver_generic  # type: ignore[import-untyped]
from equity_lake.ingestion.replay import PaidWorkBudget, ReplayBatchResult, ReplayItem, ReplayScope  # type: ignore[import-untyped]


def _silver_article_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "article_id": "art-1",
        "ticker": "AAPL",
        "source_type": "rss",
        "source_name": "test",
        "published_at": None,
        "date": date(2026, 6, 14),
        "sentiment_score": 0.7,
        "sentiment_label": "bullish",
        "confidence": 0.9,
        "event_type": "product",
        "summary": "Apple up",
        "impact_horizon": "short",
        "market_relevance": 0.8,
        "key_entities": '["Tim Cook"]',
        "source_metadata": '{"feed": "test"}',
    }
    row.update(overrides)
    return row


class TestWriteSilverGeneric:
    """The production silver write path (``_write_silver_generic``); ``write_silver`` was removed as dead code."""

    def test_empty_df_returns_false(self) -> None:
        result = _write_silver_generic(pl.DataFrame(), "02_silver/processed_articles", ["article_id", "ticker"])
        assert result is False

    def test_writes_with_correct_table_name(self) -> None:
        df = pl.DataFrame(
            {
                "article_id": ["art-1"],
                "ticker": ["AAPL"],
                "source_type": ["rss"],
                "source_name": ["test"],
                "published_at": [None],
                "date": [date(2026, 6, 14)],
                "sentiment_score": [0.7],
                "sentiment_label": ["bullish"],
                "confidence": [0.9],
                "event_type": ["product"],
                "summary": ["Apple up"],
                "impact_horizon": ["short"],
                "market_relevance": [0.8],
                "key_entities": ['["Tim Cook"]'],
                "source_metadata": ['{"feed": "test"}'],
            }
        )

        with patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True) as mock_merge:
            result = _write_silver_generic(df, "02_silver/processed_articles", ["article_id", "ticker"])
            assert result is True
            call_args = mock_merge.call_args
            assert call_args.kwargs["key_columns"] == ["article_id", "ticker"]
            # Regression (20260804 hygiene): silver writes must land under 02_silver/, not silver/
            assert call_args.args[1] == "02_silver/processed_articles"


class TestSilverQualityGate:
    """B3: silver merges enforce the pointblank article contract (ADR-0007)."""

    def test_generic_write_rejects_out_of_range_sentiment(self) -> None:
        df = pl.DataFrame([_silver_article_row(sentiment_score=5.0), _silver_article_row(article_id="art-2")])
        with patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True) as mock_merge:
            assert _write_silver_generic(df, "02_silver/processed_articles", ["article_id", "ticker"]) is False
        mock_merge.assert_not_called()

    def test_generic_write_accepts_valid_rows(self) -> None:
        df = pl.DataFrame([_silver_article_row(), _silver_article_row(article_id="art-2", ticker=None)])
        with patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True) as mock_merge:
            assert _write_silver_generic(df, "02_silver/processed_articles", ["article_id", "ticker"]) is True
        mock_merge.assert_called_once()

    def test_generic_silver_rejects_null_article_id(self) -> None:
        df = pl.DataFrame({"article_id": [None], "ticker": ["AAPL"]})
        assert _write_silver_generic(df, "02_silver/sec_extractions", ["article_id"]) is False

    def test_generic_silver_allows_null_ticker(self) -> None:
        """Market-wide news pairs (all-null ticker) are legitimate silver rows."""
        df = pl.DataFrame({"article_id": ["a-1", "a-2"], "ticker": [None, None]})
        with patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True) as mock_merge:
            assert _write_silver_generic(df, "02_silver/processed_articles", ["article_id", "ticker"]) is True
        mock_merge.assert_called_once()


class TestGetProcessedIds:
    def test_unrecognized_silver_state_fails_closed(self, tmp_path: Path) -> None:
        unrecognized = tmp_path / "silver"
        unrecognized.mkdir()
        (unrecognized / "unexpected.txt").write_text("unrecognized", encoding="utf-8")

        with pytest.raises(RuntimeError, match="Unable to read silver dedup table"):
            _get_processed_ids(unrecognized, date(2026, 6, 14))


class TestProcessBronzeToSilver:
    def test_no_bronze_returns_false(self, tmp_path: Path) -> None:
        scope = ReplayScope(
            source="rss",
            trading_date=date(2026, 6, 14),
            market="us_equity",
            instruments=(MARKET_WIDE_INSTRUMENT,),
        )
        with patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=pl.DataFrame()):
            from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

            result = process_bronze_to_silver(date(2026, 6, 14), scope=scope, checkpoint_path=tmp_path / "checkpoint.json")
            assert result is False

    def test_all_processed_returns_true(self, tmp_path: Path) -> None:
        bronze_df = pl.DataFrame(
            {
                "article_id": ["art-1", "art-2"],
                "source_type": ["rss", "reddit"],
                "source_name": ["feed1", "r/stocks"],
                "source_url": ["https://example.com/1", "https://reddit.com/1"],
                "title": ["T1", "T2"],
                "body": ["B1", "B2"],
                "author": ["", ""],
                "published_at": [None, None],
                "fetched_at": [None, None],
                "source_metadata": ["{}", "{}"],
                "date": [date(2026, 6, 14), date(2026, 6, 14)],
            }
        )
        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze_df),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", return_value={"art-1", "art-2"}),
        ):
            from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

            scope = ReplayScope(
                source="rss",
                trading_date=date(2026, 6, 14),
                market="us_equity",
                instruments=(MARKET_WIDE_INSTRUMENT,),
            )
            result = process_bronze_to_silver(date(2026, 6, 14), scope=scope, checkpoint_path=tmp_path / "checkpoint.json")
            assert result is True

    def test_filters_already_processed(self, tmp_path: Path) -> None:
        bronze_df = pl.DataFrame(
            {
                "article_id": ["art-1", "art-2", "art-3"],
                "source_type": ["rss", "rss", "rss"],
                "source_name": ["f1", "f2", "f3"],
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
        durable_ids = {"art-1"}
        scope = ReplayScope(
            source="rss",
            trading_date=date(2026, 6, 14),
            market="us_equity",
            instruments=(MARKET_WIDE_INSTRUMENT,),
        )
        captured: list[tuple[str, ...]] = []

        def process_batch(batch: Sequence[ReplayItem], _budget: PaidWorkBudget) -> ReplayBatchResult:
            item_ids = tuple(item.item_id for item in batch)
            captured.append(item_ids)
            return ReplayBatchResult(
                rows=[{"article_id": item_id, "ticker": None, "date": date(2026, 6, 14)} for item_id in item_ids],
                actual_cost=Decimal("0.02"),
            )

        def write_silver(frame: pl.DataFrame, _table: str, _keys: list[str]) -> bool:
            durable_ids.update(str(item_id) for item_id in frame["article_id"].to_list())
            return True

        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze_df),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", side_effect=lambda *_: set(durable_ids)),
            patch("equity_lake.ingestion.bronze_silver._write_silver_generic", side_effect=write_silver),
        ):
            from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

            result = process_bronze_to_silver(
                date(2026, 6, 14),
                scope=scope,
                process_batch=process_batch,
                estimate_batch_cost=lambda batch: Decimal("0.10") * len(batch),
                paid_processing_authorized=True,
                approved_estimate=Decimal("0.50"),
                actual_spend_cap=Decimal("1.00"),
                checkpoint_path=tmp_path / "checkpoint.json",
            )

            assert captured == [("art-2", "art-3")]
            assert durable_ids == {"art-1", "art-2", "art-3"}
            assert result is True
