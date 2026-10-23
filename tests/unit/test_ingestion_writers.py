"""Tests for partitioned storage writes (Delta Lake by default)."""

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import polars as pl
import pytest

from equity_lake.ingestion import writers  # type: ignore[import-untyped]


def _valid_ohlcv(ticker: str = "AAPL", trading_date: date = date(2026, 6, 2)) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "ticker": ticker,
                "mic": "XNAS",
                "date": trading_date,
                "open": 10.0,
                "high": 20.0,
                "low": 9.0,
                "close": 15.0,
                "volume": 1000,
            }
        ]
    )


def _invalid_ohlcv(trading_date: date = date(2026, 6, 2)) -> pl.DataFrame:
    """Column-valid but quality-invalid: negative close violates PriceDataSchema."""
    return pl.DataFrame(
        [
            {
                "ticker": "AAPL",
                "mic": "XNAS",
                "date": trading_date,
                "open": 10.0,
                "high": 20.0,
                "low": 9.0,
                "close": -5.0,
                "volume": 1000,
            }
        ]
    )


def test_upsert_dataset_merges_existing_rows(tmp_path: Path) -> None:
    """A second write to the same partition preserves older non-duplicate rows."""
    trading_date = date(2026, 6, 2)
    existing = pd.DataFrame([{"ticker": "AAPL", "mic": "XNAS", "date": trading_date, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100}])
    incoming = pd.DataFrame([{"ticker": "MSFT", "mic": "XNAS", "date": trading_date, "open": 3, "high": 4, "low": 3, "close": 4, "volume": 200}])
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(existing, "us_equity", trading_date)
        assert writers.upsert_dataset(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    merged = DeltaTable(str(tmp_path / "us_equity")).to_pandas()
    assert set(merged["ticker"]) == {"AAPL", "MSFT"}


def test_upsert_dataset_replaces_duplicate_rows(tmp_path: Path) -> None:
    """Incoming duplicate keys overwrite existing rows instead of duplicating them."""
    trading_date = date(2026, 6, 2)
    existing = pd.DataFrame([{"ticker": "AAPL", "mic": "XNAS", "date": trading_date, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100}])
    incoming = pd.DataFrame([{"ticker": "AAPL", "mic": "XNAS", "date": trading_date, "open": 10, "high": 20, "low": 10, "close": 20, "volume": 999}])
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        writers.upsert_dataset(existing, "us_equity", trading_date)
        writers.upsert_dataset(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    merged = DeltaTable(str(tmp_path / "us_equity")).to_pandas()
    assert len(merged) == 1
    assert float(merged.iloc[0]["close"]) == 20


def test_upsert_dataset_accepts_polars(tmp_path: Path) -> None:
    """Polars inputs round-trip through Delta writes."""
    trading_date = date(2026, 6, 2)
    incoming = _valid_ohlcv(trading_date=trading_date)
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    merged = pl.from_pandas(DeltaTable(str(tmp_path / "us_equity")).to_pandas())
    assert merged.height == 1
    assert float(merged["close"][0]) == 15.0


def test_price_write_keeps_same_symbol_on_two_mics(tmp_path: Path) -> None:
    day = date(2026, 6, 2)
    for mic, close in (("XNAS", 10.0), ("XNYS", 20.0)):
        frame = pl.DataFrame(
            {"ticker": ["DUAL"], "mic": [mic], "date": [day], "open": [close], "high": [close], "low": [close], "close": [close], "volume": [100]}
        )
        with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
            assert writers.upsert_dataset(frame, "01_bronze/market_data/us_equity", day, validate_quality=False)
    from deltalake import DeltaTable

    table = pl.DataFrame(DeltaTable(str(tmp_path / "01_bronze/market_data/us_equity")).to_pyarrow_table())
    assert set(table.select("mic", "ticker").unique().rows()) == {("XNAS", "DUAL"), ("XNYS", "DUAL")}
    assert table.height == 2


def test_new_price_write_rejects_missing_venue_provenance(tmp_path: Path) -> None:
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_valid_ohlcv().drop("mic"), "01_bronze/market_data/us_equity", date(2026, 6, 2)) is False
    assert not list(tmp_path.rglob("*.parquet"))


# B2: dry-run must be side-effect free.
def test_dry_run_with_quality_validation_persists_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.validation import pipeline as validation_pipeline  # type: ignore[import-untyped]
    from equity_lake.validation import profiling

    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path / "lake")
    monkeypatch.setattr(profiling, "PROFILES_DIR", tmp_path / "profiles")

    def _explode(self: object, *args: object, **kwargs: object) -> None:
        raise AssertionError("dry run must short-circuit before validation runs")

    monkeypatch.setattr(validation_pipeline.ValidationPipeline, "validate", _explode)
    assert writers.upsert_dataset(_valid_ohlcv(), "us_equity", date(2026, 6, 2), dry_run=True, validate_quality=True)
    assert list(tmp_path.rglob("*")) == []


# B3: pointblank quality gate is the default at the write boundary.
def test_upsert_dataset_refuses_quality_failures_by_default(tmp_path: Path) -> None:
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_invalid_ohlcv(), "us_equity", date(2026, 6, 2)) is False
    assert list(tmp_path.rglob("*")) == []


def test_price_write_invokes_real_pointblank_gate_before_delta_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typing import Any, cast

    from equity_lake.validation.pipeline import ValidationPipeline  # type: ignore[import-untyped]

    original_validate = cast(Any, ValidationPipeline.validate)
    calls: list[tuple[str, str | None]] = []

    def _observe_validate(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = original_validate(self, *args, **kwargs)
        name = kwargs.get("name")
        calls.append((str(kwargs.get("data_type", "price")), name if isinstance(name, str) else None))
        return result

    monkeypatch.setattr(ValidationPipeline, "validate", _observe_validate)
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_invalid_ohlcv(), "01_bronze/market_data/us_equity", date(2026, 6, 2)) is False
    assert calls == [("price", "01_bronze/market_data/us_equity_2026-06-02")]
    assert not list(tmp_path.rglob("*.parquet"))


def test_upsert_dataset_explicit_opt_out_skips_quality_gate(tmp_path: Path) -> None:
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_invalid_ohlcv(), "us_equity", date(2026, 6, 2), validate_quality=False)
    assert (tmp_path / "us_equity").exists()


def test_upsert_dataset_macro_routes_to_macro_schema(tmp_path: Path) -> None:
    trading_date = date(2026, 6, 2)
    df = pl.DataFrame(
        {"date": [trading_date], "indicator": ["treasury_10y"], "value": [4.2], "source": ["fred"], "updated_at": [datetime(2026, 6, 2, 12, 0)]}
    )
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(df, "01_bronze/macro", trading_date)


def test_quality_data_type_mapping() -> None:
    assert writers._quality_data_type("us_news") == "news"
    assert writers._quality_data_type("02_silver/news_sentiment") == "news"
    assert writers._quality_data_type("us_social_sentiment") == "social"
    assert writers._quality_data_type("02_silver/social_sentiment") == "social"
    assert writers._quality_data_type("macro") == "macro"
    assert writers._quality_data_type("01_bronze/macro") == "macro"
    for untyped in (
        "01_bronze/raw_articles",
        "rss_news",
        "sec_filings_fulltext",
        "02_silver/processed_articles",
        "02_silver/sec_extractions",
        "us_sec_financials",
        "us_analyst_ratings",
        "03_gold/features",
        "04_platinum/predictions",
    ):
        assert writers._quality_data_type(untyped) is None, untyped
    assert writers._quality_data_type("us_equity") == "price"
    assert writers._quality_data_type("01_bronze/market_data/jpx_equity") == "price"


def _valid_corporate_actions() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "ex_date": [date(2024, 2, 9), date(2024, 3, 14)],
            "action": ["dividend", "dividend"],
            "value": [0.25, 0.83],
            "source": ["yahoo", "yahoo"],
            "ingested_at": [datetime(2026, 6, 2, 12, 0)] * 2,
        }
    )


def test_quality_data_type_corporate_actions() -> None:
    for market in ("corporate_actions", "01_bronze/corporate_actions", "02_silver/corporate_actions"):
        assert writers._quality_data_type(market) == "corporate_action", market


def test_upsert_dataset_corporate_actions_partitions_on_ex_date(tmp_path: Path) -> None:
    df = _valid_corporate_actions()
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(df, "corporate_actions", date(2026, 6, 2), partition_by=["ex_date"])
        assert writers.upsert_dataset(df, "corporate_actions", date(2026, 6, 2), partition_by=["ex_date"])
    from deltalake import DeltaTable

    table_path = tmp_path / "corporate_actions"
    dt = DeltaTable(str(table_path))
    assert dt.metadata().partition_columns == ["ex_date"]
    assert {d.name for d in table_path.iterdir() if d.is_dir() and d.name.startswith("ex_date=")} == {"ex_date=2024-02-09", "ex_date=2024-03-14"}
    assert len(dt.to_pandas()) == df.height


def test_upsert_dataset_refuses_invalid_corporate_actions(tmp_path: Path) -> None:
    df = _valid_corporate_actions().with_columns(pl.lit(-0.25).alias("value"))
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(df, "corporate_actions", date(2026, 6, 2), partition_by=["ex_date"]) is False
    assert list(tmp_path.rglob("*")) == []


def _social_frame(trading_date: date = date(2026, 6, 2)) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "date": [trading_date] * 2,
            "datetime": [datetime(2026, 6, 2, 14, 0)] * 2,
            "source": ["reddit", "stocktwits"],
            "mention_count": [10, 5],
            "positive_score": [4.0, 2.0],
            "negative_score": [1.0, 0.5],
            "score": [0.3, -0.2],
            "social_metric": ["upvotes", "likes"],
        }
    )


def test_social_sentiment_routes_to_social_schema(tmp_path: Path) -> None:
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_social_frame(), "us_social_sentiment", date(2026, 6, 2))


def test_social_sentiment_invalid_score_rejected(tmp_path: Path) -> None:
    frame = _social_frame().with_columns(pl.when(pl.col("ticker") == "AAPL").then(5.0).otherwise(pl.col("score")).alias("score"))
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(frame, "02_silver/social_sentiment", date(2026, 6, 2)) is False
    assert list(tmp_path.rglob("*")) == []


def test_news_route_frame_validates(tmp_path: Path) -> None:
    trading_date = date(2026, 6, 2)
    frame = pl.DataFrame(
        {
            "ticker": ["AAPL"],
            "date": [trading_date],
            "datetime": [datetime(2026, 6, 2, 14, 0)],
            "source": ["finnhub"],
            "headline": ["h"],
            "summary": ["s"],
            "url": ["https://example.com/1"],
            "category": ["n"],
            "sentiment_score": [0.5],
            "sentiment_label": ["positive"],
            "relevance_score": [0.9],
        }
    )
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(frame, "us_news", trading_date)


def test_macro_route_frame_validates(tmp_path: Path) -> None:
    trading_date = date(2026, 6, 2)
    frame = pl.DataFrame(
        {
            "date": [trading_date],
            "indicator": ["vix"],
            "value": [15.0],
            "source": ["yfinance"],
            "updated_at": [datetime(2026, 6, 2, 12, 0)],
        }
    )
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(frame, "01_bronze/macro", trading_date)


def test_price_route_frame_validates(tmp_path: Path) -> None:
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_valid_ohlcv(), "01_bronze/market_data/us_equity", date(2026, 6, 2))


def test_predictions_route_frame_writes(tmp_path: Path) -> None:
    frame = pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "identity_schema_version": [1],
            "ticker": ["AAPL"],
            "date": [date(2026, 6, 2)],
            "direction": [1],
            "probability": [0.62],
        }
    )
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(frame, "04_platinum/predictions", date(2026, 6, 2))


def _coverage_proof(
    *,
    start: date = date(2026, 6, 1),
    end: date = date(2026, 6, 3),
    available: datetime | None = None,
    valid_until: datetime | None = None,
) -> dict[str, object]:
    from typing import cast

    from equity_lake.core.availability import ActionCoverageProof  # type: ignore[import-untyped]

    available_at = available or datetime(2026, 5, 29, 20, 0, tzinfo=UTC)
    proof = ActionCoverageProof(
        market="us_equity",
        mic="XNYS",
        symbol="AAPL",
        source="synthetic-source",
        coverage_start=start,
        coverage_end=end,
        source_available_at=available_at,
        source_valid_until=valid_until or datetime(2026, 6, 10, 21, 0, tzinfo=UTC),
    )
    return cast(dict[str, object], proof.model_dump())


def test_coverage_proof_upsert_uses_real_pointblank_quality_gate(tmp_path: Path) -> None:
    """A complete source-valid proof passes the normal write-boundary schema and Pointblank gate."""
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(
            pl.DataFrame([_coverage_proof()]),
            "02_silver/corporate_action_coverage/us_equity",
            date(2026, 6, 3),
            partition_by=["coverage_end"],
        )
    from deltalake import DeltaTable

    written = pl.DataFrame(DeltaTable(str(tmp_path / "02_silver/corporate_action_coverage/us_equity")).to_pyarrow_table())
    assert written.height == 1
    assert written["source_valid_until"].null_count() == 0


def test_coverage_proof_upsert_rejects_unknown_expiry(tmp_path: Path) -> None:
    proof = _coverage_proof()
    proof.pop("source_valid_until")
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(pl.DataFrame([proof]), "02_silver/corporate_action_coverage/us_equity", date(2026, 6, 3)) is False
    assert not list(tmp_path.rglob("*.parquet"))


def test_coverage_proof_upsert_rejects_duplicate_incomplete_and_conflicting_rows(tmp_path: Path) -> None:
    dataset = "02_silver/corporate_action_coverage/us_equity"
    duplicate = _coverage_proof()
    incomplete = _coverage_proof()
    incomplete["coverage_end"] = date(2026, 5, 30)
    first = _coverage_proof(start=date(2026, 6, 1), end=date(2026, 6, 4))
    conflict = _coverage_proof(
        start=date(2026, 6, 2),
        end=date(2026, 6, 5),
        available=datetime(2026, 5, 30, 20, 0, tzinfo=UTC),
        valid_until=datetime(2026, 6, 11, 21, 0, tzinfo=UTC),
    )
    batches = (
        pl.DataFrame([duplicate, duplicate]),
        pl.DataFrame([incomplete]),
        pl.DataFrame([first, conflict]),
    )
    for index, batch in enumerate(batches):
        scoped = tmp_path / str(index)
        with patch("equity_lake.storage.delta.LAKE_DIR", scoped):
            assert writers.upsert_dataset(batch, dataset, date(2026, 6, 3), partition_by=["coverage_end"]) is False
        assert not list(scoped.rglob("*.parquet"))
