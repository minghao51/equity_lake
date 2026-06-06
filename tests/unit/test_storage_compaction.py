"""Tests for parquet compaction helpers."""

from datetime import date

import pandas as pd

from equity_lake.storage.compaction import compact_market


def test_compaction_preserves_date_partition_layout(tmp_path) -> None:
    market_dir = tmp_path / "us_equity"
    day1_dir = market_dir / "date=2024-01-01"
    day2_dir = market_dir / "date=2024-01-02"
    day1_dir.mkdir(parents=True)
    day2_dir.mkdir(parents=True)

    pd.DataFrame(
        [
            {"ticker": "AAPL", "date": "2024-01-01", "close": 100.0},
            {"ticker": "MSFT", "date": "2024-01-01", "close": 200.0},
        ]
    ).to_parquet(day1_dir / "part-1.parquet", index=False)
    pd.DataFrame(
        [
            {"ticker": "AAPL", "date": "2024-01-01", "close": 101.0},
        ]
    ).to_parquet(day1_dir / "part-2.parquet", index=False)
    pd.DataFrame(
        [
            {"ticker": "AAPL", "date": "2024-01-02", "close": 102.0},
        ]
    ).to_parquet(day2_dir / "part-1.parquet", index=False)

    compacted = compact_market(market_dir, max_days_per_file=30)

    assert compacted == 1
    assert not day1_dir.exists()
    assert day2_dir.exists()
    assert sorted(p.name for p in day2_dir.glob("*.parquet")) == ["2024-01-02.parquet"]

    merged_df = pd.read_parquet(day2_dir / "2024-01-02.parquet")
    assert len(merged_df) == 3
    assert set(merged_df["date"].astype(str)) == {"2024-01-01", "2024-01-02"}


def test_compaction_dry_run_does_not_modify_files(tmp_path) -> None:
    market_dir = tmp_path / "us_equity"
    partition_dir_1 = market_dir / "date=2024-01-01"
    partition_dir_2 = market_dir / "date=2024-01-02"
    partition_dir_1.mkdir(parents=True)
    partition_dir_2.mkdir(parents=True)

    original = pd.DataFrame([{"ticker": "AAPL", "date": date(2024, 1, 1), "close": 100.0}])
    original.to_parquet(partition_dir_1 / "part-1.parquet", index=False)
    original.to_parquet(partition_dir_1 / "part-2.parquet", index=False)
    pd.DataFrame([{"ticker": "AAPL", "date": date(2024, 1, 2), "close": 101.0}]).to_parquet(partition_dir_2 / "part-1.parquet", index=False)

    compacted = compact_market(market_dir, dry_run=True)

    assert compacted == 2
    assert sorted(p.name for p in partition_dir_1.glob("*.parquet")) == ["part-1.parquet", "part-2.parquet"]
    assert sorted(p.name for p in partition_dir_2.glob("*.parquet")) == ["part-1.parquet"]
