"""Executable source routing, destination, and catalog contract checks."""

from equity_lake.catalog.datasets import ALL_DATASETS
from equity_lake.ingestion.router import MARKET_REGISTRY
from equity_lake.ingestion.types import MARKET_DIR_MAP, VALID_MARKETS


def test_every_routable_source_has_a_valid_destination() -> None:
    assert set(MARKET_REGISTRY) == VALID_MARKETS
    assert set(MARKET_REGISTRY) <= set(MARKET_DIR_MAP)
    assert all(MARKET_DIR_MAP[market].startswith(("01_bronze/", "02_silver/")) for market in MARKET_REGISTRY)
    assert set(MARKET_DIR_MAP) - {"features", "predictions"} == VALID_MARKETS


def test_market_destinations_match_catalog_paths() -> None:
    catalog_paths = {dataset.path.removeprefix("data/lake/").removesuffix("/") for dataset in ALL_DATASETS}
    assert set(MARKET_DIR_MAP.values()) - {"03_gold/features", "04_platinum/predictions"} <= catalog_paths
    assert {dataset.layer for dataset in ALL_DATASETS} == {"bronze", "silver", "gold", "platinum"}


def test_catalog_formats_match_runtime_writer_contract() -> None:
    assert all(dataset.format == "parquet" for dataset in ALL_DATASETS)
