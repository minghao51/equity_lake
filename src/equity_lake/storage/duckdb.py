#!/usr/bin/env python3
"""
DuckDB connection manager for equity data queries.

``EquityDataDB`` creates unified views across markets and executes analytical
SQL. The demo ``QueryExamples`` / ``benchmark_queries`` live in
``storage.examples``; this class instantiates them on demand for the
``run_named_query`` / ``run_all_queries`` helpers and the ``equity query`` CLI.

Usage:
    uv run equity query
    uv run equity query --query top_gainers
    uv run equity query --date 2024-12-01
"""

import logging
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl

from equity_lake.core.paths import (
    CN_ASHARE_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    US_EQUITY_DIR,
)
from equity_lake.storage.examples import QueryExamples

logger = logging.getLogger(__name__)


# =============================================================================
# Database Connection and View Creation
# =============================================================================


class EquityDataDB:
    """DuckDB connection manager for equity data queries.

    All market tables are expected to be Delta Lake tables scanned via
    ``delta_scan()``.
    """

    MARKET_VIEWS = [
        ("us_equity", US_EQUITY_DIR, "us"),
        ("cn_ashare", CN_ASHARE_DIR, "cn"),
        ("hk_sg_equity", HK_SG_EQUITY_DIR, "hk_sg"),
        ("jpx_equity", JPX_EQUITY_DIR, "jpx"),
        ("krx_equity", KRX_EQUITY_DIR, "krx"),
    ]

    def __init__(self, db_path: str | Path | None = ":memory:"):
        self.db_path = db_path if db_path is not None else ":memory:"
        self.con = duckdb.connect(self.db_path)
        self.available_views: list[str] = []
        self._views_initialized = False

    def _ensure_views(self) -> None:
        if self._views_initialized:
            return
        self._views_initialized = True
        logger.info("Setting up unified views...")
        self.con.execute("INSTALL delta; LOAD delta;")

        for view_name, data_dir, market_label in self.MARKET_VIEWS:
            self._create_market_view(view_name, data_dir, market_label)

        self._create_unified_view()
        logger.info("Views created successfully")

    def close(self) -> None:
        if hasattr(self, "con") and self.con is not None:
            self.con.close()

    def __enter__(self) -> "EquityDataDB":
        self._ensure_views()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _create_market_view(self, view_name: str, data_dir: Path, market_label: str) -> None:
        if not data_dir.exists():
            logger.warning(f"Data directory not found: {data_dir}")
            return

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan_expr = duckdb_scan_for(data_dir)
        sql = f"CREATE OR REPLACE VIEW {view_name} AS SELECT *, '{market_label}' as market FROM {scan_expr}"

        try:
            self.con.execute(sql)
            logger.debug(f"Created view: {view_name}")
            self.available_views.append(view_name)
        except Exception as e:
            logger.error(f"Failed to create view {view_name}: {e}")

    def _create_unified_view(self) -> None:
        """Create unified view across all markets."""
        if not self.available_views:
            self.con.execute("CREATE OR REPLACE VIEW equity_all AS SELECT NULL::VARCHAR AS ticker WHERE FALSE")
            return

        sql = "CREATE OR REPLACE VIEW equity_all AS " + " UNION ALL ".join(f"SELECT * FROM {view_name}" for view_name in self.available_views)

        try:
            self.con.execute(sql)
            logger.debug("Created unified view: equity_all")
        except Exception as e:
            logger.error(f"Failed to create unified view: {e}")

    def query(self, sql: str) -> pl.DataFrame:
        """Execute SQL query and return a Polars DataFrame."""
        self._ensure_views()
        try:
            return self.con.execute(sql).pl()
        except Exception as e:
            logger.error(f"Query failed: {e}")
            return pl.DataFrame()

    def query_arrow(self, sql: str) -> Any:
        """Execute SQL query and return result as PyArrow Table (zero-copy)."""
        import pyarrow as pa

        self._ensure_views()
        try:
            return self.con.execute(sql).fetch_arrow_table()
        except Exception as e:
            logger.error(f"Arrow query failed: {e}")
            return pa.table({})

    def execute(self, sql: str) -> Any:
        """Execute SQL query and return result."""
        self._ensure_views()
        try:
            return self.con.execute(sql)
        except Exception as e:
            logger.error(f"Execution failed: {e}")
            raise

    QUERY_MAP: dict[str, str] = {
        "latest_summary": "query_1_latest_data_summary",
        "top_volume": "query_2_top_volume_stocks",
        "gainers_losers": "query_3_top_gainers_losers",
        "cross_market": "query_4_cross_market_comparison",
        "moving_avg": "query_5_moving_averages",
        "volatility": "query_6_volatility_analysis",
        "market_stats": "query_7_market_summary_stats",
        "price_range": "query_8_price_range_analysis",
    }

    def run_named_query(self, name: str, **kwargs: Any) -> pl.DataFrame:
        self._ensure_views()
        examples = QueryExamples(self)
        method_name = self.QUERY_MAP.get(name)
        if method_name is None:
            available = ", ".join(self.QUERY_MAP.keys())
            logger.error(f"Unknown query: {name}. Available: {available}")
            return pl.DataFrame()
        return cast(pl.DataFrame, getattr(examples, method_name)(**kwargs))

    def run_all_queries(self) -> dict[str, pl.DataFrame]:
        self._ensure_views()
        examples = QueryExamples(self)
        results: dict[str, pl.DataFrame] = {}
        for name, method_name in self.QUERY_MAP.items():
            try:
                results[name] = getattr(examples, method_name)()
            except Exception as e:
                logger.error(f"Query {name} failed: {e}")
                results[name] = pl.DataFrame()
        return results


__all__ = ["EquityDataDB"]
