"""Parquet compaction for lake maintenance.

Merges small date-partitioned Parquet files into fewer, larger files
to improve read performance and reduce storage overhead.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import structlog

from equity_lake.core.paths import LAKE_DIR

logger = structlog.get_logger(__name__)


def compact_market(
    market_dir: Path,
    max_days_per_file: int = 30,
    dry_run: bool = False,
) -> int:
    """Compact a single market directory by merging small partition files.

    For each consecutive run of date partitions up to max_days_per_file,
    reads all files, concatenates, and writes a single merged file per group.

    Args:
        market_dir: Path to market directory (e.g., data/lake/us_equity)
        max_days_per_file: Maximum days to merge into one file
        dry_run: If True, report what would be done without writing

    Returns:
        Number of partition directories compacted
    """
    if not market_dir.exists():
        logger.warning("market_dir_not_found", path=str(market_dir))
        return 0

    partition_dirs = sorted(market_dir.glob("date=*"))
    if not partition_dirs:
        return 0

    groups = _group_consecutive_dates(partition_dirs, max_days_per_file)
    total_compacted = 0

    for group in groups:
        if len(group) <= 1:
            continue

        logger.info(
            "compacting_date_range",
            dates=f"{group[0].name}..{group[-1].name}",
            partitions=len(group),
            dry_run=dry_run,
        )

        if dry_run:
            total_compacted += len(group)
            continue

        frames: list[pd.DataFrame] = []
        for partition_dir in group:
            for pq_file in partition_dir.glob("*.parquet"):
                frames.append(pd.read_parquet(pq_file))

        if not frames:
            continue

        merged = pd.concat(frames, ignore_index=True)
        key_cols = [c for c in ("ticker", "date") if c in merged.columns]
        if key_cols:
            merged = merged.drop_duplicates(subset=key_cols, keep="last")
            merged = merged.sort_values(key_cols).reset_index(drop=True)

        # Write into the last partition in the group
        target_dir = group[-1]
        target_file = target_dir / f"{target_dir.name.split('=', 1)[1]}.parquet"
        merged.to_parquet(target_file, index=False, compression="snappy")

        # Remove older partitions that were merged
        for old_dir in group[:-1]:
            for pq_file in old_dir.glob("*.parquet"):
                pq_file.unlink()
            old_dir.rmdir()

        total_compacted += len(group) - 1

    return total_compacted


def _group_consecutive_dates(
    partition_dirs: list[Path],
    max_per_group: int,
) -> list[list[Path]]:
    """Group consecutive date-partition directories into batches."""
    if not partition_dirs:
        return []

    groups: list[list[Path]] = []
    current_group: list[Path] = [partition_dirs[0]]
    prev_date = _extract_date(partition_dirs[0].name)

    for pd_dir in partition_dirs[1:]:
        cur_date = _extract_date(pd_dir.name)
        if cur_date is None or prev_date is None:
            if current_group:
                groups.append(current_group)
            current_group = [pd_dir]
            prev_date = cur_date
            continue

        gap_days = (cur_date - prev_date).days
        if gap_days <= 3 and len(current_group) < max_per_group:
            current_group.append(pd_dir)
        else:
            if current_group:
                groups.append(current_group)
            current_group = [pd_dir]

        prev_date = cur_date

    if current_group:
        groups.append(current_group)

    return groups


def _extract_date(partition_name: str) -> date | None:
    """Extract date from 'date=YYYY-MM-DD' partition name."""
    try:
        return date.fromisoformat(partition_name.split("=", 1)[1])
    except (ValueError, IndexError):
        return None


def compact_all_markets(
    lake_dir: Path | None = None,
    max_days_per_file: int = 30,
    dry_run: bool = False,
) -> dict[str, int]:
    """Run compaction across all market directories.

    Returns dict mapping market name to number of partitions compacted.
    """
    lake = lake_dir or LAKE_DIR
    results: dict[str, int] = {}

    for market_dir in sorted(lake.iterdir()):
        if not market_dir.is_dir():
            continue
        count = compact_market(market_dir, max_days_per_file=max_days_per_file, dry_run=dry_run)
        if count > 0:
            results[market_dir.name] = count

    logger.info("compaction_complete", markets_compacted=len(results), dry_run=dry_run)
    return results
