"""Tests for Gold layer: external data enrichment joins."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import polars as pl
import pytest

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
