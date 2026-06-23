"""Tests for the medallion migration script."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.migrate_to_medallion import MIGRATION_MAP, cleanup_url_encoded_partitions, count_files


def test_migration_map_has_all_markets() -> None:
    expected_sources = {
        "us_equity",
        "cn_ashare",
        "hk_sg_equity",
        "macro_indicators",
        "us_news",
        "features",
    }
    assert expected_sources.issubset(MIGRATION_MAP.keys())


def test_migration_map_targets_medallion_paths() -> None:
    for old, new in MIGRATION_MAP.items():
        assert new.startswith(("01_bronze/", "02_silver/", "03_gold/")), f"{old} → {new} not a medallion path"


def test_migration_map_no_duplicates() -> None:
    targets = list(MIGRATION_MAP.values())
    assert len(targets) == len(set(targets)), "Duplicate target paths in MIGRATION_MAP"


def test_count_files_empty(tmp_path: Path) -> None:
    assert count_files(tmp_path) == 0


def test_count_files_with_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("y")
    assert count_files(tmp_path) == 2


def test_cleanup_url_encoded_partitions_dry_run(tmp_path: Path) -> None:
    bronze = tmp_path / "01_bronze" / "market_data" / "us_equity"
    bad_partition = bronze / "date=2026-02-04%2000%3A00%3A00.000000"
    bad_partition.mkdir(parents=True)
    (bad_partition / "data.parquet").write_text("x")

    with patch("scripts.migrate_to_medallion.LAKE_DIR", tmp_path):
        renamed = cleanup_url_encoded_partitions(dry_run=True)

    assert renamed == 1
    assert bad_partition.exists(), "Dry run should not rename"


def test_cleanup_url_encoded_partitions_execute(tmp_path: Path) -> None:
    bronze = tmp_path / "01_bronze" / "market_data" / "us_equity"
    bad_partition = bronze / "date=2026-02-04%2000%3A00%3A00.000000"
    bad_partition.mkdir(parents=True)
    (bad_partition / "data.parquet").write_text("x")

    with patch("scripts.migrate_to_medallion.LAKE_DIR", tmp_path):
        renamed = cleanup_url_encoded_partitions(dry_run=False)

    assert renamed == 1
    assert not bad_partition.exists()
    assert (bronze / "date=2026-02-04").exists()


def test_cleanup_skips_clean_partitions(tmp_path: Path) -> None:
    bronze = tmp_path / "01_bronze" / "market_data" / "us_equity"
    clean_partition = bronze / "date=2026-02-04"
    clean_partition.mkdir(parents=True)

    with patch("scripts.migrate_to_medallion.LAKE_DIR", tmp_path):
        renamed = cleanup_url_encoded_partitions(dry_run=True)

    assert renamed == 0
