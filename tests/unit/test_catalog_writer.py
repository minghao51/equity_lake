"""Tests for the catalog JSONL writer: serialization round-trip."""

from __future__ import annotations

import json

from equity_lake.catalog.builder import build_catalog
from equity_lake.catalog.models import Catalog
from equity_lake.catalog.writer import catalog_to_jsonl, write_catalog_jsonl


class TestJsonlFormat:
    def test_each_line_is_valid_json(self) -> None:
        catalog = build_catalog()
        jsonl = catalog_to_jsonl(catalog)
        for line in jsonl.strip().split("\n"):
            json.loads(line)

    def test_one_object_per_line(self) -> None:
        catalog = build_catalog()
        jsonl = catalog_to_jsonl(catalog)
        lines = jsonl.strip().split("\n")
        total_objects = len(catalog.datasets) + len(catalog.nodes) + len(catalog.edges) + 1
        assert len(lines) == total_objects

    def test_first_line_is_catalog_header(self) -> None:
        catalog = build_catalog()
        jsonl = catalog_to_jsonl(catalog)
        header = json.loads(jsonl.split("\n")[0])
        assert header["type"] == "catalog"
        assert header["dataset_count"] == len(catalog.datasets)
        assert header["node_count"] == len(catalog.nodes)
        assert header["edge_count"] == len(catalog.edges)

    def test_no_trailing_whitespace(self) -> None:
        catalog = build_catalog()
        jsonl = catalog_to_jsonl(catalog)
        for line in jsonl.split("\n"):
            assert line == line.rstrip(), f"Trailing whitespace: {line!r}"


class TestRoundTrip:
    def test_round_trip_preserves_counts(self) -> None:
        catalog = build_catalog()
        jsonl = catalog_to_jsonl(catalog)

        datasets = nodes = edges = 0
        for line in jsonl.strip().split("\n"):
            obj = json.loads(line)
            if obj["type"] == "dataset":
                datasets += 1
            elif obj["type"] == "node":
                nodes += 1
            elif obj["type"] == "edge":
                edges += 1

        assert datasets == len(catalog.datasets)
        assert nodes == len(catalog.nodes)
        assert edges == len(catalog.edges)


class TestWriteToFile:
    def test_write_to_custom_path(self, tmp_path) -> None:
        catalog = build_catalog()
        custom = tmp_path / "test_catalog.jsonl"
        result = write_catalog_jsonl(catalog, path=custom)
        assert result == custom
        assert custom.exists()
        content = custom.read_text()
        assert len(content.strip().split("\n")) > 0

    def test_default_path_creates_file(self) -> None:
        catalog: Catalog = build_catalog()
        result = write_catalog_jsonl(catalog)
        assert result.exists()
        assert result.suffix == ".jsonl"
