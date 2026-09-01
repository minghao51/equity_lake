"""Drift guards for the canonical ingestion type constants.

These constants are referenced from multiple modules (orchestrator, pipeline).
The assertions here lock the classification so a future edit to one set without
the other fails loudly rather than silently mis-routing a market.
"""

from __future__ import annotations

import pytest

from equity_lake.core.paths import (
    LONG_TO_SHORT,
    PRICE_MARKETS,
    canonical_market,
    market_dir,
)
from equity_lake.core.paths import OPTIONAL_ENRICHMENT_MARKETS as CORE_ENRICHMENT_MARKETS
from equity_lake.ingestion.types import (
    MARKET_DIR_MAP,
    OPTIONAL_ENRICHMENT_MARKETS,
    REQUIRED_PRICE_MARKETS,
    SEC_FILINGS_MARKETS,
    UNSTRUCTURED_MARKETS,
    VALID_MARKETS,
    SourceOutcome,
    SourceStatus,
    normalize_markets,
)


def test_price_and_enrichment_sets_partition_valid_markets():
    """Every valid market is either required-price or optional-enrichment, never both."""
    assert REQUIRED_PRICE_MARKETS.isdisjoint(OPTIONAL_ENRICHMENT_MARKETS)
    assert set(VALID_MARKETS) == REQUIRED_PRICE_MARKETS | OPTIONAL_ENRICHMENT_MARKETS


def test_required_price_markets_is_stable():
    """The five equity markets are the backbone of the pipeline — lock the membership.

    Price markets use the canonical long keys (ADR-0010) derived from the
    ``core/paths.py`` registry.
    """
    assert frozenset({"us_equity", "cn_ashare", "hk_sg_equity", "jpx_equity", "krx_equity"}) == REQUIRED_PRICE_MARKETS
    assert frozenset(PRICE_MARKETS) == REQUIRED_PRICE_MARKETS


def test_enrichment_markets_are_derived_from_core_registry():
    """OPTIONAL_ENRICHMENT_MARKETS is re-exported from core/paths.py — one definition."""
    assert OPTIONAL_ENRICHMENT_MARKETS is CORE_ENRICHMENT_MARKETS


def test_unstructured_and_sec_filing_markets_are_defined():
    """The pipeline's unstructured/SEC taxonomy lives beside the other market sets."""
    assert frozenset({"rss_news", "reddit_posts", "stocktwits_messages", "us_earnings_transcripts"}) == UNSTRUCTURED_MARKETS
    assert frozenset({"sec_filings_fulltext"}) == SEC_FILINGS_MARKETS
    # The two taxonomy buckets are disjoint and sit within the valid market set.
    assert UNSTRUCTURED_MARKETS.isdisjoint(SEC_FILINGS_MARKETS)
    assert UNSTRUCTURED_MARKETS <= VALID_MARKETS
    assert SEC_FILINGS_MARKETS <= VALID_MARKETS


def test_market_dir_map_price_entries_are_registry_derived():
    """Price entries in MARKET_DIR_MAP are keyed by canonical long keys and point at the registry dirs."""
    for market in REQUIRED_PRICE_MARKETS:
        assert MARKET_DIR_MAP[market] == f"01_bronze/market_data/{market}"
        assert MARKET_DIR_MAP[market] in str(market_dir(market))
    assert "us" not in MARKET_DIR_MAP and "hk_sg" not in MARKET_DIR_MAP


class TestNormalizeMarkets:
    def test_short_aliases_canonicalize_to_long_keys(self) -> None:
        assert normalize_markets(["us", "cn", "hk_sg"]) == ["us_equity", "cn_ashare", "hk_sg_equity"]

    def test_long_keys_and_dataset_ids_pass_through(self) -> None:
        assert normalize_markets(["us_equity", "macro", "us_news"]) == ["us_equity", "macro", "us_news"]

    def test_unknown_keys_raise(self) -> None:
        with pytest.raises(ValueError, match="Unknown price market"):
            normalize_markets(["uss"])


class TestCanonicalMarket:
    def test_accepts_both_vocabularies(self) -> None:
        assert canonical_market("us") == canonical_market("us_equity") == "us_equity"
        assert LONG_TO_SHORT["hk_sg_equity"] == "hk_sg"


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
