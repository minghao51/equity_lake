from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def duckdb_scan_for(market_path: Path) -> str:
    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(market_path)):
            return f"delta_scan('{market_path}')"
    except (ImportError, OSError, ValueError):
        pass
    return f"read_parquet('{market_path}/**/*.parquet', hive_partitioning=1)"
