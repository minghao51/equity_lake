"""Lightweight smart update engine for beta workflows."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import cast

import duckdb
import polars as pl
from pydantic import BaseModel, Field

from equity_lake.core.paths import LAKE_DIR
from equity_lake.core.polars_utils import FrameLike, ensure_polars, frame_is_empty
from equity_lake.ingestion.writers import write_to_partitioned_parquet
from equity_lake.sources.cn_hybrid import CNHybridFetcher
from equity_lake.sources.hk_sg import HKSGEquityFetcher
from equity_lake.sources.jpx import JPXEquityFetcher
from equity_lake.sources.krx import KRXEquityFetcher
from equity_lake.sources.us import USEquityFetcher
from equity_lake.updates.history import UpdateHistory


class SourceConfig(BaseModel):
    """Consolidated configuration for a data source."""

    dir_name: str
    default_symbols: list[str] = []
    fetcher_class: type | None = None


SOURCES: dict[str, SourceConfig] = {
    "us_equity": SourceConfig(dir_name="us_equity", default_symbols=["AAPL", "MSFT", "NVDA"], fetcher_class=USEquityFetcher),
    "hk_sg_equity": SourceConfig(dir_name="hk_sg_equity", default_symbols=["0700.HK", "D05.SI"], fetcher_class=HKSGEquityFetcher),
    "cn_ashare": SourceConfig(dir_name="cn_ashare", fetcher_class=CNHybridFetcher),
    "jpx_equity": SourceConfig(dir_name="jpx_equity", fetcher_class=JPXEquityFetcher),
    "krx_equity": SourceConfig(dir_name="krx_equity", fetcher_class=KRXEquityFetcher),
}


class UpdateStrategy(StrEnum):
    """Update range strategies."""

    FULL = "full"
    INCREMENTAL = "incremental"
    DELTA = "delta"
    SMART = "smart"


class UpdateResult(BaseModel):
    """Result of an update execution."""

    success: bool
    source: str
    records_added: int = 0
    records_skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    next_suggested_update: str | None = None


class UpdateEngine:
    """Smart update helper for hosted and local runs."""

    def __init__(self, history: UpdateHistory | None = None, validate_quality: bool = False):
        self.history = history or UpdateHistory()
        self.connection = duckdb.connect(":memory:")
        self.validate_quality = validate_quality

    def update(
        self,
        source: str,
        symbols: list[str] | None = None,
        strategy: UpdateStrategy = UpdateStrategy.SMART,
        force: bool = False,
    ) -> UpdateResult:
        src = SOURCES.get(source)
        if src is None:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"Unsupported source '{source}'. Supported sources: {', '.join(sorted(SOURCES))}"],
            )
        if src.fetcher_class is None:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"No fetcher configured for source '{source}'"],
            )

        symbols = symbols or src.default_symbols

        if symbols and source == "cn_ashare":
            return UpdateResult(
                success=False,
                source=source,
                errors=["Explicit symbols are not yet supported for cn_ashare updates; use configured tickers."],
            )

        total_records = 0
        skipped = 0
        errors: list[str] = []

        if symbols:
            # Per-symbol updates with individual history tracking.
            for symbol in symbols:
                if not force and not self.needs_update(source, symbol):
                    skipped += 1
                    continue
                start_date, end_date = self._determine_date_range(source, symbol, strategy)
                fetcher = src.fetcher_class(tickers=[symbol])
                frame = fetcher.fetch_range(start_date, end_date)
                if frame_is_empty(frame):
                    errors.append(f"{symbol}: no data returned")
                    continue
                total_records += self._write_result_frame(source, frame)
                self.history.record(source, symbol, records=frame.height)
        else:
            # Market-wide update (configured tickers), single range fetch.
            if force or self.needs_update(source):
                start_date, end_date = self._determine_date_range(source, None, strategy)
                fetcher = src.fetcher_class()
                frame = fetcher.fetch_range(start_date, end_date)
                record_count = 0 if frame_is_empty(frame) else self._write_result_frame(source, frame)
                total_records += record_count
                self.history.record(source, source, records=record_count)
            else:
                skipped += 1

        update_result = UpdateResult(
            success=len(errors) == 0,
            source=source,
            records_added=total_records,
            records_skipped=skipped,
            errors=errors,
            next_suggested_update=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )
        self.history.flush()
        return update_result

    def _write_result_frame(self, source: str, result_df: FrameLike) -> int:
        """Write fetched rows grouped by trading date."""
        src = SOURCES[source]
        frame = ensure_polars(result_df)
        trading_dates = sorted({value.date() if hasattr(value, "date") else value for value in frame["date"].to_list()})
        written_records = 0
        for trading_date in trading_dates:
            slice_df = frame.filter(pl.col("date") == trading_date)
            if write_to_partitioned_parquet(
                slice_df,
                src.dir_name,
                trading_date,
                validate_quality=self.validate_quality,
            ):
                written_records += slice_df.height
        return written_records

    def needs_update(self, source: str, symbol: str | None = None) -> bool:
        last_update = self.history.get_last_update(source, symbol)
        if last_update is None:
            return True
        return datetime.now(UTC) - last_update > timedelta(hours=18)

    def _determine_date_range(self, source: str, symbol: str | None, strategy: UpdateStrategy) -> tuple[date, date]:
        today = date.today()
        if strategy == UpdateStrategy.FULL:
            return today - timedelta(days=365), today
        if strategy == UpdateStrategy.DELTA:
            return today - timedelta(days=7), today
        if strategy == UpdateStrategy.INCREMENTAL:
            last_date = self.get_last_date(source, symbol)
            if last_date is None:
                return today - timedelta(days=30), today
            return last_date + timedelta(days=1), today

        last_date = self.get_last_date(source, symbol)
        if last_date is None:
            return today - timedelta(days=30), today
        gap_start = last_date + timedelta(days=1)
        if gap_start <= today:
            return gap_start, today
        return today - timedelta(days=7), today

    def get_last_date(self, source: str, symbol: str | None) -> date | None:
        src = SOURCES[source]
        dataset_dir = LAKE_DIR / src.dir_name
        if not dataset_dir.exists():
            return None

        query = f"""
            SELECT CAST(MAX(date) AS DATE)
            FROM read_parquet('{dataset_dir}/**/*.parquet', hive_partitioning=1)
            WHERE ticker = ?
        """
        try:
            row = self.connection.execute(query, [symbol]).fetchone()
            if row is None:
                return None
            return cast(date | None, row[0])
        except Exception:
            return None


__all__ = ["UpdateEngine", "UpdateResult", "UpdateStrategy"]
