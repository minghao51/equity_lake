# Medallion Architecture Migration Plan

**Date:** 2026-06-15
**Status:** Planning
**Scope:** Refactor flat Hamilton DAG + imperative merge pipeline into a 4-layer medallion architecture (Bronze/Silver/Gold/Platinum) with layered DAG modules, boundary validation, `@parameterize` refactoring, and Platinum persistence.

---

## 1. Problem Statement

The current feature pipeline has three structural gaps:

1. **Flat Hamilton DAG** — `features/hamilton_features.py` is a single module with ~35 hand-written functions. No layering, no `@config.when`, no `@parameterize`, no `@check_output`, no materializers. The DAG only computes technical indicators from OHLCV; all external data merges (sentiment, analyst, SEC, macro) are imperative Python in `features/engineering.py:155-209`.

2. **Inconsistent storage layering** — Bronze/Silver exists for unstructured text (`bronze/raw_articles/`, `silver/processed_articles/`, `silver/sec_extractions/`) but market OHLCV data (`us_equity/`, `cn_ashare/`, etc.) and derived tables (`us_news/`, `us_analyst_ratings/`, `features/`) use flat naming with no layer distinction. There is no Platinum layer at all — ML predictions are ephemeral.

3. **No boundary validation** — No `@check_output` or Pydantic schema enforcement at any layer transition. Data quality issues (e.g., the `date=2026-02-04%2000%3A00%3A00.000000` URL-encoded partitions in `us_equity/`) propagate undetected.

---

## 2. Current State Analysis

### 2.1 Hamilton DAG Structure

**File:** `features/hamilton_features.py` (208 lines, 35+ functions)

Single module with a flat dependency graph:

```
price_data (root input)
├── ticker, date, open_price, high, low, close, volume    [extraction nodes]
│   ├── returns → volatility_20
│   ├── rsi_14
│   ├── macd_frame → macd, macd_signal, macd_histogram
│   ├── bollinger_frame → bb_upper, bb_middle, bb_lower → bb_width, bb_pct
│   ├── atr_14
│   ├── roc_5, roc_10, roc_20                               [3 separate functions]
│   ├── return_1d, return_5d, return_10d, return_20d         [4 separate functions]
│   ├── overnight_return, intraday_return, hl_range
│   ├── volume_ma_20 → volume_ratio
│   ├── volume_roc_5
│   ├── obv
│   └── day_of_week, day_of_month, month, quarter,
│       days_to_month_end, trading_day_of_month
└── next_day_return (target variable, shift(-1))
```

**Driver construction** (`features/pipeline.py:66-75`):
- `driver.Builder().with_modules(hamilton_features).with_adapter(SimplePythonGraphAdapter(PolarsDataFrameResult()))`
- Optional `.with_cache()` when `enable_cache=True`
- No config, no materializers, no tracker

**Execution model** (`features/engineering.py:134-145`):
- Per-ticker: filter OHLCV, skip if <60 rows, call `feature_pipeline.compute(ticker_df)`
- Results concatenated vertically

### 2.2 Imperative Merge Pipeline

**File:** `features/engineering.py:155-209` — `generate_features()` sequentially calls:

| Step | Method | Source Table | Join Type |
|------|--------|-------------|-----------|
| 1 | `merge_sentiment_features()` | `us_news` | Left join on (ticker, date) |
| 2 | `merge_social_sentiment_features()` | `us_social_sentiment` | Left join on (ticker, date) |
| 3 | `merge_enriched_sentiment_features()` | `silver/processed_articles` | Left join on (ticker, date) |
| 4 | `merge_analyst_rating_features()` | `us_analyst_ratings` | Left join on (ticker, date) |
| 5 | `merge_sec_features()` | `silver/sec_extractions` | **ASOF** backward on (ticker, date↔filing_date) |
| 6 | `add_cross_modal_sentiment_features()` | Derived | Computed expressions |
| 7 | `merge_macro_features()` | `macro_indicators` | Left join + forward-fill |

External merge functions live in separate modules (`features/enriched_sentiment.py`, `features/analyst_features.py`, `features/sec_features.py`, `features/sec_financial_features.py`).

### 2.3 Current Storage Layout

**Directory:** `data/lake/`

| Current Path | Format | Contents | Layer |
|---|---|---|---|
| `us_equity/` | Hive Parquet (2600+ date= partitions) | OHLCV daily bars | (none) |
| `cn_ashare/` | Hive Parquet | OHLCV daily bars | (none) |
| `hk_sg_equity/` | Hive Parquet | OHLCV daily bars | (none) |
| `jpx_equity/` | (not on disk) | OHLCV daily bars | (none) |
| `krx_equity/` | (not on disk) | OHLCV daily bars | (none) |
| `macro_indicators/` | Parquet | Long-format macro indicators | (none) |
| `us_news/` | Delta table | Finnhub news + VADER sentiment | (none) |
| `us_social_sentiment/` | (empty) | Finnhub social sentiment | (none) |
| `bronze/raw_articles/` | Delta table | Raw RSS/Reddit/StockTwits text | Bronze |
| `silver/processed_articles/` | Delta table | LLM-enriched article-ticker pairs | Silver |
| `silver/sec_extractions/` | Delta table | LLM-extracted SEC filing insights | Silver |
| `us_analyst_ratings/` | Delta table | Analyst consensus + price targets | (none) |
| `us_sec_financials/` | (not on disk) | SEC XBRL financial data | (none) |
| `features/` | Delta table | Computed feature frames | (none) |
| (not persisted) | — | ML predictions | — |

**Path constants** (`core/paths.py:21-35`): Flat constants like `US_EQUITY_DIR = LAKE_DIR / "us_equity"`.

**Market map** (`ingestion/types.py:48-66`): `MARKET_DIR_MAP` maps market keys to directory names. Already has partial medallion awareness for bronze/silver article paths.

**Writer dedup keys** (`ingestion/writers.py:19-34`): `_dedupe_key_columns()` returns per-market dedup keys.

### 2.4 Pipeline Orchestration

**File:** `pipeline.py:35-161` — `execute_eod_pipeline()`

```
Stage 1: Ingestion     → run_daily_ingestion() → write to lake/{market}/
                         → process_bronze_to_silver() (conditional)
                         → process_sec_bronze_to_silver() (conditional)
Stage 2: Features       → run_feature_job() → write to lake/features/
Stage 3: ML             → run_prediction_job() → ephemeral (no persistence)
```

---

## 3. Target Architecture

### 3.1 Medallion Layer Definitions

| Layer | Purpose | Validation | Content |
|-------|---------|-----------|---------|
| **Bronze** (`01_bronze/`) | Immutable raw data | None | Raw API output, unvalidated |
| **Silver** (`02_silver/`) | Cleaned, validated, deduped | `@check_output` + Pydantic | Typed Parquet/Delta, business-ready |
| **Gold** (`03_gold/`) | Feature engineering | `@check_output` at entry | Technical indicators + merged enrichments |
| **Platinum** (`04_platinum/`) | ML predictions + signals | pointblank post-inference | Model outputs, confidence scores |

### 3.2 Target Storage Layout

```
data/lake/
├── 01_bronze/
│   ├── market_data/
│   │   ├── us_equity/         (Hive Parquet → Delta, date= partitions)
│   │   ├── cn_ashare/
│   │   ├── hk_sg_equity/
│   │   ├── jpx_equity/
│   │   └── krx_equity/
│   ├── raw_articles/          (Delta — moved from bronze/raw_articles/)
│   └── macro/                 (Parquet — moved from macro_indicators/)
├── 02_silver/
│   ├── news_sentiment/        (Delta — moved from us_news/)
│   ├── social_sentiment/      (Delta — moved from us_social_sentiment/)
│   ├── processed_articles/    (Delta — moved from silver/processed_articles/)
│   ├── sec_extractions/       (Delta — moved from silver/sec_extractions/)
│   ├── analyst_ratings/       (Delta — moved from us_analyst_ratings/)
│   └── sec_financials/        (Delta — moved from us_sec_financials/)
├── 03_gold/
│   └── features/              (Delta — moved from features/)
└── 04_platinum/
    └── predictions/           (Delta — NEW)
```

### 3.3 Target DAG Module Structure

```
features/
├── __init__.py                # run_feature_job() — Driver assembly + execution
├── pipeline.py                # FeaturePipeline — multi-module Driver builder
├── indicators.py              # Pure indicator math (unchanged)
│
├── dag/
│   ├── __init__.py
│   ├── raw_01.py              # BRONZE: Load OHLCV from bronze storage
│   ├── clean_02.py            # SILVER: Validation + cleaning at boundary
│   ├── features_03.py         # GOLD: Technical indicators (from hamilton_features.py)
│   ├── enrichments_04.py      # GOLD: External data joins (from imperative merge code)
│   └── schemas.py             # Pydantic models for layer boundary validation
│
├── engineering.py             # Thin wrapper — most logic moves to DAG
├── enriched_sentiment.py      # DEPRECATED — logic moves to dag/enrichments_04.py
├── analyst_features.py        # DEPRECATED
├── sec_features.py            # DEPRECATED
├── sec_financial_features.py  # DEPRECATED
└── hamilton_features.py       # DEPRECATED — logic moves to dag/features_03.py
```

---

## 4. Phased Implementation

### Phase 1: Storage Migration

**Goal:** Rename `data/lake/` directories to numbered medallion convention.

**Estimated effort:** 1 day (script + code changes + tests)

#### 4.1.1 Migration Script

**New file:** `scripts/migrate_to_medallion.py`

```python
"""One-time migration: move data/lake/ to 01_bronze/02_silver/03_gold/04_platinum."""

MIGRATION_MAP = {
    # Bronze — market data
    "us_equity":            "01_bronze/market_data/us_equity",
    "cn_ashare":            "01_bronze/market_data/cn_ashare",
    "hk_sg_equity":         "01_bronze/market_data/hk_sg_equity",
    "jpx_equity":           "01_bronze/market_data/jpx_equity",
    "krx_equity":           "01_bronze/market_data/krx_equity",
    "macro_indicators":     "01_bronze/macro",
    # Bronze — unstructured (already under bronze/, flatten)
    "bronze/raw_articles":  "01_bronze/raw_articles",
    # Silver — structured
    "us_news":              "02_silver/news_sentiment",
    "us_social_sentiment":  "02_silver/social_sentiment",
    "us_analyst_ratings":   "02_silver/analyst_ratings",
    "us_sec_financials":    "02_silver/sec_financials",
    # Silver — unstructured (already under silver/, flatten)
    "silver/processed_articles": "02_silver/processed_articles",
    "silver/sec_extractions":    "02_silver/sec_extractions",
    # Gold
    "features":             "03_gold/features",
}
```

Script behavior:
1. `--dry-run` mode: prints source → dest mapping, verifies source exists, checks dest doesn't
2. `--execute` mode: `mkdir -p` parent, `mv` (instant on same filesystem), verify file count match
3. Post-migration: verify DuckDB can read from new paths
4. Log every move for audit trail

**Dual date format cleanup:** During migration, scan `us_equity/` for URL-encoded partition names (`date=2026-02-04%2000%3A00%3A00.000000`), rename to canonical format (`date=2026-02-04`).

#### 4.1.2 Code Changes

**`core/paths.py`** — Replace flat constants with layered constants:

```python
# --- Bronze ---
BRONZE_DIR = LAKE_DIR / "01_bronze"
BRONZE_MARKET_DATA_DIR = BRONZE_DIR / "market_data"
US_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "us_equity"
CN_ASHARE_DIR = BRONZE_MARKET_DATA_DIR / "cn_ashare"
HK_SG_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "hk_sg_equity"
JPX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "jpx_equity"
KRX_EQUITY_DIR = BRONZE_MARKET_DATA_DIR / "krx_equity"
BRONZE_RAW_ARTICLES_DIR = BRONZE_DIR / "raw_articles"
BRONZE_MACRO_DIR = BRONZE_DIR / "macro"

# --- Silver ---
SILVER_DIR = LAKE_DIR / "02_silver"
SILVER_NEWS_SENTIMENT_DIR = SILVER_DIR / "news_sentiment"
SILVER_SOCIAL_SENTIMENT_DIR = SILVER_DIR / "social_sentiment"
SILVER_PROCESSED_ARTICLES_DIR = SILVER_DIR / "processed_articles"
SILVER_SEC_EXTRACTIONS_DIR = SILVER_DIR / "sec_extractions"
SILVER_ANALYST_RATINGS_DIR = SILVER_DIR / "analyst_ratings"
SILVER_SEC_FINANCIALS_DIR = SILVER_DIR / "sec_financials"

# --- Gold ---
GOLD_DIR = LAKE_DIR / "03_gold"
GOLD_FEATURES_DIR = GOLD_DIR / "features"

# --- Platinum ---
PLATINUM_DIR = LAKE_DIR / "04_platinum"
PLATINUM_PREDICTIONS_DIR = PLATINUM_DIR / "predictions"
```

Keep old constants as deprecated aliases for backward compatibility during migration:
```python
# Deprecated aliases (remove after migration complete)
MACRO_INDICATORS_DIR = BRONZE_MACRO_DIR  # Use BRONZE_MACRO_DIR
US_NEWS_DIR = SILVER_NEWS_SENTIMENT_DIR  # Use SILVER_NEWS_SENTIMENT_DIR
SEC_EXTRACTIONS_DIR = SILVER_SEC_EXTRACTIONS_DIR
ANALYST_RATINGS_DIR = SILVER_ANALYST_RATINGS_DIR
SEC_FINANCIALS_DIR = SILVER_SEC_FINANCIALS_DIR
```

**`ingestion/types.py`** — Update `MARKET_DIR_MAP`:

```python
MARKET_DIR_MAP = {
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
```

**`ingestion/writers.py`** — Update `_dedupe_key_columns()`:

```python
def _dedupe_key_columns(market: str) -> list[str]:
    if market in ("01_bronze/macro", "macro"):
        return ["indicator", "date"]
    if market in ("02_silver/news_sentiment", "us_news"):
        return ["url"]
    if market in ("02_silver/social_sentiment", "us_social_sentiment"):
        return ["ticker", "datetime", "source"]
    if market in ("01_bronze/raw_articles", "bronze/raw_articles",
                  "rss_news", "reddit_posts", "stocktwits_messages",
                  "us_earnings_transcripts", "sec_filings_fulltext"):
        return ["source_type", "source_url"]
    if market in ("02_silver/processed_articles", "silver/processed_articles"):
        return ["article_id", "ticker"]
    if market in ("02_silver/analyst_ratings", "us_analyst_ratings"):
        return ["ticker", "date"]
    if market in ("02_silver/sec_financials", "us_sec_financials"):
        return ["ticker", "date", "filing_type"]
    return ["ticker", "date"]
```

**`features/engineering.py`** — Update `_setup_views()` to scan from new bronze paths:

```python
markets = [
    ("us", "01_bronze/market_data/us_equity"),
    ("cn", "01_bronze/market_data/cn_ashare"),
    ("hk_sg", "01_bronze/market_data/hk_sg_equity"),
    ("jpx", "01_bronze/market_data/jpx_equity"),
    ("krx", "01_bronze/market_data/krx_equity"),
]
for label, market_dir in markets:
    path = LAKE_DIR / market_dir
    # ... same as before
```

**`features/engineering.py`** — Update `merge_macro_features()` path reference:

```python
macro_path = LAKE_DIR / "01_bronze" / "macro"  # was LAKE_DIR / "macro_indicators"
```

**`features/engineering.py`** — Update `merge_sentiment_features()` and `merge_social_sentiment_features()` scan paths:

```python
# Sentiment: was duckdb_scan_for(LAKE_DIR / "us_news")
duckdb_scan_for(LAKE_DIR / "02_silver" / "news_sentiment")

# Social: was duckdb_scan_for(LAKE_DIR / "us_social_sentiment")
duckdb_scan_for(LAKE_DIR / "02_silver" / "social_sentiment")
```

**`features/__init__.py`** — Update `write_to_partitioned_parquet` call:

```python
# was: write_to_partitioned_parquet(output_df, "features", output_end_date)
write_to_partitioned_parquet(output_df, "03_gold/features", output_end_date)
```

**Files with path references to update (exhaustive list):**

| File | Current Reference | New Reference |
|------|------------------|---------------|
| `core/paths.py` | All flat constants | Layered constants |
| `ingestion/types.py` | `MARKET_DIR_MAP` values | Medallion paths |
| `ingestion/writers.py` | `_dedupe_key_columns()` market strings | Medallion market strings |
| `features/engineering.py` | `_setup_views()` market dirs, `merge_macro_features()` path, `merge_sentiment_features()` scan, `merge_social_sentiment_features()` scan | Updated paths |
| `features/__init__.py` | `write_to_partitioned_parquet("features", ...)` | `write_to_partitioned_parquet("03_gold/features", ...)` |
| `features/enriched_sentiment.py` | `SILVER_PROCESSED_ARTICLES_DIR` | Updated constant (auto via paths.py alias) |
| `features/analyst_features.py` | `ANALYST_RATINGS_DIR` | Updated constant (auto via paths.py alias) |
| `features/sec_features.py` | `SEC_EXTRACTIONS_DIR` | Updated constant (auto via paths.py alias) |
| `features/sec_financial_features.py` | `SEC_FINANCIALS_DIR` | Updated constant (auto via paths.py alias) |
| `storage/delta.py` | `delta_table_path()` — no change needed (already market-string based) | — |
| `storage/lake_reader.py` | `duckdb_scan_for()` — no change needed (path-agnostic) | — |
| `storage/s3_sync.py` | Check sync paths | Update if needed |
| `storage/compaction.py` | Check compaction paths | Update if needed |

#### 4.1.3 Testing

- Migration script test: create temp directory structure, run migration, verify paths
- Integration test: `execute_eod_pipeline(dry_run=True)` after migration
- Unit test: `_dedupe_key_columns()` returns correct keys for new market strings

---

### Phase 2: Hamilton DAG Layered Modules

**Goal:** Split flat `hamilton_features.py` into medallion-layered modules.

**Estimated effort:** 2-3 days

#### 4.2.1 New Module: `features/dag/__init__.py`

```python
"""Medallion-layered Hamilton DAG modules."""
```

#### 4.2.2 New Module: `features/dag/raw_01.py` (Bronze Layer)

Extracts columns from the raw `price_data` input DataFrame. Same logic as current `hamilton_features.py:22-52`, but isolated in a module that represents the Bronze ingestion boundary.

```python
"""Bronze layer: raw OHLCV column extraction from price_data input."""

from __future__ import annotations

import polars as pl


def ticker(price_data: pl.DataFrame) -> pl.Series:
    return price_data["ticker"]


def date(price_data: pl.DataFrame) -> pl.Series:
    date_column = price_data["date"]
    if date_column.dtype == pl.Utf8:
        return date_column.str.to_datetime(strict=False)
    if date_column.dtype == pl.Date:
        return date_column.cast(pl.Datetime)
    return date_column


def open_price(price_data: pl.DataFrame) -> pl.Series:
    return price_data["open"]


def high(price_data: pl.DataFrame) -> pl.Series:
    return price_data["high"]


def low(price_data: pl.DataFrame) -> pl.Series:
    return price_data["low"]


def close(price_data: pl.DataFrame) -> pl.Series:
    return price_data["close"].cast(pl.Float64)


def volume(price_data: pl.DataFrame) -> pl.Series:
    return price_data["volume"].cast(pl.Float64)
```

**Design note:** The `price_data` input is injected via `dr.execute(inputs={"price_data": ...})`. In a future iteration, this could be replaced with a materializer (`from_.parquet(target="price_data")`) or `@config.when(source="delta")` variants. For now, keep the current injection pattern to minimize scope.

#### 4.2.3 New Module: `features/dag/clean_02.py` (Silver Layer)

Validation and cleaning at the Bronze→Silver boundary.

```python
"""Silver layer: validation and cleaning at the Bronze→Silver boundary."""

from __future__ import annotations

import polars as pl


def cleaned_ohlcv(
    ticker: pl.Series,
    date: pl.Series,
    open_price: pl.Series,
    high: pl.Series,
    low: pl.Series,
    close: pl.Series,
    volume: pl.Series,
) -> pl.DataFrame:
    """Assemble OHLCV into a typed DataFrame and remove invalid rows."""
    return (
        pl.DataFrame({
            "ticker": ticker,
            "date": date,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        .filter(pl.col("close").is_not_null() & (pl.col("close") > 0))
        .filter(pl.col("volume").is_not_null() & (pl.col("volume") >= 0))
        .unique(subset=["ticker", "date"])
    )
```

**`@check_output` at boundary** (added in Phase 4):
```python
@check_output(schema=OHLCVCleanModel)  # Pydantic model defined in dag/schemas.py
def validated_ohlcv(cleaned_ohlcv: pl.DataFrame) -> pl.DataFrame:
    return cleaned_ohlcv
```

**Design decision — no separate validation node yet:** The `validated_ohlcv` node with `@check_output` is deferred to Phase 4 (Boundary Validation). In Phase 2, `cleaned_ohlcv` serves as the Silver boundary and downstream nodes consume it directly.

**How downstream nodes adapt:** Currently, all indicator functions in `hamilton_features.py` take individual `pl.Series` inputs (`close`, `high`, `low`, `volume`). With the Silver layer producing a `cleaned_ohlcv` DataFrame, we have two options:

- **Option A (chosen):** Keep individual series nodes (`close`, `high`, etc.) in `raw_01.py` and let `clean_02.py` add a `cleaned_ohlcv` validation node that also produces individual validated series. Downstream nodes stay unchanged.
- **Option B (rejected):** Have `clean_02.py` produce a single `validated_ohlcv` DataFrame and change all downstream nodes to accept a DataFrame and extract columns internally. This would require rewriting all 35 indicator functions.

With Option A, the DAG looks like:

```
raw_01.py:  price_data → ticker, date, open_price, high, low, close, volume
clean_02.py:  (passthrough + validation marker)
features_03.py:  close → rsi_14, macd_frame, bollinger_frame, etc.
```

The validation node in `clean_02.py` simply passes through the series with a `@check_output` annotation once Phase 4 is complete.

#### 4.2.4 New Module: `features/dag/features_03.py` (Gold Layer — Technical Indicators)

Migrate all indicator functions from `hamilton_features.py`. This is the largest module.

**Functions migrated directly (no change):**

```python
# From hamilton_features.py — unchanged
def returns(close): ...
def rsi_14(close): ...
def macd_frame(close): ...
def macd(macd_frame): ...
def macd_signal(macd_frame): ...
def macd_histogram(macd_frame): ...
def bollinger_frame(close): ...
def bb_upper(bollinger_frame): ...
def bb_middle(bollinger_frame): ...
def bb_lower(bollinger_frame): ...
def bb_width(bb_upper, bb_lower, bb_middle): ...
def bb_pct(close, bb_upper, bb_lower): ...
def atr_14(high, low, close): ...
def overnight_return(open_price, close): ...
def intraday_return(open_price, close): ...
def hl_range(high, low, close): ...
def volume_ma_20(volume): ...
def volume_roc_5(volume): ...
def obv(close, volume): ...
def volume_ratio(volume, volume_ma_20): ...
def day_of_week(date): ...
def day_of_month(date): ...
def month(date): ...
def quarter(date): ...
def days_to_month_end(date): ...
def trading_day_of_month(ticker, date): ...
def volatility_20(returns): ...
def next_day_return(close): ...
```

**Functions refactored with `@parameterize` (Phase 3):**

```python
# CURRENT (hamilton_features.py:107-132) — 7 separate functions:
def roc_5(close): return roc(close, length=5)
def roc_10(close): return roc(close, length=10)
def roc_20(close): return roc(close, length=20)

def return_1d(close): return close.pct_change(1)
def return_5d(close): return close.pct_change(5)
def return_10d(close): return close.pct_change(10)
def return_20d(close): return close.pct_change(20)
```

→ **Phase 3 replaces with `@parameterize`** (see Section 4.3 below).

**Full module** (`features/dag/features_03.py`):

```python
"""Gold layer: technical indicator computation."""

from __future__ import annotations

import numpy as np
import polars as pl

from equity_lake.features.indicators import (
    atr,
    bollinger_bands,
    macd as macd_indicator,
    obv as obv_indicator,
    roc,
    rsi,
)


def returns(close: pl.Series) -> pl.Series:
    return close.pct_change()


def rsi_14(close: pl.Series) -> pl.Series:
    return rsi(close, length=14)


def macd_frame(close: pl.Series) -> pl.DataFrame:
    return macd_indicator(close, fast=12, slow=26, signal=9)


def macd(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["macd"]


def macd_signal(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["signal"]


def macd_histogram(macd_frame: pl.DataFrame) -> pl.Series:
    return macd_frame["histogram"]


def bollinger_frame(close: pl.Series) -> pl.DataFrame:
    return bollinger_bands(close, length=20, std=2)


def bb_upper(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["upper"]


def bb_middle(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["middle"]


def bb_lower(bollinger_frame: pl.DataFrame) -> pl.Series:
    return bollinger_frame["lower"]


def bb_width(bb_upper: pl.Series, bb_lower: pl.Series, bb_middle: pl.Series) -> pl.Series:
    return (bb_upper - bb_lower) / bb_middle


def bb_pct(close: pl.Series, bb_upper: pl.Series, bb_lower: pl.Series) -> pl.Series:
    return (close - bb_lower) / (bb_upper - bb_lower)


def atr_14(high: pl.Series, low: pl.Series, close: pl.Series) -> pl.Series:
    return atr(high, low, close, length=14)


# --- @parameterize replaces individual roc_N and return_N functions ---
# (See Phase 3 for full implementation)


def overnight_return(open_price: pl.Series, close: pl.Series) -> pl.Series:
    prev_close = close.shift(1)
    return (open_price - prev_close) / prev_close


def intraday_return(open_price: pl.Series, close: pl.Series) -> pl.Series:
    return (close - open_price) / open_price


def hl_range(high: pl.Series, low: pl.Series, close: pl.Series) -> pl.Series:
    return (high - low) / close


def volume_ma_20(volume: pl.Series) -> pl.Series:
    return volume.rolling_mean(window_size=20)


def volume_roc_5(volume: pl.Series) -> pl.Series:
    return volume.pct_change(5)


def obv(close: pl.Series, volume: pl.Series) -> pl.Series:
    return obv_indicator(close, volume)


def volume_ratio(volume: pl.Series, volume_ma_20: pl.Series) -> pl.Series:
    return volume / volume_ma_20


def day_of_week(date: pl.Series) -> pl.Series:
    return date.dt.weekday()


def day_of_month(date: pl.Series) -> pl.Series:
    return date.dt.day()


def month(date: pl.Series) -> pl.Series:
    return date.dt.month()


def quarter(date: pl.Series) -> pl.Series:
    return date.dt.quarter()


def days_to_month_end(date: pl.Series) -> pl.Series:
    return (date.dt.month_end() - date).dt.total_days()


def trading_day_of_month(ticker: pl.Series, date: pl.Series) -> pl.Series:
    return (
        pl.DataFrame({"ticker": ticker, "date": date})
        .with_row_index("row_nr")
        .with_columns(
            (pl.int_range(pl.len()).over(["ticker", pl.col("date").dt.year(), pl.col("date").dt.month()]) + 1).alias(
                "trading_day_of_month"
            )
        )
        .sort("row_nr")["trading_day_of_month"]
    )


def volatility_20(returns: pl.Series) -> pl.Series:
    annualization_factor = float(np.sqrt(252))
    return returns.rolling_std(window_size=20) * annualization_factor


def next_day_return(close: pl.Series) -> pl.Series:
    return close.shift(-1) / close - 1
```

#### 4.2.5 New Module: `features/dag/enrichments_04.py` (Gold Layer — External Data Joins)

Move the imperative merge logic from `features/engineering.py` and standalone modules into Hamilton DAG nodes.

**Key challenge:** The current merge functions take a DuckDB connection and execute SQL queries. Hamilton nodes are pure functions that take inputs and produce outputs. We need to inject the DuckDB connection as a config/adapter parameter.

**Design decision — DuckDB as DAG input:**

The DuckDB connection is injected via `dr.execute(inputs={"duckdb_conn": conn})`. Each enrichment node takes `duckdb_conn` + `features_df` (the upstream Gold frame) and returns an enriched DataFrame.

```python
"""Gold layer: external data enrichment joins (sentiment, analyst, SEC, macro)."""

from __future__ import annotations

import duckdb
from datetime import date

import polars as pl
import structlog

from equity_lake.core.paths import (
    SILVER_NEWS_SENTIMENT_DIR,
    SILVER_SOCIAL_SENTIMENT_DIR,
    SILVER_PROCESSED_ARTICLES_DIR,
    SILVER_ANALYST_RATINGS_DIR,
    SILVER_SEC_EXTRACTIONS_DIR,
    BRONZE_MACRO_DIR,
)
from equity_lake.storage.lake_reader import duckdb_scan_for

logger = structlog.get_logger()


def news_sentiment(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Left join aggregated Finnhub news sentiment onto feature frame."""
    # Logic migrated from FeatureEngineer.merge_sentiment_features()
    # ... (identical SQL + join logic)


def social_sentiment(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Left join aggregated social sentiment."""
    # Logic migrated from FeatureEngineer.merge_social_sentiment_features()


def enriched_sentiment(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Left join LLM-enriched article-ticker sentiment from silver layer."""
    # Logic migrated from features/enriched_sentiment.py::merge_enriched_sentiment_features()


def analyst_ratings(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Left join analyst consensus + price targets."""
    # Logic migrated from features/analyst_features.py::merge_analyst_rating_features()


def sec_extractions(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """ASOF join SEC filing extractions (point-in-time)."""
    # Logic migrated from features/sec_features.py::merge_sec_features()


def cross_modal_sentiment(features_df: pl.DataFrame) -> pl.DataFrame:
    """Derived cross-modal sentiment features."""
    # Logic migrated from FeatureEngineer.add_cross_modal_sentiment_features()


def macro_features(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """Pivot macro indicators, forward-fill, left join."""
    # Logic migrated from FeatureEngineer.merge_macro_features()
```

**DAG dependency chain for enrichments:**

```
features_03.py outputs → features_df (technical indicators + OHLCV)
                          ↓
enrichments_04.py:  news_sentiment(features_df, conn, dates) → enriched_1
                    social_sentiment(enriched_1, conn, dates) → enriched_2
                    enriched_sentiment(enriched_2, conn, dates) → enriched_3
                    analyst_ratings(enriched_3, conn, dates) → enriched_4
                    sec_extractions(enriched_4, conn, dates) → enriched_5
                    cross_modal_sentiment(enriched_5) → enriched_6
                    macro_features(enriched_6, conn, dates) → final_features
```

**Problem — sequential dependency chain:** Each enrichment node depends on the previous one's output. This creates a long linear chain. The node names must be unique (e.g., `news_sentiment` outputs a DataFrame, then `social_sentiment` takes `news_sentiment` as input).

Hamilton resolves this automatically via function signatures. But the final output variable name changes depending on which enrichments are enabled.

**Solution — conditional execution via config:**

```python
from hamilton.function_modifiers import config

@config.when(enable_news_sentiment=True)
def features_with_news(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
) -> pl.DataFrame:
    """News sentiment merge — only included when enable_news_sentiment=True."""
    ...

@config.when(enable_news_sentiment=False)
def features_with_news(features_df: pl.DataFrame) -> pl.DataFrame:
    """Passthrough when news sentiment disabled."""
    return features_df
```

Then the next enrichment node takes `features_with_news` as input, regardless of whether the merge actually happened.

**Full chain with config variants:**

```python
@config.when(enable_news_sentiment=True)
def enriched_step1(features_df, duckdb_conn, start_date, end_date) -> pl.DataFrame:
    ...  # merge news sentiment

@config.when(enable_news_sentiment=False)
def enriched_step1(features_df) -> pl.DataFrame:
    return features_df  # passthrough


@config.when(enable_social_sentiment=True)
def enriched_step2(enriched_step1, duckdb_conn, start_date, end_date) -> pl.DataFrame:
    ...  # merge social sentiment

@config.when(enable_social_sentiment=False)
def enriched_step2(enriched_step1) -> pl.DataFrame:
    return enriched_step1  # passthrough
```

**Problem with this approach:** The `@config.when` variant approach requires N×2 functions for N enrichments (one active, one passthrough). This is verbose.

**Alternative — single enrichment pipeline node:**

Instead of one node per enrichment, create a single `enriched_features` node that internally calls all enabled merges:

```python
def enriched_features(
    features_df: pl.DataFrame,
    duckdb_conn: duckdb.DuckDBPyConnection,
    start_date: date,
    end_date: date,
    enable_news_sentiment: bool = False,
    enable_social_sentiment: bool = False,
    enable_enriched_sentiment: bool = False,
    enable_analyst_ratings: bool = False,
    enable_sec_features: bool = False,
    enable_macro: bool = True,
) -> pl.DataFrame:
    """Apply all enabled external data enrichments sequentially."""
    result = features_df
    if enable_news_sentiment:
        result = _merge_news_sentiment(result, duckdb_conn, start_date, end_date)
    if enable_social_sentiment:
        result = _merge_social_sentiment(result, duckdb_conn, start_date, end_date)
    if enable_enriched_sentiment:
        result = _merge_enriched_sentiment(result, duckdb_conn, start_date, end_date)
    if enable_analyst_ratings:
        result = _merge_analyst_ratings(result, duckdb_conn, start_date, end_date)
    if enable_sec_features:
        result = _merge_sec_extractions(result, duckdb_conn, start_date, end_date)
    result = _add_cross_modal(result)
    if enable_macro:
        result = _merge_macro(result, duckdb_conn, start_date, end_date)
    return result
```

**Decision: Use the single-node approach.** Rationale:
- Fewer DAG nodes, cleaner lineage graph
- The boolean flags are passed as inputs via `dr.execute(inputs={...})`
- Individual merge logic still lives in private functions for testability
- Can be refactored to per-node later if finer-grained lineage is needed

#### 4.2.6 Updated Driver Assembly

**File:** `features/pipeline.py`

```python
FEATURE_SCHEMA_VERSION = 3  # Bump from 2 → 3

class FeaturePipeline:
    DEFAULT_FEATURES = [
        # ... all feature names (same as current, possibly with @parameterize names)
    ]

    def __init__(self, enable_cache: bool = False):
        self.enable_cache = enable_cache
        self._driver = self._build_driver()

    def _build_driver(self) -> Any:
        from hamilton import driver

        from equity_lake.features.dag import raw_01, clean_02, features_03, enrichments_04

        adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
        builder = (
            driver.Builder()
            .with_modules(raw_01, clean_02, features_03, enrichments_04)
            .with_adapter(adapter)
        )
        if self.enable_cache:
            builder = builder.with_cache()
        return builder.build()
```

**Updated `compute()` method** — pass enrichment flags + DuckDB conn:

```python
def compute(
    self,
    price_data: FrameLike,
    features: list[str] | None = None,
    inputs: dict[str, Any] | None = None,
) -> pl.DataFrame:
    requested = features or self.DEFAULT_FEATURES
    execution_inputs = {"price_data": ensure_polars(price_data)}
    if inputs:
        execution_inputs.update(inputs)
    result = self._driver.execute(requested, inputs=execution_inputs)
    # ... same post-processing as current
```

**`DEFAULT_FEATURES` update:** When requesting final output, request `enriched_features` if enrichment flags are passed, otherwise request the last features_03 output node.

```python
DEFAULT_FEATURES = [
    "ticker", "date", "open_price", "high", "low", "close", "volume",
    "rsi_14", "macd", "macd_signal", "macd_histogram",
    "bb_upper", "bb_middle", "bb_lower", "bb_width", "bb_pct",
    "atr_14", "roc_5", "roc_10", "roc_20",
    "return_1d", "return_5d", "return_10d", "return_20d",
    "overnight_return", "intraday_return", "hl_range",
    "volume_ma_20", "volume_roc_5", "obv", "volume_ratio",
    "day_of_week", "day_of_month", "month", "quarter",
    "days_to_month_end", "trading_day_of_month",
    "volatility_20", "next_day_return",
]
```

When enrichments are enabled, the caller adds `"enriched_features"` to the requested list and removes the individual feature columns (since `enriched_features` returns a complete DataFrame).

**Simplified execution model:**

```python
# In FeatureEngineer.generate_features():
if any_enrichment_enabled:
    final_features = self.feature_pipeline.compute(
        ticker_df,
        features=["enriched_features"],
        inputs={
            "duckdb_conn": self.conn,
            "start_date": start_date,
            "end_date": end_date,
            "enable_news_sentiment": include_sentiment,
            "enable_social_sentiment": include_social_sentiment,
            "enable_enriched_sentiment": include_enriched_sentiment,
            "enable_analyst_ratings": include_analyst_ratings,
            "enable_sec_features": include_sec_features,
            "enable_macro": include_macro,
        },
    )
else:
    final_features = self.feature_pipeline.compute(ticker_df)
```

**Problem with per-ticker enrichment:** The current model runs the Hamilton DAG per-ticker, then concatenates. But enrichments query DuckDB for ALL tickers at once (the SQL queries filter by ticker list). If we run enrichments inside the per-ticker DAG, we'd run N DuckDB queries (one per ticker) instead of 1 batch query.

**Solution — two-phase execution:**

```python
# Phase 1: Per-ticker technical indicators (current model)
for ticker in tickers:
    computed = self.feature_pipeline.compute(ticker_df)
    result_dfs.append(computed)
features_df = pl.concat(result_dfs)

# Phase 2: Batch enrichment (single pass, all tickers)
if any_enrichment_enabled:
    features_df = self.feature_pipeline.compute_enrichments(
        features_df,
        inputs={
            "duckdb_conn": self.conn,
            "start_date": start_date,
            "end_date": end_date,
            "enable_news_sentiment": include_sentiment,
            # ...
        },
    )
```

This requires `FeaturePipeline` to expose a separate `compute_enrichments()` method that executes only the `enrichments_04` module nodes.

**Alternative — enrichments as a separate DAG:**

Register two drivers: one for technical indicators (per-ticker), one for enrichments (batch). This is cleaner separation but requires managing two drivers.

**Decision: Single DAG, two-phase execution.** The driver can execute different subsets of final variables. Phase 1 requests technical indicator nodes; Phase 2 requests `enriched_features`.

```python
class FeaturePipeline:
    def compute_technical(self, price_data, features=None) -> pl.DataFrame:
        """Phase 1: per-ticker technical indicators."""
        return self._driver.execute(
            features or self.TECHNICAL_FEATURES,
            inputs={"price_data": ensure_polars(price_data)}
        )

    def compute_enriched(self, features_df, **enrichment_flags) -> pl.DataFrame:
        """Phase 2: batch external data enrichments."""
        inputs = {"features_df": features_df, **enrichment_flags}
        return self._driver.execute(["enriched_features"], inputs=inputs)
```

#### 4.2.7 Updated `FeatureEngineer`

**File:** `features/engineering.py`

```python
class FeatureEngineer:
    def generate_features(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
        compute_target: bool = True,
        include_sentiment: bool = False,
        include_social_sentiment: bool = False,
        include_macro: bool = True,
        include_enriched_sentiment: bool = False,
        include_analyst_ratings: bool = False,
        include_sec_features: bool = False,
        normalize_cross_sectional: bool = False,
    ) -> pl.DataFrame:
        # ... query OHLCV from DuckDB view ...

        # Phase 1: Per-ticker technical indicators
        result_dfs: list[pl.DataFrame] = []
        technical_features = list(FeaturePipeline.TECHNICAL_FEATURES)
        if not compute_target:
            technical_features = [f for f in technical_features if f != "next_day_return"]

        for ticker in tqdm(tickers, desc="Computing features"):
            ticker_df = df.filter(pl.col("ticker") == ticker)
            if ticker_df.is_empty():
                continue
            if ticker_df.height < 60:
                continue
            computed = self.feature_pipeline.compute_technical(ticker_df, features=technical_features)
            result_dfs.append(computed)

        features_df = pl.concat(result_dfs, how="vertical_relaxed")
        # ... filter nulls ...

        # Phase 2: Batch enrichment via DAG
        any_enrichment = any([
            include_sentiment, include_social_sentiment, include_macro,
            include_enriched_sentiment, include_analyst_ratings, include_sec_features,
        ])
        if any_enrichment:
            features_df = self.feature_pipeline.compute_enriched(
                features_df,
                duckdb_conn=self.conn,
                start_date=start_date,
                end_date=end_date,
                enable_news_sentiment=include_sentiment,
                enable_social_sentiment=include_social_sentiment,
                enable_enriched_sentiment=include_enriched_sentiment,
                enable_analyst_ratings=include_analyst_ratings,
                enable_sec_features=include_sec_features,
                enable_macro=include_macro,
            )

        if normalize_cross_sectional:
            features_df = self.zscore_cross_sectional(features_df)

        return features_df
```

Merge methods (`merge_sentiment_features`, `merge_social_sentiment_features`, `add_cross_modal_sentiment_features`, `merge_macro_features`) are removed from `FeatureEngineer` — their logic now lives in `dag/enrichments_04.py`.

The standalone modules (`features/enriched_sentiment.py`, `features/analyst_features.py`, `features/sec_features.py`, `features/sec_financial_features.py`) are deprecated. Their logic is moved into private functions in `dag/enrichments_04.py`.

#### 4.2.8 Testing

- Unit tests for each DAG module function (plain function calls, no framework)
- Integration test: `FeaturePipeline.compute_technical()` with sample OHLCV data
- Integration test: `FeaturePipeline.compute_enriched()` with mock DuckDB connection
- DAG lineage export: `export_lineage()` generates valid graph

---

### Phase 3: `@parameterize` Rolling Windows

**Goal:** Replace hand-written individual rolling functions with `@parameterize` decorators.

**Estimated effort:** 0.5 day

#### 4.3.1 Functions to Refactor

**Current (7 functions, `hamilton_features.py:107-132`):**

```python
def roc_5(close):  return roc(close, length=5)
def roc_10(close): return roc(close, length=10)
def roc_20(close): return roc(close, length=20)

def return_1d(close):  return close.pct_change(1)
def return_5d(close):  return close.pct_change(5)
def return_10d(close): return close.pct_change(10)
def return_20d(close): return close.pct_change(20)
```

**New (2 parameterized functions, `dag/features_03.py`):**

```python
from hamilton.function_modifiers import parameterize


@parameterize(
    roc_5={"length": 5},
    roc_10={"length": 10},
    roc_20={"length": 20},
)
def roc_pct(close: pl.Series, length: int) -> pl.Series:
    """Rate of change as percentage."""
    return roc(close, length=length)


@parameterize(
    return_1d={"window": 1},
    return_5d={"window": 5},
    return_10d={"window": 10},
    return_20d={"window": 20},
)
def pct_return(close: pl.Series, window: int) -> pl.Series:
    """N-day percentage return."""
    return close.pct_change(window)
```

**Hamilton strips the `__suffix` from the generated node name.** The `@parameterize` decorator generates nodes named `roc_5`, `roc_10`, `roc_20`, `return_1d`, `return_5d`, `return_10d`, `return_20d` — identical to the current hand-written function names. This means `DEFAULT_FEATURES` and all downstream consumers are unaffected.

**Additional `@parameterize` candidates (optional, future):**

```python
# Volume moving averages (currently only volume_ma_20)
@parameterize(
    volume_ma_5={"window": 5},
    volume_ma_20={"window": 20},
    volume_ma_50={"window": 50},
)
def volume_sma(volume: pl.Series, window: int) -> pl.Series:
    return volume.rolling_mean(window_size=window)


# SMAs (not currently in the DAG — new feature additions)
@parameterize(
    sma_7={"window": 7},
    sma_20={"window": 20},
    sma_50={"window": 50},
    sma_200={"window": 200},
)
def sma(close: pl.Series, window: int) -> pl.Series:
    return close.rolling_mean(window_size=window)


# EMAs (not currently in the DAG — new feature additions)
@parameterize(
    ema_12={"span": 12},
    ema_26={"span": 26},
    ema_50={"span": 50},
    ema_200={"span": 200},
)
def ema(close: pl.Series, span: int) -> pl.Series:
    return close.ewm_mean(span=span, adjust=False)
```

These are **optional additions** — they add new features but don't replace existing ones. Only implement if the ML models benefit from them.

#### 4.3.2 Testing

- Unit test: `roc_pct(close, length=5)` produces same output as old `roc_5(close)`
- Unit test: `pct_return(close, window=10)` produces same output as old `return_10d(close)`
- DAG test: `FeaturePipeline.compute()` with DEFAULT_FEATURES includes all parameterized nodes
- Column name verification: output columns are `roc_5`, `roc_10`, `roc_20`, `return_1d`, `return_5d`, `return_10d`, `return_20d`

---

### Phase 4: Boundary Validation

**Goal:** Add `@check_output` + Pydantic schemas at layer boundaries.

**Estimated effort:** 1 day

#### 4.4.1 Pydantic Schema Models

**New file:** `features/dag/schemas.py`

```python
"""Pydantic models for medallion layer boundary validation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class OHLCVCleanModel(BaseModel):
    """Silver boundary: validated OHLCV row."""

    ticker: str
    date: object  # datetime or date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    model_config = {"arbitrary_types_allowed": True}


class FeatureModel(BaseModel):
    """Gold boundary: validated feature row (key indicators)."""

    ticker: str
    date: object
    close: float
    rsi_14: float = Field(ge=0, le=100)
    macd: float
    volume: float = Field(ge=0)

    model_config = {"arbitrary_types_allowed": True}


class PredictionModel(BaseModel):
    """Platinum boundary: validated prediction output."""

    ticker: str
    date: object
    direction: str = Field(description="up | down")
    probability: float = Field(ge=0.0, le=1.0)
    model_version: str

    model_config = {"arbitrary_types_allowed": True}
```

#### 4.4.2 `@check_output` at Boundaries

**Silver boundary** (`dag/clean_02.py`):

```python
from hamilton.function_modifiers import check_output
from equity_lake.features.dag.schemas import OHLCVCleanModel


@check_output(schema=OHLCVCleanModel)
def validated_ohlcv(cleaned_ohlcv: pl.DataFrame) -> pl.DataFrame:
    return cleaned_ohlcv
```

**Gold boundary** (`dag/features_03.py`):

```python
@check_output(schema=FeatureModel)
def validated_features(
    ticker: pl.Series,
    date: pl.Series,
    close: pl.Series,
    rsi_14: pl.Series,
    macd: pl.Series,
    volume: pl.Series,
) -> pl.DataFrame:
    """Validation checkpoint at the Gold layer boundary."""
    return pl.DataFrame({
        "ticker": ticker,
        "date": date,
        "close": close,
        "rsi_14": rsi_14,
        "macd": macd,
        "volume": volume,
    })
```

**Design rule:** `@check_output` is applied ONLY at layer boundaries (Bronze→Silver, Silver→Gold). Not on every node.

#### 4.4.3 Platinum Boundary (pointblank)

**File:** `ml/__init__.py` or `ml/forecasting.py`

```python
import pointblank as pb


def validate_predictions(df: pl.DataFrame) -> bool:
    """Validate prediction output before writing to Platinum."""
    validation = pb.Validate(df=df)
    validation.col_vals_gt(columns="probability", value=0.0)
    validation.col_vals_lt(columns="probability", value=1.0)
    validation.col_vals_in_set(columns="direction", set=["up", "down"])
    validation.col_vals_not_null(columns="ticker")
    validation.col_vals_not_null(columns="date")
    return validation.all_passed()
```

#### 4.4.4 Testing

- Unit test: `OHLCVCleanModel` rejects negative prices
- Unit test: `FeatureModel` rejects RSI outside [0, 100]
- Unit test: `PredictionModel` rejects probability outside [0, 1]
- Integration test: `@check_output` raises on invalid data in DAG execution

---

### Phase 5: Platinum Persistence

**Goal:** Persist ML predictions to `04_platinum/predictions/`.

**Estimated effort:** 0.5 day

#### 4.5.1 Updated `run_prediction_job()`

**File:** `ml/__init__.py`

```python
def run_prediction_job(
    *,
    trading_date: date,
    tickers: list[str],
    model_dir: str | None = None,
) -> tuple[bool, dict[str, dict[str, Any]]]:
    from equity_lake.ml.forecasting import PriceForecaster

    forecaster = PriceForecaster(model_dir=model_dir)
    ticker_results: dict[str, dict[str, Any]] = {}
    prediction_rows: list[dict[str, Any]] = []
    all_success = True

    try:
        for ticker in tickers:
            try:
                prediction = forecaster.predict(ticker=ticker, date=trading_date)
                ticker_results[ticker] = {"success": True, "prediction": prediction}
                prediction_rows.append({
                    "ticker": ticker,
                    "date": trading_date,
                    "direction": prediction.get("direction", "unknown"),
                    "probability": prediction.get("probability", 0.0),
                    "model_version": prediction.get("model_version", "unknown"),
                    "model_mode": prediction.get("model_mode", "unknown"),
                })
            except Exception as exc:
                ticker_results[ticker] = {"success": False, "error": str(exc)}
                all_success = False
    finally:
        forecaster.close()

    # Persist to Platinum layer
    if prediction_rows:
        import polars as pl
        from equity_lake.storage.delta import write_delta

        predictions_df = pl.DataFrame(prediction_rows)
        write_delta(
            predictions_df,
            market="04_platinum/predictions",
            mode="merge",
            partition_by=["date"],
        )

    return all_success, ticker_results
```

#### 4.5.2 Prediction Schema

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | str | Stock ticker |
| `date` | date | Trading date (partition key) |
| `direction` | str | "up" or "down" |
| `probability` | float | Model confidence [0.0, 1.0] |
| `model_version` | str | Model identifier |
| `model_mode` | str | "v1_direction" or "v2_meta_label" |

Dedup key: `(ticker, date)` — `merge_delta` upserts.

#### 4.5.3 CLI Query Support

Add a DuckDB query helper to read predictions:

```bash
uv run equity query --sql "SELECT * FROM delta_scan('data/lake/04_platinum/predictions') WHERE date = '2026-06-15'"
```

#### 4.5.4 Testing

- Unit test: `run_prediction_job()` writes to Platinum when predictions exist
- Unit test: empty predictions don't write
- Integration test: predictions are queryable after write

---

### Phase 6: Pipeline Orchestrator Update

**Goal:** Update `execute_eod_pipeline()` to use new paths and layered DAG.

**Estimated effort:** 0.5 day

#### 4.6.1 Changes to `pipeline.py`

```python
# Feature output path change (via run_feature_job → write_to_partitioned_parquet)
# Already handled in Phase 1 — "03_gold/features" instead of "features"

# ML persistence (via run_prediction_job)
# Already handled in Phase 5 — writes to "04_platinum/predictions"

# No structural changes to execute_eod_pipeline() itself
# The orchestration flow stays: ingestion → features → ML
```

The orchestrator code is mostly unchanged because `run_feature_job()` and `run_prediction_job()` encapsulate the storage details.

#### 4.6.2 ARCHITECTURE.md Update

Update the project layout section to reflect the new directory structure and DAG modules.

---

## 5. Dependency Graph Between Phases

```
Phase 1 (Storage Migration)
    │
    ├──→ Phase 2 (DAG Modules) ──→ Phase 3 (@parameterize)
    │                                   │
    │                                   └──→ Phase 4 (Validation)
    │                                            │
    └──→ Phase 5 (Platinum) ─────────────────────┘
                 │
                 └──→ Phase 6 (Orchestrator)
```

- Phase 1 must complete first (all paths change)
- Phase 2 depends on Phase 1 (new paths in enrichments_04.py)
- Phase 3 depends on Phase 2 (operates on dag/features_03.py)
- Phase 4 depends on Phase 2 (adds `@check_output` to dag modules)
- Phase 5 depends on Phase 1 (needs Platinum dir) but is independent of Phase 2-4
- Phase 6 depends on all prior phases

**Recommended execution order:** 1 → 2 → 3 → 4 → 5 → 6

---

## 6. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Data migration corrupts partitions | High — data loss | `--dry-run` mode first; `mv` (not copy) is atomic on same filesystem; verify file counts before/after |
| Hamilton DAG node rename breaks models | Medium — ML inference fails | `@parameterize` preserves node names (`roc_5`, `return_1d` etc.); `DEFAULT_FEATURES` unchanged; test column output |
| DuckDB connection injection in DAG | Medium — execution error | Pass via `inputs={"duckdb_conn": conn}`; Hamilton supports arbitrary Python objects as inputs |
| Enrichment logic divergence during migration | Medium — feature drift | Keep old modules as deprecated wrappers that call new DAG functions during transition; remove after verification |
| `@check_output` performance overhead | Low | Apply only at boundaries (3 nodes), not every node; Hamilton validation is row-sampling, not full-scan |
| Per-ticker DAG + batch enrichment mismatch | Medium — incorrect features | Two-phase execution: Phase 1 per-ticker technicals, Phase 2 batch enrichments (same as current imperative model) |
| Backward compatibility break | Medium — existing scripts fail | Deprecated path aliases in `paths.py`; feature flag `EQUITY_MEDALLION_LAYOUT` to toggle old/new paths during transition |

---

## 7. Files Changed Summary

### New Files

| File | Purpose |
|------|---------|
| `scripts/migrate_to_medallion.py` | One-time data migration script |
| `features/dag/__init__.py` | DAG module package init |
| `features/dag/raw_01.py` | Bronze layer: OHLCV extraction |
| `features/dag/clean_02.py` | Silver layer: validation + cleaning |
| `features/dag/features_03.py` | Gold layer: technical indicators |
| `features/dag/enrichments_04.py` | Gold layer: external data joins |
| `features/dag/schemas.py` | Pydantic models for boundary validation |
| `tests/unit/features/dag/test_raw_01.py` | Bronze layer tests |
| `tests/unit/features/dag/test_clean_02.py` | Silver layer tests |
| `tests/unit/features/dag/test_features_03.py` | Gold layer tests |
| `tests/unit/features/dag/test_enrichments_04.py` | Enrichment tests |
| `tests/unit/features/dag/test_parameterize.py` | `@parameterize` regression tests |
| `tests/unit/test_medallion_migration.py` | Migration script tests |

### Modified Files

| File | Changes |
|------|---------|
| `core/paths.py` | Replace flat constants with layered constants + deprecated aliases |
| `ingestion/types.py` | Update `MARKET_DIR_MAP` values to medallion paths |
| `ingestion/writers.py` | Update `_dedupe_key_columns()` market strings |
| `features/__init__.py` | Update `write_to_partitioned_parquet` market string |
| `features/pipeline.py` | Multi-module driver assembly, two-phase compute, `FEATURE_SCHEMA_VERSION=3` |
| `features/engineering.py` | Remove merge methods, use two-phase DAG execution, update `_setup_views()` paths |
| `ml/__init__.py` | Add Platinum persistence in `run_prediction_job()` |
| `pipeline.py` | No structural changes (encapsulated by run_* functions) |
| `ARCHITECTURE.md` | Update project layout and storage sections |

### Deprecated Files (kept for backward compat, logic moved)

| File | Status |
|------|--------|
| `features/hamilton_features.py` | Logic moved to `dag/raw_01.py` + `dag/features_03.py` |
| `features/enriched_sentiment.py` | Logic moved to `dag/enrichments_04.py` |
| `features/analyst_features.py` | Logic moved to `dag/enrichments_04.py` |
| `features/sec_features.py` | Logic moved to `dag/enrichments_04.py` |
| `features/sec_financial_features.py` | Logic moved to `dag/enrichments_04.py` |

---

## 8. Verification Checklist

After all phases complete:

- [ ] `uv run ruff check .` passes clean
- [ ] `uv run mypy src/equity_lake/` passes clean
- [ ] `uv run pytest -n auto` — all existing tests pass
- [ ] Migration script `--dry-run` succeeds on current data
- [ ] Migration script `--execute` succeeds; file counts match before/after
- [ ] `execute_eod_pipeline(dry_run=True)` completes without errors
- [ ] `FeaturePipeline.export_lineage()` generates valid DAG image with all layers
- [ ] DuckDB can query all medallion layer paths
- [ ] ML predictions persist to `04_platinum/predictions/`
- [ ] `@parameterize` produces identical column names to current hand-written functions
- [ ] `@check_output` catches invalid data at Silver and Gold boundaries
- [ ] `ARCHITECTURE.md` updated with new layout

---

## 9. Estimated Timeline

| Phase | Effort | Dependencies |
|-------|--------|-------------|
| Phase 1: Storage Migration | 1 day | None |
| Phase 2: DAG Layered Modules | 2-3 days | Phase 1 |
| Phase 3: `@parameterize` | 0.5 day | Phase 2 |
| Phase 4: Boundary Validation | 1 day | Phase 2 |
| Phase 5: Platinum Persistence | 0.5 day | Phase 1 |
| Phase 6: Orchestrator + Docs | 0.5 day | All |
| **Total** | **5.5-6.5 days** | |
