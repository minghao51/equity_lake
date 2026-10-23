"""Delta Lake storage layer for equity data.

Thin wrapper around ``deltalake`` providing write, read, and maintenance
operations.  All dataset tables are date-partitioned Delta tables stored
under ``data/lake/<medallion-layer>/<dataset>/``.
"""

from __future__ import annotations

import errno
import stat
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
import structlog
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError
from deltalake.schema import Schema
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_none

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
# Only the modes the writer actually supports (append/overwrite). delta-rs
# accepts "ignore"/"error" too, but no caller uses them and advertising them
# here invites untested code paths — trim until implemented.
WriteMode = Literal["append", "overwrite"]
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
        mode: ``"append"`` or ``"overwrite"``.
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


def _open_existing_delta_table(table_path: Path) -> DeltaTable | None:
    """Open an existing valid Delta table, or return None only for true absence."""
    try:
        table_path.lstat()
    except FileNotFoundError as exc:
        if exc.errno != errno.ENOENT:
            raise
        _raise_for_dangling_ancestor(table_path)
        return None
    if not DeltaTable.is_deltatable(str(table_path)):
        raise FileNotFoundError(f"Existing path is not a readable Delta table: {table_path}")
    return DeltaTable(str(table_path))


def _raise_for_dangling_ancestor(path: Path) -> None:
    """Do not mistake a missing child behind a dangling symlink for absence."""
    candidate = path
    for _ in range(len(path.parts)):
        try:
            candidate_stat = candidate.lstat()
        except FileNotFoundError as exc:
            if exc.errno != errno.ENOENT:
                raise
        else:
            if stat.S_ISLNK(candidate_stat.st_mode):
                try:
                    candidate.stat()
                except FileNotFoundError as exc:
                    if exc.errno == errno.ENOENT:
                        raise FileNotFoundError(f"Dangling symlink in Delta table path at {candidate}") from exc
                    raise
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent


def merge_delta(
    df: FrameLike,
    table: str,
    key_columns: list[str] | None = None,
    lake_dir: Path | None = None,
    partition_by: list[str] | None = None,
) -> bool:
    """Upsert *df* into an existing Delta table, matching on *key_columns*.

    If the table does not yet exist it is created, partitioned by
    *partition_by* (``["date"]`` when None). New nullable source columns are
    added with Delta's native schema evolution; existing rows are not
    materialized or rewritten by this wrapper. Optimistic concurrency conflicts
    are retried at most three times, refreshing the table snapshot before each
    retry. Other failures, including incompatible types, remain visible.
    """
    table_path = delta_table_path(table, lake_dir)
    keys = key_columns or ["ticker", _DATE_COL]
    df_polars = normalize_temporal_columns(df, date_columns=(_DATE_COL,))

    missing_source_keys = [key for key in keys if key not in df_polars.columns]
    if missing_source_keys:
        raise DeltaMergeError(table, ValueError(f"source frame missing key columns: {missing_source_keys}"))
    if df_polars.select(keys).is_duplicated().any():
        raise DeltaMergeError(table, ValueError("source frame contains duplicate natural keys"))

    # SQL NULL-safe equality is required for intentional nullable natural-key
    # components, including article ticker=None rows. The expanded predicate is
    # supported by the installed delta-rs expression parser.
    predicate = " AND ".join(f"((target.{key} = source.{key}) OR (target.{key} IS NULL AND source.{key} IS NULL))" for key in keys)
    source_schema = Schema.from_arrow(df_polars.to_arrow().schema)
    source_fields = {field.name: field for field in source_schema.fields}
    table_path = delta_table_path(table, lake_dir)

    def refresh_after_conflict(retry_state: Any) -> None:
        """Reload the committed snapshot before tenacity starts another attempt."""
        del retry_state
        snapshot["table"] = _open_existing_delta_table(table_path)
        logger.warning("delta_merge_occ_refresh", table=table)

    snapshot: dict[str, DeltaTable | None] = {"table": None}

    def attempt_merge() -> None:
        current = snapshot["table"]
        if current is None:
            current = _open_existing_delta_table(table_path)
            if current is None:
                try:
                    # error-if-exists prevents a racing creator from silently
                    # appending duplicate natural keys after our absence check.
                    write_deltalake(
                        str(table_path),
                        df_polars.to_arrow(),
                        mode="error",
                        partition_by=partition_by or [_DATE_COL],
                    )
                except CommitFailedError:
                    raise
                except Exception:
                    # A concurrent process may have won table creation between
                    # the snapshot check and write. Re-open and keyed-merge only
                    # when a valid Delta table now exists; corrupt paths/errors
                    # remain visible.
                    current = _open_existing_delta_table(table_path)
                    if current is None:
                        raise
                    snapshot["table"] = current
                    _execute_merge(current, df_polars, predicate)
                    return
                snapshot["table"] = DeltaTable(str(table_path))
                return
            snapshot["table"] = current

        target_fields = {field.name for field in current.schema().fields}
        missing_target_keys = [key for key in keys if key not in target_fields]
        if missing_target_keys:
            # Delta 1.6 cannot bind a merge predicate against a target field
            # that does not exist yet. Add only those nullable source fields via
            # a metadata commit, then reload before the keyed merge.
            missing_fields = [source_fields[key] for key in missing_target_keys]
            current.alter.add_columns(missing_fields)
            current = DeltaTable(str(table_path))
            snapshot["table"] = current

        _execute_merge(current, df_polars, predicate)

    try:
        retryer = Retrying(
            retry=retry_if_exception_type(CommitFailedError),
            stop=stop_after_attempt(3),
            wait=wait_none(),
            before_sleep=refresh_after_conflict,
            reraise=True,
        )
        retryer(attempt_merge)
    except Exception as exc:
        logger.exception("delta_merge_failed", table=table)
        raise DeltaMergeError(table, exc) from exc

    logger.info("delta_merge", table=table, rows=df_polars.height)
    return True


def _execute_merge(dt: DeltaTable, df_polars: pl.DataFrame, predicate: str) -> None:
    """Run one keyed upsert with native nullable additive schema evolution."""
    (
        dt.merge(
            source=df_polars.to_arrow(),
            predicate=predicate,
            source_alias="source",
            target_alias="target",
            merge_schema=True,
            error_on_type_mismatch=True,
        )
        .when_matched_update_all()
        .when_not_matched_insert_all()
        .execute()
    )


def update_delta_columns(
    df: FrameLike,
    table: str,
    *,
    update_columns: list[str],
    key_columns: list[str] | None = None,
    lake_dir: Path | None = None,
) -> dict[str, Any]:
    """Matched-only, column-scoped update of existing rows (ADR-0014 §4).

    This is the minimal seam for bounded label-only finalization. Unlike
    :func:`merge_delta` it deliberately does **not**:

    - create the table when it is absent (raises :class:`DeltaMergeError`);
    - insert source keys that do not match a target row;
    - evolve the schema (unknown *update_columns* raise);
    - touch any column outside *update_columns*.

    Broad schema-evolution/DDL hardening remains W06. Returns the delta-rs
    merge metrics (``num_target_rows_updated`` is the matched-row count).
    """
    table_path = delta_table_path(table, lake_dir)
    keys = key_columns or ["ticker", _DATE_COL]
    df_polars = normalize_temporal_columns(df, date_columns=(_DATE_COL,))

    if df_polars.is_empty():
        return {"num_source_rows": 0, "num_target_rows_updated": 0, "num_target_rows_inserted": 0}
    if not DeltaTable.is_deltatable(str(table_path)):
        raise DeltaMergeError(table, FileNotFoundError(f"Delta table '{table}' does not exist"))

    dt = DeltaTable(str(table_path))
    target_fields = {field.name for field in dt.schema().fields}
    unknown = [column for column in update_columns if column not in target_fields]
    if unknown:
        raise DeltaMergeError(table, ValueError(f"update columns not in target schema (no evolution): {unknown}"))
    missing_keys = [key for key in keys if key not in df_polars.columns]
    if missing_keys:
        raise DeltaMergeError(table, ValueError(f"source frame missing key columns: {missing_keys}"))

    predicate = " AND ".join(f"target.{key} = source.{key}" for key in keys)
    updates = {column: f"source.{column}" for column in update_columns}
    try:
        metrics = (
            dt.merge(
                source=df_polars.to_arrow(),
                predicate=predicate,
                source_alias="source",
                target_alias="target",
            )
            .when_matched_update(updates)
            .execute()
        )
    except Exception as exc:
        logger.exception("delta_update_failed", table=table)
        raise DeltaMergeError(table, exc) from exc

    logger.info(
        "delta_update_columns",
        table=table,
        rows=df_polars.height,
        columns=update_columns,
        updated=metrics.get("num_target_rows_updated"),
    )
    return {key: int(value) for key, value in metrics.items()}


def read_delta(
    table: str,
    version: int | None = None,
    lake_dir: Path | None = None,
    filters: list[tuple[str, str, Any]] | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame:
    """Read a Delta table as a Polars DataFrame.

    Args:
        filters: Optional delta-rs predicate list (``[(col, op, value), ...]``
            ANDed together) for bounded/partition-pruned reads.
        columns: Optional projection; only these columns are materialized.

    Raises:
        DeltaReadError: If the table is missing or cannot be read.  Callers
            that genuinely want an empty frame on failure must catch this.
    """
    table_path = delta_table_path(table, lake_dir)
    try:
        dt = DeltaTable(str(table_path), version=version) if version is not None else DeltaTable(str(table_path))
        projected = filters is not None or columns is not None
        arrow = dt.to_pyarrow_table(columns=columns, filters=filters) if projected else dt.to_pyarrow_table()
        return cast(pl.DataFrame, pl.from_arrow(arrow))
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
        df = con.execute("SELECT * FROM read_parquet(?, hive_partitioning=1, union_by_name=true)", [glob]).pl()
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
            try:
                shutil.move(str(d), str(dest))
            except (OSError, shutil.Error) as exc:
                raise RuntimeError(f"Unable to back up legacy Delta partition {d} to {dest}") from exc
    logger.info("delta_migrate_backup", table=table_dir.name, backed_up=len(old_partitions), path=str(backup_dir))


__all__ = [
    "DeltaError",
    "DeltaMergeError",
    "DeltaReadError",
    "DeltaWriteError",
    "compact_delta",
    "delta_table_path",
    "merge_delta",
    "migrate_parquet_to_delta",
    "read_delta",
    "update_delta_columns",
    "vacuum_delta",
    "write_delta",
]
