#!/usr/bin/env python3
"""One-time migration: move data/lake/ to 01_bronze/02_silver/03_gold/04_platinum.

Usage:
    uv run scripts/migrate_to_medallion.py --dry-run    # Preview changes
    uv run scripts/migrate_to_medallion.py --execute     # Perform migration

The script uses ``mv`` (atomic on same filesystem) and verifies file counts
before and after each move.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAKE_DIR = PROJECT_ROOT / "data" / "lake"

MIGRATION_MAP: dict[str, str] = {
    # Bronze — market data
    "us_equity": "01_bronze/market_data/us_equity",
    "cn_ashare": "01_bronze/market_data/cn_ashare",
    "hk_sg_equity": "01_bronze/market_data/hk_sg_equity",
    "jpx_equity": "01_bronze/market_data/jpx_equity",
    "krx_equity": "01_bronze/market_data/krx_equity",
    "macro_indicators": "01_bronze/macro",
    # Bronze — unstructured (flatten from bronze/raw_articles → 01_bronze/raw_articles)
    "bronze/raw_articles": "01_bronze/raw_articles",
    # Silver — structured
    "us_news": "02_silver/news_sentiment",
    "us_social_sentiment": "02_silver/social_sentiment",
    "us_analyst_ratings": "02_silver/analyst_ratings",
    "us_sec_financials": "02_silver/sec_financials",
    # Silver — unstructured (flatten from silver/processed_articles → 02_silver/processed_articles)
    "silver/processed_articles": "02_silver/processed_articles",
    "silver/sec_extractions": "02_silver/sec_extractions",
    # Gold
    "features": "03_gold/features",
}


def count_files(path: Path) -> int:
    """Count all files (not dirs) recursively."""
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*") if _.is_file())


def cleanup_url_encoded_partitions(dry_run: bool = True) -> int:
    """Rename URL-encoded date= partitions to canonical format.

    Fixes partitions like ``date=2026-02-04%2000%3A00%3A00.000000``
    → ``date=2026-02-04``.

    Returns the number of partitions renamed.
    """
    renamed = 0
    date_partition_re = re.compile(r"^date=(.+)$")

    for market_dir in ["01_bronze/market_data"]:
        scan_root = LAKE_DIR / market_dir
        if not scan_root.exists():
            continue
        for ticker_dir in scan_root.rglob("date=*"):
            if not ticker_dir.is_dir():
                continue
            match = date_partition_re.match(ticker_dir.name)
            if not match:
                continue
            raw = match.group(1)
            if "%" not in raw:
                continue
            decoded = unquote(raw)
            canonical = decoded.split(" ")[0].split("T")[0]
            new_path = ticker_dir.parent / f"date={canonical}"
            if new_path.exists():
                print(f"  SKIP  URL partition {ticker_dir.name} → {new_path.name} (target exists)")
                continue
            print(f"  {'PLAN' if dry_run else 'FIX'}   {ticker_dir.name} → {new_path.name}")
            if not dry_run:
                ticker_dir.rename(new_path)
            renamed += 1

    return renamed


def migrate(dry_run: bool = True) -> bool:
    """Migrate lake directories to medallion layout.

    Returns True if all migrations succeeded (or dry-run passed).
    """
    all_ok = True
    actions: list[tuple[str, str, int]] = []  # (src, dest, file_count)

    for old_rel, new_rel in MIGRATION_MAP.items():
        src = LAKE_DIR / old_rel
        dest = LAKE_DIR / new_rel

        if not src.exists():
            print(f"  SKIP  {old_rel:40s}  (source does not exist)")
            continue

        if dest.exists() and any(dest.iterdir()):
            print(f"  SKIP  {old_rel:40s}  (destination already populated: {new_rel})")
            continue

        file_count = count_files(src)
        actions.append((str(src), str(dest), file_count))
        print(f"  {'PLAN' if dry_run else 'MOVE'}  {old_rel:40s}  →  {new_rel}  ({file_count} files)")

    if dry_run:
        total_files = sum(fc for _, _, fc in actions)
        print(f"\n  Total: {len(actions)} directories, {total_files} files")
        print("  Run with --execute to perform migration.")
        return True

    for src_str, dest_str, expected_files in actions:
        src = Path(src_str)
        dest = Path(dest_str)

        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.move(str(src), str(dest))
        except Exception as e:
            print(f"  ERROR  Failed to move {src} → {dest}: {e}")
            all_ok = False
            continue

        actual_files = count_files(dest)
        if actual_files != expected_files:
            print(f"  WARN   File count mismatch: {src} had {expected_files}, {dest} has {actual_files}")
            all_ok = False
        else:
            print(f"  OK     {dest.relative_to(LAKE_DIR)}  ({actual_files} files)")

    # Create empty medallion layer directories that don't exist yet
    for layer_dir in [
        LAKE_DIR / "01_bronze" / "market_data",
        LAKE_DIR / "02_silver",
        LAKE_DIR / "03_gold",
        LAKE_DIR / "04_platinum" / "predictions",
    ]:
        layer_dir.mkdir(parents=True, exist_ok=True)

    return all_ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate data/lake/ to medallion layout")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Preview changes without moving")
    group.add_argument("--execute", action="store_true", help="Perform the migration")
    args = parser.parse_args()

    if not LAKE_DIR.exists():
        print(f"ERROR: Lake directory does not exist: {LAKE_DIR}")
        return 1

    print(f"{'DRY RUN' if args.dry_run else 'EXECUTE'} — Medallion migration")
    print(f"  Lake dir: {LAKE_DIR}\n")

    success = migrate(dry_run=args.dry_run)
    if success:
        print("\n  Scanning for URL-encoded date partitions...")
        fixed = cleanup_url_encoded_partitions(dry_run=args.dry_run)
        if fixed:
            print(f"  {'Would fix' if args.dry_run else 'Fixed'} {fixed} URL-encoded partition(s)")
        else:
            print("  No URL-encoded partitions found")

    if success:
        print("\nMigration completed successfully.")
        return 0
    print("\nMigration completed with warnings. Review output above.")
    return 0 if args.dry_run else 1


if __name__ == "__main__":
    sys.exit(main())
