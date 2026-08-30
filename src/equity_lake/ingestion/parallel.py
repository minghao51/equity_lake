"""Parallel market fetching utilities."""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class FetchResult:
    """Result of a single market fetch operation."""

    def __init__(
        self,
        market: str,
        data: Any = None,
        success: bool = True,
        error: str | None = None,
        duration_seconds: float = 0.0,
    ):
        self.market = market
        self.data = data
        self.success = success
        self.error = error
        self.duration_seconds = duration_seconds


def fetch_markets_parallel(
    markets: list[str],
    trading_date: date,
    fetch_func_map: dict[str, tuple[Callable[..., Any], dict[str, Any]]],
    max_workers: int | None = None,
) -> dict[str, FetchResult]:
    """Fetch multiple markets in parallel using ThreadPoolExecutor.

    ``duration_seconds`` is measured from executor submit to completion, so it
    reflects real work (including any time the task waited in the executor
    queue), not the time spent iterating ``as_completed``.
    """
    workers = max_workers or len(markets)
    results: dict[str, FetchResult] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures: dict[Future[Any], str] = {}
        submitted_at: dict[Future[Any], float] = {}
        for market in markets:
            if market in fetch_func_map:
                func, kwargs = fetch_func_map[market]
                future = executor.submit(func, trading_date, **kwargs)
                futures[future] = market
                submitted_at[future] = time.monotonic()

        for future in as_completed(futures):
            market = futures[future]
            duration_seconds = time.monotonic() - submitted_at[future]
            try:
                data = future.result()
                results[market] = FetchResult(market=market, data=data, success=True, duration_seconds=duration_seconds)
            except Exception as exc:
                results[market] = FetchResult(market=market, success=False, error=str(exc), duration_seconds=duration_seconds)

    return results


def summarize_results(results: dict[str, FetchResult]) -> dict[str, Any]:
    total = len(results)
    succeeded = sum(1 for r in results.values() if r.success)
    return {
        "total_markets": total,
        "succeeded": succeeded,
        "failed": total - succeeded,
        "markets": list(results.keys()),
    }


def fetch_items_parallel(
    items: list[str],
    work_func: Callable[[str, date], list[Any]],
    trading_date: date,
    max_workers: int = 4,
    rate_limit_seconds: float = 0.0,
) -> list[Any]:
    if max_workers <= 1 or len(items) <= 1:
        results: list[Any] = []
        for i, item in enumerate(items):
            if rate_limit_seconds > 0 and i > 0:
                time.sleep(rate_limit_seconds)
            try:
                results.extend(work_func(item, trading_date))
            except Exception as exc:
                logger.error("fetch_item_failed", item=item, error=str(exc))
        return results

    workers = min(max_workers, len(items))
    all_results: list[Any] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_item = {executor.submit(work_func, item, trading_date): item for item in items}
        for future in as_completed(future_to_item):
            item = future_to_item[future]
            try:
                all_results.extend(future.result())
            except Exception as exc:
                logger.error("fetch_item_failed", item=item, error=str(exc))
    return all_results
