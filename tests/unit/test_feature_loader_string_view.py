"""Regression: the Gold feature loader must read string_view Gold files.

Label finalization rewrites matched Gold files through delta-rs, which stores
string columns as Arrow ``string_view``. PyArrow's dataset pushdown has no
``string_view`` comparison kernels, so a pushed-down symbol filter raises
``ArrowNotImplementedError`` on such a table. The loader therefore pushes down
only the date range and applies the symbol scope in Polars.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from runpy import run_path
from typing import Any, cast

import polars as pl
import pyarrow.parquet as pq
import pytest

from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.core.schemas import FEATURE_SCHEMA_VERSION  # type: ignore[import-untyped]
from equity_lake.ml.feature_loader import FeatureLoader  # type: ignore[import-untyped]
from equity_lake.storage.delta import merge_delta, read_delta, update_delta_columns  # type: ignore[import-untyped]

_pit_fixtures = run_path(str(Path(__file__).with_name("_pit_fixtures.py")))
with_synthetic_feature_proof = _pit_fixtures["with_synthetic_feature_proof"]

_DATES = [date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)]
_AAPL = InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")
_MSFT = InstrumentKey(market="us_equity", mic="XNAS", symbol="MSFT")
_KEY_COLUMNS = ["market", "mic", "symbol", "date"]


def _frame(instrument: InstrumentKey, dates: list[date]) -> pl.DataFrame:
    rows = len(dates)
    raw = pl.DataFrame(
        {
            "market": [instrument.market] * rows,
            "mic": [instrument.mic] * rows,
            "symbol": [instrument.symbol] * rows,
            "identity_schema_version": [1] * rows,
            "ticker": [instrument.symbol] * rows,
            "date": dates,
            "feature_schema_version": [FEATURE_SCHEMA_VERSION] * rows,
            "rsi_14": [50.0 + index for index in range(rows)],
            "next_day_return": [0.01] * (rows - 1) + [None],
            "label_available_date": [cast(Any, day) for day in dates[1:]] + [None],
        }
    )
    return cast(pl.DataFrame, with_synthetic_feature_proof(raw, instrument))


def _gold_lake(tmp_path: Path) -> Path:
    merge_delta(_frame(_AAPL, _DATES), "03_gold/features", key_columns=_KEY_COLUMNS, partition_by=["date"], lake_dir=tmp_path)
    # Finalizing a label rewrites the matched file through delta-rs.
    label_only = pl.DataFrame(
        {
            "market": ["us_equity"],
            "mic": ["XNAS"],
            "symbol": ["AAPL"],
            "date": [_DATES[0]],
            "next_day_return": [0.0123],
            "label_available_date": [_DATES[1]],
        }
    )
    update_delta_columns(
        label_only,
        "03_gold/features",
        update_columns=["next_day_return", "label_available_date"],
        key_columns=_KEY_COLUMNS,
        lake_dir=tmp_path,
    )
    return tmp_path / "03_gold" / "features"


def test_gold_table_really_gained_string_view_files(tmp_path: Path) -> None:
    """Precondition: the scenario exercises the string_view layout, not a lookalike."""
    gold_dir = _gold_lake(tmp_path)

    types = {str(pq.read_schema(path).field("ticker").type) for path in gold_dir.rglob("*.parquet")}
    assert "string_view" in types


def test_loader_reads_gold_string_view_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gold_dir = _gold_lake(tmp_path)
    monkeypatch.setattr("equity_lake.ml.feature_loader.GOLD_FEATURES_DIR", gold_dir)

    loader = FeatureLoader()
    loaded = loader.load_features(_AAPL, _DATES[0], _DATES[-1])

    assert loaded.height == 3
    assert set(loaded["symbol"].to_list()) == {"AAPL"}
    finalized = loaded.filter(pl.col("date") == _DATES[0]).row(0, named=True)
    assert finalized["next_day_return"] == pytest.approx(0.0123)


def test_read_delta_filters_the_same_drifted_gold_table(tmp_path: Path) -> None:
    """The shared read seam applies string filters in Polars, not in Arrow."""
    _gold_lake(tmp_path)

    frame = read_delta("03_gold/features", lake_dir=tmp_path, filters=[("ticker", "=", "AAPL")], columns=["ticker", "date"])

    assert frame.height == 3
    assert set(frame["ticker"].to_list()) == {"AAPL"}


def test_loader_still_scopes_to_the_requested_instrument(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gold_dir = _gold_lake(tmp_path)
    merge_delta(_frame(_MSFT, _DATES), "03_gold/features", key_columns=_KEY_COLUMNS, partition_by=["date"], lake_dir=tmp_path)
    monkeypatch.setattr("equity_lake.ml.feature_loader.GOLD_FEATURES_DIR", gold_dir)

    loaded = FeatureLoader().load_features(_AAPL, _DATES[0], _DATES[-1])

    assert set(loaded["symbol"].to_list()) == {"AAPL"}
