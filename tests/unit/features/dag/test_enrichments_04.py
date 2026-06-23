"""Tests for Gold layer: external data enrichment joins."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import duckdb
import polars as pl
import pytest
from deltalake import write_deltalake

from equity_lake.features.dag.enrichments_04 import enriched_features


@pytest.fixture
def features_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 1)],
            "open": [150.0, 152.0, 380.0],
            "high": [155.0, 157.0, 385.0],
            "low": [148.0, 150.0, 378.0],
            "close": [152.0, 154.0, 382.0],
            "volume": [1_000_000.0, 1_100_000.0, 800_000.0],
        }
    )


@pytest.fixture
def mock_conn() -> MagicMock:
    conn = MagicMock(spec=["execute"])
    conn.execute.return_value.pl.return_value = pl.DataFrame()
    return conn


def test_enriched_features_empty_df(mock_conn: MagicMock) -> None:
    result = enriched_features(
        pl.DataFrame(),
        mock_conn,
        date(2024, 1, 1),
        date(2024, 1, 31),
    )
    assert result.is_empty()


def test_enriched_features_no_enrichments(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    """With all enrichments disabled, cross_modal still runs but adds nothing."""
    result = enriched_features(
        features_df,
        mock_conn,
        date(2024, 1, 1),
        date(2024, 1, 31),
        enable_news_sentiment=False,
        enable_social_sentiment=False,
        enable_enriched_sentiment=False,
        enable_analyst_ratings=False,
        enable_sec_features=False,
        enable_macro=False,
    )
    assert result.height == features_df.height


def test_enriched_features_preserves_columns(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    result = enriched_features(
        features_df,
        mock_conn,
        date(2024, 1, 1),
        date(2024, 1, 31),
        enable_macro=False,
    )
    for col in features_df.columns:
        assert col in result.columns


def test_merge_enriched_sentiment_populates_columns(features_df: pl.DataFrame, monkeypatch, tmp_path) -> None:
    """Regression test for missing GROUP BY (P0).

    Without ``GROUP BY ticker, date``, the aggregate query raised a DuckDB
    error that was swallowed, leaving enriched columns zero-filled forever.
    """
    import contextlib

    # Build a silver processed_articles Delta table with two articles for AAPL
    # on the same date — forces the aggregate to actually group.
    silver_dir = tmp_path / "processed_articles"
    articles = pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 1)],
            "filing_date": [date(2024, 1, 1), date(2024, 1, 1), date(2024, 1, 1)],
            "sentiment_score": [0.8, 0.6, -0.3],
            "sentiment_label": ["bullish", "bullish", "bearish"],
            "confidence": [0.9, 0.7, 0.8],
            "market_relevance": [0.95, 0.6, 0.5],
            "source_type": ["news", "reddit", "news"],
            "impact_horizon": ["short", "medium", "short"],
        }
    )
    write_deltalake(str(silver_dir), articles.to_arrow(), mode="append")

    # Point the module-level constant at the temp dir
    from equity_lake.features.dag import enrichments_04

    monkeypatch.setattr(enrichments_04, "SILVER_PROCESSED_ARTICLES_DIR", silver_dir)

    # Real DuckDB connection with delta extension
    conn = duckdb.connect(":memory:")
    with contextlib.suppress(Exception):
        conn.execute("INSTALL delta; LOAD delta;")

    from equity_lake.features.dag.enrichments_04 import _merge_enriched_sentiment

    result = _merge_enriched_sentiment(features_df, conn, date(2024, 1, 1), date(2024, 1, 2))

    # AAPL had two bullish articles on 2024-01-01 → enriched columns must be populated
    aapl_row = result.filter((pl.col("ticker") == "AAPL") & (pl.col("date") == date(2024, 1, 1)))
    assert aapl_row.height == 1
    assert aapl_row["enriched_article_count"][0] == 2
    assert aapl_row["enriched_sentiment_mean"][0] == pytest.approx(0.7)
    assert aapl_row["bullish_ratio"][0] == pytest.approx(1.0)
    # social_volume counts reddit + stocktwits sources (one reddit here)
    assert aapl_row["social_volume"][0] == 1
