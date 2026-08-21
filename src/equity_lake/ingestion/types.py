"""Type definitions for the ingestion module."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from equity_lake.core.paths import (
    BRONZE_MACRO_DIR,
    BRONZE_RAW_ARTICLES_DIR,
    CN_ASHARE_DIR,
    GOLD_FEATURES_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    LAKE_DIR,
    PLATINUM_PREDICTIONS_DIR,
    SILVER_ANALYST_RATINGS_DIR,
    SILVER_NEWS_SENTIMENT_DIR,
    SILVER_SEC_FINANCIALS_DIR,
    SILVER_SOCIAL_SENTIMENT_DIR,
    US_EQUITY_DIR,
)

# Supported market identifiers
Market = Literal[
    "us",
    "cn",
    "hk_sg",
    "jpx",
    "krx",
    "macro",
    "us_news",
    "us_social_sentiment",
    "rss_news",
    "reddit_posts",
    "stocktwits_messages",
    "us_earnings_transcripts",
    "us_analyst_ratings",
    "sec_filings_fulltext",
    "us_sec_financials",
]

# Valid market set for validation
VALID_MARKETS: set[Market] = {
    "us",
    "cn",
    "hk_sg",
    "jpx",
    "krx",
    "macro",
    "us_news",
    "us_social_sentiment",
    "rss_news",
    "reddit_posts",
    "stocktwits_messages",
    "us_earnings_transcripts",
    "us_analyst_ratings",
    "sec_filings_fulltext",
    "us_sec_financials",
}

# Canonical market classification — single source of truth for pipeline routing.
# REQUIRED_PRICE_MARKETS block features/ML on failure; OPTIONAL_ENRICHMENT_MARKETS
# only degrade enrichment. Together they partition VALID_MARKETS.
REQUIRED_PRICE_MARKETS: frozenset[str] = frozenset({"us", "cn", "hk_sg", "jpx", "krx"})
OPTIONAL_ENRICHMENT_MARKETS: frozenset[str] = frozenset(
    {
        "macro",
        "us_news",
        "us_social_sentiment",
        "rss_news",
        "reddit_posts",
        "stocktwits_messages",
        "us_earnings_transcripts",
        "us_analyst_ratings",
        "sec_filings_fulltext",
        "us_sec_financials",
    }
)


def _rel(path) -> str:
    """Relative medallion path string for a lake directory (single source: paths.py)."""
    return str(path.relative_to(LAKE_DIR))


# Market to directory mapping (medallion paths).
# Derived from ``equity_lake.core.paths`` constants so there is one canonical
# source of truth for where each market is stored.
MARKET_DIR_MAP: dict[str, str] = {
    # Bronze — market data
    "us": _rel(US_EQUITY_DIR),
    "cn": _rel(CN_ASHARE_DIR),
    "hk_sg": _rel(HK_SG_EQUITY_DIR),
    "jpx": _rel(JPX_EQUITY_DIR),
    "krx": _rel(KRX_EQUITY_DIR),
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
    "features": _rel(GOLD_FEATURES_DIR),
    # Platinum
    "predictions": _rel(PLATINUM_PREDICTIONS_DIR),
}

# Reverse lookup (medallion path -> market key), derived from MARKET_DIR_MAP so
# it cannot drift from the forward mapping.
MARKET_DIR_REVERSE: dict[str, str] = {v: k for k, v in MARKET_DIR_MAP.items()}


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
    "Market",
    "VALID_MARKETS",
    "REQUIRED_PRICE_MARKETS",
    "OPTIONAL_ENRICHMENT_MARKETS",
    "MARKET_DIR_MAP",
    "MARKET_DIR_REVERSE",
    "SourceStatus",
    "SourceOutcome",
]
