"""Static dataset definitions for the medallion catalog.

Each dataset maps to a directory in ``data/lake/`` with a known schema.
These are the "anchor" entries — Hamilton topology adds per-node detail.
"""

from __future__ import annotations

from equity_lake.catalog.models import ColumnInfo, DatasetEntry
from equity_lake.core.schemas import (
    ANALYST_RATING_COLUMNS,
    BRONZE_ARTICLE_COLUMNS,
    MACRO_COLUMNS,
    NEWS_COLUMNS,
    SEC_EXTRACTION_COLUMNS,
    SEC_FINANCIAL_COLUMNS,
    SILVER_ARTICLE_COLUMNS,
    SOCIAL_COLUMNS,
    STANDARD_COLUMNS,
)

_OHLCV_DESCRIPTION = (
    "Daily open/high/low/close/volume with adjusted close. "
    "Hive-partitioned by date. Source: yfinance (US), "
    "akshare (CN), efinance (HK/SG), JPX/KRX APIs."
)

_GOLD_DESCRIPTION = "Hamilton-computed technical indicators and external-data enrichments."


# Canonical Polars dtype per column name. Columns absent here fall back to
# "unknown". Cross-referenced against core/schemas.py column constants.
_DTYPE_MAP: dict[str, str] = {
    # Identifiers & categorical text
    "ticker": "string",
    "source": "string",
    "indicator": "string",
    "category": "string",
    "sentiment_label": "string",
    "consensus_label": "string",
    "article_id": "string",
    "source_type": "string",
    "source_name": "string",
    "source_url": "string",
    "source_metadata": "string",
    "headline": "string",
    "title": "string",
    "body": "string",
    "author": "string",
    "summary": "string",
    "url": "string",
    "event_type": "string",
    "filing_type": "string",
    "section_type": "string",
    "fiscal_period": "string",
    "period": "string",
    "risk_sentiment": "float64",
    "key_risks": "string",
    "guidance_direction": "string",
    "forward_statements": "string",
    "management_tone": "string",
    "new_vs_repeated": "string",
    "key_entities": "string",
    "impact_horizon": "string",
    # Timestamps
    "date": "datetime",
    "datetime": "datetime",
    "published_at": "datetime",
    "fetched_at": "datetime",
    "updated_at": "datetime",
    "filing_date": "datetime",
    # Counts / integers
    "mention_count": "int64",
    "strong_buy": "int64",
    "buy": "int64",
    "hold": "int64",
    "sell": "int64",
    "strong_sell": "int64",
    "price_target_count": "int64",
    "shares_outstanding": "int64",
    "fiscal_year": "int32",
    # Prices / floats
    "open": "float64",
    "high": "float64",
    "low": "float64",
    "close": "float64",
    "volume": "float64",
    "adj_close": "float64",
    "value": "float64",
    "sentiment_score": "float64",
    "relevance_score": "float64",
    "positive_score": "float64",
    "negative_score": "float64",
    "score": "float64",
    "social_metric": "float64",
    "confidence": "float64",
    "market_relevance": "float64",
    "consensus_score": "float64",
    "price_target_mean": "float64",
    "price_target_median": "float64",
    "price_target_high": "float64",
    "price_target_low": "float64",
    "revenue": "float64",
    "net_income": "float64",
    "operating_income": "float64",
    "total_assets": "float64",
    "total_liabilities": "float64",
    "stockholders_equity": "float64",
    "total_debt": "float64",
    "cash_and_equivalents": "float64",
    "operating_cash_flow": "float64",
    "capex": "float64",
    "eps": "float64",
    "roe": "float64",
    "roa": "float64",
    "debt_to_equity": "float64",
    "net_margin": "float64",
    "operating_margin": "float64",
}


def _columns_from_list(column_names: list[str]) -> list[ColumnInfo]:
    return [ColumnInfo(name=c, dtype=_DTYPE_MAP.get(c, "unknown")) for c in column_names]


def _list_to_str(cols: list[str]) -> str:
    return ", ".join(cols)


# ---------------------------------------------------------------------------
# Bronze — immutable raw data
# ---------------------------------------------------------------------------

BRONZE_DATASETS: list[DatasetEntry] = [
    DatasetEntry(
        name="us_equity_ohlcv",
        layer="bronze",
        path="data/lake/01_bronze/market_data/us_equity/",
        description=f"US equity OHLCV ({_list_to_str(STANDARD_COLUMNS)}). {_OHLCV_DESCRIPTION}",
        format="parquet",
        columns=_columns_from_list(STANDARD_COLUMNS),
    ),
    DatasetEntry(
        name="cn_ashare_ohlcv",
        layer="bronze",
        path="data/lake/01_bronze/market_data/cn_ashare/",
        description=f"China A-share OHLCV ({_list_to_str(STANDARD_COLUMNS)}). {_OHLCV_DESCRIPTION}",
        format="parquet",
        columns=_columns_from_list(STANDARD_COLUMNS),
    ),
    DatasetEntry(
        name="hk_sg_equity_ohlcv",
        layer="bronze",
        path="data/lake/01_bronze/market_data/hk_sg_equity/",
        description=f"Hong Kong/Singapore equity OHLCV ({_list_to_str(STANDARD_COLUMNS)}). {_OHLCV_DESCRIPTION}",
        format="parquet",
        columns=_columns_from_list(STANDARD_COLUMNS),
    ),
    DatasetEntry(
        name="jpx_equity_ohlcv",
        layer="bronze",
        path="data/lake/01_bronze/market_data/jpx_equity/",
        description=f"Japanese equity OHLCV ({_list_to_str(STANDARD_COLUMNS)}). {_OHLCV_DESCRIPTION}",
        format="parquet",
        columns=_columns_from_list(STANDARD_COLUMNS),
    ),
    DatasetEntry(
        name="krx_equity_ohlcv",
        layer="bronze",
        path="data/lake/01_bronze/market_data/krx_equity/",
        description=f"Korean equity OHLCV ({_list_to_str(STANDARD_COLUMNS)}). {_OHLCV_DESCRIPTION}",
        format="parquet",
        columns=_columns_from_list(STANDARD_COLUMNS),
    ),
    DatasetEntry(
        name="macro_indicators",
        layer="bronze",
        path="data/lake/01_bronze/macro/",
        description=f"Macro-economic indicators ({_list_to_str(MACRO_COLUMNS)}). "
        "Long-format: one row per indicator per date. "
        "Sources: yfinance (VIX, DXY), FRED (treasury, inflation).",
        format="parquet",
        columns=_columns_from_list(MACRO_COLUMNS),
    ),
    DatasetEntry(
        name="raw_articles",
        layer="bronze",
        path="data/lake/01_bronze/raw_articles/",
        description=f"Unstructured articles ({_list_to_str(BRONZE_ARTICLE_COLUMNS)}). "
        "RSS feeds, Reddit posts, StockTwits messages — raw text before LLM processing.",
        format="parquet",
        columns=_columns_from_list(BRONZE_ARTICLE_COLUMNS),
    ),
]

# ---------------------------------------------------------------------------
# Silver — validated, cleaned, deduped
# ---------------------------------------------------------------------------

SILVER_DATASETS: list[DatasetEntry] = [
    DatasetEntry(
        name="news_sentiment",
        layer="silver",
        path="data/lake/02_silver/news_sentiment/",
        description=f"Finnhub news articles with VADER sentiment ({_list_to_str(NEWS_COLUMNS)}). "
        "Aggregated per ticker per date for feature enrichment.",
        format="parquet",
        columns=_columns_from_list(NEWS_COLUMNS),
    ),
    DatasetEntry(
        name="social_sentiment",
        layer="silver",
        path="data/lake/02_silver/social_sentiment/",
        description=f"Finnhub social sentiment scores ({_list_to_str(SOCIAL_COLUMNS)}). Aggregated per ticker per date for feature enrichment.",
        format="parquet",
        columns=_columns_from_list(SOCIAL_COLUMNS),
    ),
    DatasetEntry(
        name="processed_articles",
        layer="silver",
        path="data/lake/02_silver/processed_articles/",
        description=f"LLM-enriched article-ticker pairs ({_list_to_str(SILVER_ARTICLE_COLUMNS)}). "
        "DeepSeek-processed articles with ticker attribution, sentiment, event type, "
        "impact horizon, and market relevance scores.",
        format="parquet",
        columns=_columns_from_list(SILVER_ARTICLE_COLUMNS),
    ),
    DatasetEntry(
        name="analyst_ratings",
        layer="silver",
        path="data/lake/02_silver/analyst_ratings/",
        description=f"Analyst consensus ratings and price targets ({_list_to_str(ANALYST_RATING_COLUMNS)}). "
        "Finnhub analyst recommendations with consensus scores.",
        format="parquet",
        columns=_columns_from_list(ANALYST_RATING_COLUMNS),
    ),
    DatasetEntry(
        name="sec_extractions",
        layer="silver",
        path="data/lake/02_silver/sec_extractions/",
        description=f"LLM-extracted SEC filing insights ({_list_to_str(SEC_EXTRACTION_COLUMNS)}). "
        "DeepSeek-extracted risk sentiment, guidance direction, management tone, "
        "and key risk factors from 10-K/10-Q filings.",
        format="parquet",
        columns=_columns_from_list(SEC_EXTRACTION_COLUMNS),
    ),
    DatasetEntry(
        name="sec_financials",
        layer="silver",
        path="data/lake/02_silver/sec_financials/",
        description=f"SEC XBRL financial statements ({_list_to_str(SEC_FINANCIAL_COLUMNS)}). "
        "Revenue, net income, assets, liabilities, cash flow, EPS, "
        "and derived ratios (ROE, ROA, D/E, margins).",
        format="parquet",
        columns=_columns_from_list(SEC_FINANCIAL_COLUMNS),
    ),
]

# ---------------------------------------------------------------------------
# Gold — feature engineering output
# ---------------------------------------------------------------------------

GOLD_DATASETS: list[DatasetEntry] = [
    DatasetEntry(
        name="technical_features",
        layer="gold",
        path="data/lake/03_gold/features/",
        description="Hamilton-computed technical indicators (momentum, volatility, "
        "volume, calendar) from per-ticker OHLCV data. "
        f"{_GOLD_DESCRIPTION}",
        format="parquet",
        columns=[
            ColumnInfo(name="ticker", dtype="string"),
            ColumnInfo(name="date", dtype="datetime"),
            ColumnInfo(name="open", dtype="float64"),
            ColumnInfo(name="high", dtype="float64"),
            ColumnInfo(name="low", dtype="float64"),
            ColumnInfo(name="close", dtype="float64"),
            ColumnInfo(name="volume", dtype="float64"),
            ColumnInfo(name="rsi_14", dtype="float64"),
            ColumnInfo(name="macd", dtype="float64"),
            ColumnInfo(name="macd_signal", dtype="float64"),
            ColumnInfo(name="macd_histogram", dtype="float64"),
            ColumnInfo(name="bb_upper", dtype="float64"),
            ColumnInfo(name="bb_middle", dtype="float64"),
            ColumnInfo(name="bb_lower", dtype="float64"),
            ColumnInfo(name="bb_width", dtype="float64"),
            ColumnInfo(name="bb_pct", dtype="float64"),
            ColumnInfo(name="atr_14", dtype="float64"),
            ColumnInfo(name="roc_5", dtype="float64"),
            ColumnInfo(name="roc_10", dtype="float64"),
            ColumnInfo(name="roc_20", dtype="float64"),
            ColumnInfo(name="return_1d", dtype="float64"),
            ColumnInfo(name="return_5d", dtype="float64"),
            ColumnInfo(name="return_10d", dtype="float64"),
            ColumnInfo(name="return_20d", dtype="float64"),
            ColumnInfo(name="overnight_return", dtype="float64"),
            ColumnInfo(name="intraday_return", dtype="float64"),
            ColumnInfo(name="hl_range", dtype="float64"),
            ColumnInfo(name="volume_ma_20", dtype="float64"),
            ColumnInfo(name="volume_roc_5", dtype="float64"),
            ColumnInfo(name="obv", dtype="float64"),
            ColumnInfo(name="volume_ratio", dtype="float64"),
            ColumnInfo(name="day_of_week", dtype="int8"),
            ColumnInfo(name="day_of_month", dtype="int8"),
            ColumnInfo(name="month", dtype="int8"),
            ColumnInfo(name="quarter", dtype="int8"),
            ColumnInfo(name="days_to_month_end", dtype="int8"),
            ColumnInfo(name="trading_day_of_month", dtype="int16"),
            ColumnInfo(name="volatility_20", dtype="float64"),
            ColumnInfo(name="feature_schema_version", dtype="int8"),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Platinum — ML predictions and signals
# ---------------------------------------------------------------------------

PLATINUM_DATASETS: list[DatasetEntry] = [
    DatasetEntry(
        name="predictions",
        layer="platinum",
        path="data/lake/04_platinum/predictions/",
        description="ML model price direction predictions with confidence scores. "
        "XGBoost model outputs with probability and direction classification.",
        format="parquet",
        columns=[
            ColumnInfo(name="ticker", dtype="string"),
            ColumnInfo(name="date", dtype="datetime"),
            ColumnInfo(name="direction", dtype="string", description="up / down / flat"),
            ColumnInfo(name="probability", dtype="float64", description="Prediction confidence 0.0–1.0"),
            ColumnInfo(name="model_version", dtype="string"),
            ColumnInfo(name="feature_schema_version", dtype="int8"),
        ],
    ),
]

# ---------------------------------------------------------------------------
# Unified catalog
# ---------------------------------------------------------------------------

ALL_DATASETS: list[DatasetEntry] = BRONZE_DATASETS + SILVER_DATASETS + GOLD_DATASETS + PLATINUM_DATASETS

LAYER_ORDER = ["bronze", "silver", "gold", "platinum"]
