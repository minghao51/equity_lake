"""Tests for devtools/seed_demo (synthetic path; the real path needs network)."""

from __future__ import annotations

import pytest

from equity_lake.devtools.seed_demo import DEMO_UNIVERSE, resolve_universe, seed_demo

pytestmark = pytest.mark.slow


def test_resolve_explicit_tickers_uppercased_and_filtered() -> None:
    assert resolve_universe(["aapl", "  msft  ", ""]) == ["AAPL", "MSFT"]


def test_resolve_default_universe_nonempty() -> None:
    out = resolve_universe(None)
    # resolves the config `demo` group (50) or the built-in default
    assert len(out) >= 20
    assert all(isinstance(t, str) and t == t.upper() for t in out)


def test_seed_demo_synthetic_writes_and_summarizes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    summary = seed_demo(years=1, tickers=["AAA", "BBB", "CCC"], seed=7, lake_dir=tmp_path)
    assert summary["source"] == "synthetic"
    assert summary["tickers"] == 3
    assert summary["days"] > 0
    assert summary["rows"] == summary["days"] * 3


def test_seed_demo_data_is_readable(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from equity_lake.storage.delta import read_delta

    seed_demo(years=1, tickers=["AAA"], seed=1, lake_dir=tmp_path)
    df = read_delta("01_bronze/market_data/us_equity", lake_dir=tmp_path)
    assert df.height > 0
    assert {"ticker", "date", "open", "high", "low", "close", "volume"}.issubset(df.columns)


def test_seed_demo_is_idempotent(tmp_path) -> None:  # type: ignore[no-untyped-def]
    first = seed_demo(years=1, tickers=["AAA"], seed=1, lake_dir=tmp_path)
    second = seed_demo(years=1, tickers=["AAA"], seed=1, lake_dir=tmp_path)
    assert first["rows"] == second["rows"]  # overwrite, no duplication


def test_demo_universe_matches_config_size() -> None:
    # The built-in default and the config `demo` group are intentionally the same 50.
    assert len(DEMO_UNIVERSE) == 50
