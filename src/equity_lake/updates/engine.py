"""Lightweight smart update engine for beta workflows."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import cast

import duckdb
from pydantic import BaseModel, Field

from equity_lake.core.runtime import LAKE_DIR
from equity_lake.ingestion.writers import write_to_partitioned_parquet
from equity_lake.loaders import registry
from equity_lake.updates.history import UpdateHistory

MARKET_DIR_MAP = {
    "us_equity": "us_equity",
    "hk_sg_equity": "hk_sg_equity",
}

SOURCE_DEFAULT_SYMBOLS = {
    "us_equity": ["AAPL", "MSFT", "NVDA"],
    "hk_sg_equity": ["0700.HK", "D05.SI"],
}

SOURCE_LOADER_MAP = {
    "us_equity": "yfinance",
    "hk_sg_equity": "yfinance",
}


class UpdateStrategy(str, Enum):
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

    def __init__(self, history: UpdateHistory | None = None):
        self.history = history or UpdateHistory()
        self.connection = duckdb.connect(":memory:")

    def update(
        self,
        source: str,
        symbols: list[str] | None = None,
        strategy: UpdateStrategy = UpdateStrategy.SMART,
        force: bool = False,
    ) -> UpdateResult:
        symbols = symbols or SOURCE_DEFAULT_SYMBOLS.get(source, [])
        if not symbols:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"No symbols configured for source '{source}'"],
            )

        loader_name = SOURCE_LOADER_MAP.get(source)
        if not loader_name:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"No loader configured for source '{source}'"],
            )

        loader = registry.create(loader_name, {})
        total_records = 0
        skipped = 0
        errors: list[str] = []

        for symbol in symbols:
            if not force and not self.needs_update(source, symbol):
                skipped += 1
                continue

            start_date, end_date = self._determine_date_range(source, symbol, strategy)
            result = loader.load([symbol], start_date, end_date + timedelta(days=1))
            if not result.success or result.data is None or result.data.empty:
                errors.extend(result.errors or [f"{symbol}: no data returned"])
                continue

            trading_dates = sorted({d.date() for d in result.data["date"]})
            for trading_date in trading_dates:
                slice_df = result.data[result.data["date"].dt.date == trading_date]
                if write_to_partitioned_parquet(
                    slice_df,
                    MARKET_DIR_MAP[source],
                    trading_date,
                ):
                    total_records += len(slice_df)

            self.history.record(source, symbol, records=len(result.data))

        return UpdateResult(
            success=not errors,
            source=source,
            records_added=total_records,
            records_skipped=skipped,
            errors=errors,
            next_suggested_update=(datetime.now(UTC) + timedelta(hours=24)).isoformat(),
        )

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


__all__ = ["UpdateEngine", "UpdateResult", "UpdateStrategy", "main"]
