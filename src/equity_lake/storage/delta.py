"""Delta Lake storage layer for equity data.

Thin wrapper around ``deltalake`` providing write, read, and maintenance
operations.  All dataset tables are date-partitioned Delta tables stored
under ``data/lake/<medallion-layer>/<dataset>/``.
"""

from __future__ import annotations

import errno
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pa_parquet
import structlog
from deltalake import DeltaTable, write_deltalake
from deltalake.exceptions import CommitFailedError
from deltalake.schema import Schema
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_none

from equity_lake.core.paths import LAKE_DIR, price_dataset_market
from equity_lake.core.polars_utils import FrameLike, normalize_temporal_columns
from equity_lake.core.price_provenance import PriceProvenanceError, prepare_price_provenance_frame

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

    if price_dataset_market(table) is not None:
        # The direct writer route (seeders, bootstrap, migration) is audited like the
        # canonical one: absent evidence is stamped null and a malformed or unsupported
        # claim is refused instead of persisted (ADR-0027 §2/§3). A non-price table is
        # untouched.
        try:
            df_polars = prepare_price_provenance_frame(df_polars)
        except PriceProvenanceError as exc:
            raise DeltaWriteError(table, exc) from exc

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

    if price_dataset_market(table) is not None:
        # Replacing a row's values replaces its origin envelope in the same row operation:
        # a matched update must never inherit the previous marker, and a batch carrying
        # no evidence must write null rather than keep the target's claim (ADR-0027 §2).
        # A malformed claim is refused before any merge is attempted.
        try:
            df_polars = prepare_price_provenance_frame(df_polars)
        except PriceProvenanceError as exc:
            raise DeltaMergeError(table, exc) from exc

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
        filters: Optional predicate list (``[(col, op, value), ...]`` ANDed
            together) for bounded reads. Date/numeric predicates are pushed down
            to delta-rs; **string** predicates are applied in Polars after the
            read (see :func:`_split_filters`).
        columns: Optional projection; only these columns are materialized.

    Raises:
        DeltaReadError: If the table is missing or cannot be read, if a filter
            names an absent column, or if a string filter uses an unsupported
            operator. Callers that genuinely want an empty frame on failure must
            catch this.
    """
    frame, _version = _read_delta_frame(table, version=version, lake_dir=lake_dir, filters=filters, columns=columns, capture_version=False)
    return frame


def read_delta_with_version(
    table: str,
    *,
    lake_dir: Path | None = None,
    filters: list[tuple[str, str, Any]] | None = None,
    columns: list[str] | None = None,
) -> tuple[pl.DataFrame, int]:
    """Read a Delta table **and** the version that produced the frame (E01-4).

    Snapshot binding (ADR-0025 Decision 4) requires the version to be captured by
    the read itself: a version looked up after the computation describes the table
    as it is then, not the snapshot the computation consumed. This shares one
    implementation with :func:`read_delta`, which asks for the same frame *without*
    the version, so the two can never disagree about what was read. The difference is
    only the version requirement: this entry point needs a table object that can
    report its version, while :func:`read_delta` never calls ``version()``.

    Args:
        filters: Same predicate contract as :func:`read_delta`.
        columns: Same projection contract as :func:`read_delta`.

    Returns:
        ``(frame, version)`` where ``version`` is read from the
        :class:`deltalake.DeltaTable` instance that produced ``frame``, taken
        immediately after construction and before the scan (the latest version at
        that moment).

    Raises:
        DeltaReadError: Under exactly :func:`read_delta`'s conditions, and also if
            the table object cannot supply its version — no version is invented to
            stand in for one that was never read.
    """
    frame, read_version = _read_delta_frame(table, lake_dir=lake_dir, filters=filters, columns=columns, capture_version=True)
    if read_version is None:  # pragma: no cover - capture_version=True always supplies one or raises
        raise DeltaReadError(table, RuntimeError("table did not report a version for the read"))
    return frame, read_version


def _read_delta_frame(
    table: str,
    *,
    version: int | None = None,
    lake_dir: Path | None = None,
    filters: list[tuple[str, str, Any]] | None = None,
    columns: list[str] | None = None,
    capture_version: bool = True,
) -> tuple[pl.DataFrame, int | None]:
    """Read a Delta table and optionally return its read-seam version.

    ``version`` selects an explicit snapshot; ``None`` reads the latest.

    ``capture_version`` makes the version requirement explicit. When it is ``True``
    the version is taken from the constructed table object **before** the scan, so it
    names the snapshot the scan consumed rather than whatever is current afterwards,
    and a table object that cannot report one is a read failure. When it is
    ``False`` ``version()`` is never called and the returned version is ``None``:
    that is what :func:`read_delta` asks for, so the blind reader's capability
    contract stays exactly what it was before version capture existed.
    """
    table_path = delta_table_path(table, lake_dir)
    try:
        dt = DeltaTable(str(table_path), version=version) if version is not None else DeltaTable(str(table_path))
    except Exception as exc:
        logger.exception("delta_read_failed", table=table, path=str(table_path))
        raise DeltaReadError(table, exc) from exc

    read_version: int | None = None
    if capture_version:
        try:
            read_version = int(dt.version())
        except Exception as exc:  # noqa: BLE001 - an unreadable version is an unreadable snapshot
            logger.exception("delta_read_failed", table=table, path=str(table_path))
            raise DeltaReadError(table, exc) from exc

    pushdown, residual = _split_filters(dt, table, filters)
    read_columns, hidden_columns = _with_residual_columns(columns, residual)
    projected = read_columns is not None or bool(pushdown)
    try:
        # pyarrow rejects an empty filter list ("Malformed filters"), so an
        # all-residual predicate set reads with filters=None.
        arrow_filters = pushdown or None
        arrow = dt.to_pyarrow_table(columns=read_columns, filters=arrow_filters) if projected else dt.to_pyarrow_table()
        frame = cast(pl.DataFrame, pl.from_arrow(arrow))
    except Exception as exc:
        logger.exception("delta_read_failed", table=table, path=str(table_path))
        raise DeltaReadError(table, exc) from exc

    if residual:
        logger.debug("delta_read_residual_filter", table=table, columns=[column for column, _op, _value in residual])
        frame = _apply_residual_filters(frame, residual)
    if hidden_columns:
        frame = frame.drop(hidden_columns)
    return frame, read_version


#: Operators accepted for a string filter that cannot be pushed down.
RESIDUAL_STRING_OPERATORS: tuple[str, ...] = ("=", "==", "!=", "in", "not in", "<", "<=", ">", ">=")


def _split_filters(
    dt: DeltaTable,
    table: str,
    filters: list[tuple[str, str, Any]] | None,
) -> tuple[list[tuple[str, str, Any]], list[tuple[str, str, Any]]]:
    """Split predicates into arrow pushdown and Polars residual filters.

    Merge rewrites (e.g. label-only updates) make delta-rs store string columns
    as Arrow ``string_view``, and PyArrow has no comparison kernels for that type
    (verified 21.x and 22.x): ``=``, ``in``, ``>=`` and even a cast-then-compare
    raise ``ArrowNotImplementedError``. The Delta log still declares ``string``,
    so the only safe policy is to keep string predicates out of the pushdown.
    """
    if not filters:
        return [], []
    schema = pa.schema(dt.schema().to_arrow())
    string_columns = {field.name for field in schema if pa.types.is_string(field.type) or pa.types.is_large_string(field.type)}
    pushdown: list[tuple[str, str, Any]] = []
    residual: list[tuple[str, str, Any]] = []
    for column, op, value in filters:
        if column not in schema.names:
            raise DeltaReadError(table, ValueError(f"filter column not in table schema: {column}"))
        normalized = " ".join(op.lower().split())
        if column in string_columns:
            if normalized not in RESIDUAL_STRING_OPERATORS:
                raise DeltaReadError(
                    table,
                    ValueError(f"unsupported string filter operator {op!r} on {column!r}; supported: {', '.join(RESIDUAL_STRING_OPERATORS)}"),
                )
            residual.append((column, normalized, value))
        else:
            pushdown.append((column, op, value))
    return pushdown, residual


def _with_residual_columns(
    columns: list[str] | None,
    residual: list[tuple[str, str, Any]],
) -> tuple[list[str] | None, list[str]]:
    """Keep residual filter columns readable without leaking them into a projection."""
    if columns is None or not residual:
        return columns, []
    hidden = [column for column, _op, _value in residual if column not in columns]
    return [*columns, *hidden], hidden


def _residual_expression(column: str, op: str, value: Any) -> pl.Expr:
    """Translate one residual predicate to Polars (nulls drop, matching Arrow)."""
    target = pl.col(column)
    expression: Any
    if op in {"=", "=="}:
        expression = target == value
    elif op == "!=":
        expression = target != value
    elif op == "in":
        expression = target.is_in(list(value))
    elif op == "not in":
        expression = ~target.is_in(list(value))
    elif op == "<":
        expression = target < value
    elif op == "<=":
        expression = target <= value
    elif op == ">":
        expression = target > value
    else:
        expression = target >= value
    return cast(pl.Expr, expression)


def _apply_residual_filters(frame: pl.DataFrame, residual: list[tuple[str, str, Any]]) -> pl.DataFrame:
    for column, op, value in residual:
        frame = frame.filter(_residual_expression(column, op, value))
    return frame


@dataclass(frozen=True)
class StringViewDrift:
    """Arrow ``string_view`` string columns found by reading Parquet footers."""

    columns: list[str]
    scanned_files: int

    @property
    def drifted(self) -> bool:
        return bool(self.columns)


def string_view_columns(
    table: str,
    *,
    lake_dir: Path | None = None,
    max_files: int = 50,
) -> StringViewDrift:
    """Report string columns stored as Arrow ``string_view`` in the newest files.

    A merge rewrite (label finalization, upserts) makes delta-rs emit
    ``string_view`` string columns while the Delta log keeps declaring ``string``,
    so only the Parquet footers can answer this. Read-only: metadata only, no data
    pages, capped at *max_files* newest files.
    """
    table_path = delta_table_path(table, lake_dir)
    if not DeltaTable.is_deltatable(str(table_path)):
        raise DeltaReadError(table, FileNotFoundError(f"Delta table '{table}' does not exist"))
    files = sorted(table_path.rglob("*.parquet"), key=lambda path: path.stat().st_mtime, reverse=True)[:max_files]
    drifted: set[str] = set()
    for path in files:
        schema = pa_parquet.read_schema(path)
        drifted.update(field.name for field in schema if pa.types.is_string_view(field.type))
    return StringViewDrift(columns=sorted(drifted), scanned_files=len(files))


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
    "read_delta_with_version",
    "update_delta_columns",
    "vacuum_delta",
    "write_delta",
]
