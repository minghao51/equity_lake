"""Ingestion job helpers for library and CLI use."""

from __future__ import annotations

from datetime import date
from typing import Any


def run_ingestion_job(
    *,
    trading_date: date,
    markets: list[str],
    dry_run: bool = False,
    ticker_config: Any = None,
    filters: dict[str, Any] | None = None,
    explicit_tickers: str | None = None,
    parallel: bool = True,
    max_workers: int | None = None,
) -> dict[str, bool]:
    """Run ingestion through the canonical orchestration implementation."""
    from equity_lake.ingestion.orchestrator import run_daily_ingestion

    return run_daily_ingestion(
        trading_date=trading_date,
        markets=markets,
        dry_run=dry_run,
        ticker_config=ticker_config,
        filters=filters,
        explicit_tickers=explicit_tickers,
        parallel=parallel,
        max_workers=max_workers,
    )
