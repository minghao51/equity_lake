"""Smoke test for cli.bootstrap sample-data generation (Delta write path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from deltalake import DeltaTable

from equity_lake.cli.bootstrap import SAMPLE_TICKERS, cmd_sample


class TestCmdSampleWrite:
    def test_synthetic_generation_writes_delta_tables(self, tmp_path: Path) -> None:
        # Point MARKET_DIRS at non-existent paths so _try_load_real_data short-circuits
        # to None, forcing the synthetic-generation branch.
        fake_dirs = {market: tmp_path / f"absent_{market}" for market in SAMPLE_TICKERS}
        with patch("equity_lake.cli.bootstrap.MARKET_DIRS", fake_dirs):
            cmd_sample(days=3, output_dir=str(tmp_path / "sample"), seed=42)

        sample_root = tmp_path / "sample"
        for market in SAMPLE_TICKERS:
            market_path = sample_root / market
            assert market_path.exists(), f"{market} delta table missing"
            assert DeltaTable.is_deltatable(str(market_path)), f"{market} is not a Delta table"

    def test_delta_tables_are_queryable(self, tmp_path: Path) -> None:
        import duckdb

        fake_dirs = {market: tmp_path / f"absent_{market}" for market in SAMPLE_TICKERS}
        with patch("equity_lake.cli.bootstrap.MARKET_DIRS", fake_dirs):
            cmd_sample(days=2, output_dir=str(tmp_path / "sample"), seed=7)

        con = duckdb.connect(":memory:")
        con.execute("INSTALL delta; LOAD delta;")
        df = con.execute(f"SELECT COUNT(*) AS n FROM delta_scan('{tmp_path / 'sample' / 'us_equity'}')").pl()
        assert int(df["n"][0]) > 0
