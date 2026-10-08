"""Drift guards for the canonical ingestion type constants.

These constants are referenced from multiple modules (orchestrator, pipeline).
The assertions here lock the classification so a future edit to one set without
the other fails loudly rather than silently mis-routing a market.
"""

from __future__ import annotations

import pytest

from equity_lake.core.config import TickerConfig
from equity_lake.core.config_models import MarketConfig, TickerConfigRoot, TickerMetadata
from equity_lake.core.paths import (
    LONG_TO_SHORT,
    PRICE_MARKETS,
    canonical_market,
    market_dir,
)
from equity_lake.core.paths import OPTIONAL_ENRICHMENT_MARKETS as CORE_ENRICHMENT_MARKETS
from equity_lake.ingestion.types import (
    EXPLICIT_TICKER_MARKETS,
    MARKET_DIR_MAP,
    OPTIONAL_ENRICHMENT_MARKETS,
    PRICE_MARKET_CONFIG_KEYS,
    REQUIRED_PRICE_MARKETS,
    SEC_FILINGS_MARKETS,
    UNSTRUCTURED_MARKETS,
    VALID_MARKETS,
    SourceOutcome,
    SourceStatus,
    normalize_markets,
    resolve_explicit_scope,
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
    """WRITTEN/SKIPPED_EXISTING/DRY_RUN permit downstream stages; NO_DATA/FAILED do not."""
    assert SourceOutcome(SourceStatus.WRITTEN).succeeded is True
    assert SourceOutcome(SourceStatus.SKIPPED_EXISTING).succeeded is True
    # A direct dry-run keeps its zero-exit contract but never claims a write.
    assert SourceOutcome(SourceStatus.DRY_RUN).succeeded is True
    assert SourceOutcome(SourceStatus.FAILED).succeeded is False
    assert SourceOutcome(SourceStatus.FAILED, error="boom").succeeded is False
    # A genuine empty fetch is not a verified partition and not a write.
    assert SourceOutcome(SourceStatus.NO_DATA).succeeded is False


def test_source_status_distinguishes_no_data_dry_run_and_failure():
    """W02/F25: empty fetch, simulated write, and failure are never conflated."""
    assert SourceStatus.NO_DATA.value == "no_data"
    assert SourceStatus.DRY_RUN.value == "dry_run"
    assert SourceOutcome(SourceStatus.NO_DATA).status is not SourceStatus.SKIPPED_EXISTING
    assert SourceOutcome(SourceStatus.DRY_RUN).status is not SourceStatus.WRITTEN


def test_source_status_serializes_to_string_value():
    """The published pipeline payload relies on ``.value`` being a plain string."""
    assert SourceStatus.WRITTEN.value == "written"
    assert SourceStatus.SKIPPED_EXISTING.value == "skipped_existing"
    assert SourceStatus.FAILED.value == "failed"


def test_explicit_ticker_markets_cover_all_price_markets():
    """W03/F04: explicit scope must reach all five price markets, not just US."""
    assert REQUIRED_PRICE_MARKETS <= EXPLICIT_TICKER_MARKETS
    assert (
        frozenset({"stocktwits_messages", "us_earnings_transcripts", "us_analyst_ratings", "sec_filings_fulltext", "us_sec_financials"})
        <= EXPLICIT_TICKER_MARKETS
    )


def test_price_market_config_keys_cover_price_markets():
    """Every price market maps to at least one ticker-config section key."""
    assert set(PRICE_MARKET_CONFIG_KEYS) == REQUIRED_PRICE_MARKETS
    # hk_sg_equity spans the separate hk/sg config sections.
    assert PRICE_MARKET_CONFIG_KEYS["hk_sg_equity"] == ("hk", "sg")


def _ticker_metadata(symbol: str) -> TickerMetadata:
    return TickerMetadata(symbol=symbol, name=symbol, exchange="TEST", sector="Tech")


class TestResolveExplicitScope:
    def test_none_preserves_configured_default(self) -> None:
        scope = resolve_explicit_scope(["us_equity", "cn_ashare"], None, TickerConfig())
        assert scope == {"us_equity": None, "cn_ashare": None}

    def test_empty_list_is_preserved_and_never_becomes_none(self) -> None:
        scope = resolve_explicit_scope(["us_equity"], [], TickerConfig())
        assert scope == {"us_equity": []}

    def test_single_price_market_assigns_every_explicit_ticker(self) -> None:
        scope = resolve_explicit_scope(["us_equity"], ["AAPL", "NOT_IN_CONFIG"], TickerConfig())
        # Arbitrary single-market overrides are preserved (no metadata lookup).
        assert scope == {"us_equity": ["AAPL", "NOT_IN_CONFIG"]}

    def test_single_price_market_extends_to_all_five(self) -> None:
        for market in sorted(REQUIRED_PRICE_MARKETS):
            scope = resolve_explicit_scope([market], ["X1", "X2"], TickerConfig())
            assert scope == {market: ["X1", "X2"]}

    def test_multi_market_resolves_membership_from_config(self) -> None:
        config = TickerConfig()
        us_ticker = config.get_tickers_for_market("us", active_only=True)[0]
        cn_ticker = config.get_tickers_for_market("cn", active_only=True)[0]
        scope = resolve_explicit_scope(["us_equity", "cn_ashare"], [cn_ticker, us_ticker], config)
        assert scope["us_equity"] == [us_ticker]
        assert scope["cn_ashare"] == [cn_ticker]

    def test_hk_sg_membership_spans_hk_and_sg_sections(self) -> None:
        config = TickerConfig()
        us_ticker = config.get_tickers_for_market("us", active_only=True)[0]
        hk_ticker = config.get_tickers_for_market("hk", active_only=True)[0]
        sg_ticker = config.get_tickers_for_market("sg", active_only=True)[0]
        scope = resolve_explicit_scope(["hk_sg_equity", "us_equity"], [hk_ticker, sg_ticker, us_ticker], config)
        assert scope["hk_sg_equity"] == [hk_ticker, sg_ticker]
        assert scope["us_equity"] == [us_ticker]

    def test_unresolved_multi_market_ticker_raises(self) -> None:
        config = TickerConfig()
        us_ticker = config.get_tickers_for_market("us", active_only=True)[0]
        with pytest.raises(ValueError, match="unresolved: NOPE"):
            resolve_explicit_scope(["us_equity", "cn_ashare"], [us_ticker, "NOPE"], config)

    def test_ambiguous_multi_market_ticker_raises_without_fanout(self) -> None:
        config = TickerConfigRoot(
            markets={
                "us": MarketConfig(currency="USD", tickers=[_ticker_metadata("DUP")]),
                "cn": MarketConfig(currency="CNY", tickers=[_ticker_metadata("DUP")]),
            }
        )
        with pytest.raises(ValueError, match="ambiguous: DUP"):
            resolve_explicit_scope(["us_equity", "cn_ashare"], ["DUP"], config)

    def test_ticker_scoped_enrichment_keeps_raw_explicit_list(self) -> None:
        scope = resolve_explicit_scope(["us_equity", "stocktwits_messages"], ["AAPL"], TickerConfig())
        assert scope["us_equity"] == ["AAPL"]
        assert scope["stocktwits_messages"] == ["AAPL"]

    def test_non_ticker_scoped_enrichment_stays_none(self) -> None:
        scope = resolve_explicit_scope(["us_equity", "macro", "rss_news"], ["AAPL"], TickerConfig())
        assert scope["macro"] is None
        assert scope["rss_news"] is None

    def test_enrichment_only_request_keeps_raw_list(self) -> None:
        scope = resolve_explicit_scope(["stocktwits_messages"], ["AAPL"], TickerConfig())
        assert scope == {"stocktwits_messages": ["AAPL"]}

    def test_duplicate_tickers_are_deduplicated_in_order(self) -> None:
        scope = resolve_explicit_scope(["us_equity"], ["AAPL", "MSFT", "AAPL"], TickerConfig())
        assert scope["us_equity"] == ["AAPL", "MSFT"]
