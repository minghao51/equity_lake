"""Tests for catalog dataset definitions: structure, layers, conventions."""

from __future__ import annotations

import pytest

from equity_lake.catalog.datasets import (
    ALL_DATASETS,
    BRONZE_DATASETS,
    GOLD_DATASETS,
    LAYER_ORDER,
    PLATINUM_DATASETS,
    SILVER_DATASETS,
)
from equity_lake.catalog.models import DatasetEntry

VALID_LAYERS = {"bronze", "silver", "gold", "platinum"}

LAYER_PATH_MAP = {
    "bronze": "01_bronze",
    "silver": "02_silver",
    "gold": "03_gold",
    "platinum": "04_platinum",
}


class TestLayerStructure:
    def test_layer_order_matches_definitions(self) -> None:
        assert LAYER_ORDER == ["bronze", "silver", "gold", "platinum"]

    def test_all_datasets_have_valid_layer(self) -> None:
        for ds in ALL_DATASETS:
            assert ds.layer in VALID_LAYERS, f"Dataset {ds.name} has invalid layer: {ds.layer!r}"

    def test_no_duplicate_dataset_names(self) -> None:
        names = [ds.name for ds in ALL_DATASETS]
        assert len(names) == len(set(names)), f"Duplicate names: {names}"

    def test_all_lists_concatenate_correctly(self) -> None:
        assert ALL_DATASETS == BRONZE_DATASETS + SILVER_DATASETS + GOLD_DATASETS + PLATINUM_DATASETS


class TestPathConventions:
    def test_paths_follow_medallion_convention(self) -> None:
        for ds in ALL_DATASETS:
            expected_segment = LAYER_PATH_MAP[ds.layer]
            assert expected_segment in ds.path, f"Dataset {ds.name} path {ds.path!r} missing medallion segment {expected_segment!r}"

    def test_paths_start_with_data_lake(self) -> None:
        for ds in ALL_DATASETS:
            assert ds.path.startswith("data/lake/"), f"Dataset {ds.name} path doesn't start with data/lake/: {ds.path!r}"

    def test_paths_end_with_slash(self) -> None:
        for ds in ALL_DATASETS:
            assert ds.path.endswith("/"), f"Dataset {ds.name} path doesn't end with /: {ds.path!r}"


class TestDatasetContent:
    def test_all_datasets_have_columns(self) -> None:
        for ds in ALL_DATASETS:
            assert len(ds.columns) > 0, f"Dataset {ds.name} has no columns"

    def test_all_datasets_have_description(self) -> None:
        for ds in ALL_DATASETS:
            assert len(ds.description) > 10, f"Dataset {ds.name} has empty/short description"

    def test_all_datasets_have_parquet_format(self) -> None:
        for ds in ALL_DATASETS:
            assert ds.format == "parquet", f"Dataset {ds.name} has format {ds.format!r}, expected 'parquet'"

    def test_all_datasets_have_partition(self) -> None:
        for ds in ALL_DATASETS:
            assert ds.partition, f"Dataset {ds.name} has empty partition"

    @pytest.mark.parametrize("dataset", ALL_DATASETS, ids=lambda d: d.name)
    def test_no_duplicate_column_names(self, dataset: DatasetEntry) -> None:
        names = [c.name for c in dataset.columns]
        assert len(names) == len(set(names)), f"Dataset {dataset.name} has duplicate column names"


class TestColumnDtypes:
    def test_bronze_columns_have_known_dtypes(self) -> None:
        for ds in BRONZE_DATASETS:
            unknowns = [c.name for c in ds.columns if c.dtype == "unknown"]
            assert not unknowns, f"Bronze dataset {ds.name} has unknown-dtype columns: {unknowns}"

    def test_silver_columns_have_known_dtypes(self) -> None:
        for ds in SILVER_DATASETS:
            unknowns = [c.name for c in ds.columns if c.dtype == "unknown"]
            assert not unknowns, f"Silver dataset {ds.name} has unknown-dtype columns: {unknowns}"

    def test_all_dtypes_are_valid_polars_types(self) -> None:
        valid = {"string", "datetime", "float64", "int64", "int32", "int16", "int8"}
        for ds in ALL_DATASETS:
            for col in ds.columns:
                assert col.dtype in valid, f"{ds.name}.{col.name} has unrecognized dtype {col.dtype!r}"


class TestLayerCounts:
    def test_bronze_has_multiple_datasets(self) -> None:
        assert len(BRONZE_DATASETS) >= 5

    def test_silver_has_multiple_datasets(self) -> None:
        assert len(SILVER_DATASETS) >= 3

    def test_gold_has_one_dataset(self) -> None:
        assert len(GOLD_DATASETS) == 1

    def test_platinum_has_one_dataset(self) -> None:
        assert len(PLATINUM_DATASETS) == 1

    def test_total_dataset_count(self) -> None:
        assert len(ALL_DATASETS) == len(BRONZE_DATASETS) + len(SILVER_DATASETS) + len(GOLD_DATASETS) + len(PLATINUM_DATASETS)
