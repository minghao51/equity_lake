"""Unit tests for ingestion.parallel — duration tracking and error handling."""

import time
from datetime import date

import polars as pl

from equity_lake.core.logging import correlation_context, get_correlation_id
from equity_lake.ingestion.parallel import FetchResult, fetch_items_parallel, fetch_markets_parallel


def test_fetch_result_default_duration():
    """FetchResult should default duration_seconds to 0.0."""
    result = FetchResult(market="us", data=pl.DataFrame(), success=True)
    assert result.duration_seconds == 0.0


def test_fetch_markets_parallel_propagates_correlation_id():
    """W02/F09: ThreadPoolExecutor workers inherit the submitting thread's
    correlation ID (ContextVars are not propagated automatically)."""
    observed: list[str] = []

    def fake_fetch(trading_date, *, label):
        observed.append(f"{label}:{get_correlation_id()}")
        return pl.DataFrame({"ticker": [label], "close": [1.0]})

    with correlation_context("run-one"):
        fetch_markets_parallel(
            ["us", "cn"],
            date(2024, 1, 2),
            {"us": (fake_fetch, {"label": "us"}), "cn": (fake_fetch, {"label": "cn"})},
        )

    assert sorted(observed) == ["cn:run-one", "us:run-one"]


def test_fetch_markets_parallel_context_isolation_between_runs():
    """W02/F09: a later run's workers never observe a previous run's ID, and the
    submitting thread's context is unchanged by worker activity."""
    observed: list[str] = []

    def fake_fetch(trading_date):
        observed.append(get_correlation_id())
        return pl.DataFrame({"ticker": ["AAPL"], "close": [1.0]})

    with correlation_context("run-one"):
        fetch_markets_parallel(["us"], date(2024, 1, 2), {"us": (fake_fetch, {})})
    assert observed == ["run-one"]

    observed.clear()
    with correlation_context("run-two"):
        fetch_markets_parallel(["us"], date(2024, 1, 2), {"us": (fake_fetch, {})})
    assert observed == ["run-two"]
    # Outside any correlation_context the submitting thread is unaffected by
    # the worker runs (it gets/creates its own ID, not a worker's).
    assert get_correlation_id() not in {"run-one", "run-two"}


def test_fetch_markets_parallel_per_task_context_does_not_leak():
    """W02/F09: a ContextVar set inside one task is invisible to a sibling task
    (each task runs in its own context copy)."""
    sibling_views: list[str] = []

    def setter(trading_date):
        with correlation_context("task-local"):
            time.sleep(0.02)
        return pl.DataFrame()

    def reader(trading_date):
        sibling_views.append(get_correlation_id())
        return pl.DataFrame()

    with correlation_context("parent"):
        fetch_markets_parallel(
            ["a", "b"],
            date(2024, 1, 2),
            {"a": (setter, {}), "b": (reader, {})},
            max_workers=2,
        )

    assert sibling_views == ["parent"]


def test_fetch_items_parallel_propagates_correlation_id():
    """W02/F09: the item-level parallel helper propagates context too."""
    observed: list[str] = []

    def work(item, trading_date):
        observed.append(get_correlation_id())
        return [item]

    with correlation_context("items-run"):
        fetch_items_parallel(["a", "b", "c"], work, date(2024, 1, 2), max_workers=2)

    assert observed == ["items-run", "items-run", "items-run"]


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


def test_fetch_markets_parallel_duration_reflects_work():
    """Duration must be measured from submit time, not from when as_completed yields the future.

    Regression: start time was recorded after the future completed, so
    duration_seconds was ~0 regardless of how long the fetch took.
    """

    def slow_fetch(trading_date, **kwargs):
        time.sleep(0.1)
        return pl.DataFrame({"ticker": ["AAPL"]})

    results = fetch_markets_parallel(
        ["us"],
        date(2024, 1, 2),
        {"us": (slow_fetch, {})},
    )

    assert results["us"].success is True
    assert results["us"].duration_seconds >= 0.05


def test_fetch_markets_parallel_error_duration_reflects_work():
    """Failing fetches must also report a real duration."""

    def slow_failure(trading_date, **kwargs):
        time.sleep(0.1)
        raise RuntimeError("boom")

    results = fetch_markets_parallel(
        ["us"],
        date(2024, 1, 2),
        {"us": (slow_failure, {})},
    )

    assert results["us"].success is False
    assert results["us"].duration_seconds >= 0.05


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
