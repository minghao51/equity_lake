"""Update history persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import polars as pl

from equity_lake.core.paths import DATA_DIR


class UpdateHistory:
    """Track loader updates for freshness checks."""

    def __init__(self, path: Path | None = None):
        self.path = path or (DATA_DIR / "update_history.parquet")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._history: pl.DataFrame | None = None

    @property
    def history(self) -> pl.DataFrame:
        if self._history is None:
            self._history = self._load()
        return self._history

    def _load(self) -> pl.DataFrame:
        if self.path.exists():
            return pl.read_parquet(self.path)
        return pl.DataFrame(schema={"source": pl.Utf8, "symbol": pl.Utf8, "updated_at": pl.Datetime, "records": pl.Int64})

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
        self._history.write_parquet(self.path)


__all__ = ["UpdateHistory"]
