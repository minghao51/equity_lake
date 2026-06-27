"""Tests for the catalog builder: Hamilton DAG topology extraction."""

from __future__ import annotations

import pytest

from equity_lake.catalog.builder import _is_hamilton_internal, build_catalog
from equity_lake.catalog.models import Catalog


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return build_catalog()


class TestNodeFiltering:
    def test_no_hamilton_internal_nodes(self, catalog: Catalog) -> None:
        for node in catalog.nodes:
            assert not _is_hamilton_internal(node.name), f"Internal node leaked: {node.name}"

    def test_no_hamilton_internal_suffixes(self, catalog: Catalog) -> None:
        suffixes = ("_raw", "_data_type_validator", "_range_validator")
        for node in catalog.nodes:
            for suffix in suffixes:
                assert not node.name.endswith(suffix), f"{node.name} ends with {suffix}"

    def test_all_nodes_have_layer(self, catalog: Catalog) -> None:
        for node in catalog.nodes:
            assert node.layer in ("bronze", "silver", "gold"), f"Node {node.name} has unexpected layer: {node.layer!r}"

    def test_node_count_reasonable(self, catalog: Catalog) -> None:
        assert len(catalog.nodes) >= 30, f"Expected >=30 nodes, got {len(catalog.nodes)}"


class TestModuleTagFiltering:
    def test_no_module_tag_in_output(self, catalog: Catalog) -> None:
        for node in catalog.nodes:
            assert "module" not in node.tags, f"Node {node.name} has 'module' tag leaked"
            assert not any(k.startswith("hamilton.") for k in node.tags), f"Node {node.name} has hamilton.* tag leaked"


class TestProducesSplit:
    def test_parameterized_produces_splits_on_pipe(self, catalog: Catalog) -> None:
        roc_nodes = [n for n in catalog.nodes if n.name in ("roc_5", "roc_10", "roc_20")]
        assert len(roc_nodes) == 3
        for node in roc_nodes:
            assert node.produces == ["roc_5", "roc_10", "roc_20"], f"Node {node.name} produces mismatch: {node.produces}"

    def test_single_produces_not_split(self, catalog: Catalog) -> None:
        rsi_nodes = [n for n in catalog.nodes if n.name == "rsi_14"]
        assert len(rsi_nodes) == 1
        assert rsi_nodes[0].produces == ["rsi_14"]

    def test_return_nodes_produce_split(self, catalog: Catalog) -> None:
        return_nodes = [n for n in catalog.nodes if n.name.startswith("return_")]
        assert len(return_nodes) == 4
        for node in return_nodes:
            assert node.produces == ["return_1d", "return_5d", "return_10d", "return_20d"]


class TestEdges:
    def test_no_self_referencing_edges(self, catalog: Catalog) -> None:
        for edge in catalog.edges:
            assert edge.source != edge.target, f"Self-referencing edge: {edge.source} -> {edge.target}"

    def test_no_duplicate_edges(self, catalog: Catalog) -> None:
        edge_keys = [(e.source, e.target) for e in catalog.edges]
        assert len(edge_keys) == len(set(edge_keys)), "Duplicate edges found"

    def test_edge_sources_are_known_nodes(self, catalog: Catalog) -> None:
        node_names = {n.name for n in catalog.nodes}
        for edge in catalog.edges:
            assert edge.source in node_names, f"Edge source {edge.source!r} not in nodes"
            assert edge.target in node_names, f"Edge target {edge.target!r} not in nodes"

    def test_edge_count_reasonable(self, catalog: Catalog) -> None:
        assert len(catalog.edges) >= 50, f"Expected >=50 edges, got {len(catalog.edges)}"


class TestDatasets:
    def test_dataset_count(self, catalog: Catalog) -> None:
        assert len(catalog.datasets) == 15

    def test_all_layers_present(self, catalog: Catalog) -> None:
        layers = {ds.layer for ds in catalog.datasets}
        assert layers == {"bronze", "silver", "gold", "platinum"}


class TestDatasetLineage:
    def test_bronze_has_no_upstream(self, catalog: Catalog) -> None:
        for ds in catalog.datasets:
            if ds.layer == "bronze":
                assert ds.upstream == [], f"Bronze dataset {ds.name} should have no upstream, got {ds.upstream}"

    def test_platinum_has_no_downstream(self, catalog: Catalog) -> None:
        for ds in catalog.datasets:
            if ds.layer == "platinum":
                assert ds.downstream == [], f"Platinum dataset {ds.name} should have no downstream, got {ds.downstream}"

    def test_silver_upstream_is_all_bronze(self, catalog: Catalog) -> None:
        bronze_names = {ds.name for ds in catalog.datasets if ds.layer == "bronze"}
        for ds in catalog.datasets:
            if ds.layer == "silver":
                assert set(ds.upstream) == bronze_names, f"Silver {ds.name} upstream mismatch"

    def test_gold_upstream_is_all_silver(self, catalog: Catalog) -> None:
        silver_names = {ds.name for ds in catalog.datasets if ds.layer == "silver"}
        for ds in catalog.datasets:
            if ds.layer == "gold":
                assert set(ds.upstream) == silver_names, f"Gold {ds.name} upstream mismatch"

    def test_platinum_upstream_is_all_gold(self, catalog: Catalog) -> None:
        gold_names = {ds.name for ds in catalog.datasets if ds.layer == "gold"}
        for ds in catalog.datasets:
            if ds.layer == "platinum":
                assert set(ds.upstream) == gold_names, f"Platinum {ds.name} upstream mismatch"

    def test_downstream_is_reverse_of_upstream(self, catalog: Catalog) -> None:
        """If A is upstream of B, then B must be in A's downstream."""
        for ds in catalog.datasets:
            for upstream_name in ds.upstream:
                upstream_ds = next(d for d in catalog.datasets if d.name == upstream_name)
                assert ds.name in upstream_ds.downstream, f"{ds.name} lists {upstream_name} as upstream but is not in its downstream"

    def test_predictions_upstream_is_technical_features(self, catalog: Catalog) -> None:
        predictions = next(d for d in catalog.datasets if d.name == "predictions")
        assert predictions.upstream == ["technical_features"]

    def test_lineage_does_not_mutate_static_definitions(self) -> None:
        """The static ALL_DATASETS must remain unpopulated after build_catalog()."""
        from equity_lake.catalog.datasets import ALL_DATASETS

        for ds in ALL_DATASETS:
            assert ds.upstream == [], f"Static dataset {ds.name} was mutated: upstream={ds.upstream}"
            assert ds.downstream == [], f"Static dataset {ds.name} was mutated: downstream={ds.downstream}"
