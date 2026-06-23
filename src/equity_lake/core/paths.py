"""Canonical filesystem paths for the project.

All constants are computed from ``PROJECT_ROOT`` (derived from ``__file__``).
No filesystem I/O happens at import time — call :func:`ensure_dirs` at
application startup to create directories.

Medallion layers
----------------
Storage follows a four-layer medallion architecture:

- **Bronze** (``01_bronze/``) — immutable raw data
- **Silver** (``02_silver/``) — validated, cleaned, deduped
- **Gold** (``03_gold/``) — feature engineering output
- **Platinum** (``04_platinum/``) — ML predictions and signals

Legacy constant names (``US_EQUITY_DIR``, ``US_NEWS_DIR``, etc.) are kept
as aliases pointing to their new medallion locations for backward
compatibility.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = PROJECT_ROOT
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
# Backward-compatible aliases (deprecated — use medallion constants above)
# ---------------------------------------------------------------------------
MACRO_INDICATORS_DIR = BRONZE_MACRO_DIR
US_NEWS_DIR = SILVER_NEWS_SENTIMENT_DIR
US_SOCIAL_SENTIMENT_DIR = SILVER_SOCIAL_SENTIMENT_DIR
ANALYST_RATINGS_DIR = SILVER_ANALYST_RATINGS_DIR
SEC_EXTRACTIONS_DIR = SILVER_SEC_EXTRACTIONS_DIR
SEC_FINANCIALS_DIR = SILVER_SEC_FINANCIALS_DIR


def ensure_dirs() -> None:
    """Create all required runtime directories.

    Safe to call multiple times. Intended for CLI entry-points only.
    """
    for d in (LAKE_DIR, LOGS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


__all__ = [
    "ANALYST_RATINGS_DIR",
    "BASE_DIR",
    "BRONZE_DIR",
    "BRONZE_MACRO_DIR",
    "BRONZE_MARKET_DATA_DIR",
    "BRONZE_RAW_ARTICLES_DIR",
    "CN_ASHARE_DIR",
    "CONFIG_DIR",
    "DATA_DIR",
    "GOLD_DIR",
    "GOLD_FEATURES_DIR",
    "HK_SG_EQUITY_DIR",
    "JPX_EQUITY_DIR",
    "KRX_EQUITY_DIR",
    "LAKE_DIR",
    "LOGS_DIR",
    "MACRO_INDICATORS_DIR",
    "MODELS_DIR",
    "PLATINUM_DIR",
    "PLATINUM_PREDICTIONS_DIR",
    "PROJECT_ROOT",
    "SEC_EXTRACTIONS_DIR",
    "SEC_FINANCIALS_DIR",
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
    "ensure_dirs",
]
