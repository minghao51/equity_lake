"""Tests for bronze-to-silver transform."""

from datetime import date
from unittest.mock import patch

import polars as pl

from equity_lake.ingestion.bronze_silver import _get_processed_ids, _write_silver_generic


def _silver_article_row(**overrides) -> dict:
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

    def test_empty_df_returns_false(self):
        result = _write_silver_generic(pl.DataFrame(), "02_silver/processed_articles", ["article_id", "ticker"])
        assert result is False

    def test_writes_with_correct_table_name(self):
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

    def test_generic_write_rejects_out_of_range_sentiment(self):
        df = pl.DataFrame([_silver_article_row(sentiment_score=5.0), _silver_article_row(article_id="art-2")])
        with patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True) as mock_merge:
            assert _write_silver_generic(df, "02_silver/processed_articles", ["article_id", "ticker"]) is False
        mock_merge.assert_not_called()

    def test_generic_write_accepts_valid_rows(self):
        df = pl.DataFrame([_silver_article_row(), _silver_article_row(article_id="art-2", ticker=None)])
        with patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True) as mock_merge:
            assert _write_silver_generic(df, "02_silver/processed_articles", ["article_id", "ticker"]) is True
        mock_merge.assert_called_once()

    def test_generic_silver_rejects_null_article_id(self):
        df = pl.DataFrame({"article_id": [None], "ticker": ["AAPL"]})
        assert _write_silver_generic(df, "02_silver/sec_extractions", ["article_id"]) is False

    def test_generic_silver_allows_null_ticker(self):
        """Market-wide news pairs (all-null ticker) are legitimate silver rows."""
        df = pl.DataFrame({"article_id": ["a-1", "a-2"], "ticker": [None, None]})
        with patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True) as mock_merge:
            assert _write_silver_generic(df, "02_silver/processed_articles", ["article_id", "ticker"]) is True
        mock_merge.assert_called_once()


class TestGetProcessedIds:
    def test_returns_empty_set_on_error(self):
        from pathlib import Path

        with patch("equity_lake.ingestion.bronze_silver.SILVER_PROCESSED_ARTICLES_DIR"):
            result = _get_processed_ids(Path("/nonexistent"), date(2026, 6, 14))
            assert result == set()


class TestProcessBronzeToSilver:
    def test_no_bronze_returns_false(self):
        with patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=pl.DataFrame()):
            from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

            result = process_bronze_to_silver(date(2026, 6, 14))
            assert result is False

    def test_all_processed_returns_true(self):
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

            result = process_bronze_to_silver(date(2026, 6, 14))
            assert result is True

    def test_filters_already_processed(self):
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
        with (
            patch("equity_lake.ingestion.bronze_silver.read_bronze", return_value=bronze_df),
            patch("equity_lake.ingestion.bronze_silver._get_processed_ids", return_value={"art-1"}),
            patch("equity_lake.ingestion.llm_processor.run_llm_processing") as mock_llm,
            patch("equity_lake.ingestion.bronze_silver.merge_delta", return_value=True),
            patch("equity_lake.ingestion.bronze_silver._load_known_tickers", return_value=[]),
        ):
            from equity_lake.ingestion.bronze_silver import process_bronze_to_silver

            mock_llm.return_value = pl.DataFrame({"article_id": ["art-2", "art-3"], "ticker": [None, None]})
            result = process_bronze_to_silver(date(2026, 6, 14))

            passed_df = mock_llm.call_args[0][0]
            assert passed_df.height == 2
            assert "art-1" not in passed_df["article_id"].to_list()
            assert result is True
