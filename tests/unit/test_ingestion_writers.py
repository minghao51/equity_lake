"""Tests for partitioned storage writes (Delta Lake by default)."""

from datetime import date
from unittest.mock import patch

import pandas as pd
import polars as pl

from equity_lake.ingestion import writers


def test_write_to_partitioned_parquet_merges_existing_rows(tmp_path) -> None:
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
        assert writers.write_to_partitioned_parquet(existing, "us_equity", trading_date)
        assert writers.write_to_partitioned_parquet(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    market_dir = tmp_path / "us_equity"
    dt = DeltaTable(str(market_dir))
    merged = dt.to_pandas()
    assert set(merged["ticker"]) == {"AAPL", "MSFT"}


def test_write_to_partitioned_parquet_replaces_duplicate_rows(tmp_path) -> None:
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
        writers.write_to_partitioned_parquet(existing, "us_equity", trading_date)
        writers.write_to_partitioned_parquet(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    market_dir = tmp_path / "us_equity"
    dt = DeltaTable(str(market_dir))
    merged = dt.to_pandas()

    assert len(merged) == 1
    assert float(merged.iloc[0]["close"]) == 20


def test_write_to_partitioned_parquet_accepts_polars(tmp_path) -> None:
    """Polars inputs should round-trip through Delta writes."""
    trading_date = date(2026, 6, 2)
    incoming = pl.DataFrame(
        [
            {"ticker": "AAPL", "date": trading_date, "open": 10, "high": 20, "low": 10, "close": 20, "volume": 999},
        ]
    )

    with patch("equity_lake.storage.delta.LAKE_DIR", tmp_path):
        assert writers.write_to_partitioned_parquet(incoming, "us_equity", trading_date)

    from deltalake import DeltaTable

    market_dir = tmp_path / "us_equity"
    dt = DeltaTable(str(market_dir))
    merged = pl.from_pandas(dt.to_pandas())

    assert isinstance(merged, pl.DataFrame)
    assert merged.height == 1
    assert float(merged["close"][0]) == 20
