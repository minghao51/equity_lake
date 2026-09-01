"""Executable source routing, destination, and catalog contract checks."""

import ast
from pathlib import Path

import equity_lake
from equity_lake.catalog.datasets import ALL_DATASETS
from equity_lake.core.paths import PRICE_MARKETS, SHORT_TO_LONG, market_dir
from equity_lake.ingestion.router import MARKET_REGISTRY
from equity_lake.ingestion.types import MARKET_DIR_MAP, REQUIRED_PRICE_MARKETS, VALID_MARKETS


def test_every_routable_source_has_a_valid_destination() -> None:
    assert set(MARKET_REGISTRY) == VALID_MARKETS
    assert set(MARKET_REGISTRY) <= set(MARKET_DIR_MAP)
    assert all(MARKET_DIR_MAP[market].startswith(("01_bronze/", "02_silver/")) for market in MARKET_REGISTRY)
    assert set(MARKET_DIR_MAP) - {"features", "predictions"} == VALID_MARKETS


def test_price_market_vocabulary_is_registry_derived() -> None:
    """ADR-0010: the five price markets are long-keyed and pinned to the registry."""
    assert frozenset(PRICE_MARKETS) == REQUIRED_PRICE_MARKETS
    assert {"us_equity", "cn_ashare", "hk_sg_equity", "jpx_equity", "krx_equity"} == REQUIRED_PRICE_MARKETS
    assert set(MARKET_REGISTRY) & REQUIRED_PRICE_MARKETS == set(PRICE_MARKETS)
    assert {market: MARKET_DIR_MAP[market] for market in PRICE_MARKETS} == {market: f"01_bronze/market_data/{market}" for market in PRICE_MARKETS}
    # Registry dirs point at the real bronze constants.
    assert all(market_dir(market).name == market for market in PRICE_MARKETS)
    # Short aliases are only reachable through the registry alias map.
    assert set(SHORT_TO_LONG) == {"us", "cn", "hk_sg", "jpx", "krx"}
    assert all(alias not in MARKET_REGISTRY for alias in SHORT_TO_LONG)


def test_market_destinations_match_catalog_paths() -> None:
    catalog_paths = {dataset.path.removeprefix("data/lake/").removesuffix("/") for dataset in ALL_DATASETS}
    assert set(MARKET_DIR_MAP.values()) - {"03_gold/features", "04_platinum/predictions"} <= catalog_paths
    assert {dataset.layer for dataset in ALL_DATASETS} == {"bronze", "silver", "gold", "platinum"}


def test_catalog_formats_match_runtime_writer_contract() -> None:
    # The runtime writer contract is Delta (deltalake with Parquet data files);
    # the catalog must not drift back to declaring raw parquet.
    assert all(dataset.format == "delta" for dataset in ALL_DATASETS)


# =============================================================================
# ADR-0010 registry exclusivity contract
# =============================================================================

_PRICE_MARKET_KEYS = frozenset(PRICE_MARKETS) | frozenset(SHORT_TO_LONG)  # long + short forms


def _is_path_like(value: ast.expr) -> bool:
    """Heuristic: does this dict-literal value denote a filesystem path?"""
    if isinstance(value, ast.Attribute):
        return value.attr.endswith("_DIR") or value.attr.endswith("_PATH")
    if isinstance(value, ast.Call):
        # Path(...), _rel(...), market_dir(...) — calls that build paths.
        func = value.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        return name in {"Path", "_rel", "market_dir"}
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        # Medallion-style relative path string ("01_bronze/market_data/us_equity");
        # display names like "HK/SG Equity" contain whitespace and are not paths.
        return "/" in value.value and not any(ch.isspace() for ch in value.value)
    return False


def test_no_market_to_path_dict_literal_outside_the_registry() -> None:
    """ADR-0010: market->directory metadata lives ONLY in core/paths.py.

    A dict literal keyed by a price-market identifier (long or short form) with
    a path-like value must not exist outside the registry module — that is
    exactly the private-copy drift (bootstrap, duckdb views, health checks,
    backtest loader, dashboard) this decision deleted.
    """
    src_root = Path(equity_lake.__file__).parent
    registry_module = src_root / "core" / "paths.py"
    violations: list[str] = []

    for py_file in sorted(src_root.rglob("*.py")):
        if py_file == registry_module:
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for key, value in zip(node.keys, node.values, strict=True):
                if isinstance(key, ast.Constant) and key.value in _PRICE_MARKET_KEYS and _is_path_like(value):
                    violations.append(f"{py_file.relative_to(src_root.parent)}:{node.lineno} key {key.value!r}")

    assert not violations, "market->path dict literals must be derived from core.paths.PRICE_MARKETS (ADR-0010), found:\n" + "\n".join(violations)
