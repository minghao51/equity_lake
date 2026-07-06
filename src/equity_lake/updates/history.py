"""Update history persistence (Delta-backed ACID merge on source+symbol)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import polars as pl
import structlog
from deltalake import DeltaTable, write_deltalake

from equity_lake.core.paths import DATA_DIR, UPDATE_HISTORY_DIR

logger = structlog.get_logger(__name__)

_SCHEMA = {"source": pl.Utf8, "symbol": pl.Utf8, "updated_at": pl.Datetime, "records": pl.Int64}
_MERGE_KEYS = ["source", "symbol"]
_LEGACY_PATH = DATA_DIR / "update_history.parquet"


class UpdateHistory:
    """Track loader updates for freshness checks."""

    def __init__(self, path: Path | None = None):
        self.path = path or UPDATE_HISTORY_DIR
        self._history: pl.DataFrame | None = None

    @property
    def history(self) -> pl.DataFrame:
        if self._history is None:
            self._history = self._load()
        return self._history

    def _load(self) -> pl.DataFrame:
        self._migrate_legacy_parquet()
        if DeltaTable.is_deltatable(str(self.path)):
            return cast(pl.DataFrame, pl.from_arrow(DeltaTable(str(self.path)).to_pyarrow_table()))
        return pl.DataFrame(schema=_SCHEMA)

    def _migrate_legacy_parquet(self) -> None:
        """One-time seed from the pre-Delta single-file parquet (idempotent, default location only)."""
        if DeltaTable.is_deltatable(str(self.path)) or not _LEGACY_PATH.exists():
            return
        if self.path != UPDATE_HISTORY_DIR:
            return
        try:
            legacy = pl.read_parquet(_LEGACY_PATH)
            write_deltalake(str(self.path), legacy.to_arrow(), mode="overwrite")
            logger.info("update_history_migrated_legacy", rows=legacy.height, path=str(self.path))
        except Exception:
            logger.exception("update_history_migration_failed", path=str(_LEGACY_PATH))

    def get_last_update(self, source: str, symbol: str | None = None) -> datetime | None:
        subset = self.history.filter(pl.col("source") == source)
        if symbol is not None:
            subset = subset.filter(pl.col("symbol") == symbol)
        if subset.is_empty():
            return None
        latest = subset["updated_at"].max()
        if latest is None:
            return None
        return cast(datetime, latest)

    def record(self, source: str, symbol: str, records: int = 0) -> None:
        new_row = pl.DataFrame([{"source": source, "symbol": symbol, "updated_at": datetime.now(UTC), "records": records}])
        self._history = new_row if self.history.is_empty() else pl.concat([self.history, new_row], how="vertical_relaxed")

    def flush(self) -> None:
        """Merge buffered history into the Delta table on (source, symbol). Call once after all record() calls."""
        if self._history is None or self._history.is_empty():
            return
        self._migrate_legacy_parquet()
        if not DeltaTable.is_deltatable(str(self.path)):
            write_deltalake(str(self.path), self._history.to_arrow(), mode="overwrite")
            return
        predicate = " AND ".join(f"target.{k} = source.{k}" for k in _MERGE_KEYS)
        dt = DeltaTable(str(self.path))
        (
            dt.merge(source=self._history.to_arrow(), predicate=predicate, source_alias="source", target_alias="target")
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute()
        )


__all__ = ["UpdateHistory"]
