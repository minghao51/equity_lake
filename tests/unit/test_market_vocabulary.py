"""Market-vocabulary parity guards (ADR-0010; handoff 20260910-01 Wave B).

Locks the three market vocabularies together so registry/dir-map drift fails
fast in unit tests — the drift guard that makes the Tier-1 derivation
(ADR-0012) safe.
"""

from equity_lake.ingestion.router import MARKET_REGISTRY  # type: ignore[import-untyped]
from equity_lake.ingestion.types import MARKET_DIR_MAP, VALID_MARKETS  # type: ignore[import-untyped]


def test_market_registry_covers_valid_markets() -> None:
    """Every valid market has exactly one registered source fetcher."""
    assert set(MARKET_REGISTRY) == VALID_MARKETS


def test_market_dir_map_partitions_valid_markets() -> None:
    """The directory map covers every market; features/predictions are routes, not markets."""
    assert set(MARKET_DIR_MAP) - {"features", "predictions"} == VALID_MARKETS
