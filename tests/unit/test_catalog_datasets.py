"""Tests for catalog dataset definitions: structure, layers, conventions."""

from __future__ import annotations

import runpy

import pytest

from equity_lake.catalog.datasets import (  # type: ignore[import-untyped]
    ALL_DATASETS,
    BRONZE_DATASETS,
    GOLD_DATASETS,
    LAYER_ORDER,
    PLATINUM_DATASETS,
    SILVER_DATASETS,
)
from equity_lake.catalog.models import DatasetEntry  # type: ignore[import-untyped]

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
        # Dataset names are unique within a layer; a bronze/silver pair may
        # intentionally share a name (corporate_actions, ADR-0011) since all
        # lineage is layer-scoped.
        keys = [(ds.layer, ds.name) for ds in ALL_DATASETS]
        assert len(keys) == len(set(keys)), f"Duplicate (layer, name): {keys}"

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

    def test_paths_derive_from_core_paths_constants(self) -> None:
        # Execute the source in a temporary namespace to compare against the
        # canonical definitions without reloading or unisolating runtime paths.
        from pathlib import Path

        from equity_lake.core import paths as runtime_paths  # type: ignore[import-untyped]

        source_paths = runpy.run_path(str(Path(runtime_paths.__file__).resolve()))
        project_root = source_paths["PROJECT_ROOT"]

        def rel(directory: Path) -> str:
            return directory.relative_to(project_root).as_posix() + "/"

        expected = {
            rel(source_paths["US_EQUITY_DIR"]),
            rel(source_paths["CN_ASHARE_DIR"]),
            rel(source_paths["HK_SG_EQUITY_DIR"]),
            rel(source_paths["JPX_EQUITY_DIR"]),
            rel(source_paths["KRX_EQUITY_DIR"]),
            rel(source_paths["BRONZE_MACRO_DIR"]),
            rel(source_paths["BRONZE_RAW_ARTICLES_DIR"]),
            rel(source_paths["BRONZE_CORPORATE_ACTIONS_DIR"]),
            rel(source_paths["SILVER_NEWS_SENTIMENT_DIR"]),
            rel(source_paths["SILVER_SOCIAL_SENTIMENT_DIR"]),
            rel(source_paths["SILVER_PROCESSED_ARTICLES_DIR"]),
            rel(source_paths["SILVER_ANALYST_RATINGS_DIR"]),
            rel(source_paths["SILVER_SEC_EXTRACTIONS_DIR"]),
            rel(source_paths["SILVER_SEC_FINANCIALS_DIR"]),
            rel(source_paths["SILVER_CORPORATE_ACTIONS_DIR"]),
            rel(source_paths["SILVER_CORPORATE_ACTIONS_DIR"].parent / "corporate_action_coverage"),
            rel(source_paths["GOLD_FEATURES_DIR"]),
            rel(source_paths["PLATINUM_PREDICTIONS_DIR"]),
        }
        actual = {ds.path for ds in ALL_DATASETS}
        assert actual == expected, f"Catalog paths drifted from core.paths constants: {actual ^ expected}"

        repository_data = runtime_paths.PROJECT_ROOT / "data"
        repository_logs = runtime_paths.PROJECT_ROOT / "logs"
        for name, value in vars(runtime_paths).items():
            if isinstance(value, Path):
                assert not value.is_relative_to(repository_data), f"{name} is not isolated during catalog test: {value}"
                assert not value.is_relative_to(repository_logs), f"{name} is not isolated during catalog test: {value}"


class TestDatasetContent:
    def test_all_datasets_have_columns(self) -> None:
        for ds in ALL_DATASETS:
            assert len(ds.columns) > 0, f"Dataset {ds.name} has no columns"

    def test_shared_feature_and_prediction_catalogs_include_identity(self) -> None:
        expected_identity = {"market": "string", "mic": "string", "symbol": "string", "ticker": "string", "identity_schema_version": "int8"}
        for name in ("technical_features", "predictions"):
            dataset = next(ds for ds in ALL_DATASETS if ds.name == name)
            columns = {column.name: column.dtype for column in dataset.columns}
            assert {column: columns.get(column) for column in expected_identity} == expected_identity

    def test_action_coverage_catalog_is_separate_and_versioned(self) -> None:
        dataset = next(ds for ds in ALL_DATASETS if ds.name == "corporate_action_coverage")
        columns = {column.name: column.dtype for column in dataset.columns}
        assert dataset.layer == "silver"
        assert dataset.partition == "coverage_end"
        assert columns["market"] == "string"
        assert columns["mic"] == "string"
        assert columns["symbol"] == "string"
        assert columns["source_available_at"] == "datetime"
        assert columns["source_valid_until"] == "datetime"
        assert columns["coverage_schema_version"] == "int8"
        assert "source_valid_until" in [column.name for column in dataset.columns]

    def test_gold_features_catalog_source_proof_metadata_is_explicit(self) -> None:
        dataset = next(ds for ds in ALL_DATASETS if ds.name == "technical_features")
        columns = {column.name: column.dtype for column in dataset.columns}
        assert columns["action_source_valid_until"] == "datetime"
        assert columns["action_source_available_at"] == "datetime"
        assert columns["action_coverage_source"] == "string"
        assert columns["action_coverage_start"] == "date"
        assert columns["action_coverage_end"] == "date"
        assert columns["action_coverage_schema_version"] == "int8"

    def test_gold_catalog_includes_all_action_proof_columns_and_identity(self) -> None:
        from equity_lake.core.availability import FEATURE_ACTION_COVERAGE_PROOF_COLUMNS  # type: ignore[import-untyped]

        dataset = next(ds for ds in ALL_DATASETS if ds.name == "technical_features")
        catalog_columns = {column.name for column in dataset.columns}
        proof_identity_columns = {"action_coverage_market", "action_coverage_mic", "action_coverage_symbol"}
        assert proof_identity_columns.issubset(catalog_columns)
        assert set(FEATURE_ACTION_COVERAGE_PROOF_COLUMNS).issubset(catalog_columns)

    def test_all_datasets_have_description(self) -> None:
        for ds in ALL_DATASETS:
            assert len(ds.description) > 10, f"Dataset {ds.name} has empty/short description"

    def test_all_datasets_declare_delta_format(self) -> None:
        # Cataloged lake tables are Delta tables (deltalake writes with Parquet
        # data files); declaring "parquet" here drifted from reality and only
        # worked because duckdb_scan_for() auto-detects.
        for ds in ALL_DATASETS:
            assert ds.format == "delta", f"Dataset {ds.name} has format {ds.format!r}, expected 'delta'"

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
        valid = {"string", "date", "datetime", "float64", "int64", "int32", "int16", "int8"}
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
