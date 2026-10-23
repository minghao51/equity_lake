"""Two-phase enrichment regressions for fail-closed availability semantics."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import polars as pl
import pytest

from equity_lake.features.pipeline import FeaturePipeline  # type: ignore[import-untyped]
from equity_lake.storage.lake_reader import ensure_delta_extension  # type: ignore[import-untyped]


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@pytest.fixture()
def technical_frame() -> pl.DataFrame:
    """Real technical output without canonical identity or adjustment proof."""
    days = _business_days(date(2024, 1, 1), 90)
    close = [101.0 + index * 0.5 + (index % 7) * 0.2 for index in range(len(days))]
    open_ = [100.0 + index * 0.5 + (index % 5) * 0.1 for index in range(len(days))]
    prices = pl.DataFrame(
        {
            "ticker": ["AAPL"] * len(days),
            "date": days,
            "open": open_,
            "high": [max(open_value, close_value) + 1.0 for open_value, close_value in zip(open_, close, strict=True)],
            "low": [min(open_value, close_value) - 1.0 for open_value, close_value in zip(open_, close, strict=True)],
            "close": close,
            "volume": [1_000_000.0 + (index % 10) * 10_000 for index in range(len(days))],
        }
    )
    return FeaturePipeline().compute_technical(prices, include_target=True)


@pytest.fixture()
def conn() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    ensure_delta_extension(connection)
    return connection


def test_unproven_enrichments_remain_null_through_public_pipeline(technical_frame: pl.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    """Ticker-only technical rows cannot receive unproven external predictors."""
    result = FeaturePipeline().compute_enriched(
        technical_frame,
        duckdb_conn=conn,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 5, 3),
        enable_news_sentiment=True,
        enable_social_sentiment=True,
        enable_enriched_sentiment=True,
        enable_analyst_ratings=True,
        enable_sec_features=True,
    )

    for flag in (
        "news_available",
        "social_available",
        "enriched_available",
        "analyst_available",
        "sec_available",
        "macro_available",
    ):
        assert result[flag].unique().to_list() == [0]

    for predictor in (
        "avg_daily_sentiment",
        "news_count",
        "social_mention_count",
        "enriched_article_count",
        "analyst_consensus_score",
        "sec_risk_sentiment",
        "vix",
        "sentiment_x_log_volume",
        "social_sentiment_x_log_volume",
    ):
        assert result[predictor].null_count() == result.height, predictor

    assert result["date"].to_list() == technical_frame["date"].to_list()
    assert result["close"].to_list() == technical_frame["close"].to_list()


def test_disabled_enrichment_adds_no_optional_columns(technical_frame: pl.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    result = FeaturePipeline().compute_enriched(
        technical_frame,
        duckdb_conn=conn,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        enable_macro=False,
    )

    assert "news_count" not in result.columns
    assert "social_mention_count" not in result.columns


def test_datetime_feature_keys_normalize_to_date(technical_frame: pl.DataFrame, conn: duckdb.DuckDBPyConnection) -> None:
    datetime_frame = technical_frame.with_columns(pl.col("date").cast(pl.Datetime))
    start_date = date(2024, 1, 1)
    end_date = date(2024, 5, 3)

    result = FeaturePipeline().compute_enriched(
        datetime_frame,
        duckdb_conn=conn,
        start_date=start_date,
        end_date=end_date,
        enable_news_sentiment=True,
        enable_macro=False,
    )

    assert result.schema["date"] == pl.Date
    assert result["news_available"].unique().to_list() == [0]
    assert result["news_count"].null_count() == result.height
