#!/usr/bin/env python3
"""One-time migration: convert Hive-partitioned Parquet to Delta Lake tables.

Usage:
    uv run python scripts/migrate_to_delta.py
    uv run python scripts/migrate_to_delta.py --markets us_equity cn_ashare --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("EQUITY__PROJECT__LOG_LEVEL", "INFO")

from equity_lake.core.paths import LAKE_DIR

logger = structlog.get_logger(__name__)

DELTA_MARKETS = ["us_equity", "cn_ashare", "hk_sg_equity", "jpx_equity", "krx_equity", "features", "macro_indicators"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate Hive-partitioned Parquet to Delta Lake")
    parser.add_argument("--markets", nargs="+", default=DELTA_MARKETS, help="Markets to migrate")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--lake-dir", type=Path, default=LAKE_DIR, help="Lake root directory")
    args = parser.parse_args()

    from equity_lake.core.logging import setup_structured_logging

    setup_structured_logging(level="INFO")

    from equity_lake.storage.delta import migrate_parquet_to_delta

    for market in args.markets:
        logger.info("migrating", market=market)
        ok = migrate_parquet_to_delta(market, lake_dir=args.lake_dir, dry_run=args.dry_run)
        status = "OK" if ok else "SKIPPED/FAILED"
        logger.info("migration_result", market=market, status=status)

    logger.info("migration_complete", markets=args.markets, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
