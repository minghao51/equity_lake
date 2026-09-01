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
"""

from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl
import structlog

from equity_lake.storage.examples import QueryExamples
from equity_lake.storage.lake_reader import create_market_views

logger = structlog.get_logger(__name__)


# =============================================================================
# Database Connection and View Creation
# =============================================================================


class EquityDataDB:
    """DuckDB connection manager for equity data queries.

    All market tables are expected to be Delta Lake tables scanned via
    ``delta_scan()``. Market views are created from the ``core/paths.py``
    price-market registry (ADR-0010): one view per registry market, keyed and
    labelled by its canonical long key.
    """

    def __init__(self, db_path: str | Path | None = ":memory:"):
        self.db_path = db_path if db_path is not None else ":memory:"
        self.con = duckdb.connect(self.db_path)
        self.available_views: list[str] = []
        self._views_initialized = False

    def _ensure_views(self) -> None:
        if self._views_initialized:
            return
        self._views_initialized = True
        logger.info("Setting up unified views")
        self.available_views = create_market_views(self.con)

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

    def _create_market_view(self, view_name: str, data_dir: Path) -> None:
        if not data_dir.exists():
            logger.warning("Data directory not found", path=str(data_dir))
            return

        from equity_lake.storage.lake_reader import duckdb_scan_for

        scan_expr = duckdb_scan_for(data_dir)
        # The unified ``market`` column carries the canonical long key (ADR-0010).
        sql = f"CREATE OR REPLACE VIEW {view_name} AS SELECT *, '{view_name}' as market FROM {scan_expr}"

        try:
            self.con.execute(sql)
            logger.debug("Created view", view=view_name)
            self.available_views.append(view_name)
        except Exception as e:
            logger.error("Failed to create view", view=view_name, error=str(e))

    def _create_unified_view(self) -> None:
        """Create unified view across all markets."""
        if not self.available_views:
            self.con.execute("CREATE OR REPLACE VIEW equity_all AS SELECT NULL::VARCHAR AS ticker WHERE FALSE")
            return

        sql = "CREATE OR REPLACE VIEW equity_all AS " + " UNION ALL ".join(f"SELECT * FROM {view_name}" for view_name in self.available_views)

        try:
            self.con.execute(sql)
            logger.debug("Created unified view", view="equity_all")
        except Exception as e:
            logger.error("Failed to create unified view", error=str(e))

    def query(self, sql: str, params: list[Any] | None = None) -> pl.DataFrame:
        """Execute SQL query (optionally parameter-bound) and return a Polars DataFrame."""
        self._ensure_views()
        try:
            return self.con.execute(sql, params).pl() if params is not None else self.con.execute(sql).pl()
        except Exception as e:
            logger.error("Query failed", error=str(e))
            return pl.DataFrame()

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
            logger.error("Unknown query", name=name, available=available)
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
                logger.error("Query failed", name=name, error=str(e))
                results[name] = pl.DataFrame()
        return results


__all__ = ["EquityDataDB"]
