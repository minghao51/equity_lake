from __future__ import annotations

import contextlib
from datetime import date
from pathlib import Path

import duckdb
import polars as pl
import structlog

from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
LAKE_DIR = DATA_DIR / "lake"
FEATURE_GLOB = str(LAKE_DIR / "features" / "**" / "*.parquet")


class FeatureLoader:
    def __init__(self) -> None:
        self.conn = duckdb.connect(":memory:")
        with contextlib.suppress(Exception):
            self.conn.execute("INSTALL delta; LOAD delta;")
        self._setup_feature_view()

    def _setup_feature_view(self) -> None:
        features_path = LAKE_DIR / "features"
        if features_path.exists():
            scan = duckdb_scan_for(features_path)
        else:
            scan = f"read_parquet('{FEATURE_GLOB}', hive_partitioning=1, union_by_name=true)"
        self.conn.execute(
            f"""
            CREATE OR REPLACE VIEW features_all AS
            SELECT * REPLACE (CAST(date AS TIMESTAMP) AS date)
            FROM {scan}
            """
        )
        logger.info("duckdb_feature_view_ready")

    def load_features(self, ticker: str, start_date: date, end_date: date) -> pl.DataFrame:
        query = """
            SELECT * FROM features_all
            WHERE ticker = $1
            AND date BETWEEN $2 AND $3
            ORDER BY date
        """
        df = self.conn.execute(query, [ticker, start_date, end_date]).pl()
        if df.is_empty():
            logger.warning("features_not_found", ticker=ticker, start_date=str(start_date), end_date=str(end_date))
        return df

    def close(self) -> None:
        if hasattr(self, "conn"):
            self.conn.close()
