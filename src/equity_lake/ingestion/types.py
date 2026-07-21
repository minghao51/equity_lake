"""Type definitions for the ingestion module."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal

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

# Market to directory mapping (medallion paths)
MARKET_DIR_MAP: dict[str, str] = {
    # Bronze — market data
    "us": "01_bronze/market_data/us_equity",
    "cn": "01_bronze/market_data/cn_ashare",
    "hk_sg": "01_bronze/market_data/hk_sg_equity",
    "jpx": "01_bronze/market_data/jpx_equity",
    "krx": "01_bronze/market_data/krx_equity",
    "macro": "01_bronze/macro",
    # Bronze — unstructured
    "rss_news": "01_bronze/raw_articles",
    "reddit_posts": "01_bronze/raw_articles",
    "stocktwits_messages": "01_bronze/raw_articles",
    "us_earnings_transcripts": "01_bronze/raw_articles",
    "sec_filings_fulltext": "01_bronze/raw_articles",
    # Silver — structured
    "us_news": "02_silver/news_sentiment",
    "us_social_sentiment": "02_silver/social_sentiment",
    "us_analyst_ratings": "02_silver/analyst_ratings",
    "us_sec_financials": "02_silver/sec_financials",
    # Gold
    "features": "03_gold/features",
    # Platinum
    "predictions": "04_platinum/predictions",
}


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
    "SourceStatus",
    "SourceOutcome",
]
