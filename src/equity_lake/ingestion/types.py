"""Type definitions for the ingestion module.

Market vocabulary: the five price markets are keyed by their canonical long
form (ADR-0010), derived from the :data:`equity_lake.core.paths.PRICE_MARKETS`
registry together with their directory mapping. The ten enrichment dataset
identifiers keep their single literal form. Short price-market aliases are
accepted only at boundaries (CLI flags, settings) and normalized there via
:func:`normalize_markets` / ``canonical_market``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from equity_lake.core.paths import (
    BRONZE_MACRO_DIR,
    BRONZE_RAW_ARTICLES_DIR,
    GOLD_FEATURES_DIR,
    LAKE_DIR,
    OPTIONAL_ENRICHMENT_MARKETS,
    PLATINUM_PREDICTIONS_DIR,
    PRICE_MARKETS,
    SHORT_TO_LONG,
    SILVER_ANALYST_RATINGS_DIR,
    SILVER_NEWS_SENTIMENT_DIR,
    SILVER_SEC_FINANCIALS_DIR,
    SILVER_SOCIAL_SENTIMENT_DIR,
    canonical_market,
    market_dir,
)

if TYPE_CHECKING:
    from equity_lake.core.config import TickerConfig

# Valid market set for validation. Price entries are the canonical long keys
# derived from the registry; enrichment entries are single-form dataset ids.
VALID_MARKETS: set[str] = set(PRICE_MARKETS) | set(OPTIONAL_ENRICHMENT_MARKETS)

# Canonical market classification — single source of truth for pipeline routing.
# REQUIRED_PRICE_MARKETS block features/ML on failure; OPTIONAL_ENRICHMENT_MARKETS
# only degrade enrichment. Together they partition VALID_MARKETS.
REQUIRED_PRICE_MARKETS: frozenset[str] = frozenset(PRICE_MARKETS)

# Unstructured (LLM-processed) and SEC-filing enrichment markets that trigger the
# optional bronze→silver processing stages in the EOD pipeline
# (``pipeline._run_ingestion_stage``). Defined beside the price/enrichment
# classification so the taxonomy lives in one place.
UNSTRUCTURED_MARKETS: frozenset[str] = frozenset({"rss_news", "reddit_posts", "stocktwits_messages", "us_earnings_transcripts"})
SEC_FILINGS_MARKETS: frozenset[str] = frozenset({"sec_filings_fulltext"})

# Mapping from a canonical price-market key to the ticker-config section key(s)
# that hold its instruments. Single-market price markets use the short alias
# (``config/tickers.yaml`` is a separate vocabulary, out of ADR-0010 scope);
# ``hk_sg_equity`` spans the separate ``hk`` and ``sg`` config sections.
PRICE_MARKET_CONFIG_KEYS: dict[str, tuple[str, ...]] = {
    "us_equity": ("us",),
    "cn_ashare": ("cn",),
    "hk_sg_equity": ("hk", "sg"),
    "jpx_equity": ("jpx",),
    "krx_equity": ("krx",),
}

# Markets whose fetchers honor explicit per-ticker overrides. All five price
# markets are ticker-scoped (W03/F04: explicit scope must reach every provider,
# not just US); the enrichment entries are the Finnhub/SEC sources whose fetch
# is ticker-scoped rather than a broad-market pull. Considered by
# :func:`resolve_explicit_scope` in both the parallel and serial paths.
EXPLICIT_TICKER_MARKETS: frozenset[str] = frozenset(
    {
        *REQUIRED_PRICE_MARKETS,
        "stocktwits_messages",
        "us_earnings_transcripts",
        "us_analyst_ratings",
        "sec_filings_fulltext",
        "us_sec_financials",
    }
)


def _rel(path: Path) -> str:
    """Relative medallion path string for a lake directory (single source: paths.py)."""
    return str(path.relative_to(LAKE_DIR))


# Market to directory mapping (medallion paths).
# Price-market entries are derived from the ``core/paths.py`` registry so there
# is one canonical source of truth for where each market is stored; enrichment
# entries stay literal (they are dataset->path routes, not market vocabulary).
MARKET_DIR_MAP: dict[str, str] = {
    # Bronze — market data (registry-derived)
    **{market: _rel(market_dir(market)) for market in PRICE_MARKETS},
    "macro": _rel(BRONZE_MACRO_DIR),
    # Bronze — unstructured
    "rss_news": _rel(BRONZE_RAW_ARTICLES_DIR),
    "reddit_posts": _rel(BRONZE_RAW_ARTICLES_DIR),
    "stocktwits_messages": _rel(BRONZE_RAW_ARTICLES_DIR),
    "us_earnings_transcripts": _rel(BRONZE_RAW_ARTICLES_DIR),
    "sec_filings_fulltext": _rel(BRONZE_RAW_ARTICLES_DIR),
    # Silver — structured
    "us_news": _rel(SILVER_NEWS_SENTIMENT_DIR),
    "us_social_sentiment": _rel(SILVER_SOCIAL_SENTIMENT_DIR),
    "us_analyst_ratings": _rel(SILVER_ANALYST_RATINGS_DIR),
    "us_sec_financials": _rel(SILVER_SEC_FINANCIALS_DIR),
    # Gold
    # NOTE: "features" / "predictions" are NOT markets (outside VALID_MARKETS) —
    # they are gold/platinum medallion table routes kept here so callers can
    # resolve their lake paths through the same map. Do not add them to
    # VALID_MARKETS.
    "features": _rel(GOLD_FEATURES_DIR),
    # Platinum
    "predictions": _rel(PLATINUM_PREDICTIONS_DIR),
}

# Reverse lookup (medallion path -> market key), derived from MARKET_DIR_MAP so
# it cannot drift from the forward mapping.
MARKET_DIR_REVERSE: dict[str, str] = {v: k for k, v in MARKET_DIR_MAP.items()}


def normalize_markets(markets: Iterable[str]) -> list[str]:
    """Canonicalize market identifiers at the pipeline boundary (ADR-0010).

    Short price-market aliases (``us``) are mapped to their canonical long keys
    (``us_equity``); long price keys and enrichment dataset identifiers pass
    through. Unknown keys raise ``ValueError`` so a typo fails loudly instead
    of silently fetching nothing.
    """
    normalized: list[str] = []
    for market in markets:
        if market in SHORT_TO_LONG:
            normalized.append(SHORT_TO_LONG[market])
        elif market in VALID_MARKETS:
            normalized.append(market)
        else:
            normalized.append(canonical_market(market))  # raises ValueError on unknown keys
    return normalized


def resolve_explicit_scope(
    markets: list[str],
    tickers: list[str] | None,
    ticker_config: TickerConfig | None,
) -> dict[str, list[str] | None]:
    """Resolve an explicit ticker request to a per-market scope (W03/F04/F05).

    The returned mapping has an entry for every requested market (deduplicated
    by key):

    * ``tickers is None`` -> every market maps to ``None`` (use the configured
      default). An unspecified request is never turned into an explicit list.
    * exactly one requested price market -> every explicit ticker is assigned to
      it, preserving arbitrary overrides for US, CN, HK/SG, JPX, and KRX.
    * multiple requested price markets -> membership is resolved from the
      approved ``TickerConfig``; an unresolved or ambiguous ticker raises
      ``ValueError`` (no fan-out to every provider, no guessing).
    * ticker-scoped enrichment markets keep today's behavior: the raw explicit
      list is preserved (deduplicated in request order). Non-ticker-scoped
      enrichment markets stay ``None`` so their own defaults are unchanged.
    * ``[]`` is preserved and never becomes ``None``: an explicit empty request
      can never broaden into the configured universe.

    Raises:
        ValueError: An explicit ticker crossing multiple price markets cannot be
            assigned to exactly one of them (unresolved or ambiguous symbol).
    """
    if tickers is None:
        return {market: None for market in markets}

    unique_tickers = list(dict.fromkeys(tickers))
    resolved: dict[str, list[str] | None] = {market: ([] if market in REQUIRED_PRICE_MARKETS else None) for market in markets}
    price_markets = list(dict.fromkeys(market for market in markets if market in REQUIRED_PRICE_MARKETS))

    if len(price_markets) == 1:
        resolved[price_markets[0]] = list(unique_tickers)
    elif len(price_markets) > 1:
        if unique_tickers and ticker_config is None:
            # Multiple markets need approved metadata; load the shared config only
            # when the caller did not supply one (lazy: keeps None requests pure).
            from equity_lake.core.config import TickerConfig as _TickerConfig

            ticker_config = _TickerConfig()
        matched: dict[str, list[str]] = {}
        for ticker in unique_tickers:
            matched[ticker] = [
                market
                for market in price_markets
                if ticker_config is not None
                and any(ticker_config.get_ticker_metadata(ticker, market=key) is not None for key in PRICE_MARKET_CONFIG_KEYS.get(market, ()))
            ]
        unresolved = sorted(ticker for ticker, owners in matched.items() if not owners)
        ambiguous = sorted(ticker for ticker, owners in matched.items() if len(owners) > 1)
        if unresolved or ambiguous:
            details: list[str] = []
            if unresolved:
                details.append(f"unresolved: {', '.join(unresolved)}")
            if ambiguous:
                details.append(f"ambiguous: {', '.join(ambiguous)}")
            raise ValueError(
                "Explicit ticker scope cannot be resolved across multiple price markets "
                f"({', '.join(price_markets)}); {'; '.join(details)}. "
                "Request a single price market for arbitrary ticker overrides."
            )
        assigned: dict[str, list[str]] = {market: [] for market in price_markets}
        for ticker, owners in matched.items():
            assigned[owners[0]].append(ticker)
        for market in price_markets:
            resolved[market] = assigned[market]

    # Ticker-scoped enrichment markets keep their existing explicit-list behavior.
    for market in markets:
        if market not in REQUIRED_PRICE_MARKETS and market in EXPLICIT_TICKER_MARKETS:
            resolved[market] = list(unique_tickers)
    return resolved


class SourceStatus(str, Enum):
    """Outcome status for a single market in an ingestion run.

    ``str`` mixin so ``.value`` serializes cleanly into the pipeline's published
    ``results["ingestion"]["markets"]`` payload.

    W02 (F08/F10/F25): every terminal condition has its own status. Optionality
    never hides a failure — an optional market that fails reports ``FAILED`` and
    is classified as an optional failure by the pipeline, which decides blocking
    and exit status. ``SKIPPED_EXISTING`` is reserved for a verified existing
    partition; a genuine empty fetch is ``NO_DATA``; a simulated write in a
    direct dry-run is ``DRY_RUN`` (never ``written``).
    """

    WRITTEN = "written"  # newly fetched + persisted
    SKIPPED_EXISTING = "skipped_existing"  # partition already present (and validated)
    NO_DATA = "no_data"  # fetched successfully; the source returned zero rows
    DRY_RUN = "dry_run"  # direct dry-run: write-capable path simulated, nothing persisted
    FAILED = "failed"  # fetch failed, schema rejected, write returned False, or exception


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """Structured per-market ingestion outcome.

    Replaces the previous ``dict[str, bool]`` result shape so callers can
    distinguish a freshly-written partition from an idempotent skip — both of
    which leave downstream stages eligible to proceed.
    """

    status: SourceStatus
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        """True when downstream stages (features/ML) may proceed.

        ``WRITTEN`` and ``SKIPPED_EXISTING`` count as success; ``DRY_RUN`` also
        counts so direct dry-runs keep their zero-exit contract (while never
        claiming a write). ``NO_DATA`` and ``FAILED`` do not. Missing keys at
        call sites are treated as ``FAILED``.
        """
        return self.status in (SourceStatus.WRITTEN, SourceStatus.SKIPPED_EXISTING, SourceStatus.DRY_RUN)


__all__ = [
    "VALID_MARKETS",
    "REQUIRED_PRICE_MARKETS",
    "OPTIONAL_ENRICHMENT_MARKETS",
    "UNSTRUCTURED_MARKETS",
    "SEC_FILINGS_MARKETS",
    "EXPLICIT_TICKER_MARKETS",
    "PRICE_MARKET_CONFIG_KEYS",
    "MARKET_DIR_MAP",
    "MARKET_DIR_REVERSE",
    "SourceStatus",
    "SourceOutcome",
    "normalize_markets",
    "resolve_explicit_scope",
]
