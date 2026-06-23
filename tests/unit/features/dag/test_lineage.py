"""Tests for DAG lineage structure and topology."""

from __future__ import annotations

from hamilton import base, driver
from hamilton.plugins import h_polars

from equity_lake.features.dag import (
    clean_02,
    enrichments_04,
    features_03,
    raw_01,
)


def _build_driver() -> driver.Driver:
    adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
    return driver.Builder().with_modules(raw_01, clean_02, features_03, enrichments_04).with_adapter(adapter).build()


def test_all_four_modules_connected() -> None:
    """All 4 medallion modules are registered in the DAG."""
    dr = _build_driver()
    nodes = dr.list_available_variables()
    node_names = {n.name if hasattr(n, "name") else str(n) for n in nodes}
    assert "close" in node_names
    assert "validated_ohlcv" in node_names
    assert "rsi_14" in node_names
    assert "enriched_features" in node_names


def test_bronze_to_gold_dependency_chain() -> None:
    """Bronze (raw) → Silver (clean) → Gold (features) dependency is wired."""
    dr = _build_driver()
    nodes = dr.list_available_variables()
    node_names = {n.name if hasattr(n, "name") else str(n) for n in nodes}

    assert {"close", "volume", "high", "low"}.issubset(node_names), "Bronze nodes missing"
    assert {"returns", "validated_ohlcv"}.issubset(node_names), "Silver nodes missing"
    assert {"rsi_14", "macd", "bb_upper", "atr_14"}.issubset(node_names), "Gold nodes missing"
    assert "validated_features" in node_names, "Gold boundary node missing"


def test_enrichment_node_exists() -> None:
    """The enriched_features terminal node is in the DAG."""
    dr = _build_driver()
    nodes = dr.list_available_variables()
    node_names = {n.name if hasattr(n, "name") else str(n) for n in nodes}
    assert "enriched_features" in node_names
