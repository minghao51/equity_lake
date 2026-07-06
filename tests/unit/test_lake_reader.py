"""Tests for storage.lake_reader.duckdb_scan_for."""

from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl
from deltalake import write_deltalake

from equity_lake.storage.lake_reader import duckdb_scan_for


def _write_hive_parquet(base: Path, partition_date: str, rows: list[dict]) -> None:
    part_dir = base / f"date={partition_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(part_dir / "part.parquet")


class TestDuckdbScanForParquet:
    def test_plain_parquet_dir_returns_hive_glob(self, tmp_path: Path) -> None:
        _write_hive_parquet(tmp_path, "2024-01-02", [{"ticker": "AAPL", "close": 150.0}])
        scan = duckdb_scan_for(tmp_path)
        assert "delta_scan" not in scan
        assert "read_parquet" in scan
        assert "hive_partitioning=1" in scan
        assert str(tmp_path) in scan

    def test_nonexistent_path_falls_through_to_parquet_glob(self, tmp_path: Path) -> None:
        # Delta check returns False for a path that doesn't exist -> parquet glob.
        scan = duckdb_scan_for(tmp_path / "never-existed")
        assert "read_parquet" in scan
        assert "delta_scan" not in scan

    def test_returned_sql_executes_against_hive_parquet(self, tmp_path: Path) -> None:
        _write_hive_parquet(tmp_path, "2024-01-02", [{"ticker": "AAPL", "close": 150.0}])
        _write_hive_parquet(tmp_path, "2024-01-03", [{"ticker": "AAPL", "close": 152.0}])
        scan = duckdb_scan_for(tmp_path)
        con = duckdb.connect(":memory:")
        df = con.execute(f"SELECT COUNT(*) AS n, MAX(date) AS latest FROM {scan}").pl()
        assert int(df["n"][0]) == 2
        assert str(df["latest"][0]) == "2024-01-03"


class TestDuckdbScanForDelta:
    def test_delta_table_dir_returns_delta_scan(self, tmp_path: Path) -> None:
        write_deltalake(str(tmp_path), pl.DataFrame({"ticker": ["AAPL"], "close": [150.0]}).to_arrow())
        scan = duckdb_scan_for(tmp_path)
        assert scan.startswith("delta_scan(")
        assert "read_parquet" not in scan

    def test_returned_delta_sql_executes(self, tmp_path: Path) -> None:
        write_deltalake(str(tmp_path), pl.DataFrame({"ticker": ["AAPL", "MSFT"], "close": [150.0, 380.0]}).to_arrow())
        scan = duckdb_scan_for(tmp_path)
        con = duckdb.connect(":memory:")
        con.execute("INSTALL delta; LOAD delta;")
        df = con.execute(f"SELECT COUNT(*) AS n FROM {scan}").pl()
        assert int(df["n"][0]) == 2
