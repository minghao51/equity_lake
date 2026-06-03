"""Lightweight smart update engine for beta workflows."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import cast

import duckdb
import pandas as pd
from pydantic import BaseModel, Field

from equity_lake.core.paths import LAKE_DIR
from equity_lake.ingestion.sources.cn_hybrid import CNHybridFetcher
from equity_lake.ingestion.sources.jpx import JPXEquityFetcher
from equity_lake.ingestion.sources.krx import KRXEquityFetcher
from equity_lake.ingestion.writers import write_to_partitioned_parquet
from equity_lake.loaders import registry
from equity_lake.updates.history import UpdateHistory


class SourceConfig(BaseModel):
    """Consolidated configuration for a data source."""

    dir_name: str
    default_symbols: list[str] = []
    loader_name: str | None = None
    fetcher_class: type | None = None


SOURCES: dict[str, SourceConfig] = {
    "us_equity": SourceConfig(dir_name="us_equity", default_symbols=["AAPL", "MSFT", "NVDA"], loader_name="yfinance"),
    "hk_sg_equity": SourceConfig(dir_name="hk_sg_equity", default_symbols=["0700.HK", "D05.SI"], loader_name="yfinance"),
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

        symbols = symbols or src.default_symbols
        if src.loader_name and not symbols:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"No symbols configured for source '{source}'"],
            )

        total_records = 0
        skipped = 0
        errors: list[str] = []

        target_symbols = symbols or ["__market__"]
        for symbol in target_symbols:
            if not force and not self.needs_update(source, symbol):
                skipped += 1
                continue

            start_date, end_date = self._determine_date_range(source, symbol, strategy)
            if src.loader_name is not None:
                loader = registry.create(src.loader_name, {})
                result = loader.load([symbol], start_date, end_date + timedelta(days=1))
                if not result.success or result.data is None or result.data.empty:
                    errors.extend(result.errors or [f"{symbol}: no data returned"])
                    continue
                total_records += self._write_result_frame(source, result.data)
                self.history.record(source, symbol, records=len(result.data))
                continue

            if src.fetcher_class is None:
                continue
            if symbols and source == "cn_ashare":
                errors.append("Explicit symbols are not yet supported for cn_ashare updates; use configured tickers.")
                break

            fetched_rows = self._run_fetcher_updates(
                source=source,
                fetcher_class=src.fetcher_class,
                start_date=start_date,
                end_date=end_date,
                explicit_symbols=symbols,
            )
            total_records += fetched_rows
            self.history.record(source, source if symbol == "__market__" else symbol, records=fetched_rows)

        return UpdateResult(
            success=len(errors) == 0,
            source=source,
            records_added=total_records,
            records_skipped=skipped,
            errors=errors,
            next_suggested_update=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )

    def _run_fetcher_updates(
        self,
        source: str,
        fetcher_class: type,
        start_date: date,
        end_date: date,
        explicit_symbols: list[str] | None,
    ) -> int:
        """Fetch and write a date range using a market fetcher."""
        fetcher = fetcher_class(tickers=explicit_symbols) if explicit_symbols and source in ("jpx_equity", "krx_equity") else fetcher_class()

        total_records = 0
        current_date = start_date
        while current_date <= end_date:
            frame = fetcher.fetch(current_date)
            if not frame.empty:
                total_records += self._write_result_frame(source, frame)
            current_date += timedelta(days=1)
        return total_records

    def _write_result_frame(self, source: str, result_df: pd.DataFrame) -> int:
        """Write fetched rows grouped by trading date."""
        src = SOURCES[source]
        trading_dates = sorted({d.date() for d in result_df["date"]})
        written_records = 0
        for trading_date in trading_dates:
            slice_df = result_df[result_df["date"].dt.date == trading_date]
            if write_to_partitioned_parquet(
                slice_df,
                src.dir_name,
                trading_date,
                validate_quality=self.validate_quality,
            ):
                written_records += len(slice_df)
        return written_records

    def needs_update(self, source: str, symbol: str | None = None) -> bool:
        last_update = self.history.get_last_update(source, symbol)
        if last_update is None:
            return True
        return datetime.now(UTC) - last_update > timedelta(hours=18)

    def _determine_date_range(self, source: str, symbol: str, strategy: UpdateStrategy) -> tuple[date, date]:
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

    def get_last_date(self, source: str, symbol: str) -> date | None:
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


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Smart updates for Equity Lake")
    parser.add_argument("source", help="Source to update, e.g. us_equity")
    parser.add_argument("--symbols", help="Comma-separated symbol list")
    parser.add_argument(
        "--strategy",
        choices=[strategy.value for strategy in UpdateStrategy],
        default=UpdateStrategy.SMART.value,
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--output-json", type=Path)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""
    args = parse_args()
    symbols = [symbol.strip() for symbol in args.symbols.split(",")] if args.symbols else None
    engine = UpdateEngine()
    result = engine.update(
        source=args.source,
        symbols=symbols,
        strategy=UpdateStrategy(args.strategy),
        force=args.force,
    )
    payload = result.model_dump()
    if args.output_json:
        args.output_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if not result.success:
        raise SystemExit(1)


__all__ = ["UpdateEngine", "UpdateResult", "UpdateStrategy", "main"]
