"""Shared constants and helpers for dashboard modules."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from equity_lake.core.paths import (
    GOLD_FEATURES_DIR,
    LOGS_DIR,
    PRICE_MARKETS,
    market_dir,
)
from equity_lake.storage.lake_reader import duckdb_scan_for

# Price-market entries derived from the core/paths.py registry (ADR-0010);
# "features" is a gold medallion table route, not a market.
MARKET_DATASETS: dict[str, Path] = {
    **{market: market_dir(market) for market in PRICE_MARKETS},
    "features": GOLD_FEATURES_DIR,
}

_EMPTY_SUMMARY: dict[str, Any] = {
    "name": "",
    "available": False,
    "rows": 0,
    "symbols": 0,
    "latest_date": None,
    "path": "",
}


def summarize_dataset(conn: Any, name: str, path: Path) -> dict[str, Any]:
    """Summarize a dataset for the dashboard."""
    if not path.exists():
        return {**_EMPTY_SUMMARY, "name": name, "path": str(path)}

    query = f"""
        SELECT
            COUNT(*) AS rows,
            COUNT(DISTINCT ticker) AS symbols,
            CAST(MAX(date) AS VARCHAR) AS latest_date
        FROM {duckdb_scan_for(path)}
    """
    try:
        row = conn.execute(query).fetchone()
    except Exception:
        return {**_EMPTY_SUMMARY, "name": name, "path": str(path)}

    if row is None:
        return {**_EMPTY_SUMMARY, "name": name, "path": str(path)}

    return {
        "name": name,
        "available": True,
        "rows": int(row[0] or 0),
        "symbols": int(row[1] or 0),
        "latest_date": row[2],
        "path": str(path),
    }


def load_health_report(output_dir: Path | None = None) -> dict[str, Any] | None:
    """Load the pipeline health report from the canonical location."""
    candidates = [
        output_dir / "health-report.json" if output_dir else None,
        Path("site") / "health-report.json",
        LOGS_DIR / "health-report.json",
    ]
    for path in candidates:
        if path is not None and path.exists():
            try:
                result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
                return result
            except json.JSONDecodeError:
                return {"alerts": ["Health report could not be parsed."], "metrics": {}}
    return None
