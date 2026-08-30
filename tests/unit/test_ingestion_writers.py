"""Tests for partitioned storage writes (Delta Lake by default)."""

from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
import polars as pl

from equity_lake.ingestion import writers


def _valid_ohlcv(ticker: str = "AAPL", trading_date: date = date(2026, 6, 2)) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "ticker": ticker,
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
                "date": trading_date,
                "open": 10.0,
                "high": 20.0,
                "low": 9.0,
                "close": -5.0,
                "volume": 1000,
            }
        ]
    )


def test_upsert_dataset_merges_existing_rows(tmp_path) -> None:
    """A second write to the same partition should preserve older non-duplicate rows."""
    trading_date = date(2026, 6, 2)
    existing = pd.DataFrame(
        [
            {"ticker": "AAPL", "date": trading_date, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"ticker": "MSFT", "date": trading_date, "open": 3, "high": 4, "low": 3, "close": 4, "volume": 200},
        ]
    )

    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(existing, "us_equity", trading_date)
        assert writers.upsert_dataset(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    market_dir = tmp_path / "us_equity"
    dt = DeltaTable(str(market_dir))
    merged = dt.to_pandas()
    assert set(merged["ticker"]) == {"AAPL", "MSFT"}


def test_upsert_dataset_replaces_duplicate_rows(tmp_path) -> None:
    """Incoming duplicate keys should overwrite older rows instead of duplicating them."""
    trading_date = date(2026, 6, 2)
    existing = pd.DataFrame(
        [
            {"ticker": "AAPL", "date": trading_date, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 100},
        ]
    )
    incoming = pd.DataFrame(
        [
            {"ticker": "AAPL", "date": trading_date, "open": 10, "high": 20, "low": 10, "close": 20, "volume": 999},
        ]
    )

    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        writers.upsert_dataset(existing, "us_equity", trading_date)
        writers.upsert_dataset(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    market_dir = tmp_path / "us_equity"
    dt = DeltaTable(str(market_dir))
    merged = dt.to_pandas()

    assert len(merged) == 1
    assert float(merged.iloc[0]["close"]) == 20


def test_upsert_dataset_accepts_polars(tmp_path) -> None:
    """Polars inputs should round-trip through Delta writes."""
    trading_date = date(2026, 6, 2)
    incoming = pl.DataFrame(
        [
            {"ticker": "AAPL", "date": trading_date, "open": 10, "high": 20, "low": 10, "close": 20, "volume": 999},
        ]
    )

    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    market_dir = tmp_path / "us_equity"
    dt = DeltaTable(str(market_dir))
    merged = pl.from_pandas(dt.to_pandas())

    assert isinstance(merged, pl.DataFrame)
    assert merged.height == 1
    assert float(merged["close"][0]) == 20


# ---------------------------------------------------------------------------
# B2: dry-run must be side-effect free
# ---------------------------------------------------------------------------


def test_dry_run_with_quality_validation_persists_nothing(tmp_path, monkeypatch) -> None:
    """Dry-run + validate_quality=True must not create lake data or profile artifacts anywhere."""
    from equity_lake.validation import pipeline as validation_pipeline
    from equity_lake.validation import profiling

    monkeypatch.setattr("equity_lake.storage.delta.LAKE_DIR", tmp_path / "lake")
    monkeypatch.setattr(profiling, "PROFILES_DIR", tmp_path / "profiles")

    def _explode(self, *args, **kwargs):  # pragma: no cover - regression tripwire
        raise AssertionError("dry run must short-circuit before validation runs")

    monkeypatch.setattr(validation_pipeline.ValidationPipeline, "validate", _explode)

    assert writers.upsert_dataset(_valid_ohlcv(), "us_equity", date(2026, 6, 2), dry_run=True, validate_quality=True)
    assert list(tmp_path.rglob("*")) == []


# ---------------------------------------------------------------------------
# B3: pointblank quality gate is the default at the write boundary
# ---------------------------------------------------------------------------


def test_upsert_dataset_refuses_quality_failures_by_default(tmp_path) -> None:
    """validate_quality defaults to True: a batch violating its schema contract does not land."""
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_invalid_ohlcv(), "us_equity", date(2026, 6, 2)) is False
    assert list(tmp_path.rglob("*")) == []


def test_upsert_dataset_explicit_opt_out_skips_quality_gate(tmp_path) -> None:
    """validate_quality=False opts out for devtools/backfills: the batch lands."""
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(_invalid_ohlcv(), "us_equity", date(2026, 6, 2), validate_quality=False)
    assert (tmp_path / "us_equity").exists()


def test_upsert_dataset_macro_routes_to_macro_schema(tmp_path) -> None:
    """Macro tables validate against MacroDataSchema, not the price schema."""
    trading_date = date(2026, 6, 2)
    df = pl.DataFrame(
        {
            "date": [trading_date],
            "indicator": ["treasury_10y"],
            "value": [4.2],
            "source": ["fred"],
            "updated_at": [datetime(2026, 6, 2, 12, 0)],
        }
    )
    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.upsert_dataset(df, "01_bronze/macro", trading_date)


def test_quality_data_type_mapping() -> None:
    """Schema routing: news/markets -> news, macro -> macro, article tables -> None (silver path owns them)."""
    assert writers._quality_data_type("us_news") == "news"
    assert writers._quality_data_type("02_silver/social_sentiment") == "news"
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
