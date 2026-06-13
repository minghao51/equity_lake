from __future__ import annotations

from pathlib import Path


def duckdb_scan_for(market_path: Path) -> str:
    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(market_path)):
            return f"delta_scan('{market_path}')"
    except Exception:
        pass
    return f"read_parquet('{market_path}/**/*.parquet', hive_partitioning=1)"
