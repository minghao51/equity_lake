"""Tests for @parameterize rolling window functions."""

from __future__ import annotations

import polars as pl
from hamilton import base, driver
from hamilton.plugins import h_polars

from equity_lake.features.dag import features_03, raw_01
from equity_lake.features.indicators import roc


def _close_price_df(n: int = 80) -> pl.DataFrame:
    import numpy as np

    rng = np.random.default_rng(42)
    prices = 100.0 + rng.standard_normal(n).cumsum()
    return pl.DataFrame(
        {
            "ticker": ["AAPL"] * n,
            "date": pl.datetime_range(pl.datetime(2024, 1, 1), pl.datetime(2024, 1, 1) + pl.duration(days=n - 1), "1d", eager=True),
            "open": prices,
            "high": prices + 2,
            "low": prices - 2,
            "close": prices,
            "volume": [1_000_000.0] * n,
        }
    )


def test_parameterized_roc_matches_manual() -> None:
    """roc_5 from @parameterize should match roc(close, length=5)."""
    close = _close_price_df()["close"]
    expected = roc(close, length=5)

    adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
    dr = driver.Builder().with_modules(raw_01, features_03).with_adapter(adapter).build()
    result = dr.execute(["roc_5"], inputs={"price_data": _close_price_df()})

    actual = result["roc_5"]
    for i in range(len(close)):
        exp_val = expected[i]
        act_val = actual[i]
        if exp_val is None and act_val is None:
            continue
        assert abs(act_val - exp_val) < 1e-9, f"Mismatch at index {i}: {act_val} vs {exp_val}"


def test_parameterized_returns_match_manual() -> None:
    """return_1d from @parameterize should match close.pct_change(1)."""
    df = _close_price_df()
    expected = df["close"].pct_change(1)

    adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
    dr = driver.Builder().with_modules(raw_01, features_03).with_adapter(adapter).build()
    result = dr.execute(["return_1d"], inputs={"price_data": df})

    actual = result["return_1d"]
    for i in range(len(df)):
        exp_val = expected[i]
        act_val = actual[i]
        if exp_val is None and act_val is None:
            continue
        assert abs(act_val - exp_val) < 1e-9, f"Mismatch at index {i}: {act_val} vs {exp_val}"


def test_parameterized_nodes_exist_in_dag() -> None:
    """All parameterized node names should be present in the DAG."""
    adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
    dr = driver.Builder().with_modules(raw_01, features_03).with_adapter(adapter).build()

    dag_nodes = {v.name for v in dr.list_available_variables()}
    for name in ["roc_5", "roc_10", "roc_20", "return_1d", "return_5d", "return_10d", "return_20d"]:
        assert name in dag_nodes, f"Missing parameterized node: {name}"
