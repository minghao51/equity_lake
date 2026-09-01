"""Type definitions for the ingestion module.

Market vocabulary: the five price markets are keyed by their canonical long
form (ADR-0010), derived from the :data:`equity_lake.core.paths.PRICE_MARKETS`
registry together with their directory mapping. The ten enrichment dataset
identifiers keep their single literal form. Short price-market aliases are
accepted only at boundaries (CLI flags, settings) and normalized there via
:func:`normalize_markets` / ``canonical_market``.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

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


class SourceStatus(str, Enum):
    """Outcome status for a single market in an ingestion run.

    ``str`` mixin so ``.value`` serializes cleanly into the pipeline's published
    ``results["ingestion"]["markets"]`` payload.
    """

    WRITTEN = "written"  # newly fetched + persisted
    SKIPPED_EXISTING = "skipped_existing"  # partition already present (and validated)
    FAILED = "failed"  # fetch failed, empty frame, write returned False, or exception


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

        Both ``WRITTEN`` and ``SKIPPED_EXISTING`` count as success; ``FAILED``
        does not. Missing keys at call sites are treated as ``FAILED``.
        """
        return self.status in (SourceStatus.WRITTEN, SourceStatus.SKIPPED_EXISTING)


__all__ = [
    "VALID_MARKETS",
    "REQUIRED_PRICE_MARKETS",
    "OPTIONAL_ENRICHMENT_MARKETS",
    "UNSTRUCTURED_MARKETS",
    "SEC_FILINGS_MARKETS",
    "MARKET_DIR_MAP",
    "MARKET_DIR_REVERSE",
    "SourceStatus",
    "SourceOutcome",
    "normalize_markets",
]
