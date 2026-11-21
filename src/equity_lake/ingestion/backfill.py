#!/usr/bin/env python3
"""
Backfill Historical Data

Delegates to ``run_daily_ingestion`` per trading day in the requested range,
avoiding duplicated fetcher logic. Driven via the ``equity backfill`` Typer
command (see ``cli/commands/data.py``).

Usage:
    uv run equity backfill --start 2023-04-06 --end 2026-04-05
    uv run equity backfill --days-back 1095
    uv run equity backfill --days-back 365 --markets us_equity
"""

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import structlog

from equity_lake.core.config import TickerConfig, get_settings
from equity_lake.core.paths import PRICE_MARKETS

logger = structlog.get_logger()
SETTINGS = get_settings()

# Registry-derived (ADR-0010): all five price markets, canonical long keys.
DEFAULT_MARKETS = sorted(PRICE_MARKETS)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """Frozen scope plus outcome counts for one backfill invocation (W03/F25).

    The scope fields are captured before the date loop so logs and callers report
    what was actually authorized/attempted rather than re-deriving it afterwards.
    ``total_dates`` preserves the historical return contract: the number of
    successful (market, date) work items. ``trading_dates`` counts the calendar
    days iterated in the range (pre-existing loop semantics, deliberately
    unchanged) — it is NOT an exchange-session count; session iteration stays
    future work (wave-page limitation note).
    """

    start_date: date
    end_date: date
    markets: tuple[str, ...]
    instruments: tuple[str, ...] | None
    authorized: bool
    trading_dates: int
    market_date_attempts: int
    succeeded: int
    failed: int
    dates_recovered: int
    per_market: dict[str, int]

    @property
    def total_dates(self) -> int:
        """Successful market-date count (the historical ``backfill_date_range`` return)."""
        return self.succeeded

    def to_payload(self) -> dict[str, Any]:
        """JSON-friendly summary for pipeline stage results and logs."""
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "markets": list(self.markets),
            "instruments": list(self.instruments) if self.instruments is not None else None,
            "authorized": self.authorized,
            "trading_dates": self.trading_dates,
            "market_date_attempts": self.market_date_attempts,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dates_recovered": self.dates_recovered,
            "per_market": dict(self.per_market),
        }


def backfill_date_range(
    start_date: date,
    end_date: date,
    markets: list[str] | None = None,
    ticker_config: TickerConfig | None = None,
    dry_run: bool = False,
    explicit_tickers: list[str] | None = None,
    authorized: bool = False,
    lake_dir: Path | None = None,
) -> BackfillResult:
    from equity_lake.ingestion.orchestrator import run_daily_ingestion
    from equity_lake.ingestion.types import SourceOutcome, SourceStatus

    if markets is None:
        markets = DEFAULT_MARKETS
    market_list = list(markets)
    instruments = tuple(explicit_tickers) if explicit_tickers is not None else None

    per_market: dict[str, int] = {market: 0 for market in market_list}
    trading_dates = 0
    market_date_attempts = 0
    succeeded = 0
    failed = 0
    dates_recovered = 0

    current = start_date
    while current <= end_date:
        trading_dates += 1
        day_success = 0
        for market in market_list:
            market_date_attempts += 1
            try:
                result = run_daily_ingestion(
                    trading_date=current,
                    markets=[market],
                    dry_run=dry_run,
                    ticker_config=ticker_config,
                    explicit_tickers=explicit_tickers,
                    skip_existing=True,
                    parallel=False,
                    lake_dir=lake_dir,
                )
                outcome = result.get(market, SourceOutcome(SourceStatus.FAILED))
                if outcome.status is SourceStatus.NOT_SCHEDULED:
                    # D04/F11: a closed exchange is a planned skip, not a
                    # recovered date and not a failure. It stays counted in
                    # market_date_attempts but is excluded from succeeded/failed
                    # so recovery counts are not inflated by weekends/holidays.
                    logger.info("backfill_not_scheduled", date=str(current), market=market)
                elif outcome.succeeded:
                    logger.info("backfill_ok", date=str(current), market=market)
                    succeeded += 1
                    day_success += 1
                    per_market[market] += 1
                else:
                    failed += 1
                    logger.warning("backfill_skip", date=str(current), market=market)
            except Exception as exc:
                failed += 1
                logger.error("backfill_error", date=str(current), market=market, error=str(exc))
        if market_list and day_success == len(market_list):
            dates_recovered += 1
        current += timedelta(days=1)

    return BackfillResult(
        start_date=start_date,
        end_date=end_date,
        markets=tuple(market_list),
        instruments=instruments,
        authorized=authorized,
        trading_dates=trading_dates,
        market_date_attempts=market_date_attempts,
        succeeded=succeeded,
        failed=failed,
        dates_recovered=dates_recovered,
        per_market=per_market,
    )


def widen_history_range(
    *,
    market: str = "us_equity",
    tickers: list[str] | None = None,
    start_date: date,
    end_date: date,
    lake_dir: Path | None = None,
    dry_run: bool = False,
    ticker_config: TickerConfig | None = None,
) -> dict[str, Any]:
    """Widen a real-source root with one range fetch per ticker.

    ``equity backfill`` delegates to the daily pipeline and re-fetches every trading
    day, which is the right shape for gap recovery and the wrong shape for decades:
    forty years is roughly ten thousand provider requests. This path fetches each
    ticker's whole window in one range request, reconstructs raw prices once, and
    writes through the same :func:`_write_market` boundary the daily pipeline uses —
    identity preparation, ADR-0029 reconstruction, the pointblank quality gate and
    the Delta merge are not re-implemented here.

    Each ticker is clamped to its own evidence in the **target root**: the coverage
    table's ``coverage_start``..``coverage_end`` (ADR-0032), or the stored actions'
    first/last ``ex_date``. ADR-0029 Part I refuses a price row past an action
    watermark and the adjusted read refuses rows before a coverage start, so a
    wider request is silently *not* what this function does: it is a refusal or a
    clamp, reported per ticker. A ticker with no coverage and no actions is reported
    as ``no_action_coverage`` and never guessed.
    """
    import polars as pl

    from equity_lake.core.availability import VendorEventCoverageProof
    from equity_lake.core.config import TickerConfig, get_settings
    from equity_lake.core.paths import LONG_TO_SHORT, canonical_market
    from equity_lake.ingestion.corporate_actions import (
        corporate_action_coverage_table,
        coverage_frame_for_write,
        load_corporate_action_inputs,
    )
    from equity_lake.ingestion.orchestrator import _write_market
    from equity_lake.ingestion.router import MARKET_REGISTRY
    from equity_lake.ingestion.writers import upsert_dataset

    canonical = canonical_market(market)
    factory = MARKET_REGISTRY.get(canonical)
    if factory is None:
        raise ValueError(f"unknown price market {canonical!r}")

    settings = get_settings()
    if ticker_config is None:
        ticker_config = TickerConfig(config_path=Path(settings.ingestion.ticker_config_path))
    requested = list(tickers or [])
    if not requested:
        for key in {LONG_TO_SHORT[canonical], canonical}:
            requested.extend(ticker_config.get_tickers_for_market(key, active_only=True))
        requested = list(dict.fromkeys(requested))

    actions, coverage = load_corporate_action_inputs(canonical, lake_dir=lake_dir)
    bounds: dict[str, tuple[date, date]] = {}
    if not coverage.is_empty():
        for row in coverage.to_dicts():
            start, end = row["coverage_start"], row["coverage_end"]
            if row["symbol"] in bounds:
                start = min(start, bounds[row["symbol"]][0])
                end = max(end, bounds[row["symbol"]][1])
            bounds[row["symbol"]] = (start, end)
    if not actions.is_empty():
        for row in actions.group_by("ticker").agg(pl.col("ex_date").min().alias("first"), pl.col("ex_date").max().alias("last")).to_dicts():
            bounds.setdefault(row["ticker"], (row["first"], row["last"]))

    outcomes: dict[str, dict[str, Any]] = {}
    total_rows = 0
    # Proven-empty scopes (ADR-0033): a ticker with no action row is landable only
    # when a fetch that did not fail returns no vendor events at all. The resulting
    # weak proof covers the price window, so the split factor is provably 1.
    for ticker in [name for name in requested if name not in bounds]:
        probe = factory(
            retry_attempts=settings.ingestion.retry_attempts,
            retry_delay=settings.ingestion.retry_delay,
            ticker_config=ticker_config,
            filters=None,
            explicit_tickers=[ticker],
        )
        try:
            events = probe.fetch_vendor_action_list(ticker)
        except Exception as exc:
            outcomes[ticker] = {"ok": False, "reason": f"action_fetch_failed: {exc}", "rows": 0}
            logger.error("proven_empty_action_fetch_failed", ticker=ticker, market=canonical, error=str(exc))
            continue
        if events is not None:
            outcomes[ticker] = {"ok": False, "reason": "actions_not_ingested", "rows": 0}
            logger.warning("widen_actions_not_ingested", ticker=ticker, market=canonical, events=events.height)
            continue
        mics: set[str] = set()
        for key in {LONG_TO_SHORT[canonical], canonical}:
            configured = ticker_config.get_ticker_metadata(ticker, market=key)
            if configured is not None:
                mics.add(configured.instrument_key(canonical).mic)
        if len(mics) != 1:
            outcomes[ticker] = {"ok": False, "reason": "identity_unavailable", "rows": 0}
            logger.warning("proven_empty_identity_unavailable", ticker=ticker, market=canonical)
            continue
        proof = VendorEventCoverageProof(
            market=canonical,
            mic=next(iter(mics)),
            symbol=ticker,
            source=getattr(probe, "source_name", "yahoo"),
            coverage_start=start_date,
            coverage_end=end_date,
        )
        if not dry_run:
            written = upsert_dataset(
                coverage_frame_for_write([proof]),
                corporate_action_coverage_table(canonical),
                end_date,
                dry_run=False,
                lake_dir=lake_dir,
                partition_by=["coverage_end"],
            )
            if not written:
                outcomes[ticker] = {"ok": False, "reason": "coverage_write_failed", "rows": 0}
                continue
        bounds[ticker] = (start_date, end_date)
        logger.info("proven_empty_action_scope", ticker=ticker, market=canonical, start=str(start_date), end=str(end_date))
    for ticker in requested:
        if ticker not in bounds:
            outcomes.setdefault(ticker, {"ok": False, "reason": "no_action_coverage", "rows": 0})
            logger.warning("widen_no_action_coverage", ticker=ticker, market=canonical)
            continue
        window_start = max(start_date, bounds[ticker][0])
        window_end = min(end_date, bounds[ticker][1])
        if window_start > window_end:
            outcomes[ticker] = {"ok": False, "reason": "empty_window", "rows": 0}
            logger.warning("widen_empty_window", ticker=ticker, market=canonical, start=str(window_start), end=str(window_end))
            continue
        fetcher = factory(
            retry_attempts=settings.ingestion.retry_attempts,
            retry_delay=settings.ingestion.retry_delay,
            ticker_config=ticker_config,
            filters=None,
            explicit_tickers=[ticker],
        )
        frame = fetcher.fetch_range(window_start, window_end)
        if frame.is_empty():
            outcomes[ticker] = {"ok": True, "reason": "no_price_rows", "rows": 0, "start": window_start.isoformat(), "end": window_end.isoformat()}
            continue
        written = _write_market(frame, canonical, window_end, dry_run, lake_dir)
        outcomes[ticker] = {
            "ok": written,
            "reason": None if written else "write_returned_false",
            "rows": frame.height,
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        }
        total_rows += frame.height
    ok = all(outcome["ok"] for outcome in outcomes.values())
    logger.info(
        "widen_history_complete",
        market=canonical,
        tickers=len(outcomes),
        rows=total_rows,
        dry_run=dry_run,
        failed=sorted(ticker for ticker, outcome in outcomes.items() if not outcome["ok"]),
    )
    return {"ok": ok, "market": canonical, "rows": total_rows, "dry_run": dry_run, "tickers": outcomes}
