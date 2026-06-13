"""Plugin-friendly data loader abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from equity_lake.core.polars_utils import ensure_polars


class LoaderMetadata(BaseModel):
    """Metadata for a data loader."""

    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = "Equity Lake"
    supported_markets: list[str] = Field(default_factory=list)
    supported_intervals: list[str] = Field(default_factory=lambda: ["1d"])
    requires_auth: bool = False
    data_types: list[str] = Field(default_factory=lambda: ["ohlcv"])


class LoadResult(BaseModel):
    """Result of a loader fetch operation."""

    success: bool
    data: pl.DataFrame | None = None
    records_count: int = 0
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    loaded_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    model_config = {"arbitrary_types_allowed": True}


class BaseDataLoader(ABC):
    """Abstract base class for pluggable loaders."""

    metadata: LoaderMetadata

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self._validate_config()

    @abstractmethod
    def _validate_config(self) -> None:
        """Validate loader-specific configuration."""

    @abstractmethod
    def load(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        interval: str = "1d",
    ) -> LoadResult:
        """Load data for the given symbols and date range."""

    @abstractmethod
    def get_available_symbols(self) -> list[str]:
        """List symbols available from this source."""

    @abstractmethod
    def validate_connection(self) -> bool:
        """Return whether the data source is reachable."""

    def normalize_data(self, df: pl.DataFrame) -> pl.DataFrame:
        """Normalize to the common OHLCV schema."""
        df = ensure_polars(df)
        required = ["ticker", "date", "open", "high", "low", "close", "volume"]
        missing = [column for column in required if column not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        return df.select(required)


__all__ = ["BaseDataLoader", "LoadResult", "LoaderMetadata"]
