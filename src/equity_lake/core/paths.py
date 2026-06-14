"""Canonical filesystem paths for the project.

All constants are computed from ``PROJECT_ROOT`` (derived from ``__file__``).
No filesystem I/O happens at import time — call :func:`ensure_dirs` at
application startup to create directories.
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

US_EQUITY_DIR = LAKE_DIR / "us_equity"
CN_ASHARE_DIR = LAKE_DIR / "cn_ashare"
HK_SG_EQUITY_DIR = LAKE_DIR / "hk_sg_equity"
JPX_EQUITY_DIR = LAKE_DIR / "jpx_equity"
KRX_EQUITY_DIR = LAKE_DIR / "krx_equity"
MACRO_INDICATORS_DIR = LAKE_DIR / "macro_indicators"
US_NEWS_DIR = LAKE_DIR / "us_news"
US_SOCIAL_SENTIMENT_DIR = LAKE_DIR / "us_social_sentiment"
BRONZE_DIR = LAKE_DIR / "bronze"
SILVER_DIR = LAKE_DIR / "silver"
BRONZE_RAW_ARTICLES_DIR = BRONZE_DIR / "raw_articles"
SILVER_PROCESSED_ARTICLES_DIR = SILVER_DIR / "processed_articles"


def ensure_dirs() -> None:
    """Create all required runtime directories.

    Safe to call multiple times. Intended for CLI entry-points only.
    """
    for d in (LAKE_DIR, LOGS_DIR, MODELS_DIR):
        d.mkdir(parents=True, exist_ok=True)


__all__ = [
    "BASE_DIR",
    "BRONZE_DIR",
    "BRONZE_RAW_ARTICLES_DIR",
    "CN_ASHARE_DIR",
    "CONFIG_DIR",
    "DATA_DIR",
    "HK_SG_EQUITY_DIR",
    "JPX_EQUITY_DIR",
    "KRX_EQUITY_DIR",
    "LAKE_DIR",
    "LOGS_DIR",
    "MACRO_INDICATORS_DIR",
    "MODELS_DIR",
    "PROJECT_ROOT",
    "SILVER_DIR",
    "SILVER_PROCESSED_ARTICLES_DIR",
    "US_EQUITY_DIR",
    "US_NEWS_DIR",
    "US_SOCIAL_SENTIMENT_DIR",
    "ensure_dirs",
]
