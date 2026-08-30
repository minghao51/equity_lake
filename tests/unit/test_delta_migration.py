"""Tests for migrate_parquet_to_delta: write-then-cleanup ordering.

Regression guard: with ``keep_backup=False`` the legacy ``date=`` partitions
used to be deleted *before* the Delta rewrite was attempted, so a failed write
destroyed the source data.  Cleanup must happen only after a successful write.
"""

from datetime import date
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest

from equity_lake.storage.delta import migrate_parquet_to_delta, read_delta

LEGACY_ROWS = {
    "2024-01-02": [("AAPL", 150.0), ("MSFT", 380.0)],
    "2024-01-03": [("NVDA", 900.0)],
}


def _legacy_layout(table_dir: Path) -> None:
    for day, rows in LEGACY_ROWS.items():
        partition = table_dir / f"date={day}"
        partition.mkdir(parents=True)
        pl.DataFrame(
            {
                "date": [date.fromisoformat(day)] * len(rows),
                "ticker": [r[0] for r in rows],
                "close": [r[1] for r in rows],
            }
        ).write_parquet(partition / "old.parquet")


def _legacy_files(table_dir: Path) -> list[Path]:
    return sorted(table_dir.rglob("old.parquet"))


@pytest.fixture()
def legacy_table(tmp_path: Path) -> Path:
    table_dir = tmp_path / "tbl"
    _legacy_layout(table_dir)
    return table_dir


class TestMigrateParquetToDelta:
    def test_destroy_migration_succeeds_and_cleans_up(self, legacy_table: Path, tmp_path: Path) -> None:
        assert migrate_parquet_to_delta("tbl", lake_dir=tmp_path, keep_backup=False) is True

        # Delta table holds every legacy row, partitioned by date.
        out = read_delta("tbl", lake_dir=tmp_path).sort("ticker")
        assert out["ticker"].to_list() == ["AAPL", "MSFT", "NVDA"]

        # Legacy source files are gone; no backup dir was created.
        assert _legacy_files(legacy_table) == []
        assert not (legacy_table / ".pre_delta_backup").exists()

    def test_failed_write_never_deletes_source_partitions(self, legacy_table: Path, tmp_path: Path) -> None:
        with patch("equity_lake.storage.delta.write_delta", return_value=False) as mock_write:
            assert migrate_parquet_to_delta("tbl", lake_dir=tmp_path, keep_backup=False) is False

        mock_write.assert_called_once()
        # The ordering fix: source parquet files still exist after the failed write.
        remaining = _legacy_files(legacy_table)
        assert [p.name for p in remaining] == ["old.parquet", "old.parquet"]
        assert not (legacy_table / "_delta_log").exists()

    def test_backup_migration_moves_partitions_aside(self, legacy_table: Path, tmp_path: Path) -> None:
        assert migrate_parquet_to_delta("tbl", lake_dir=tmp_path, keep_backup=True) is True

        out = read_delta("tbl", lake_dir=tmp_path).sort("ticker")
        assert out["ticker"].to_list() == ["AAPL", "MSFT", "NVDA"]

        backup = legacy_table / ".pre_delta_backup"
        assert backup.is_dir()
        assert sorted(d.name for d in backup.iterdir()) == [f"date={d}" for d in LEGACY_ROWS]
        assert all((d / "old.parquet").exists() for d in backup.iterdir())

    def test_dry_run_persists_nothing(self, legacy_table: Path, tmp_path: Path) -> None:
        assert migrate_parquet_to_delta("tbl", lake_dir=tmp_path, dry_run=True, keep_backup=False) is True
        assert not (legacy_table / "_delta_log").exists()
        assert len(_legacy_files(legacy_table)) == 2

    def test_missing_directory_returns_false(self, tmp_path: Path) -> None:
        assert migrate_parquet_to_delta("nope", lake_dir=tmp_path, keep_backup=False) is False
