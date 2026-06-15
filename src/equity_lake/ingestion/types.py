"""Type definitions for the ingestion module."""

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
    "bronze_raw_articles",
    "silver_processed_articles",
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
    "bronze_raw_articles",
    "silver_processed_articles",
}

# Market to directory mapping
MARKET_DIR_MAP: dict[str, str] = {
    "us": "us_equity",
    "cn": "cn_ashare",
    "hk_sg": "hk_sg_equity",
    "jpx": "jpx_equity",
    "krx": "krx_equity",
    "macro": "macro_indicators",
    "us_news": "us_news",
    "us_social_sentiment": "us_social_sentiment",
    "rss_news": "bronze/raw_articles",
    "reddit_posts": "bronze/raw_articles",
    "stocktwits_messages": "bronze/raw_articles",
    "us_earnings_transcripts": "bronze/raw_articles",
    "us_analyst_ratings": "us_analyst_ratings",
    "sec_filings_fulltext": "bronze/raw_articles",
    "bronze_raw_articles": "bronze/raw_articles",
    "silver_processed_articles": "silver/processed_articles",
}


__all__ = ["Market", "VALID_MARKETS", "MARKET_DIR_MAP"]
