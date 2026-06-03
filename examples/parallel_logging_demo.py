#!/usr/bin/env python3
"""
Demo Script for Parallel Fetching and Structured Logging

This script demonstrates the new parallel fetching and structured logging features.
Run this to see the improvements in action!
"""

import time
from datetime import date

import pandas as pd

from equity_lake.core.logging import (
    correlation_context,
    get_correlation_id,
    setup_structured_logging,
    timed,
    timer,
)
from equity_lake.ingestion.parallel import (
    fetch_markets_parallel,
    fetch_markets_sequential,
    summarize_results,
)


# Mock fetch functions for demonstration
def mock_fetch_us(trading_date: date) -> "pd.DataFrame":
    """Mock US market fetch (simulates 2 second API call)."""
    time.sleep(2)
    print("  [Simulated] Fetched 5000 rows from US market")
    return None  # Would return DataFrame in real usage


def mock_fetch_cn(trading_date: date) -> "pd.DataFrame":
    """Mock China market fetch (simulates 2 second API call)."""
    time.sleep(2)
    print("  [Simulated] Fetched 3000 rows from China market")
    return None  # Would return DataFrame in real usage


def mock_fetch_hk_sg(trading_date: date) -> "pd.DataFrame":
    """Mock HK/SG market fetch (simulates 2 second API call)."""
    time.sleep(2)
    print("  [Simulated] Fetched 1500 rows from HK/SG market")
    return None  # Would return DataFrame in real usage


def demo_structured_logging():
    """Demonstrate structured logging features."""
    print("\n" + "=" * 70)
    print("DEMO 1: Structured Logging with Timing")
    print("=" * 70 + "\n")

    # Setup structured logging
    logger = setup_structured_logging(
        level="INFO",
        json_output=True,  # JSON format for machine readability
        console=True,
    )

    # Demonstrate @timed decorator
    @timed(operation="data_fetch", market="demo")
    def fetch_with_timing():
        time.sleep(1)
        print("  [Simulated] Fetch operation completed")
        return {"data": "sample"}

    print("1. Using @timed decorator:")
    fetch_with_timing()

    # Demonstrate timer context manager
    print("\n2. Using timer context manager:")
    with timer("batch_processing", record_count=1000):
        time.sleep(1)
        print("  [Simulated] Batch processing completed")

    # Demonstrate correlation context
    print("\n3. Using correlation context:")
    with correlation_context("demo-run-123"):
        logger.info("step_1_started", step="fetch")
        logger.info("step_2_started", step="process")
        print(f"  Correlation ID: {get_correlation_id()}")


def demo_sequential_fetching():
    """Demonstrate sequential fetching (original behavior)."""
    print("\n" + "=" * 70)
    print("DEMO 2: Sequential Market Fetching (Original)")
    print("=" * 70 + "\n")

    trading_date = date.today()

    # Build fetch function map
    fetch_func_map = {
        "us": (mock_fetch_us, {}),
        "cn": (mock_fetch_cn, {}),
        "hk_sg": (mock_fetch_hk_sg, {}),
    }

    markets = ["us", "cn", "hk_sg"]

    print(f"Fetching {len(markets)} markets sequentially...")
    print("(Each market takes ~2 seconds, so total ~6 seconds)\n")

    start_time = time.time()

    results = fetch_markets_sequential(markets=markets, trading_date=trading_date, fetch_func_map=fetch_func_map)

    elapsed = time.time() - start_time

    print(f"\n✅ Sequential fetching completed in {elapsed:.2f} seconds")

    summary = summarize_results(results)
    print(f"   Successful: {summary['successful']}/{summary['total_markets']}")
    print(f"   Avg duration: {summary['avg_duration_seconds']:.2f}s per market")

    return elapsed


def demo_parallel_fetching():
    """Demonstrate parallel fetching (new feature)."""
    print("\n" + "=" * 70)
    print("DEMO 3: Parallel Market Fetching (New Feature!) 🚀")
    print("=" * 70 + "\n")

    trading_date = date.today()

    # Build fetch function map
    fetch_func_map = {
        "us": (mock_fetch_us, {}),
        "cn": (mock_fetch_cn, {}),
        "hk_sg": (mock_fetch_hk_sg, {}),
    }

    markets = ["us", "cn", "hk_sg"]

    print(f"Fetching {len(markets)} markets in parallel...")
    print("(All markets fetched concurrently, so total ~2 seconds)\n")

    start_time = time.time()

    results = fetch_markets_parallel(
        markets=markets,
        trading_date=trading_date,
        fetch_func_map=fetch_func_map,
        max_workers=len(markets),
    )

    elapsed = time.time() - start_time

    print(f"\n✅ Parallel fetching completed in {elapsed:.2f} seconds")

    summary = summarize_results(results)
    print(f"   Successful: {summary['successful']}/{summary['total_markets']}")
    print(f"   Total duration: {summary['total_duration_seconds']:.2f}s")
    print(f"   Avg duration: {summary['avg_duration_seconds']:.2f}s per market")
    print(f"   Fastest market: {summary['fastest_market']}")
    print(f"   Slowest market: {summary['slowest_market']}")

    return elapsed


def demo_comparison():
    """Compare sequential vs parallel performance."""
    print("\n" + "=" * 70)
    print("DEMO 4: Performance Comparison")
    print("=" * 70 + "\n")

    print("Running both modes to compare performance...\n")

    # Run sequential
    sequential_time = demo_sequential_fetching()

    print("\n" + "-" * 70 + "\n")

    # Run parallel
    parallel_time = demo_parallel_fetching()

    # Show comparison
    print("\n" + "=" * 70)
    print("COMPARISON RESULTS")
    print("=" * 70)
    print(f"Sequential time: {sequential_time:.2f}s")
    print(f"Parallel time:   {parallel_time:.2f}s")
    print(f"Speedup:         {sequential_time / parallel_time:.2f}x 🚀")
    print(f"Time saved:      {sequential_time - parallel_time:.2f}s")


def main():
    """Run all demos."""
    print("\n" + "=" * 70)
    print("🎯 Parallel Fetching & Structured Logging Demo")
    print("=" * 70)
    print("\nThis demo showcases the new features:")
    print("  1. Structured logging with automatic timing")
    print("  2. Parallel market fetching for 3x speedup")
    print("  3. Correlation tracking across operations")
    print("\nNote: This uses mock data (no actual API calls)")

    # Demo 1: Structured logging
    demo_structured_logging()

    # Demo 2 & 3 & 4: Sequential vs Parallel comparison
    demo_comparison()

    print("\n" + "=" * 70)
    print("✅ Demo Complete!")
    print("=" * 70)
    print("\nTo use these features in your daily ingestion:")
    print("  uv run equity-daily --parallel")
    print("\nFor more information, see: docs/architecture/parallel-ingestion.md")
    print()


if __name__ == "__main__":
    main()
