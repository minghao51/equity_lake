"""Update history persistence."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pandas as pd

from equity_lake.core.runtime import DATA_DIR


class UpdateHistory:
    """Track loader updates for freshness checks."""

    def __init__(self, path: Path | None = None):
        self.path = path or (DATA_DIR / "update_history.parquet")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._history: pd.DataFrame | None = None

    @property
    def history(self) -> pd.DataFrame:
        if self._history is None:
            self._history = self._load()
        return self._history

    def _load(self) -> pd.DataFrame:
        if self.path.exists():
            return pd.read_parquet(self.path)
        return pd.DataFrame(columns=["source", "symbol", "updated_at", "records"])

    def get_last_update(self, source: str, symbol: str | None = None) -> datetime | None:
        frame = self.history
        mask = frame["source"] == source
        if symbol is not None:
            mask &= frame["symbol"] == symbol
        subset = frame[mask]
        if subset.empty:
            return None
        return cast(datetime, pd.to_datetime(subset["updated_at"].max()).to_pydatetime())

    def record(self, source: str, symbol: str, records: int = 0) -> None:
        new_row = pd.DataFrame(
            [
                {
                    "source": source,
                    "symbol": symbol,
                    "updated_at": datetime.now(UTC),
                    "records": records,
                }
            ]
        )
        if self.history.empty:
            self._history = new_row
        else:
            self._history = pd.concat([self.history, new_row], ignore_index=True)
        self._history.to_parquet(self.path, index=False)


__all__ = ["UpdateHistory"]
