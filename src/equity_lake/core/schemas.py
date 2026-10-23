"""Canonical column schemas and data-type definitions."""

from __future__ import annotations

import polars as pl

# Shared Gold feature contract versions (ADR-0015).
FEATURE_SCHEMA_VERSION = 7

# Canonical shared-table identity (ADR-0016). Bronze OHLCV remains unchanged;
# identity is added only when explicit source provenance is available.
INSTRUMENT_IDENTITY_COLUMNS = ["market", "mic", "symbol"]
INSTRUMENT_DATE_KEY_COLUMNS = [*INSTRUMENT_IDENTITY_COLUMNS, "date"]

STANDARD_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    # Optional provider/current-config identity metadata is propagated when
    # available; ingestion write boundaries reject unidentified new price rows.
    "market",
    "mic",
    "symbol",
    "exchange",
    "identity_schema_version",
    "identity_provenance",
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

# Corporate actions (ADR-0011): one row per (ticker, ex_date, action).
# ``value`` is the cash dividend per share (``dividend``) or the split ratio
# (``split``, e.g. 0.5 for a 2-for-1 split).
CORPORATE_ACTION_COLUMNS = [
    "ticker",
    "ex_date",
    "action",
    "value",
    "source",
    "ingested_at",
    # Source publication/availability clock; nullable means unknown and is
    # unusable for a strict point-in-time adjustment (ADR-0015).
    "source_available_at",
]
CORPORATE_ACTION_REQUIRED_COLUMNS = ["ticker", "ex_date", "action", "value", "source", "ingested_at"]
CORPORATE_ACTION_TYPES = ("dividend", "split")

CORPORATE_ACTION_COVERAGE_COLUMNS = [
    "market",
    "mic",
    "symbol",
    "source",
    "coverage_start",
    "coverage_end",
    "source_available_at",
    "source_valid_until",
    "coverage_status",
    "coverage_schema_version",
]

# The legacy ingestion timestamp remains naive UTC. A source clock is stored as
# timezone-aware UTC and is never synthesized from ingestion time.
CORPORATE_ACTION_SCHEMA: dict[str, pl.DataType] = {
    "ticker": pl.String(),
    "ex_date": pl.Date(),
    "action": pl.String(),
    "value": pl.Float64(),
    "source": pl.String(),
    "ingested_at": pl.Datetime("us"),
    "source_available_at": pl.Datetime("us", "UTC"),
}

CORPORATE_ACTION_COVERAGE_SCHEMA: dict[str, pl.DataType] = {
    "market": pl.String(),
    "mic": pl.String(),
    "symbol": pl.String(),
    "source": pl.String(),
    "coverage_start": pl.Date(),
    "coverage_end": pl.Date(),
    "source_available_at": pl.Datetime("us", "UTC"),
    "source_valid_until": pl.Datetime("us", "UTC"),
    "coverage_status": pl.String(),
    "coverage_schema_version": pl.Int8(),
}

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

SEC_EXTRACTION_COLUMNS = [
    "article_id",
    "ticker",
    "filing_type",
    "section_type",
    "filing_date",
    "date",
    "risk_sentiment",
    "key_risks",
    "guidance_direction",
    "forward_statements",
    "management_tone",
    "new_vs_repeated",
    "summary",
    "fetched_at",
]

SEC_FINANCIAL_COLUMNS = [
    "ticker",
    "date",
    "filing_type",
    "fiscal_year",
    "fiscal_period",
    "revenue",
    "net_income",
    "operating_income",
    "total_assets",
    "total_liabilities",
    "stockholders_equity",
    "total_debt",
    "cash_and_equivalents",
    "operating_cash_flow",
    "capex",
    "shares_outstanding",
    "eps",
    "roe",
    "roa",
    "debt_to_equity",
    "net_margin",
    "operating_margin",
    "fetched_at",
]

__all__ = [
    "ANALYST_RATING_COLUMNS",
    "BRONZE_ARTICLE_COLUMNS",
    "CORPORATE_ACTION_COLUMNS",
    "CORPORATE_ACTION_COVERAGE_COLUMNS",
    "CORPORATE_ACTION_COVERAGE_SCHEMA",
    "CORPORATE_ACTION_REQUIRED_COLUMNS",
    "CORPORATE_ACTION_SCHEMA",
    "CORPORATE_ACTION_TYPES",
    "FEATURE_SCHEMA_VERSION",
    "INSTRUMENT_DATE_KEY_COLUMNS",
    "INSTRUMENT_IDENTITY_COLUMNS",
    "MACRO_COLUMNS",
    "MACRO_INDICATOR_CONFIG",
    "NEWS_COLUMNS",
    "SEC_EXTRACTION_COLUMNS",
    "SEC_FINANCIAL_COLUMNS",
    "SILVER_ARTICLE_COLUMNS",
    "SOCIAL_COLUMNS",
    "STANDARD_COLUMNS",
]
