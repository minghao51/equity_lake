"""Tests for Gold layer: external data enrichment joins."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import duckdb
import polars as pl
import pytest
from deltalake import write_deltalake
from hamilton import base, driver
from hamilton.plugins import h_polars

from equity_lake.features.dag import enrichments_04
from equity_lake.features.dag.enrichments_04 import (
    _add_cross_modal,
    _merge_news_sentiment,
    _merge_social_sentiment,
)


def _build_driver() -> driver.Driver:
    """Build a Hamilton driver over the enrichment chain only."""
    adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
    return driver.Builder().with_modules(enrichments_04).with_adapter(adapter).build()


def _run_enriched(
    features_df: pl.DataFrame,
    conn: MagicMock | duckdb.DuckDBPyConnection,
    *,
    enable_news_sentiment: bool = False,
    enable_social_sentiment: bool = False,
    enable_enriched_sentiment: bool = False,
    enable_analyst_ratings: bool = False,
    enable_sec_features: bool = False,
    enable_macro: bool = True,
) -> pl.DataFrame:
    """Execute the full enrichment chain via the driver (mirrors FeaturePipeline.compute_enriched)."""
    dr = _build_driver()
    result = dr.execute(
        ["enriched_features"],
        inputs={
            "features_df": features_df,
            "duckdb_conn": conn,
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 1, 31),
            "enable_news_sentiment": enable_news_sentiment,
            "enable_social_sentiment": enable_social_sentiment,
            "enable_enriched_sentiment": enable_enriched_sentiment,
            "enable_analyst_ratings": enable_analyst_ratings,
            "enable_sec_features": enable_sec_features,
            "enable_macro": enable_macro,
        },
    )
    return result if isinstance(result, pl.DataFrame) else pl.DataFrame(result)


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
    # spec=duckdb.DuckDBPyConnection so Hamilton's input-type validation accepts the mock.
    conn = MagicMock(spec=duckdb.DuckDBPyConnection)
    conn.execute.return_value.pl.return_value = pl.DataFrame()
    return conn


def test_enriched_features_empty_df(mock_conn: MagicMock) -> None:
    result = _run_enriched(pl.DataFrame(), mock_conn)
    assert result.is_empty()


def test_enriched_features_no_enrichments(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    """With all enrichments disabled, cross_modal still runs but adds nothing."""
    result = _run_enriched(
        features_df,
        mock_conn,
        enable_news_sentiment=False,
        enable_social_sentiment=False,
        enable_enriched_sentiment=False,
        enable_analyst_ratings=False,
        enable_sec_features=False,
        enable_macro=False,
    )
    assert result.height == features_df.height


def test_enriched_features_preserves_columns(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    result = _run_enriched(features_df, mock_conn, enable_macro=False)
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


def test_enrichment_chain_nodes_exposed() -> None:
    """All 8 enrichment nodes are registered in the DAG (regression for the single-node collapse)."""
    dr = _build_driver()
    node_names = {n.name if hasattr(n, "name") else str(n) for n in dr.list_available_variables()}
    for expected in [
        "news_sentiment_enriched",
        "social_sentiment_enriched",
        "enriched_sentiment_merged",
        "analyst_ratings_enriched",
        "sec_extractions_enriched",
        "cross_modal_features",
        "macro_enriched",
        "enriched_features",
    ]:
        assert expected in node_names, f"{expected} node missing from enrichment chain"


def _populated_news_agg() -> pl.DataFrame:
    """Aggregated news-sentiment shape returned by the DuckDB query on success."""
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 1)],
            "avg_daily_sentiment": [0.4, -0.2, 0.1],
            "news_count": [3, 1, 2],
            "positive_count": [2, 0, 1],
            "negative_count": [0, 1, 0],
            "neutral_count": [1, 0, 1],
            "sentiment_std": [0.1, 0.0, 0.05],
        }
    )


def _populated_social_agg() -> pl.DataFrame:
    """Aggregated social-sentiment shape returned by the DuckDB query on success."""
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "AAPL", "MSFT"],
            "date": [date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 1)],
            "social_mention_count": [10, 5, 7],
            "social_sentiment_score": [0.3, -0.1, 0.2],
            "social_positive_score": [4.0, 1.0, 3.0],
            "social_negative_score": [1.0, 2.0, 0.5],
            "social_reddit_mentions": [6, 3, 4],
            "social_twitter_mentions": [4, 2, 3],
        }
    )


def _raising_conn(exc: Exception) -> MagicMock:
    conn = MagicMock(spec=duckdb.DuckDBPyConnection)
    conn.execute.side_effect = exc
    return conn


def test_news_merge_error_path_keeps_feature_schema(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    """A5 (handoff 08): a transient DuckDB/Polars error must not change the news
    feature column set — the error path returns the same zero-filled defaults
    as the no-data path, so train/inference schemas stay stable."""
    mock_conn.execute.return_value.pl.return_value = _populated_news_agg()
    success = _merge_news_sentiment(features_df, mock_conn, date(2024, 1, 1), date(2024, 1, 31))
    assert {"avg_daily_sentiment", "sentiment_ewma_3d", "sentiment_ewma_30d"}.issubset(success.columns)

    mock_conn.execute.return_value.pl.return_value = pl.DataFrame()
    no_data = _merge_news_sentiment(features_df, mock_conn, date(2024, 1, 1), date(2024, 1, 31))
    error = _merge_news_sentiment(features_df, _raising_conn(duckdb.Error("table missing")), date(2024, 1, 1), date(2024, 1, 31))

    assert error.columns == success.columns == no_data.columns
    assert error.height == features_df.height
    # Zero-filled defaults, not silent column loss.
    assert set(error["news_count"].to_list()) == {0}


def test_social_merge_error_path_keeps_feature_schema(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    """A5 (handoff 08): a transient error in the social merge must not change the
    social feature column set downstream."""
    mock_conn.execute.return_value.pl.return_value = _populated_social_agg()
    success = _merge_social_sentiment(features_df, mock_conn, date(2024, 1, 1), date(2024, 1, 31))
    assert {"social_sentiment_score", "social_momentum", "social_sentiment_ewma_30d"}.issubset(success.columns)

    mock_conn.execute.return_value.pl.return_value = pl.DataFrame()
    no_data = _merge_social_sentiment(features_df, mock_conn, date(2024, 1, 1), date(2024, 1, 31))
    error = _merge_social_sentiment(features_df, _raising_conn(duckdb.Error("table missing")), date(2024, 1, 1), date(2024, 1, 31))

    assert error.columns == success.columns == no_data.columns
    assert error.height == features_df.height
    assert set(error["social_mention_count"].to_list()) == {0}


def test_social_momentum_5d_reuses_computed_momentum(mock_conn: MagicMock) -> None:
    """A6 (handoff 08): ``social_sentiment_momentum_5d`` is the identical
    ``diff(5)`` expression as ``social_sentiment_momentum`` — compute once,
    alias for both output names. Values (and the schema) must stay unchanged."""
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(8)]
    scores = [0.1 * ((i % 5) - 2) for i in range(8)]
    features = pl.DataFrame(
        {
            "ticker": ["AAPL"] * 8,
            "date": dates,
            "close": [150.0 + i for i in range(8)],
            "volume": [1_000_000.0] * 8,
        }
    )
    agg = pl.DataFrame(
        {
            "ticker": ["AAPL"] * 8,
            "date": dates,
            "social_mention_count": [5 + i for i in range(8)],
            "social_sentiment_score": scores,
            "social_positive_score": [1.0] * 8,
            "social_negative_score": [1.0] * 8,
            "social_reddit_mentions": [3] * 8,
            "social_twitter_mentions": [2] * 8,
        }
    )
    mock_conn.execute.return_value.pl.return_value = agg

    merged = _merge_social_sentiment(features, mock_conn, dates[0], dates[-1])
    cross = _add_cross_modal(merged)

    # Both output names present (schema unchanged) and perfectly correlated —
    # aliasing keeps them bit-identical.
    assert "social_sentiment_momentum" in cross.columns
    assert "social_sentiment_momentum_5d" in cross.columns
    assert cross["social_sentiment_momentum_5d"].to_list() == cross["social_sentiment_momentum"].to_list()

    # Values identical to the pre-fix independent diff(5) recompute.
    expected = pl.DataFrame({"score": scores}).with_columns(pl.col("score").diff(5).fill_null(0.0))["score"].to_list()
    assert cross["social_sentiment_momentum_5d"].to_list() == expected
