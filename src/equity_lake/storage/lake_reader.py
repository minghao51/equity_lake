from __future__ import annotations

from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def ensure_delta_extension(con: object) -> None:
    """Idempotently install+load the DuckDB delta extension on *con*.

    Single home for the ``INSTALL delta; LOAD delta;`` bootstrap previously
    hand-rolled at ~10 call sites (storage, ingestion, monitoring, backtesting,
    ml/features). Cheap when already loaded; safe to call per-connection.
    """
    con.execute("INSTALL delta; LOAD delta;")  # type: ignore[attr-defined]


def create_market_views(
    con: object,
    *,
    view_prefix: str = "",
    columns: list[str] | None = None,
) -> list[str]:
    """Create one per-market view over the price-market registry (ADR-0010).

    Single home for the per-market ``CREATE OR REPLACE VIEW`` loop previously
    duplicated in ``storage/duckdb.py`` (unified query views, ``view_prefix=""``,
    all columns via ``SELECT *``) and ``backtesting/data_loader.py`` (backtest-
    prefixed OHLCV views, ``view_prefix="backtest_"``, projected to the OHLCV
    columns). ``columns=None`` selects every column; otherwise the view projects
    exactly ``columns``. Returns the names of the views actually created.
    """
    from equity_lake.core.paths import PRICE_MARKETS, market_dir

    ensure_delta_extension(con)
    created: list[str] = []
    for market in PRICE_MARKETS:
        data_dir = market_dir(market)
        if not data_dir.exists():
            logger.warning("Data directory not found, skipping view", market=market, path=str(data_dir))
            continue
        view_name = f"{view_prefix}{market}"
        scan = duckdb_scan_for(data_dir)
        col_expr = "*" if columns is None else ", ".join(columns)
        sql = f"CREATE OR REPLACE VIEW {view_name} AS SELECT {col_expr}, '{market}' as market FROM {scan}"
        try:
            con.execute(sql)  # type: ignore[attr-defined]
            created.append(view_name)
            logger.debug("Created market view", view=view_name)
        except Exception as e:
            logger.error("Failed to create view", view=view_name, error=str(e))
    return created


def duckdb_scan_for(market_path: Path) -> str:
    try:
        from deltalake import DeltaTable

        if DeltaTable.is_deltatable(str(market_path)):
            return f"delta_scan('{market_path}')"
    except (ImportError, OSError, ValueError):
        pass
    return f"read_parquet('{market_path}/**/*.parquet', hive_partitioning=1)"
