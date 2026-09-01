"""Canonical filesystem paths for the project.

All constants are computed from ``PROJECT_ROOT`` (derived from ``__file__``).
No filesystem I/O happens at import time — call :func:`ensure_dirs` at
application startup to create directories.

This module is also the single home of the market vocabulary (ADR-0010):
the :data:`PRICE_MARKETS` registry maps each price market to its directory
constant, short alias, exchange MIC codes, and timezone. Every other module
(calendar, ingestion routing, storage views, monitoring, backtesting, CLI)
derives its market metadata from this registry.

Medallion layers
----------------
Storage follows a four-layer medallion architecture:

- **Bronze** (``01_bronze/``) — immutable raw data
- **Silver** (``02_silver/``) — validated, cleaned, deduped
- **Gold** (``03_gold/``) — feature engineering output
- **Platinum** (``04_platinum/``) — ML predictions and signals

Short aliases (``US_NEWS_DIR``, ``US_SOCIAL_SENTIMENT_DIR``,
``SEC_EXTRACTIONS_DIR``, …) point at the same medallion locations and are
used directly at several call sites.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"

DATA_DIR = PROJECT_ROOT / "data"
LAKE_DIR = PROJECT_ROOT / "data" / "lake"
LOGS_DIR = PROJECT_ROOT / "logs"
MODELS_DIR = PROJECT_ROOT / "data" / "models"

# ---------------------------------------------------------------------------
# Medallion layer roots
# ---------------------------------------------------------------------------
BRONZE_DIR = LAKE_DIR / "01_bronze"
SILVER_DIR = LAKE_DIR / "02_silver"
GOLD_DIR = LAKE_DIR / "03_gold"
PLATINUM_DIR = LAKE_DIR / "04_platinum"

# ---------------------------------------------------------------------------
# Bronze layer (01_bronze/) — immutable raw data
# ---------------------------------------------------------------------------
BRONZE_MARKET_DATA_DIR = BRONZE_DIR / "market_data"
US_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "us_equity"
CN_ASHARE_DIR = BRONZE_MARKET_DATA_DIR / "cn_ashare"
HK_SG_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "hk_sg_equity"
JPX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "jpx_equity"
KRX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "krx_equity"
BRONZE_RAW_ARTICLES_DIR = BRONZE_DIR / "raw_articles"
BRONZE_MACRO_DIR = BRONZE_DIR / "macro"

# ---------------------------------------------------------------------------
# Silver layer (02_silver/) — validated, cleaned, deduped
# ---------------------------------------------------------------------------
SILVER_NEWS_SENTIMENT_DIR = SILVER_DIR / "news_sentiment"
SILVER_SOCIAL_SENTIMENT_DIR = SILVER_DIR / "social_sentiment"
SILVER_PROCESSED_ARTICLES_DIR = SILVER_DIR / "processed_articles"
SILVER_SEC_EXTRACTIONS_DIR = SILVER_DIR / "sec_extractions"
SILVER_ANALYST_RATINGS_DIR = SILVER_DIR / "analyst_ratings"
SILVER_SEC_FINANCIALS_DIR = SILVER_DIR / "sec_financials"

# ---------------------------------------------------------------------------
# Gold layer (03_gold/) — feature engineering output
# ---------------------------------------------------------------------------
GOLD_FEATURES_DIR = GOLD_DIR / "features"

# ---------------------------------------------------------------------------
# Platinum layer (04_platinum/) — ML predictions and signals
# ---------------------------------------------------------------------------
PLATINUM_PREDICTIONS_DIR = PLATINUM_DIR / "predictions"

# ---------------------------------------------------------------------------
# Auxiliary tables (outside the medallion lake) — signal history bookkeeping
# ---------------------------------------------------------------------------
SIGNALS_DIR = DATA_DIR / "signals"
# Research artifacts (FindingCards, backtest & risk reports) — NOT cataloged.
FINDINGS_DIR = DATA_DIR / "findings"
# Data-quality profiles written by the explicit `equity validate profile` CLI path
# (in-ingest validation profiles stay in memory) — NOT cataloged.
PROFILES_DIR = DATA_DIR / "profiles"
# Default DuckDB scratch database for `equity query` — NOT cataloged.
DUCKDB_DEFAULT_PATH = DATA_DIR / "equity_data.duckdb"

# ---------------------------------------------------------------------------
# Short aliases for silver tables — used directly at their call sites
# (ingest CLI, sentiment signal generator, SEC processor)
# ---------------------------------------------------------------------------
US_NEWS_DIR = SILVER_NEWS_SENTIMENT_DIR
US_SOCIAL_SENTIMENT_DIR = SILVER_SOCIAL_SENTIMENT_DIR
SEC_EXTRACTIONS_DIR = SILVER_SEC_EXTRACTIONS_DIR


# ---------------------------------------------------------------------------
# Market vocabulary registry (ADR-0010)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PriceMarket:
    """Static metadata for one price market (a :data:`PRICE_MARKETS` entry).

    The bronze directory is stored as the *attribute name* of its paths
    constant and resolved at call time by :func:`market_dir`, so runtime
    patches of the module-level ``*_DIR`` constants keep working.
    """

    market: str  # canonical long key (also the bronze directory name)
    alias: str  # deprecated short key, accepted at boundaries only
    dir_attr: str  # attribute name of the bronze directory constant below
    exchanges: tuple[str, ...]  # exchange_calendars MIC codes
    timezone: str  # IANA timezone


PRICE_MARKETS: dict[str, PriceMarket] = {
    entry.market: entry
    for entry in (
        PriceMarket("us_equity", "us", "US_EQUITY_DIR", ("XNYS",), "America/New_York"),
        PriceMarket("cn_ashare", "cn", "CN_ASHARE_DIR", ("XSHG",), "Asia/Shanghai"),
        PriceMarket("hk_sg_equity", "hk_sg", "HK_SG_EQUITY_DIR", ("XHKG", "XSES"), "Asia/Hong_Kong"),
        PriceMarket("jpx_equity", "jpx", "JPX_EQUITY_DIR", ("JPX",), "Asia/Tokyo"),
        PriceMarket("krx_equity", "krx", "KRX_EQUITY_DIR", ("XKRX",), "Asia/Seoul"),
    )
}

# Derived alias maps — never hand-maintained (ADR-0010 Decision 2).
LONG_TO_SHORT: dict[str, str] = {entry.market: entry.alias for entry in PRICE_MARKETS.values()}
SHORT_TO_LONG: dict[str, str] = {entry.alias: entry.market for entry in PRICE_MARKETS.values()}

# The ten single-form dataset identifiers that share the ingestion market
# vocabulary (VALID_MARKETS) but have no price-market duality (ADR-0010:
# out of scope for aliasing). ``ingestion.types`` re-exports this as
# OPTIONAL_ENRICHMENT_MARKETS; ``core.settings`` validates ``default_markets``
# against it.
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


def canonical_market(market: str) -> str:
    """Return the canonical long key for a price-market identifier.

    Accepts the canonical long form (``us_equity``) and the deprecated short
    alias (``us``). Anything else — including the enrichment dataset
    identifiers — raises ``ValueError``; a typo must fail loudly, never
    silently map to a calendar-less no-op (the ``_subtract_trading_days``
    infinite-loop root cause, ADR-0010).
    """
    if market in PRICE_MARKETS:
        return market
    if market in SHORT_TO_LONG:
        return SHORT_TO_LONG[market]
    raise ValueError(f"Unknown price market: {market!r}. Valid: {', '.join(PRICE_MARKETS)} (aliases: {', '.join(SHORT_TO_LONG)})")


def market_dir(market: str) -> Path:
    """Bronze directory for a price market, resolved at call time.

    The registry stores the *attribute name* of the directory constant, so the
    lookup goes through ``getattr`` on this module — runtime patches of the
    module-level ``*_DIR`` constants (e.g. tests pointing them at tmp dirs)
    are honoured.
    """
    entry = PRICE_MARKETS[canonical_market(market)]
    return cast(Path, getattr(sys.modules[__name__], entry.dir_attr))


def ensure_dirs() -> None:
    """Create all required runtime directories.

    Safe to call multiple times. Intended for CLI entry-points only.
    """
    for d in (LAKE_DIR, LOGS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


__all__ = [
    "BRONZE_DIR",
    "BRONZE_MACRO_DIR",
    "BRONZE_MARKET_DATA_DIR",
    "BRONZE_RAW_ARTICLES_DIR",
    "CN_ASHARE_DIR",
    "CONFIG_DIR",
    "DATA_DIR",
    "DUCKDB_DEFAULT_PATH",
    "FINDINGS_DIR",
    "GOLD_DIR",
    "GOLD_FEATURES_DIR",
    "HK_SG_EQUITY_DIR",
    "JPX_EQUITY_DIR",
    "KRX_EQUITY_DIR",
    "LAKE_DIR",
    "LONG_TO_SHORT",
    "LOGS_DIR",
    "MODELS_DIR",
    "OPTIONAL_ENRICHMENT_MARKETS",
    "PLATINUM_DIR",
    "PLATINUM_PREDICTIONS_DIR",
    "PRICE_MARKETS",
    "PriceMarket",
    "PROJECT_ROOT",
    "PROFILES_DIR",
    "SEC_EXTRACTIONS_DIR",
    "SHORT_TO_LONG",
    "SIGNALS_DIR",
    "SILVER_ANALYST_RATINGS_DIR",
    "SILVER_DIR",
    "SILVER_NEWS_SENTIMENT_DIR",
    "SILVER_PROCESSED_ARTICLES_DIR",
    "SILVER_SEC_EXTRACTIONS_DIR",
    "SILVER_SEC_FINANCIALS_DIR",
    "SILVER_SOCIAL_SENTIMENT_DIR",
    "US_EQUITY_DIR",
    "US_NEWS_DIR",
    "US_SOCIAL_SENTIMENT_DIR",
    "canonical_market",
    "ensure_dirs",
    "market_dir",
]
