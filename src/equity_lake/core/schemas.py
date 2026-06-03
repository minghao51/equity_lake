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

__all__ = [
    "MACRO_COLUMNS",
    "MACRO_INDICATOR_CONFIG",
    "NEWS_COLUMNS",
    "SOCIAL_COLUMNS",
    "STANDARD_COLUMNS",
]
