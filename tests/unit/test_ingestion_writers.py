"""Tests for partitioned parquet writes."""

from datetime import date

import pandas as pd

from equity_lake.ingestion import writers


def test_write_to_partitioned_parquet_merges_existing_rows(tmp_path, monkeypatch) -> None:
    """A second write to the same partition should preserve older non-duplicate rows."""
    market_dir = tmp_path / "us_equity"
    monkeypatch.setattr(writers, "US_EQUITY_DIR", market_dir)

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

    assert writers.write_to_partitioned_parquet(existing, "us_equity", trading_date)
    assert writers.write_to_partitioned_parquet(incoming, "us_equity", trading_date)

    output_file = market_dir / f"date={trading_date}" / f"{trading_date}.parquet"
    merged = pd.read_parquet(output_file)

    assert set(merged["ticker"]) == {"AAPL", "MSFT"}


def test_write_to_partitioned_parquet_replaces_duplicate_rows(tmp_path, monkeypatch) -> None:
    """Incoming duplicate keys should overwrite older rows instead of duplicating them."""
    market_dir = tmp_path / "us_equity"
    monkeypatch.setattr(writers, "US_EQUITY_DIR", market_dir)

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

    writers.write_to_partitioned_parquet(existing, "us_equity", trading_date)
    writers.write_to_partitioned_parquet(incoming, "us_equity", trading_date)

    output_file = market_dir / f"date={trading_date}" / f"{trading_date}.parquet"
    merged = pd.read_parquet(output_file)

    assert len(merged) == 1
    assert float(merged.iloc[0]["close"]) == 20
