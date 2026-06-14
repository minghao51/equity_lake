"""Canonical column schemas and data-type definitions."""

from __future__ import annotations

STANDARD_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
]

NEWS_COLUMNS = [
    "ticker",
    "date",
    "datetime",
    "source",
    "headline",
    "summary",
    "url",
    "category",
    "sentiment_score",
    "sentiment_label",
    "relevance_score",
]

SOCIAL_COLUMNS = [
    "ticker",
    "date",
    "datetime",
    "source",
    "mention_count",
    "positive_score",
    "negative_score",
    "score",
    "social_metric",
]

MACRO_COLUMNS = [
    "date",
    "indicator",
    "value",
    "source",
    "updated_at",
]

MACRO_INDICATOR_CONFIG = {
    "dxy": {"source": "yfinance", "ticker": "^DXY"},
    "treasury_10y": {"source": "yfinance", "ticker": "^TNX"},
    "tips_yield": {"source": "fred", "series": "DFII10"},
    "breakeven_inflation": {"source": "fred", "series": "T10YIE"},
    "vix": {"source": "yfinance", "ticker": "^VIX"},
    "gld": {"source": "yfinance", "ticker": "GLD"},
    "iau": {"source": "yfinance", "ticker": "IAU"},
    "policy_uncertainty": {"source": "fred", "series": "USEPUINDXD"},
}

BRONZE_ARTICLE_COLUMNS = [
    "article_id",
    "source_type",
    "source_name",
    "source_url",
    "title",
    "body",
    "author",
    "published_at",
    "fetched_at",
    "source_metadata",
    "date",
]

SILVER_ARTICLE_COLUMNS = [
    "article_id",
    "ticker",
    "source_type",
    "source_name",
    "published_at",
    "date",
    "sentiment_score",
    "sentiment_label",
    "confidence",
    "event_type",
    "summary",
    "impact_horizon",
    "market_relevance",
    "key_entities",
    "source_metadata",
]

ANALYST_RATING_COLUMNS = [
    "ticker",
    "date",
    "period",
    "strong_buy",
    "buy",
    "hold",
    "sell",
    "strong_sell",
    "consensus_score",
    "consensus_label",
    "price_target_mean",
    "price_target_median",
    "price_target_high",
    "price_target_low",
    "price_target_count",
    "fetched_at",
]

__all__ = [
    "ANALYST_RATING_COLUMNS",
    "BRONZE_ARTICLE_COLUMNS",
    "MACRO_COLUMNS",
    "MACRO_INDICATOR_CONFIG",
    "NEWS_COLUMNS",
    "SILVER_ARTICLE_COLUMNS",
    "SOCIAL_COLUMNS",
    "STANDARD_COLUMNS",
]
