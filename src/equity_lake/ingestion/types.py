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
    "us_sec_financials",
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
    "us_sec_financials",
    "bronze_raw_articles",
    "silver_processed_articles",
}

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
    "bronze_raw_articles": "01_bronze/raw_articles",
    # Silver — structured
    "us_news": "02_silver/news_sentiment",
    "us_social_sentiment": "02_silver/social_sentiment",
    "us_analyst_ratings": "02_silver/analyst_ratings",
    "us_sec_financials": "02_silver/sec_financials",
    # Silver — unstructured
    "silver_processed_articles": "02_silver/processed_articles",
    # Gold
    "features": "03_gold/features",
    # Platinum
    "predictions": "04_platinum/predictions",
}


__all__ = ["Market", "VALID_MARKETS", "MARKET_DIR_MAP"]
