"""Drift guards for the canonical ingestion type constants.

These constants are referenced from multiple modules (orchestrator, pipeline).
The assertions here lock the classification so a future edit to one set without
the other fails loudly rather than silently mis-routing a market.
"""

from __future__ import annotations

from equity_lake.ingestion.types import (
    OPTIONAL_ENRICHMENT_MARKETS,
    REQUIRED_PRICE_MARKETS,
    VALID_MARKETS,
    SourceOutcome,
    SourceStatus,
)


def test_price_and_enrichment_sets_partition_valid_markets():
    """Every valid market is either required-price or optional-enrichment, never both."""
    assert REQUIRED_PRICE_MARKETS.isdisjoint(OPTIONAL_ENRICHMENT_MARKETS)
    assert set(VALID_MARKETS) == REQUIRED_PRICE_MARKETS | OPTIONAL_ENRICHMENT_MARKETS


def test_required_price_markets_is_stable():
    """The five equity markets are the backbone of the pipeline — lock the membership."""
    assert frozenset({"us", "cn", "hk_sg", "jpx", "krx"}) == REQUIRED_PRICE_MARKETS


def test_source_outcome_succeeded_semantics():
    """WRITTEN and SKIPPED_EXISTING both permit downstream stages; FAILED does not."""
    assert SourceOutcome(SourceStatus.WRITTEN).succeeded is True
    assert SourceOutcome(SourceStatus.SKIPPED_EXISTING).succeeded is True
    assert SourceOutcome(SourceStatus.FAILED).succeeded is False
    assert SourceOutcome(SourceStatus.FAILED, error="boom").succeeded is False


def test_source_status_serializes_to_string_value():
    """The published pipeline payload relies on ``.value`` being a plain string."""
    assert SourceStatus.WRITTEN.value == "written"
    assert SourceStatus.SKIPPED_EXISTING.value == "skipped_existing"
    assert SourceStatus.FAILED.value == "failed"
