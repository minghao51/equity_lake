"""Bronze-to-silver transform for unstructured articles.

Reads raw articles from the bronze Delta table, filters out already-processed
ones, runs LLM batch processing, then explodes to silver article-ticker pairs
and writes to the silver Delta table.

Provides a unified :func:`process_unstructured_to_silver` that both article
and SEC pipelines delegate to, eliminating duplicated orchestration logic.
"""

from __future__ import annotations

import errno
import hashlib
import json
import stat
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl
import structlog

from equity_lake.core.paths import BRONZE_RAW_ARTICLES_DIR, DATA_DIR, SILVER_PROCESSED_ARTICLES_DIR
from equity_lake.ingestion.replay import (
    EstimateBatchCost,
    PaidWorkBudget,
    ProcessBatch,
    ReplayAuthorizationError,
    ReplayBatchResult,
    ReplayItem,
    ReplayScope,
    replay_bounded,
)
from equity_lake.storage.delta import merge_delta
from equity_lake.storage.lake_reader import ensure_delta_extension

logger = structlog.get_logger()
MARKET_WIDE_INSTRUMENT = "__market_wide__"


def read_bronze(trading_date: date | None = None, table_path: Path | None = None) -> pl.DataFrame:
    """Read bronze articles, optionally filtered by date.

    An absent table (or an existing empty table directory) returns an empty
    frame. A present but unreadable table raises so callers cannot mistake a
    storage failure for an empty corpus.

    Args:
        trading_date: If provided, filter to this date only.
        table_path: Override the bronze directory path.
    """
    path = table_path or BRONZE_RAW_ARTICLES_DIR
    try:
        if not _table_has_data(path):
            return pl.DataFrame()

        import duckdb

        query, scan_path = _parameterized_scan_for(path)
        con = duckdb.connect(":memory:")
        try:
            ensure_delta_extension(con)
            parameters: list[object] = [scan_path]
            if trading_date:
                query = query + " WHERE date = ?"
                parameters.append(trading_date)
            df = con.execute(query, parameters).pl()
        finally:
            con.close()
        return df
    except Exception as exc:
        logger.exception("bronze_read_failed", path=str(path))
        raise RuntimeError(f"Unable to read bronze table at {path}: {exc}") from exc


def process_unstructured_to_silver(
    trading_date: date,
    *,
    scope: ReplayScope,
    source_type_filter: str | None,
    exclude_source_types: list[str] | None = None,
    process_batch: ProcessBatch | None = None,
    estimate_batch_cost: EstimateBatchCost | None = None,
    paid_processing_authorized: bool = False,
    approved_estimate: Decimal | None = None,
    actual_spend_cap: Decimal = Decimal("0"),
    checkpoint_path: Path | None = None,
    silver_path: Path,
    silver_table_name: str,
    silver_key_columns: list[str],
    log_label: str = "article",
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    """Run the bounded, explicit Bronze→Silver replay for one source scope.

    Candidate reads are date/source/instrument scoped. Paid processing is
    disabled by default; an authorized callback must estimate each selected
    batch and return provider-reported actual cost. The historic unbounded
    ``process_fn(DataFrame)`` path is intentionally not available.
    """

    def _diag(mode: str, **extra: Any) -> None:
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update({"mode": mode, **extra})

    if scope.trading_date != trading_date:
        _diag("processing_failed", error="replay scope date does not match processor date")
        return False
    if source_type_filter is None or scope.source != source_type_filter:
        _diag("processing_failed", error="one explicit source_type must match the replay scope")
        return False

    try:
        bronze_df = read_bronze(trading_date)
    except Exception as exc:
        _diag("bronze_read_failed", error=str(exc))
        return False
    if bronze_df.is_empty():
        logger.info("no_bronze_to_process", log_label=log_label, trading_date=str(trading_date))
        _diag("no_bronze_data")
        return False

    if "source_type" not in bronze_df.columns:
        _diag("bronze_read_failed", error="bronze source_type column is required by the explicit source scope")
        return False
    if "article_id" not in bronze_df.columns or "date" not in bronze_df.columns:
        _diag("bronze_read_failed", error="bronze article_id and date columns are required for bounded replay")
        return False
    bronze_df = bronze_df.filter(pl.col("date") == trading_date)
    if source_type_filter is not None:
        bronze_df = bronze_df.filter(pl.col("source_type") == source_type_filter)
    if exclude_source_types:
        bronze_df = bronze_df.filter(~pl.col("source_type").is_in(exclude_source_types))

    try:
        replay_items = _scoped_replay_items(bronze_df, scope, source_type_filter)
    except Exception as exc:
        _diag("processing_failed", error=str(exc))
        return False
    if not replay_items:
        _diag("no_bronze_data")
        return False

    phase = "silver_read"
    write_rows = 0

    def read_processed_ids(current_scope: ReplayScope) -> set[str]:
        nonlocal phase
        phase = "silver_read"
        return _get_processed_ids(silver_path, current_scope.trading_date)

    def process_scoped_batch(batch: Sequence[ReplayItem], budget: PaidWorkBudget) -> ReplayBatchResult:
        nonlocal phase
        phase = "processing"
        if process_batch is None:
            raise ReplayAuthorizationError("no cost-reporting processor callback was supplied")
        result = process_batch(batch, budget)
        if not isinstance(result, ReplayBatchResult):
            raise ReplayAuthorizationError("processor must return ReplayBatchResult with provider actual cost")
        items_by_id = {item.item_id: item for item in batch}
        annotated: list[dict[str, Any]] = []
        for row in result.rows:
            article_id = row.get("article_id")
            if not isinstance(article_id, str) or not article_id:
                raise ValueError("processed Silver rows must preserve a non-empty article_id")
            item = items_by_id.get(article_id)
            if item is None:
                # The generic replay validator rejects out-of-batch IDs before writes.
                item_date = row.get("date")
                item_source = scope.source
            else:
                item_date = row.get("date")
                item_source = item.source
            ticker = row.get("ticker")
            instrument = str(ticker).strip() if ticker is not None and str(ticker).strip() else MARKET_WIDE_INSTRUMENT
            annotated.append(
                {
                    "item_id": article_id,
                    "source": item_source,
                    "date": item_date,
                    "market": scope.market,
                    "instrument": instrument,
                    "__silver_row": dict(row),
                }
            )
        return ReplayBatchResult(rows=annotated, actual_cost=result.actual_cost)

    def write_silver(rows: Sequence[Mapping[str, Any]]) -> bool:
        nonlocal phase, write_rows
        phase = "write"
        silver_rows = [dict(row["__silver_row"]) for row in rows if isinstance(row.get("__silver_row"), Mapping)]
        if len(silver_rows) != len(rows) or not silver_rows:
            return False
        silver_df = pl.DataFrame(silver_rows)
        write_rows = silver_df.height
        return _write_silver_generic(silver_df, silver_table_name, silver_key_columns)

    replay_path = checkpoint_path or _checkpoint_path(scope)
    try:
        result = replay_bounded(
            scope=scope,
            items=replay_items,
            checkpoint_path=replay_path,
            state_namespace=f"silver:{silver_path.resolve(strict=False)}",
            process_batch=process_scoped_batch if process_batch is not None else None,
            estimate_batch_cost=estimate_batch_cost,
            write_silver=write_silver,
            read_processed_ids=read_processed_ids,
            paid_processing_authorized=paid_processing_authorized,
            approved_estimate=approved_estimate,
            actual_spend_cap=actual_spend_cap,
        )
    except ReplayAuthorizationError as exc:
        mode = "paid_processing_disabled" if not paid_processing_authorized and "zero-paid-authorization" in str(exc) else "processing_failed"
        _diag(mode, error=str(exc))
        return False
    except Exception as exc:
        mode = {"silver_read": "silver_read_failed", "write": "write_failed"}.get(phase, "processing_failed")
        _diag(mode, error=str(exc))
        return False

    if not result.selected_item_ids:
        if result.already_processed_count:
            _diag("already_processed", skipped=result.already_processed_count)
            return True
        _diag("no_bronze_data")
        return False
    _diag(
        "processed",
        rows=write_rows,
        selected=len(result.selected_item_ids),
        deferred=result.deferred_count,
        actual_spend=str(result.actual_spend),
        remaining_allowance=str(result.remaining_allowance),
    )
    return bool(result.processed_item_ids)


def _scoped_replay_items(bronze_df: pl.DataFrame, scope: ReplayScope, source_type_filter: str | None) -> list[ReplayItem]:
    items: list[ReplayItem] = []
    for row in bronze_df.to_dicts():
        article_id = row.get("article_id")
        if not isinstance(article_id, str) or not article_id.strip():
            raise ValueError("bronze rows require a non-empty article_id for bounded replay")
        item_date = row.get("date")
        if isinstance(item_date, datetime):
            item_date = item_date.date()
        if item_date != scope.trading_date:
            continue
        if source_type_filter is not None and row.get("source_type") != source_type_filter:
            continue
        source_instrument = _source_instrument(row)
        if source_instrument is None:
            if source_type_filter == "sec_filing":
                continue
            source_instrument = MARKET_WIDE_INSTRUMENT
        if source_instrument not in scope.instruments:
            continue
        items.append(
            ReplayItem(
                item_id=article_id.strip(),
                source=scope.source,
                trading_date=scope.trading_date,
                market=scope.market,
                instrument=source_instrument,
                payload=row,
            )
        )
    return items


def _source_instrument(row: Mapping[str, Any]) -> str | None:
    ticker = row.get("ticker")
    if ticker is not None and str(ticker).strip():
        return str(ticker).strip()
    raw_metadata = row.get("source_metadata")
    if isinstance(raw_metadata, str):
        try:
            metadata = json.loads(raw_metadata)
        except json.JSONDecodeError:
            return None
    else:
        metadata = raw_metadata
    if isinstance(metadata, Mapping):
        candidate = metadata.get("ticker")
        if candidate is not None and str(candidate).strip():
            return str(candidate).strip()
    return None


def _checkpoint_path(scope: ReplayScope) -> Path:
    digest = hashlib.sha256(scope.model_dump_json().encode("utf-8")).hexdigest()
    return DATA_DIR / "replay" / f"{digest}.checkpoint.json"


def _validate_silver_articles(df: pl.DataFrame, table_name: str) -> bool:
    """Enforce the pointblank article contract before a silver merge (ADR-0007).

    Silver merges bypass ``upsert_dataset`` entirely, so this is the write
    boundary for ``02_silver/processed_articles`` and
    ``02_silver/sec_extractions``. Profiling stays in memory — validation
    never persists artifacts (ADR-0006 auxiliary paths).
    """
    from equity_lake.validation.pipeline import ValidationPipeline

    vp = ValidationPipeline()
    result = vp.validate(df, data_type="article")
    if not result.success:
        logger.error("Silver quality validation failed", table=table_name, errors=result.errors)
        return False
    if result.warnings:
        for w in result.warnings:
            logger.warning("Silver quality warning", table=table_name, warning=w)
    return True


def _write_silver_generic(df: pl.DataFrame, table_name: str, key_columns: list[str]) -> bool:
    """Write silver DataFrame to Delta table."""
    if df.is_empty():
        logger.warning("Empty DataFrame, skipping silver write")
        return False
    if not _validate_silver_articles(df, table_name):
        return False
    return merge_delta(df, table_name, key_columns=key_columns)


def _get_processed_ids(silver_path: Path, trading_date: date) -> set[str]:
    """Return article_ids already present in the given silver table.

    ``trading_date`` is retained for caller compatibility but no longer filters
    the scan: dedup is global so backfilled historical rows are never reprocessed.
    Only absent/empty storage means no prior IDs; unreadable state raises to
    prevent the Bronze corpus from being submitted again for processing.
    """
    try:
        if not _table_has_data(silver_path):
            return set()

        import duckdb

        query, scan_path = _parameterized_scan_for(silver_path, distinct_article_ids=True)
        con = duckdb.connect(":memory:")
        try:
            ensure_delta_extension(con)
            rows = con.execute(query, [scan_path]).fetchall()
        finally:
            con.close()
        return {r[0] for r in rows}
    except Exception as exc:
        logger.exception("silver_read_failed", path=str(silver_path))
        raise RuntimeError(f"Unable to read silver dedup table at {silver_path}: {exc}") from exc


def _parameterized_scan_for(path: Path, *, distinct_article_ids: bool = False) -> tuple[str, str]:
    """Return an allowlisted DuckDB query and a bound local table path."""
    from deltalake import DeltaTable

    if DeltaTable.is_deltatable(str(path)):
        query = "SELECT DISTINCT article_id FROM delta_scan(?)" if distinct_article_ids else "SELECT * FROM delta_scan(?)"
        return query, str(path)
    parquet_path = str(path / "**" / "*.parquet")
    query = (
        "SELECT DISTINCT article_id FROM read_parquet(?, hive_partitioning=1)"
        if distinct_article_ids
        else "SELECT * FROM read_parquet(?, hive_partitioning=1)"
    )
    return query, parquet_path


def _table_has_data(path: Path) -> bool:
    """Distinguish absent/empty storage from recognized or corrupt table state."""
    try:
        path_stat = path.stat()
    except FileNotFoundError as exc:
        if exc.errno != errno.ENOENT:
            raise
        # A dangling table-root/ancestor link must not look like ordinary
        # absence. Inspect each bounded component without masking ENOTDIR or
        # permission failures from the original stat.
        candidate = path
        for _ in range(len(path.parts)):
            try:
                candidate_stat = candidate.lstat()
            except FileNotFoundError as candidate_exc:
                if candidate_exc.errno != errno.ENOENT:
                    raise
            else:
                if stat.S_ISLNK(candidate_stat.st_mode):
                    try:
                        candidate.stat()
                    except FileNotFoundError as link_exc:
                        if link_exc.errno != errno.ENOENT:
                            raise
                        raise RuntimeError(f"Dangling symlink in table path at {candidate}") from link_exc
            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent
        return False
    if not stat.S_ISDIR(path_stat.st_mode):
        raise RuntimeError(f"Expected a table directory at {path}")

    delta_log = path / "_delta_log"
    try:
        delta_log_stat = delta_log.stat()
    except FileNotFoundError as exc:
        if exc.errno != errno.ENOENT:
            raise
        delta_log_stat = None
    if delta_log_stat is not None:
        if not stat.S_ISDIR(delta_log_stat.st_mode):
            raise RuntimeError(f"Delta log is not a directory at {path}")
        if next(delta_log.iterdir(), None) is None:
            raise RuntimeError(f"Delta log is empty at {path}")

        # Opening the table validates its log. ``is_deltatable`` alone can
        # return true for malformed commit JSON, so do not treat its presence
        # as a readable snapshot. Let Delta errors propagate to the caller.
        from deltalake import DeltaTable

        DeltaTable(str(path))
        return True

    if any(candidate.is_file() for candidate in path.rglob("*.parquet")):
        return True
    if next(path.iterdir(), None) is None:
        return False
    raise RuntimeError(f"Unrecognized contents in table directory at {path}")


def process_bronze_to_silver(
    trading_date: date,
    *,
    scope: ReplayScope | None = None,
    process_batch: ProcessBatch | None = None,
    estimate_batch_cost: EstimateBatchCost | None = None,
    paid_processing_authorized: bool = False,
    approved_estimate: Decimal | None = None,
    actual_spend_cap: Decimal = Decimal("0"),
    checkpoint_path: Path | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    """Run scoped unstructured-article replay; paid processing is disabled by default.

    A processor must be explicitly injected with a finite cost estimator and
    actual-cost result contract before this route can perform paid work. Calls
    from legacy unscoped entry points fail closed.
    """
    if scope is None:
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update({"mode": "processing_failed", "error": "explicit replay scope is required"})
        return False
    if scope.source not in {"rss", "reddit", "stocktwits", "transcript"}:
        if diagnostics is not None:
            diagnostics.clear()
            diagnostics.update({"mode": "processing_failed", "error": "article replay requires one supported source type"})
        return False
    return process_unstructured_to_silver(
        trading_date,
        scope=scope,
        source_type_filter=scope.source,
        exclude_source_types=None,
        process_batch=process_batch,
        estimate_batch_cost=estimate_batch_cost,
        paid_processing_authorized=paid_processing_authorized,
        approved_estimate=approved_estimate,
        actual_spend_cap=actual_spend_cap,
        checkpoint_path=checkpoint_path,
        silver_path=SILVER_PROCESSED_ARTICLES_DIR,
        silver_table_name="02_silver/processed_articles",
        silver_key_columns=["article_id", "ticker"],
        log_label="article",
        diagnostics=diagnostics,
    )


__all__ = [
    "process_bronze_to_silver",
    "process_unstructured_to_silver",
    "read_bronze",
]
