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
from types import TracebackType
from typing import Any, cast

import duckdb
import polars as pl
import structlog

from equity_lake.storage.examples import QueryExamples
from equity_lake.storage.lake_reader import create_market_views

logger = structlog.get_logger(__name__)


class QueryError(RuntimeError):
    """Raised when a DuckDB query fails or names an unknown named query."""


# Column types for the empty-lake ``equity_all`` fallback view; unlisted
# STANDARD_COLUMNS default to VARCHAR. Mirrors the real market-view contract.
_EMPTY_VIEW_COLUMN_TYPES: dict[str, str] = {
    "date": "DATE",
    "open": "DOUBLE",
    "high": "DOUBLE",
    "low": "DOUBLE",
    "close": "DOUBLE",
    "volume": "DOUBLE",
    "adj_close": "DOUBLE",
    "identity_schema_version": "BIGINT",
}

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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _create_unified_view(self) -> None:
        """Create unified view across all markets."""
        if not self.available_views:
            # Empty-lake fallback: keep the real market-view column contract so named
            # queries bind and genuinely return zero rows instead of binder errors
            # (column names/types come from the fixed STANDARD_COLUMNS constant, not input).
            from equity_lake.core.schemas import STANDARD_COLUMNS

            columns = ", ".join(f"CAST(NULL AS {_EMPTY_VIEW_COLUMN_TYPES.get(column, 'VARCHAR')}) AS {column}" for column in STANDARD_COLUMNS)
            self.con.sql(f"CREATE OR REPLACE VIEW equity_all AS SELECT {columns} WHERE FALSE")
            return

        sql = "CREATE OR REPLACE VIEW equity_all AS " + " UNION ALL ".join(f"SELECT * FROM {view_name}" for view_name in self.available_views)

        try:
            self.con.sql(sql)
            logger.debug("Created unified view", view="equity_all")
        except Exception as e:
            logger.error("Failed to create unified view", error=str(e))

    def query(self, sql: str, params: list[Any] | None = None) -> pl.DataFrame:
        """Execute SQL query (optionally parameter-bound) and return a Polars DataFrame.

        Raises:
            QueryError: When the SQL is invalid or execution fails — SQL bugs are
                never masked as an empty result.
        """
        self._ensure_views()
        try:
            # Relation API: same engine/results as execute(), keeps dynamic operator SQL
            # (``equity query``) out of the statement-execution sink; bind params stay bound.
            return self.con.sql(sql, params=params).pl() if params is not None else self.con.sql(sql).pl()
        except Exception as e:
            logger.error("Query failed", error=str(e))
            raise QueryError(str(e)) from e

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
            raise QueryError(f"Unknown query {name!r}; available: {available}")
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


__all__ = ["EquityDataDB", "QueryError"]
