"""W01 regression: real technical output through enrichment over Delta tables.

Exercises the *public* two-phase pipeline (``compute_technical`` →
``compute_enriched``) against temporary Delta tables in the real silver/bronze
shapes — not isolated mocks. Guards the F01 date-key contract, F15 finite-value
semantics, and the disabled/missing/error enrichment branches.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import polars as pl
import pytest
from deltalake import write_deltalake

import equity_lake.features.dag.enrichments_04 as e4
from equity_lake.features.pipeline import FeaturePipeline
from equity_lake.storage.lake_reader import ensure_delta_extension


def _business_days(start: date, n: int) -> list[date]:
    days: list[date] = []
    current = start
    while len(days) < n:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return days


@pytest.fixture()
def technical_frame() -> pl.DataFrame:
    """Real technical output from the public compute method (>= 80 obs)."""
    days = _business_days(date(2024, 1, 1), 90)
    close = [101.0 + i * 0.5 + (i % 7) * 0.2 for i in range(len(days))]
    open_ = [100.0 + i * 0.5 + (i % 5) * 0.1 for i in range(len(days))]
    prices = pl.DataFrame(
        {
            "ticker": ["AAPL"] * len(days),
            "date": days,
            "open": open_,
            "high": [max(o, c) + 1.0 for o, c in zip(open_, close, strict=True)],
            "low": [min(o, c) - 1.0 for o, c in zip(open_, close, strict=True)],
            "close": close,
            "volume": [1_000_000.0 + (i % 10) * 10_000 for i in range(len(days))],
        }
    )
    return FeaturePipeline().compute_technical(prices, include_target=True)


@pytest.fixture()
def conn() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    ensure_delta_extension(connection)
    return connection


@pytest.fixture()
def lake_dirs(tmp_path, monkeypatch):
    """Point every enrichment source at temporary Delta tables."""
    dirs = {
        "news": tmp_path / "silver_news_sentiment",
        "social": tmp_path / "silver_social_sentiment",
        "analyst": tmp_path / "silver_analyst_ratings",
        "sec": tmp_path / "silver_sec_extractions",
        "macro": tmp_path / "bronze_macro",
    }
    monkeypatch.setattr(e4, "SILVER_NEWS_SENTIMENT_DIR", dirs["news"])
    monkeypatch.setattr(e4, "SILVER_SOCIAL_SENTIMENT_DIR", dirs["social"])
    monkeypatch.setattr(e4, "SILVER_ANALYST_RATINGS_DIR", dirs["analyst"])
    monkeypatch.setattr(e4, "SILVER_SEC_EXTRACTIONS_DIR", dirs["sec"])
    monkeypatch.setattr(e4, "BRONZE_MACRO_DIR", dirs["macro"])
    return dirs


def _write(path, frame: pl.DataFrame) -> None:
    write_deltalake(str(path), frame.to_arrow(), mode="append")


class TestPopulatedEnrichmentsPreserveData:
    """F01: matching data survives when technical output joins external tables."""

    def test_news_values_survive_join_with_date_keys(self, technical_frame, conn, lake_dirs) -> None:
        days = technical_frame["date"].to_list()
        _write(
            lake_dirs["news"],
            pl.DataFrame(
                {
                    "ticker": ["AAPL"] * 3,
                    "date": [days[20], days[20], days[21]],
                    "datetime": [datetime(2024, 1, 1, 10, 0)] * 3,
                    "source": ["fn", "fn", "fn"],
                    "headline": ["a", "b", "c"],
                    "summary": ["x", "y", "z"],
                    "url": ["u1", "u2", "u3"],
                    "category": ["n", "n", "n"],
                    "sentiment_score": [0.8, 0.2, -0.5],
                    "sentiment_label": ["positive", "positive", "negative"],
                    "relevance_score": [0.9, 0.8, 0.7],
                }
            ),
        )
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=days[0],
            end_date=days[-1],
            enable_news_sentiment=True,
            enable_macro=False,
        )
        row_20 = result.filter(pl.col("date") == days[20])
        row_21 = result.filter(pl.col("date") == days[21])
        assert row_20["news_count"][0] == 2
        assert row_20["avg_daily_sentiment"][0] == pytest.approx(0.5)
        assert row_21["news_count"][0] == 1
        # Key dtype agreement (F01): the join ran on equal dtypes.
        assert result.schema["date"] == pl.Date

    def test_social_values_survive_join_without_infinity(self, technical_frame, conn, lake_dirs) -> None:
        days = technical_frame["date"].to_list()
        counts = [0, 0, 0, 0, 0, 12, 0, 0]  # zero-baseline jump on the 6th row
        _write(
            lake_dirs["social"],
            pl.DataFrame(
                {
                    "ticker": ["AAPL"] * 8,
                    "date": days[:8],
                    "datetime": [datetime(2024, 1, 1, 10, 0)] * 8,
                    "source": ["reddit"] * 8,
                    "mention_count": counts,
                    "positive_score": [1.0] * 8,
                    "negative_score": [0.0] * 8,
                    "score": [0.2] * 8,
                    "social_metric": ["upvotes"] * 8,
                }
            ),
        )
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=days[0],
            end_date=days[-1],
            enable_social_sentiment=True,
            enable_macro=False,
        )
        assert result.filter(pl.col("date") == days[5])["social_mention_count"][0] == 12
        # F15: zero-baseline momentum is null, never infinity.
        assert not result["social_momentum"].is_infinite().any()
        momentum_5 = result.filter(pl.col("date") == days[5])["social_momentum"][0]
        assert momentum_5 is None

    def test_analyst_values_survive_join(self, technical_frame, conn, lake_dirs) -> None:
        days = technical_frame["date"].to_list()
        _write(
            lake_dirs["analyst"],
            pl.DataFrame(
                {
                    "ticker": ["AAPL"],
                    "date": [days[30]],
                    "period": ["2024Q1"],
                    "strong_buy": [5],
                    "buy": [10],
                    "hold": [3],
                    "sell": [1],
                    "strong_sell": [0],
                    "consensus_score": [0.7],
                    "consensus_label": ["buy"],
                    "price_target_mean": [150.0],
                    "price_target_median": [148.0],
                    "price_target_high": [160.0],
                    "price_target_low": [140.0],
                    "price_target_count": [19],
                    "fetched_at": [datetime(2024, 1, 1)],
                }
            ),
        )
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=days[0],
            end_date=days[-1],
            enable_analyst_ratings=True,
            enable_macro=False,
        )
        row = result.filter(pl.col("date") == days[30])
        assert row["analyst_consensus_score"][0] == pytest.approx(0.7)
        assert row["analyst_coverage_count"][0] == 19
        close = row["close"][0]
        assert row["analyst_price_target_upside"][0] == pytest.approx(150.0 / close - 1)

    def test_sec_asof_backward_join_only(self, technical_frame, conn, lake_dirs) -> None:
        """SEC filings asof-join backward: a feature date gets the latest
        filing at or before it — never a later filing (no lookahead)."""
        days = technical_frame["date"].to_list()
        early_filing, late_filing = days[10], days[40]
        frame = pl.DataFrame(
            {
                "article_id": ["s1", "s2"],
                "ticker": ["AAPL", "AAPL"],
                "filing_type": ["10-K", "10-Q"],
                "section_type": ["risk", "risk"],
                "filing_date": [early_filing, late_filing],
                "date": [early_filing, late_filing],
                "risk_sentiment": [0.4, 0.9],
                "key_risks": ["r1", "r2"],
                "guidance_direction": ["positive", "negative"],
                "forward_statements": ["f1", "f2"],
                "management_tone": [0.3, 0.8],
                "new_vs_repeated": ["new", "modified"],
                "summary": ["s1", "s2"],
                "fetched_at": [datetime(2024, 1, 1)] * 2,
            }
        )
        _write(lake_dirs["sec"], frame)
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=days[0],
            end_date=days[-1],
            enable_sec_features=True,
            enable_macro=False,
        )
        mid = result.filter(pl.col("date") == days[25])
        after_late = result.filter(pl.col("date") == days[50])
        # Before the late filing: only the early filing may join backward.
        assert mid["sec_risk_sentiment"][0] == pytest.approx(0.4)
        assert mid["sec_guidance_positive"][0] == 1
        # After the late filing: the late one (most recent <= date).
        assert after_late["sec_risk_sentiment"][0] == pytest.approx(0.9)
        assert after_late["sec_guidance_positive"][0] == 0

    def test_macro_forward_fill_joins(self, technical_frame, conn, lake_dirs) -> None:
        days = technical_frame["date"].to_list()
        _write(
            lake_dirs["macro"],
            pl.DataFrame(
                {
                    "date": [days[0], days[45]],
                    "indicator": ["vix", "vix"],
                    "value": [15.0, 20.0],
                    "source": ["yfinance", "yfinance"],
                    "updated_at": [datetime(2024, 1, 1)] * 2,
                }
            ),
        )
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=days[0],
            end_date=days[-1],
        )
        assert result.filter(pl.col("date") == days[50])["vix"][0] == pytest.approx(20.0)
        assert result.filter(pl.col("date") == days[30])["vix"][0] == pytest.approx(15.0)

    def test_enriched_sentiment_values_survive_join(self, technical_frame, conn, lake_dirs, tmp_path, monkeypatch) -> None:
        """Populated-path coverage for the LLM-enriched article merge (review
        gap): aggregated article-ticker sentiment joins on Date keys."""
        days = technical_frame["date"].to_list()
        processed_dir = tmp_path / "processed_articles"
        _write(
            processed_dir,
            pl.DataFrame(
                {
                    "article_id": ["a1", "a2"],
                    "ticker": ["AAPL", "AAPL"],
                    "source_type": ["news", "reddit"],
                    "source_name": ["reuters", "reddit"],
                    "published_at": [datetime(2024, 1, 1, 9, 0)] * 2,
                    "date": [days[20]] * 2,
                    "sentiment_score": [0.8, 0.6],
                    "sentiment_label": ["bullish", "bullish"],
                    "confidence": [0.9, 0.7],
                    "event_type": ["earnings", "guidance"],
                    "summary": ["s1", "s2"],
                    "impact_horizon": ["short", "medium"],
                    "market_relevance": [0.95, 0.6],
                    "key_entities": ['["AAPL"]', '["AAPL"]'],
                    "source_metadata": ["{}", "{}"],
                }
            ),
        )
        monkeypatch.setattr(e4, "SILVER_PROCESSED_ARTICLES_DIR", processed_dir)
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=days[0],
            end_date=days[-1],
            enable_enriched_sentiment=True,
            enable_macro=False,
        )
        row = result.filter(pl.col("date") == days[20])
        assert row["enriched_article_count"][0] == 2
        assert row["enriched_sentiment_mean"][0] == pytest.approx(0.7)
        assert row["bullish_ratio"][0] == pytest.approx(1.0)
        assert row["social_volume"][0] == 1  # one reddit source_type row

    def test_feature_identities_unchanged_by_enrichment(self, technical_frame, conn, lake_dirs) -> None:
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=technical_frame["date"].min(),
            end_date=technical_frame["date"].max(),
            enable_macro=False,
        )
        assert result.height == technical_frame.height
        assert result["ticker"].to_list() == technical_frame["ticker"].to_list()
        for column in ("rsi_14", "macd", "close"):
            assert result[column].to_list() == technical_frame[column].to_list()


class TestDegradationBranches:
    """Disabled/missing/error enrichments keep the schema stable and remain
    distinguishable from the populated path."""

    def test_disabled_enrichment_adds_no_columns(self, technical_frame, conn, lake_dirs) -> None:
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            enable_macro=False,
        )
        assert "news_count" not in result.columns
        assert "social_mention_count" not in result.columns

    def test_missing_table_yields_zero_filled_columns(self, technical_frame, conn, lake_dirs) -> None:
        # lake_dirs paths exist as directories but hold no Delta tables.
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            enable_news_sentiment=True,
            enable_macro=False,
        )
        assert set(result["news_count"].to_list()) == {0}
        assert result.height == technical_frame.height

    def test_query_error_yields_zero_filled_columns_like_no_data(self, technical_frame, conn, lake_dirs) -> None:
        """A transient query error degrades to the same zero-filled schema as
        the no-data path (handoff 08 A5) — technical features still complete."""
        # A corrupt parquet file under the scan path forces a real duckdb
        # IOException inside the news query (no mock of the connection).
        news_dir = lake_dirs["news"]
        news_dir.mkdir(parents=True, exist_ok=True)
        (news_dir / "part-0000-corrupt.parquet").write_bytes(b"not a parquet file")
        days = technical_frame["date"].to_list()
        result = FeaturePipeline().compute_enriched(
            technical_frame,
            duckdb_conn=conn,
            start_date=days[0],
            end_date=days[-1],
            enable_news_sentiment=True,
            enable_macro=False,
        )
        assert set(result["news_count"].to_list()) == {0}
        assert result.height == technical_frame.height


class TestDatetimeKeyedFeaturesStillJoin:
    """D01 normalization at the shared boundary: callers passing Datetime-keyed
    feature frames still join Date-keyed external tables (no silent zero-fill)."""

    def test_datetime_features_normalize_to_date_at_boundary(self, technical_frame, conn, lake_dirs) -> None:
        days = technical_frame["date"].to_list()
        _write(
            lake_dirs["news"],
            pl.DataFrame(
                {
                    "ticker": ["AAPL"],
                    "date": [days[20]],
                    "datetime": [datetime(2024, 1, 1, 10, 0)],
                    "source": ["fn"],
                    "headline": ["a"],
                    "summary": ["x"],
                    "url": ["u1"],
                    "category": ["n"],
                    "sentiment_score": [0.8],
                    "sentiment_label": ["positive"],
                    "relevance_score": [0.9],
                }
            ),
        )
        datetime_frame = technical_frame.with_columns(pl.col("date").cast(pl.Datetime))
        result = FeaturePipeline().compute_enriched(
            datetime_frame,
            duckdb_conn=conn,
            start_date=datetime_frame["date"].min().date(),
            end_date=datetime_frame["date"].max().date(),
            enable_news_sentiment=True,
            enable_macro=False,
        )
        # Pre-W01 this zero-filled via a caught SchemaError (F01).
        assert result.filter(pl.col("date") == days[20])["news_count"][0] == 1
