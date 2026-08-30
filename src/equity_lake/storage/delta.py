"""Delta Lake storage layer for equity data.

Thin wrapper around ``deltalake`` providing write, read, and maintenance
operations.  All dataset tables are date-partitioned Delta tables stored
under ``data/lake/<medallion-layer>/<dataset>/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
import structlog
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import SchemaMismatchError

from equity_lake.core.paths import LAKE_DIR
from equity_lake.core.polars_utils import FrameLike, normalize_temporal_columns

logger = structlog.get_logger(__name__)


class DeltaError(RuntimeError):
    """Base class for Delta storage failures, carrying the originating table."""


class DeltaWriteError(DeltaError):
    """Raised when a Delta write fails; the original cause is attached."""

    def __init__(self, table: str, cause: Exception) -> None:
        super().__init__(f"Failed to write Delta table '{table}'")
        self.table = table
        self.__cause__ = cause


class DeltaMergeError(DeltaError):
    """Raised when a Delta merge fails; the original cause is attached."""

    def __init__(self, table: str, cause: Exception) -> None:
        super().__init__(f"Failed to merge into Delta table '{table}'")
        self.table = table
        self.__cause__ = cause


class DeltaReadError(DeltaError):
    """Raised when a Delta table is missing or cannot be read."""

    def __init__(self, table: str, cause: Exception) -> None:
        super().__init__(f"Failed to read Delta table '{table}'")
        self.table = table
        self.__cause__ = cause


_DATE_COL = "date"
WriteMode = Literal["append", "overwrite", "ignore", "error"]
SchemaMode = Literal["merge", "overwrite"] | None


def delta_table_path(table: str, lake_dir: Path | None = None) -> Path:
    """Return the lake path for *table* (e.g. ``"04_platinum/predictions"``)."""
    return (lake_dir or LAKE_DIR) / table


def write_delta(
    df: FrameLike,
    table: str,
    mode: WriteMode = "append",
    partition_by: list[str] | None = None,
    lake_dir: Path | None = None,
    schema_mode: SchemaMode = None,
) -> bool:
    """Write a DataFrame to a date-partitioned Delta table.

    Args:
        df: Data to write. Must contain a ``date`` column.
        table: Table path relative to the lake root (e.g. ``"01_bronze/us_equity"``).
        mode: ``"append"``, ``"overwrite"``, ``"ignore"``, or ``"error"``.
        partition_by: Partition columns. Defaults to ``["date"]``.
        schema_mode: ``"merge"`` to evolve the schema, ``"overwrite"`` to replace it.
    """
    table_path = delta_table_path(table, lake_dir)
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
            table=table,
            rows=df_polars.height,
            mode=mode,
            path=str(table_path),
        )
        return True
    except Exception as exc:
        logger.exception("delta_write_failed", table=table)
        raise DeltaWriteError(table, exc) from exc


def merge_delta(
    df: FrameLike,
    table: str,
    key_columns: list[str] | None = None,
    lake_dir: Path | None = None,
) -> bool:
    """Upsert *df* into an existing Delta table, matching on *key_columns*.

    If the table does not yet exist it is created.  On a schema mismatch the
    target table is evolved to the incoming schema (all existing rows are
    preserved) and the merge is retried — rows are never appended on top of
    existing keys, so keyed upserts stay idempotent.
    """
    table_path = delta_table_path(table, lake_dir)
    keys = key_columns or ["ticker", _DATE_COL]
    df_polars = normalize_temporal_columns(df, date_columns=(_DATE_COL,))

    if not DeltaTable.is_deltatable(str(table_path)):
        return write_delta(df_polars, table, mode="append", lake_dir=lake_dir)

    predicate = " AND ".join(f"target.{k} = source.{k}" for k in keys)

    try:
        _execute_merge(DeltaTable(str(table_path)), df_polars, predicate)
    except Exception as exc:
        if not _is_schema_mismatch(exc):
            logger.exception("delta_merge_failed", table=table)
            raise DeltaMergeError(table, exc) from exc
        logger.warning(
            "delta_merge_schema_mismatch",
            table=table,
            error=str(exc),
            action="evolve schema and re-merge",
        )
        try:
            _evolve_table_schema(table, df_polars, lake_dir)
            _execute_merge(DeltaTable(str(table_path)), df_polars, predicate)
        except Exception as retry_exc:
            logger.exception("delta_merge_failed_after_evolution", table=table)
            raise DeltaMergeError(table, retry_exc) from retry_exc

    logger.info("delta_merge", table=table, rows=df_polars.height)
    return True


def _execute_merge(dt: DeltaTable, df_polars: pl.DataFrame, predicate: str) -> None:
    """Run one keyed upsert merge; any delta-rs failure raises."""
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


def _is_schema_mismatch(exc: Exception) -> bool:
    """True when *exc* looks like a source/target schema incompatibility."""
    if isinstance(exc, SchemaMismatchError):
        return True
    lowered = str(exc).lower()
    return "schema" in lowered or "column" in lowered


def _evolve_table_schema(table: str, df_polars: pl.DataFrame, lake_dir: Path | None) -> None:
    """Rewrite the table under the incoming schema, preserving every existing row.

    Seed-overwrite evolution: existing rows are re-written with the incoming
    (superset) schema — new columns are null-filled, conflicting types are
    widened to a common supertype — so the caller can re-run the keyed merge.
    The replacement is a single atomic Delta commit.
    """
    table_path = delta_table_path(table, lake_dir)
    existing = cast(pl.DataFrame, pl.from_arrow(DeltaTable(str(table_path)).to_pyarrow_table()))
    seed = pl.concat([existing, df_polars.head(0)], how="diagonal_relaxed")
    write_delta(seed, table, mode="overwrite", schema_mode="overwrite", lake_dir=lake_dir)


def read_delta(
    table: str,
    version: int | None = None,
    lake_dir: Path | None = None,
) -> pl.DataFrame:
    """Read a Delta table as a Polars DataFrame.

    Raises:
        DeltaReadError: If the table is missing or cannot be read.  Callers
            that genuinely want an empty frame on failure must catch this.
    """
    table_path = delta_table_path(table, lake_dir)
    try:
        dt = DeltaTable(str(table_path), version=version) if version is not None else DeltaTable(str(table_path))
        return cast(pl.DataFrame, pl.from_arrow(dt.to_pyarrow_table()))
    except Exception as exc:
        logger.exception("delta_read_failed", table=table, path=str(table_path))
        raise DeltaReadError(table, exc) from exc


def compact_delta(table: str, lake_dir: Path | None = None) -> dict[str, Any]:
    """Compact small files in a Delta table for better read performance."""
    table_path = delta_table_path(table, lake_dir)
    if not DeltaTable.is_deltatable(str(table_path)):
        logger.warning("delta_compact_skip", table=table, reason="not a delta table")
        return {}
    dt = DeltaTable(str(table_path))
    metrics: dict[str, Any] = dict(dt.optimize.compact())
    logger.info("delta_compact", table=table, metrics=metrics)
    return metrics


def vacuum_delta(
    table: str,
    retention_hours: int = 168,
    dry_run: bool = True,
    lake_dir: Path | None = None,
) -> list[str]:
    """Remove stale files from a Delta table."""
    table_path = delta_table_path(table, lake_dir)
    if not DeltaTable.is_deltatable(str(table_path)):
        return []
    dt = DeltaTable(str(table_path))
    files = list(dt.vacuum(retention_hours=retention_hours, dry_run=dry_run))
    logger.info("delta_vacuum", table=table, dry_run=dry_run, files=len(files))
    return files


def delta_table_version(table: str, lake_dir: Path | None = None) -> int | None:
    """Return the current version of a Delta table, or None if not a Delta table."""
    table_path = delta_table_path(table, lake_dir)
    if not DeltaTable.is_deltatable(str(table_path)):
        return None
    return int(DeltaTable(str(table_path)).version())


def migrate_parquet_to_delta(
    table: str,
    lake_dir: Path | None = None,
    dry_run: bool = False,
    keep_backup: bool = True,
) -> bool:
    """One-time migration: read existing Hive-partitioned Parquet and write as Delta.

    Creates a Delta table with ``partition_by=["date"]`` from the existing
    ``date=YYYY-MM-DD/*.parquet`` layout.

    When *keep_backup* is True, old ``date=`` directories are moved to a
    ``.pre_delta_backup/`` sibling directory before the write.  When False,
    the legacy parquet files are deleted only *after* the Delta rewrite has
    committed, so a failed write can never destroy the source data.
    """
    table_path = delta_table_path(table, lake_dir)
    lake = lake_dir or LAKE_DIR
    table_dir = lake / table

    if DeltaTable.is_deltatable(str(table_path)):
        logger.info("delta_migrate_skip", table=table, reason="already delta")
        return True

    if not table_dir.exists():
        logger.warning("delta_migrate_skip", table=table, reason="directory not found")
        return False

    logger.info("delta_migrate_start", table=table, path=str(table_dir))

    import duckdb

    con = duckdb.connect(":memory:")
    glob = str(table_dir / "**" / "*.parquet")
    try:
        df = con.execute(f"SELECT * FROM read_parquet('{glob}', hive_partitioning=1, union_by_name=true)").pl()
    except Exception as exc:
        logger.error("delta_migrate_read_failed", table=table, error=str(exc))
        return False
    finally:
        con.close()

    if df.is_empty():
        logger.warning("delta_migrate_empty", table=table)
        return False

    row_count = df.height
    logger.info("delta_migrate_data", table=table, rows=row_count)

    if dry_run:
        logger.info("delta_migrate_dry_run", table=table, rows=row_count)
        return True

    legacy_files = [] if keep_backup else _legacy_partition_files(table_dir)

    if keep_backup:
        # Move old Hive date= partitions aside BEFORE writing, otherwise Delta
        # (which also partitions by date) writes into the same date= dirs and
        # the backup step would relocate the freshly-written Delta data files,
        # leaving the Delta log pointing at missing files.  A failed write
        # leaves the source data recoverable under .pre_delta_backup/.
        _backup_old_partitions(table_dir)

    success = write_delta(df, table, mode="overwrite", lake_dir=lake_dir)
    if not success:
        return False

    if not keep_backup:
        # Write-then-cleanup: remove only the legacy parquet files that existed
        # before the rewrite, never the freshly-written Delta data files.
        _remove_legacy_files(legacy_files)
        logger.info("delta_migrate_cleanup", table=table, removed=len(legacy_files))

    logger.info("delta_migrate_done", table=table, rows=row_count)
    return True


def _legacy_partition_files(table_dir: Path) -> list[Path]:
    """List files inside legacy Hive ``date=`` directories (the pre-Delta source data)."""
    files: list[Path] = []
    for d in sorted(table_dir.iterdir()):
        if d.is_dir() and d.name.startswith("date=") and "%" not in d.name:
            files.extend(sorted(p for p in d.iterdir() if p.is_file()))
    return files


def _remove_legacy_files(legacy_files: list[Path]) -> None:
    """Delete snapshotted legacy parquet files and prune emptied ``date=`` dirs."""
    for f in legacy_files:
        f.unlink(missing_ok=True)
    for d in sorted({f.parent for f in legacy_files}):
        if d.name.startswith("date=") and not any(d.iterdir()):
            d.rmdir()


def _backup_old_partitions(table_dir: Path) -> None:
    """Move old Hive date= directories aside so they don't pollute the Delta table."""
    import shutil

    old_partitions = [d for d in table_dir.iterdir() if d.is_dir() and d.name.startswith("date=") and "%" not in d.name]
    if not old_partitions:
        return

    backup_dir = table_dir / ".pre_delta_backup"
    backup_dir.mkdir(exist_ok=True)
    for d in old_partitions:
        dest = backup_dir / d.name
        if not dest.exists():
            shutil.move(str(d), str(dest))
    logger.info("delta_migrate_backup", table=table_dir.name, backed_up=len(old_partitions), path=str(backup_dir))


__all__ = [
    "DeltaError",
    "DeltaMergeError",
    "DeltaReadError",
    "DeltaWriteError",
    "compact_delta",
    "delta_table_path",
    "delta_table_version",
    "merge_delta",
    "migrate_parquet_to_delta",
    "read_delta",
    "vacuum_delta",
    "write_delta",
]
