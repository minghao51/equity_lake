"""Type definitions for the ingestion module."""

from typing import Literal

# Supported market identifiers
Market = Literal["us", "cn", "hk_sg", "macro", "us_news", "us_social_sentiment"]

# Valid market set for validation
VALID_MARKETS: set[Market] = {"us", "cn", "hk_sg", "macro", "us_news", "us_social_sentiment"}

# Market to directory mapping
MARKET_DIR_MAP: dict[str, str] = {
    "us": "us_equity",
    "cn": "cn_ashare",
    "hk_sg": "hk_sg_equity",
    "macro": "macro_indicators",
    "us_news": "us_news",
    "us_social_sentiment": "us_social_sentiment",
}


__all__ = ["Market", "VALID_MARKETS", "MARKET_DIR_MAP"]
