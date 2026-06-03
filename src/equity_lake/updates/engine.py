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
from equity_lake.ingestion.sources.base import MarketDataFetcher
from equity_lake.ingestion.sources.cn_hybrid import CNHybridFetcher
from equity_lake.ingestion.sources.jpx import JPXEquityFetcher
from equity_lake.ingestion.sources.krx import KRXEquityFetcher
from equity_lake.ingestion.writers import write_to_partitioned_parquet
from equity_lake.loaders import registry
from equity_lake.updates.history import UpdateHistory

MARKET_DIR_MAP = {
    "us_equity": "us_equity",
    "hk_sg_equity": "hk_sg_equity",
    "cn_ashare": "cn_ashare",
    "jpx_equity": "jpx_equity",
    "krx_equity": "krx_equity",
}

SOURCE_DEFAULT_SYMBOLS = {
    "us_equity": ["AAPL", "MSFT", "NVDA"],
    "hk_sg_equity": ["0700.HK", "D05.SI"],
}

SOURCE_LOADER_MAP = {
    "us_equity": "yfinance",
    "hk_sg_equity": "yfinance",
}

SOURCE_FETCHER_MAP = {
    "cn_ashare": CNHybridFetcher,
    "jpx_equity": JPXEquityFetcher,
    "krx_equity": KRXEquityFetcher,
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
        symbols = symbols or SOURCE_DEFAULT_SYMBOLS.get(source, [])
        if source in SOURCE_LOADER_MAP and not symbols:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"No symbols configured for source '{source}'"],
            )

        loader_name = SOURCE_LOADER_MAP.get(source)
        fetcher_class = SOURCE_FETCHER_MAP.get(source)
        if loader_name is None and fetcher_class is None:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"Unsupported source '{source}'. Supported sources: {', '.join(sorted(MARKET_DIR_MAP))}"],
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
            if loader_name is not None:
                loader = registry.create(loader_name, {})
                result = loader.load([symbol], start_date, end_date + timedelta(days=1))
                if not result.success or result.data is None or result.data.empty:
                    errors.extend(result.errors or [f"{symbol}: no data returned"])
                    continue
                total_records += self._write_result_frame(source, result.data)
                self.history.record(source, symbol, records=len(result.data))
                continue

            if fetcher_class is None:
                continue
            if symbols and source == "cn_ashare":
                errors.append("Explicit symbols are not yet supported for cn_ashare updates; use configured tickers.")
                break

            fetched_rows = self._run_fetcher_updates(
                source=source,
                start_date=start_date,
                end_date=end_date,
                explicit_symbols=symbols,
            )
            total_records += fetched_rows
            self.history.record(source, source if symbol == "__market__" else symbol, records=fetched_rows)

        return UpdateResult(
            success=not errors,
            source=source,
            records_added=total_records,
            records_skipped=skipped,
            errors=errors,
            next_suggested_update=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )

    def _run_fetcher_updates(
        self,
        source: str,
        start_date: date,
        end_date: date,
        explicit_symbols: list[str] | None,
    ) -> int:
        """Fetch and write a date range using a market fetcher."""
        fetcher: MarketDataFetcher
        if source == "jpx_equity" and explicit_symbols:
            fetcher = JPXEquityFetcher(tickers=explicit_symbols)
        elif source == "krx_equity" and explicit_symbols:
            fetcher = KRXEquityFetcher(tickers=explicit_symbols)
        elif source == "cn_ashare":
            fetcher = CNHybridFetcher()
        elif source == "jpx_equity":
            fetcher = JPXEquityFetcher()
        else:
            fetcher = KRXEquityFetcher()

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
        trading_dates = sorted({d.date() for d in result_df["date"]})
        written_records = 0
        for trading_date in trading_dates:
            slice_df = result_df[result_df["date"].dt.date == trading_date]
            if write_to_partitioned_parquet(
                slice_df,
                MARKET_DIR_MAP[source],
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
        dataset_dir = LAKE_DIR / MARKET_DIR_MAP[source]
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
