"""Shared dashboard dataset/health query helpers.

Used by both the static HTML exporter (``exporter.py``) and the Streamlit
app (``streamlit_app.py``) so the two dashboards summarise the lake through
one code path instead of drifting apart.

Polars is the single dataframe engine (matches the rest of the project);
``duckdb_scan_for`` auto-detects Delta vs Hive-parquet partitions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl

from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    GOLD_FEATURES_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    US_EQUITY_DIR,
)
from equity_lake.storage.lake_reader import duckdb_scan_for

# Canonical market + feature datasets surfaced on the dashboard.
MARKET_DATASETS: dict[str, Path] = {
    "us_equity": US_EQUITY_DIR,
    "cn_ashare": CN_ASHARE_DIR,
    "hk_sg_equity": HK_SG_EQUITY_DIR,
    "jpx_equity": JPX_EQUITY_DIR,
    "krx_equity": KRX_EQUITY_DIR,
    "features": GOLD_FEATURES_DIR,
}


def _empty_summary(name: str, path: Path) -> dict[str, Any]:
    return {
        "name": name,
        "available": False,
        "rows": 0,
        "symbols": 0,
        "latest_date": None,
        "path": str(path),
    }


def summarize_dataset(conn: duckdb.DuckDBPyConnection, name: str, dataset_dir: Path) -> dict[str, Any]:
    """Return a row/symbol/latest-date summary for one dataset partition.

    Returns an ``available=False`` placeholder when the directory is missing
    or the scan fails (e.g. no parquet files yet).
    """
    if not dataset_dir.exists():
        return _empty_summary(name, dataset_dir)

    scan = duckdb_scan_for(dataset_dir)
    query = f"""
        SELECT
            COUNT(*) AS rows,
            COUNT(DISTINCT ticker) AS symbols,
            CAST(MAX(date) AS VARCHAR) AS latest_date
        FROM {scan}
    """

    try:
        row = conn.execute(query).fetchone()
    except Exception:
        return _empty_summary(name, dataset_dir)

    if row is None:
        return _empty_summary(name, dataset_dir)

    return {
        "name": name,
        "available": True,
        "rows": int(row[0] or 0),
        "symbols": int(row[1] or 0),
        "latest_date": row[2],
        "path": str(dataset_dir),
    }


def load_health_report(search_dirs: list[Path]) -> dict[str, Any] | None:
    """Load the first parseable ``health-report.json`` from ``search_dirs``.

    Returns ``None`` when no report exists; returns an alerts-shaped dict
    when a report exists but cannot be parsed.
    """
    for directory in search_dirs:
        health_path = directory / "health-report.json"
        if health_path.exists():
            try:
                return cast(dict[str, Any], json.loads(health_path.read_text(encoding="utf-8")))
            except json.JSONDecodeError:
                return {"alerts": ["Health report could not be parsed."], "metrics": {}}
    return None


def load_update_history(parquet_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """Load recent update-history rows from the parquet checkpoint.

    Returns an empty list when the checkpoint is missing, empty, or unreadable.
    """
    if not parquet_path.exists():
        return []
    try:
        frame = pl.read_parquet(parquet_path)
    except Exception:
        return []
    if frame.is_empty():
        return []
    recent = frame.sort("updated_at", descending=True).head(limit)
    return recent.to_dicts()
