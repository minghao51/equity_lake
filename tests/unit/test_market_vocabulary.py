"""Market-vocabulary parity guards (ADR-0010; handoff 20260910-01 Wave B).

Locks the three market vocabularies together so registry/dir-map drift fails
fast in unit tests — the drift guard that makes the Tier-1 derivation
(ADR-0012) safe.
"""

from equity_lake.ingestion.router import MARKET_REGISTRY
from equity_lake.ingestion.types import MARKET_DIR_MAP, VALID_MARKETS


def test_market_registry_covers_valid_markets() -> None:
    """Every valid market has exactly one registered source fetcher."""
    assert set(MARKET_REGISTRY) == VALID_MARKETS


def test_market_dir_map_partitions_valid_markets() -> None:
    """The directory map covers every market; features/predictions are routes, not markets."""
    assert set(MARKET_DIR_MAP) - {"features", "predictions"} == VALID_MARKETS


def test_declared_contracts_read_the_canonical_vocabulary_not_the_patchable_registry() -> None:
    """A narrowed `paths.PRICE_MARKETS` must not leak into static declarations.

    Regression: `market_contracts` used to import `PRICE_MARKETS` by name, so a test that
    scoped the registry to one market and imported the module first made the declared table
    raise `KeyError: 'cn_ashare'` — a failure that only appeared when that test ran alone.
    """
    from equity_lake.core import market_contracts, paths

    assert set(market_contracts.MARKET_CONTRACTS) == {entry.market for entry in paths.CANONICAL_PRICE_MARKETS.values()}
    assert not hasattr(market_contracts, "PRICE_MARKETS"), "the patchable registry must not be bound by name"
