"""Delta Lake storage layer for equity data.

Thin wrapper around ``deltalake`` providing write, read, and maintenance
operations.  All market tables are date-partitioned Delta tables stored
under ``data/lake/<medallion-layer>/<dataset>/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import duckdb
import polars as pl
import structlog
from deltalake import DeltaTable, write_deltalake

from equity_lake.core.paths import LAKE_DIR
from equity_lake.core.polars_utils import FrameLike, normalize_temporal_columns

logger = structlog.get_logger(__name__)

_DATE_COL = "date"
WriteMode = Literal["append", "overwrite", "ignore", "error"]
SchemaMode = Literal["merge", "overwrite"] | None


def delta_table_path(market: str, lake_dir: Path | None = None) -> Path:
    return (lake_dir or LAKE_DIR) / market


def write_delta(
    df: FrameLike,
    market: str,
    mode: WriteMode = "append",
    partition_by: list[str] | None = None,
    lake_dir: Path | None = None,
    schema_mode: SchemaMode = None,
) -> bool:
    """Write a DataFrame to a date-partitioned Delta table.

    Args:
        df: Data to write. Must contain a ``date`` column.
        market: Market name (e.g. ``"us_equity"``).
        mode: ``"append"`` or ``"overwrite"``.
        partition_by: Partition columns. Defaults to ``["date"]``.
        schema_mode: ``"merge"`` to allow schema evolution.
    """
    table_path = delta_table_path(market, lake_dir)
    partitions = partition_by or [_DATE_COL]
    df_polars = normalize_temporal_columns(df, date_columns=(_DATE_COL,))

    try:
        write_deltalake(
            str(table_path),
            df_polars.to_arrow(),
            mode=mode,
            partition_by=partitions,
            schema_mode=schema_mode,
        )
        logger.info(
            "delta_write",
            market=market,
            rows=df_polars.height,
            mode=mode,
            path=str(table_path),
        )
        return True
    except Exception:
        logger.exception("delta_write_failed", market=market)
        return False


def merge_delta(
    df: FrameLike,
    market: str,
    key_columns: list[str] | None = None,
    lake_dir: Path | None = None,
) -> bool:
    """Upsert *df* into an existing Delta table, matching on *key_columns*.

    If the table does not yet exist, falls back to ``write_delta(mode="append")``.
    """
    table_path = delta_table_path(market, lake_dir)
    keys = key_columns or ["ticker", _DATE_COL]
    df_polars = normalize_temporal_columns(df, date_columns=(_DATE_COL,))

    if not DeltaTable.is_deltatable(str(table_path)):
        return write_delta(df_polars, market, mode="append", lake_dir=lake_dir)

    dt = DeltaTable(str(table_path))

    predicate = " AND ".join(f"target.{k} = source.{k}" for k in keys)

    try:
        (
            dt.merge(
                source=df_polars.to_arrow(),
                predicate=predicate,
                source_alias="source",
                target_alias="target",
            )
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute()
        )
        logger.info("delta_merge", market=market, rows=df_polars.height)
        return True
    except Exception as exc:
        if "schema" in str(exc).lower() or "column" in str(exc).lower():
            logger.warning("delta_merge_schema_mismatch", market=market, hint="falling back to append with schema merge")
            return write_delta(df_polars, market, mode="append", schema_mode="merge", lake_dir=lake_dir)
        logger.exception("delta_merge_failed", market=market)
        return False


def read_delta(
    market: str,
    version: int | None = None,
    lake_dir: Path | None = None,
) -> pl.DataFrame:
    """Read a Delta table as a Polars DataFrame."""
    table_path = delta_table_path(market, lake_dir)
    try:
        dt = DeltaTable(str(table_path), version=version) if version is not None else DeltaTable(str(table_path))
        return cast(pl.DataFrame, pl.from_arrow(dt.to_pyarrow_table()))
    except Exception:
        logger.exception("delta_read_failed", market=market)
        return pl.DataFrame()


def compact_delta(market: str, lake_dir: Path | None = None) -> dict[str, Any]:
    """Compact small files in a Delta table for better read performance."""
    table_path = delta_table_path(market, lake_dir)
    if not DeltaTable.is_deltatable(str(table_path)):
        logger.warning("delta_compact_skip", market=market, reason="not a delta table")
        return {}
    dt = DeltaTable(str(table_path))
    metrics: dict[str, Any] = dict(dt.optimize.compact())
    logger.info("delta_compact", market=market, metrics=metrics)
    return metrics


def vacuum_delta(
    market: str,
    retention_hours: int = 168,
    dry_run: bool = True,
    lake_dir: Path | None = None,
) -> list[str]:
    """Remove stale files from a Delta table."""
    table_path = delta_table_path(market, lake_dir)
    if not DeltaTable.is_deltatable(str(table_path)):
        return []
    dt = DeltaTable(str(table_path))
    files = list(dt.vacuum(retention_hours=retention_hours, dry_run=dry_run))
    logger.info("delta_vacuum", market=market, dry_run=dry_run, files=len(files))
    return files


def delta_table_version(market: str, lake_dir: Path | None = None) -> int | None:
    """Return the current version of a Delta table, or None if not a Delta table."""
    table_path = delta_table_path(market, lake_dir)
    if not DeltaTable.is_deltatable(str(table_path)):
        return None
    return int(DeltaTable(str(table_path)).version())


def migrate_parquet_to_delta(
    market: str,
    lake_dir: Path | None = None,
    dry_run: bool = False,
    keep_backup: bool = True,
) -> bool:
    """One-time migration: read existing Hive-partitioned Parquet and write as Delta.

    Creates a Delta table with ``partition_by=["date"]`` from the existing
    ``date=YYYY-MM-DD/*.parquet`` layout.

    When *keep_backup* is True, old ``date=`` directories are moved to a
    ``.pre_delta_backup/`` sibling directory rather than deleted.
    """
    table_path = delta_table_path(market, lake_dir)
    lake = lake_dir or LAKE_DIR
    market_dir = lake / market

    if DeltaTable.is_deltatable(str(table_path)):
        logger.info("delta_migrate_skip", market=market, reason="already delta")
        return True

    if not market_dir.exists():
        logger.warning("delta_migrate_skip", market=market, reason="directory not found")
        return False

    logger.info("delta_migrate_start", market=market, path=str(market_dir))

    con = duckdb.connect(":memory:")
    glob = str(market_dir / "**" / "*.parquet")
    try:
        df = con.execute(f"SELECT * FROM read_parquet('{glob}', hive_partitioning=1, union_by_name=true)").pl()
    except Exception as exc:
        logger.error("delta_migrate_read_failed", market=market, error=str(exc))
        return False
    finally:
        con.close()

    if df.is_empty():
        logger.warning("delta_migrate_empty", market=market)
        return False

    row_count = df.height
    logger.info("delta_migrate_data", market=market, rows=row_count)

    if dry_run:
        logger.info("delta_migrate_dry_run", market=market, rows=row_count)
        return True

    # Move old Hive date= partitions aside BEFORE writing, otherwise Delta
    # (which also partitions by date) writes into the same date= dirs and the
    # backup step below would relocate the freshly-written Delta data files,
    # leaving the Delta log pointing at missing files.
    _backup_old_partitions(market_dir, keep_backup=keep_backup)

    success = write_delta(df, market, mode="overwrite", lake_dir=lake_dir)
    if success:
        logger.info("delta_migrate_done", market=market, rows=row_count)
    return success


def _backup_old_partitions(market_dir: Path, keep_backup: bool = True) -> None:
    """Move old Hive date= directories aside so they don't pollute the Delta table."""
    import shutil

    old_partitions = [d for d in market_dir.iterdir() if d.is_dir() and d.name.startswith("date=") and "%" not in d.name]
    if not old_partitions:
        return

    if keep_backup:
        backup_dir = market_dir / ".pre_delta_backup"
        backup_dir.mkdir(exist_ok=True)
        for d in old_partitions:
            dest = backup_dir / d.name
            if not dest.exists():
                shutil.move(str(d), str(dest))
        logger.info("delta_migrate_backup", market=market_dir.name, backed_up=len(old_partitions), path=str(backup_dir))
    else:
        for d in old_partitions:
            shutil.rmtree(d)
        logger.info("delta_migrate_cleanup", market=market_dir.name, removed=len(old_partitions))


__all__ = [
    "compact_delta",
    "delta_table_path",
    "delta_table_version",
    "merge_delta",
    "migrate_parquet_to_delta",
    "read_delta",
    "vacuum_delta",
    "write_delta",
]
