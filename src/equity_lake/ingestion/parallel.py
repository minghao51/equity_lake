"""
Parallel Market Data Fetching

This module provides concurrent execution of market data fetching
to significantly reduce daily ingestion time.

Example:
    Fetching 3 markets sequentially: 15 seconds
    Fetching 3 markets in parallel:   5 seconds (3x speedup)
"""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
import structlog

from equity_lake.core.logging import get_correlation_id, timer

# Use structlog for structured logging (supports keyword arguments)
logger = structlog.get_logger()


@dataclass
class MarketFetchResult:
    """Result of a market data fetch operation."""

    market: str
    success: bool
    data: pd.DataFrame | None = None
    error: str | None = None
    duration_seconds: float = 0.0

    def __bool__(self) -> bool:
        """Truthiness based on success."""
        return self.success


def fetch_market_with_timing(
    market: str,
    trading_date: date,
    fetch_func: Callable,
    fetch_func_kwargs: dict | None = None,
) -> MarketFetchResult:
    """
    Fetch data for a single market with timing and error handling.

    Args:
        market: Market identifier (e.g., 'us', 'cn', 'hk_sg')
        trading_date: Date to fetch
        fetch_func: Function to call for fetching data
        fetch_func_kwargs: Additional keyword arguments for fetch function

    Returns:
        MarketFetchResult with data or error information
    """
    import time

    kwargs = fetch_func_kwargs or {}
    start_time = time.time()

    logger.info(
        "market_fetch_started",
        market=market,
        trading_date=str(trading_date),
        correlation_id=get_correlation_id(),
    )

    try:
        # Call the fetch function
        df = fetch_func(trading_date, **kwargs)

        duration = time.time() - start_time

        if df is None or df.empty:
            logger.warning(
                "market_fetch_empty",
                market=market,
                duration_seconds=round(duration, 3),
            )
            return MarketFetchResult(
                market=market,
                success=False,
                data=None,
                error="No data returned",
                duration_seconds=round(duration, 3),
            )

        logger.info(
            "market_fetch_completed",
            market=market,
            row_count=len(df),
            duration_seconds=round(duration, 3),
        )

        return MarketFetchResult(market=market, success=True, data=df, duration_seconds=round(duration, 3))

    except Exception as e:
        duration = time.time() - start_time
        error_msg = f"{type(e).__name__}: {str(e)}"

        logger.error(
            "market_fetch_failed",
            market=market,
            error=error_msg,
            duration_seconds=round(duration, 3),
            exc_info=True,
        )

        return MarketFetchResult(
            market=market,
            success=False,
            data=None,
            error=error_msg,
            duration_seconds=round(duration, 3),
        )


def fetch_markets_parallel(
    markets: list[str],
    trading_date: date,
    fetch_func_map: dict[str, tuple[Callable, dict]],
    max_workers: int | None = None,
    timeout_seconds: int = 300,
) -> dict[str, MarketFetchResult]:
    """
    Fetch multiple markets concurrently using thread pool.

    Args:
        markets: List of market identifiers to fetch
        trading_date: Date to fetch data for
        fetch_func_map: Dictionary mapping market to (fetch_function, kwargs) tuple
        max_workers: Maximum number of concurrent threads (default: len(markets))
        timeout_seconds: Maximum time to wait for all fetches (default: 5 minutes)

    Returns:
        Dictionary mapping market name to MarketFetchResult

    Example:
        >>> fetch_func_map = {
        ...     'us': (fetch_us_data, {'retry_attempts': 3}),
        ...     'cn': (fetch_cn_data, {'retry_attempts': 3}),
        ...     'hk_sg': (fetch_hk_sg_data, {'retry_attempts': 3}),
        ... }
        >>> results = fetch_markets_parallel(['us', 'cn', 'hk_sg'], date.today(), fetch_func_map)
        >>> for market, result in results.items():
        ...     print(f"{market}: {'✅' if result.success else '❌'} ({result.duration_seconds}s)")
    """
    if not markets:
        logger.warning("no_markets_provided")
        return {}

    # Set max_workers to number of markets if not specified
    if max_workers is None:
        max_workers = len(markets)

    logger.info(
        "parallel_fetch_started",
        markets=markets,
        max_workers=max_workers,
        trading_date=str(trading_date),
    )

    results = {}

    with timer("parallel_market_fetching", market_count=len(markets)), ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_market = {}

        for market in markets:
            if market not in fetch_func_map:
                logger.error(
                    "fetch_function_not_found",
                    market=market,
                    available_markets=list(fetch_func_map.keys()),
                )
                results[market] = MarketFetchResult(
                    market=market,
                    success=False,
                    error=f"No fetch function defined for market: {market}",
                    duration_seconds=0.0,
                )
                continue

            fetch_func, fetch_kwargs = fetch_func_map[market]

            # Submit fetch job to thread pool
            future = executor.submit(
                fetch_market_with_timing,
                market=market,
                trading_date=trading_date,
                fetch_func=fetch_func,
                fetch_func_kwargs=fetch_kwargs,
            )

            future_to_market[future] = market

        # Collect results as they complete
        completed_futures = as_completed(future_to_market, timeout=timeout_seconds)

        for future in completed_futures:
            market = future_to_market[future]

            try:
                result = future.result()
                results[market] = result

            except Exception as e:
                logger.error(
                    "future_result_exception",
                    market=market,
                    error=str(e),
                    exc_info=True,
                )
                results[market] = MarketFetchResult(
                    market=market,
                    success=False,
                    error=f"Future execution failed: {str(e)}",
                    duration_seconds=0.0,
                )

    # Log summary
    successful = sum(1 for r in results.values() if r.success)
    failed = len(results) - successful
    total_duration = sum(r.duration_seconds for r in results.values())

    logger.info(
        "parallel_fetch_completed",
        successful=successful,
        failed=failed,
        total_markets=len(results),
        total_duration_seconds=round(total_duration, 3),
        avg_duration_seconds=round(total_duration / len(results), 3) if results else 0,
    )

    return results


def fetch_markets_sequential(
    markets: list[str],
    trading_date: date,
    fetch_func_map: dict[str, tuple[Callable, dict]],
) -> dict[str, MarketFetchResult]:
    """
    Fetch multiple markets sequentially (for fallback or comparison).

    Args:
        markets: List of market identifiers to fetch
        trading_date: Date to fetch data for
        fetch_func_map: Dictionary mapping market to (fetch_function, kwargs) tuple

    Returns:
        Dictionary mapping market name to MarketFetchResult

    Note:
        This function is primarily for testing or fallback when parallel execution fails.
    """
    logger.info("sequential_fetch_started", markets=markets, trading_date=str(trading_date))

    results = {}

    for market in markets:
        if market not in fetch_func_map:
            logger.error("fetch_function_not_found", market=market)
            results[market] = MarketFetchResult(
                market=market,
                success=False,
                error=f"No fetch function defined for market: {market}",
                duration_seconds=0.0,
            )
            continue

        fetch_func, fetch_kwargs = fetch_func_map[market]
        result = fetch_market_with_timing(
            market=market,
            trading_date=trading_date,
            fetch_func=fetch_func,
            fetch_func_kwargs=fetch_kwargs,
        )

        results[market] = result

    return results


def summarize_results(results: dict[str, MarketFetchResult]) -> dict[str, Any]:
    """
    Generate summary statistics from fetch results.

    Args:
        results: Dictionary of market fetch results

    Returns:
        Summary dictionary with statistics
    """
    if not results:
        return {
            "total_markets": 0,
            "successful": 0,
            "failed": 0,
            "success_rate": 0.0,
            "total_duration_seconds": 0.0,
            "avg_duration_seconds": 0.0,
        }

    successful = sum(1 for r in results.values() if r.success)
    failed = len(results) - successful
    total_duration = sum(r.duration_seconds for r in results.values())

    return {
        "total_markets": len(results),
        "successful": successful,
        "failed": failed,
        "success_rate": successful / len(results),
        "total_duration_seconds": round(total_duration, 3),
        "avg_duration_seconds": round(total_duration / len(results), 3),
        "slowest_market": max(results.items(), key=lambda x: x[1].duration_seconds)[0] if results else None,
        "fastest_market": min(results.items(), key=lambda x: x[1].duration_seconds)[0] if results else None,
    }


__all__ = [
    "MarketFetchResult",
    "fetch_market_with_timing",
    "fetch_markets_parallel",
    "fetch_markets_sequential",
    "summarize_results",
]
