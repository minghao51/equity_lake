"""Regression tests for the Delta read filter policy (F10).

A merge rewrite (label finalization, upserts) makes delta-rs store string columns
as Arrow ``string_view`` while the Delta log keeps declaring ``string``. PyArrow
(verified 21.x and 22.x) has no comparison kernels for ``string_view``, so
``read_delta`` pushes down date/numeric predicates only and applies string
predicates in Polars. These tests pin that policy, including its loud failures.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from deltalake import DeltaTable

from equity_lake.storage.delta import (
    DeltaReadError,
    _split_filters,
    merge_delta,
    read_delta,
    string_view_columns,
    update_delta_columns,
)

TABLE = "test/strings"
KEYS = ["market", "mic", "symbol", "date"]
DATES = [date(2026, 9, 1), date(2026, 9, 2)]


def _rows() -> pl.DataFrame:
    rows = [
        {"market": "us_equity", "mic": "XNAS", "symbol": "AAPL", "ticker": "AAPL", "date": DATES[0], "value": 1.0},
        {"market": "us_equity", "mic": "XNAS", "symbol": "AAPL", "ticker": "AAPL", "date": DATES[1], "value": 2.0},
        {"market": "us_equity", "mic": "XNAS", "symbol": "MSFT", "ticker": "MSFT", "date": DATES[0], "value": 3.0},
        {"market": "us_equity", "mic": "XNAS", "symbol": "NVDA", "ticker": "NVDA", "date": DATES[1], "value": 4.0},
        {"market": "us_equity", "mic": "XNAS", "symbol": "AMD", "ticker": None, "date": DATES[0], "value": 5.0},
    ]
    return pl.DataFrame(rows, schema_overrides={"value": pl.Float64})


def _drifted_lake(tmp_path: Path) -> Path:
    """Write the table, then force one merge rewrite so files become string_view."""
    merge_delta(_rows(), TABLE, key_columns=KEYS, partition_by=["date"], lake_dir=tmp_path)
    label_only = pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "date": [DATES[0]],
            "value": [9.5],
        }
    )
    update_delta_columns(label_only, TABLE, update_columns=["value"], key_columns=KEYS, lake_dir=tmp_path)
    return tmp_path


def test_fixture_really_produces_string_view_files(tmp_path: Path) -> None:
    """Precondition: the policy test must exercise string_view, not a lookalike."""
    lake = _drifted_lake(tmp_path)

    drift = string_view_columns(TABLE, lake_dir=lake)

    assert drift.drifted
    assert {"market", "mic", "symbol", "ticker"}.issubset(set(drift.columns))
    assert drift.scanned_files >= 1


def test_string_filters_are_residual_and_date_filters_still_push_down(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)
    table = DeltaTable(str(lake / TABLE))

    pushdown, residual = _split_filters(
        table,
        TABLE,
        [("date", ">=", DATES[0]), ("ticker", "=", "AAPL"), ("value", ">", 1.0)],
    )

    assert pushdown == [("date", ">=", DATES[0]), ("value", ">", 1.0)]
    assert residual == [("ticker", "=", "AAPL")]


def test_string_equality_filter_returns_only_matching_rows(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)

    frame = read_delta(TABLE, lake_dir=lake, filters=[("ticker", "=", "AAPL")], columns=["symbol", "date"])

    assert frame.height == 2
    assert set(frame["symbol"].to_list()) == {"AAPL"}


def test_string_ordering_and_set_operators_are_supported(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)

    greater = read_delta(TABLE, lake_dir=lake, filters=[("ticker", ">", "M")], columns=["ticker"])
    in_set = read_delta(TABLE, lake_dir=lake, filters=[("ticker", "in", ["AAPL", "NVDA"])], columns=["ticker"])
    not_in = read_delta(TABLE, lake_dir=lake, filters=[("ticker", "not in", ["AAPL"])], columns=["ticker"])
    not_equal = read_delta(TABLE, lake_dir=lake, filters=[("ticker", "!=", "AAPL")], columns=["ticker"])

    assert set(greater["ticker"].to_list()) == {"MSFT", "NVDA"}
    assert set(in_set["ticker"].to_list()) == {"AAPL", "NVDA"}
    # Null ticker rows drop, matching Arrow's comparison semantics.
    assert set(not_in["ticker"].to_list()) == {"MSFT", "NVDA"}
    assert set(not_equal["ticker"].to_list()) == {"MSFT", "NVDA"}


def test_residual_filter_column_is_hidden_from_a_projection(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)

    frame = read_delta(TABLE, lake_dir=lake, filters=[("ticker", "=", "AAPL")], columns=["value"])

    assert frame.columns == ["value"]
    assert sorted(frame["value"].to_list()) == [2.0, 9.5]


def test_residual_filter_matches_a_native_polars_filter(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)
    full = read_delta(TABLE, lake_dir=lake).sort("symbol", "date")
    expected = full.filter(pl.col("ticker") == "AAPL").sort("symbol", "date")

    filtered = read_delta(TABLE, lake_dir=lake, filters=[("ticker", "=", "AAPL")]).sort("symbol", "date")

    assert filtered.equals(expected)


def test_absent_filter_column_raises_loudly(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)

    with pytest.raises(DeltaReadError) as excinfo:
        read_delta(TABLE, lake_dir=lake, filters=[("not_a_column", "=", "x")])

    assert "not in table schema" in str(excinfo.value.__cause__)


def test_unsupported_string_operator_raises_loudly(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)

    with pytest.raises(DeltaReadError) as excinfo:
        read_delta(TABLE, lake_dir=lake, filters=[("ticker", "like", "A%")])

    assert "unsupported string filter operator" in str(excinfo.value.__cause__)


def test_date_only_filters_stay_on_the_pushdown_path(tmp_path: Path) -> None:
    lake = _drifted_lake(tmp_path)

    frame = read_delta(TABLE, lake_dir=lake, filters=[("date", ">=", DATES[1]), ("date", "<=", DATES[1])], columns=["date"])

    assert frame.height == 2  # AAPL + NVDA on the later session
    assert set(frame["date"].to_list()) == {DATES[1]}
