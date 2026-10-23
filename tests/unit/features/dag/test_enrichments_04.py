"""Tests for Gold layer: external data enrichment joins."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest.mock import MagicMock

import duckdb
import polars as pl
import pytest
from hamilton import base, driver
from hamilton.plugins import h_polars

from equity_lake.features.dag import enrichments_04  # type: ignore[import-untyped]
from equity_lake.features.dag.enrichments_04 import (  # type: ignore[import-untyped]
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


def test_legacy_enriched_sentiment_is_explicitly_unavailable(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    """Ticker-only enriched sentiment has no trusted identity or source clock."""
    result = enrichments_04._merge_enriched_sentiment(features_df, mock_conn, date(2024, 1, 1), date(2024, 1, 2))
    assert result["enriched_available"].to_list() == [0] * features_df.height
    assert result["enriched_article_count"].null_count() == features_df.height
    assert result["enriched_sentiment_mean"].null_count() == features_df.height
    assert result["social_volume"].null_count() == features_df.height


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


def test_macro_enrichment_metadata_describes_fail_closed_availability() -> None:
    """The catalog description must match the macro node's current behavior."""
    macro_node = next(node for node in _build_driver().list_available_variables() if node.name == "macro_enriched")

    assert macro_node.tags["description"] == (
        "Fail closed: macro values remain null until persisted source vintages verify point-in-time availability (ADR-0015)"
    )
    assert "unavailable" in (enrichments_04.macro_enriched.__doc__ or "")
    assert "verified" in (enrichments_04.__doc__ or "")


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


def test_news_without_identity_or_provenance_is_unavailable(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    result = _merge_news_sentiment(features_df, mock_conn, date(2024, 1, 1), date(2024, 1, 31))
    assert result.height == features_df.height
    assert result["news_available"].to_list() == [0] * features_df.height
    assert result["news_count"].null_count() == features_df.height
    assert result["avg_daily_sentiment"].null_count() == features_df.height


def test_social_without_identity_or_provenance_is_unavailable(features_df: pl.DataFrame, mock_conn: MagicMock) -> None:
    result = _merge_social_sentiment(features_df, mock_conn, date(2024, 1, 1), date(2024, 1, 31))
    assert result.height == features_df.height
    assert result["social_available"].to_list() == [0] * features_df.height
    assert result["social_mention_count"].null_count() == features_df.height
    assert result["social_sentiment_score"].null_count() == features_df.height


def test_social_partial_source_null_metrics_and_duplicates_stay_distinct(monkeypatch: pytest.MonkeyPatch, mock_conn: MagicMock) -> None:
    source_time = datetime(2024, 1, 2, 15, 0, tzinfo=UTC)
    observations = pl.DataFrame(
        {
            "market": ["us_equity"] * 3,
            "mic": ["XNYS"] * 3,
            "symbol": ["AAPL"] * 3,
            "ticker": ["AAPL"] * 3,
            "date": [date(2024, 1, 2)] * 3,
            "datetime": [source_time, source_time, source_time.replace(hour=16)],
            "source": ["reddit", "reddit", "twitter"],
            "source_available_at": [source_time, source_time, source_time.replace(hour=16)],
            "mention_count": [5, 5, 0],
            "score": [0.2, 0.2, None],
            "positive_score": [2.0, 2.0, None],
            "negative_score": [None, None, None],
        }
    )
    monkeypatch.setattr(enrichments_04.pl, "read_delta", lambda _path: observations)
    features = pl.DataFrame(
        {
            "market": ["us_equity", "us_equity"],
            "mic": ["XNYS", "XNYS"],
            "symbol": ["AAPL", "AAPL"],
            "ticker": ["AAPL", "AAPL"],
            "date": [date(2024, 1, 3), date(2024, 1, 12)],
        }
    )

    result = _merge_social_sentiment(features, mock_conn, date(2024, 1, 1), date(2024, 1, 12))
    assert result["social_available"].to_list() == [1, 0]
    assert result["social_sentiment_score"][0] == pytest.approx(0.2)
    assert result["social_sentiment_score"][1] is None
    assert result["social_mention_count"].to_list() == [5, None]
    assert result["social_reddit_mentions"].to_list() == [5, None]
    assert result["social_reddit_available"].to_list() == [1, 0]
    # A source-reported zero is measured; a source with no row is unavailable.
    assert result["social_twitter_mentions"].to_list() == [0, None]
    assert result["social_twitter_available"].to_list() == [1, 0]
    assert result["social_positive_score"].to_list() == [2.0, None]
    assert result["social_positive_available"].to_list() == [1, 0]
    assert result["social_negative_score"].null_count() == 2
    assert result["social_negative_available"].to_list() == [0, 0]


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

    # Unavailable social values stay null through cross-modal calculations.
    assert "social_sentiment_momentum" in cross.columns
    assert "social_sentiment_momentum_5d" in cross.columns
    assert cross["social_available"].to_list() == [0] * len(dates)
    assert cross["social_sentiment_momentum_5d"].null_count() == len(dates)
    assert cross["social_sentiment_momentum"].null_count() == len(dates)
