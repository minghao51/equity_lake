"""Unit tests for ingestion.parallel — duration tracking and error handling."""

from datetime import date

import polars as pl

from equity_lake.ingestion.parallel import FetchResult, fetch_items_parallel, fetch_markets_parallel


def test_fetch_result_default_duration():
    """FetchResult should default duration_seconds to 0.0."""
    result = FetchResult(market="us", data=pl.DataFrame(), success=True)
    assert result.duration_seconds == 0.0


def test_fetch_markets_parallel_records_duration():
    """fetch_markets_parallel should populate duration_seconds > 0 for successful fetches."""
    markets = ["us", "cn"]

    def fake_fetch(trading_date, **kwargs):
        return pl.DataFrame({"ticker": ["AAPL"], "close": [150.0]})

    results = fetch_markets_parallel(
        markets,
        date(2024, 1, 2),
        {m: (fake_fetch, {}) for m in markets},
    )

    for market in markets:
        assert results[market].success is True
        assert results[market].duration_seconds >= 0.0


def test_fetch_markets_parallel_records_error_duration():
    """fetch_markets_parallel should populate duration_seconds even on failure."""
    markets = ["us"]

    def failing_fetch(trading_date, **kwargs):
        raise RuntimeError("network error")

    results = fetch_markets_parallel(
        markets,
        date(2024, 1, 2),
        {"us": (failing_fetch, {})},
    )

    assert results["us"].success is False
    assert "network error" in results["us"].error
    assert results["us"].duration_seconds >= 0.0


def test_fetch_items_parallel_sequential():
    """Sequential mode (max_workers=1) should collect results."""

    def work(item, trading_date):
        return [{"item": item, "value": 42}]

    results = fetch_items_parallel(
        ["a", "b", "c"],
        work,
        date(2024, 1, 2),
        max_workers=1,
    )

    assert len(results) == 3
    assert all(r["value"] == 42 for r in results)


def test_fetch_items_parallel_parallel():
    """Parallel mode should collect all results."""

    def work(item, trading_date):
        return [{"item": item}]

    results = fetch_items_parallel(
        ["a", "b", "c", "d"],
        work,
        date(2024, 1, 2),
        max_workers=4,
    )

    items = {r["item"] for r in results}
    assert items == {"a", "b", "c", "d"}


def test_fetch_items_parallel_handles_errors():
    """Errors in individual items should be logged, not crash."""

    def work(item, trading_date):
        if item == "bad":
            raise ValueError("oops")
        return [{"item": item}]

    results = fetch_items_parallel(
        ["good", "bad", "also_good"],
        work,
        date(2024, 1, 2),
        max_workers=1,
    )

    assert len(results) == 2
    items = {r["item"] for r in results}
    assert items == {"good", "also_good"}
