from __future__ import annotations

import contextlib
from datetime import date

import duckdb
import polars as pl
import structlog

from equity_lake.core.paths import GOLD_FEATURES_DIR
from equity_lake.storage.lake_reader import duckdb_scan_for, ensure_delta_extension

logger = structlog.get_logger(__name__)


class FeatureLoader:
    def __init__(self) -> None:
        self.conn = duckdb.connect(":memory:")
        with contextlib.suppress(Exception):
            ensure_delta_extension(self.conn)
        self._setup_feature_view()

    def _setup_feature_view(self) -> None:
        scan = self._feature_scan()
        if scan is None:
            self.conn.execute(
                """
                CREATE OR REPLACE VIEW features_all AS
                SELECT
                    CAST(NULL AS VARCHAR) AS ticker,
                    CAST(NULL AS TIMESTAMP) AS date
                WHERE FALSE
                """
            )
            logger.info("duckdb_feature_view_ready", source="empty")
            return

        self.conn.execute(
            f"""
            CREATE OR REPLACE VIEW features_all AS
            SELECT * REPLACE (CAST(date AS TIMESTAMP) AS date)
            FROM {scan}
            """
        )
        logger.info("duckdb_feature_view_ready")

    def _feature_scan(self) -> str | None:
        # The gold feature table is the single source; scan it only when it
        # actually holds Parquet files (an empty dir would fail the Delta scan).
        if GOLD_FEATURES_DIR.exists() and any(GOLD_FEATURES_DIR.rglob("*.parquet")):
            return duckdb_scan_for(GOLD_FEATURES_DIR)
        return None

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
