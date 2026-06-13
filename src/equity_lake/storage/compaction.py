"""Delta Lake compaction for lake maintenance.

Compacts small files in Delta tables using native ``optimize.compact()``
for better read performance and reduced storage overhead.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from equity_lake.core.paths import LAKE_DIR

logger = structlog.get_logger(__name__)


def compact_market(
    market_dir: Path,
    max_days_per_file: int = 30,
    dry_run: bool = False,
) -> int:
    """Compact a single market Delta table using ``optimize.compact()``.

    Returns the number of files removed, or 0 if the directory is not a
    Delta table.
    """
    if not market_dir.exists():
        logger.warning("market_dir_not_found", path=str(market_dir))
        return 0

    from deltalake import DeltaTable

    if not DeltaTable.is_deltatable(str(market_dir)):
        logger.warning("compact_skip", market=str(market_dir.name), reason="not a delta table")
        return 0

    if dry_run:
        logger.info("delta_compact_dry_run", market=str(market_dir.name))
        return 0

    dt = DeltaTable(str(market_dir))
    metrics: dict[str, Any] = dt.optimize.compact()
    removed = int(metrics.get("numFilesRemoved", 0))
    logger.info("delta_compact_done", market=str(market_dir.name), metrics=metrics)
    return removed


def compact_all_markets(
    lake_dir: Path | None = None,
    max_days_per_file: int = 30,
    dry_run: bool = False,
) -> dict[str, int]:
    """Run compaction across all market directories.

    Returns dict mapping market name to number of files removed.
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
