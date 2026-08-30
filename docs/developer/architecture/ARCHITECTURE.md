# Architecture

**Last Updated**: 2026-08-29
**Project**: Equity EOD Data Pipeline

> This is the canonical architecture document. The former root-level
> `ARCHITECTURE.md` is a pointer to this page (moved 2026-08-29); this page is
> also published in the MkDocs site.

## Current Canonical Architecture

### Local-First ETL With Explicit Module Ownership

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Cloud (S3)    │    │   Local APIs    │    │  China A-shares │    │   Macro APIs    │
│ US Historical   │    │  US/HK/SG/JPX  │    │  akshare/efin   │    │  FRED / yfinance│
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │                      │
         │ One-time sync        │ Daily fetch          │ Daily fetch          │ Daily fetch
         ▼                      ▼                      ▼                      ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              Ingestion Layer                                          │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │ S3 Syncer   │  │ yfinance    │  │ akshare     │  │ Macro       │                 │
│  │ (bootstrap) │  │ Fetcher     │  │ Fetcher     │  │ Fetcher     │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘                 │
│         │                │                    │                 │                    │
│         └────────────────┴────────────────────┴─────────────────┘                    │
│                          │                                                            │
│                   Router + Orchestrator                                              │
│                   (retry, validation, backfill)                                      │
└───────────────────────────┬──────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              Storage Layer                                            │
│                  Numbered Medallion Delta Tables                                     │
│  data/lake/01_bronze/<dataset>/date=YYYY-MM-DD/*.parquet                            │
│  data/lake/02_silver/<dataset>/date=YYYY-MM-DD/*.parquet                            │
│  data/lake/03_gold/features/ and 04_platinum/predictions/                            │
└───────────────────────────┬──────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              Query / Feature / ML Layer                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐                       │
│  │ DuckDB Engine   │  │ Feature Eng     │  │ ML Pipeline     │                       │
│  │ (named queries) │  │ (Hamilton+Polars)│ │ (FeatureLoader → │                       │
│  │                 │  │                 │  │  PriceForecaster)│                       │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘                       │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Equity Lake is a multi-market equity data platform on this four-layer medallion
architecture (Bronze / Silver / Gold / Platinum). It ingests OHLCV market data,
news sentiment, analyst ratings, and SEC filings, computes technical and
enriched features via a Hamilton DAG, and runs ML inference for price
direction prediction.

### Key Design Principles

1. **Local-First**: After initial S3 bootstrap, all operations run locally
2. **Idempotent**: Safe to re-run any operation without side effects
3. **Failure Isolation**: Optional enrichments may degrade; required price failures block dependent outputs
4. **Partitioned Storage**: Numbered medallion Delta tables partitioned by date
5. **Zero-Copy Queries**: DuckDB reads Parquet/Delta files directly without loading
6. **Single Canonical Path**: no supported duplicate source tree or package-root shims
7. **Composition over Inheritance**: ML pipeline decomposed into `FeatureLoader` + `trainer` utilities

### Technology Choices

- **Polars** is the primary DataFrame engine (ADR-0003). Pandas only at external library boundaries (yfinance, akshare, efinance, FinanceDataReader).
- **DuckDB** for analytical queries over Parquet/Delta partitions.
- **Delta Lake** for ACID writes, merge/upsert, and time-travel.
- **Hamilton** for declarative DAG composition with lineage export (catalog).
- **pointblank** for Polars-native data validation at ingestion and Platinum write boundaries (ADR-0007).
- **structlog** for structured JSON logging with correlation IDs.
- **tenacity** for retry/backoff on all source fetchers.

## Storage Layout

```
data/lake/
├── 01_bronze/                      # Immutable raw data (Delta, date-partitioned)
│   ├── market_data/
│   │   ├── us_equity/
│   │   ├── cn_ashare/
│   │   ├── hk_sg_equity/
│   │   ├── jpx_equity/
│   │   └── krx_equity/
│   ├── raw_articles/               # RSS/Reddit/StockTwits/transcript text
│   └── macro/                      # Long-format macro indicators
├── 02_silver/                      # Validated, cleaned, deduped (Delta)
│   ├── news_sentiment/             # Finnhub news + VADER sentiment
│   ├── social_sentiment/           # Finnhub social sentiment
│   ├── processed_articles/         # LLM-enriched article-ticker pairs
│   ├── sec_extractions/            # LLM-extracted SEC filing insights
│   ├── analyst_ratings/            # Analyst consensus + price targets
│   └── sec_financials/             # SEC XBRL financial data
├── 03_gold/                        # Feature engineering output (Delta)
│   └── features/
└── 04_platinum/                    # ML predictions and signals (Delta)
    └── predictions/
```

All cataloged tables are date-partitioned Delta tables whose data files are
Parquet (ADR-0001). Path constants live in `core/paths.py`; market-data
constants (`US_EQUITY_DIR`, …) point directly at their medallion locations,
while `US_NEWS_DIR`, `US_SOCIAL_SENTIMENT_DIR`, and `SEC_EXTRACTIONS_DIR` are
deprecated aliases for their silver paths.

## Canonical Module Boundaries

- `src/equity_lake/sources/`: all market and external-data source adapters (including `macro.py`)
- `src/equity_lake/ingestion/`: authoritative ingestion runtime (`orchestrator.py`, `router.py`, `backfill.py`, `writers.py`, `parallel.py`)
- `src/equity_lake/storage/`: persistence layer (`duckdb.py`, `delta.py`, `s3_sync.py`, `lake_reader.py`, `examples.py`)
- `src/equity_lake/core/config.py`: canonical settings and ticker-config module
- `src/equity_lake/core/ticker_utils.py`: shared ticker symbol conversion utilities
- `src/equity_lake/core/paths.py`: directory constants and market-to-path mappings
- `src/equity_lake/ml/forecasting.py`: public forecasting orchestrator (`PriceForecaster`)
- `src/equity_lake/ml/feature_loader.py`: DuckDB-backed feature loading (`FeatureLoader`)
- `src/equity_lake/ml/trainer.py`: extracted training utilities (`compute_class_weights`, `compute_shap_importance`, `optimize_threshold`)
- `src/equity_lake/ml/candidates.py`, `labeling.py`, `validation.py`: ML helper modules

Unsupported after the June 2026 refactor:

- `equity_lake.ingestion.sources.*`
- package-root helper imports from `equity_lake`
- legacy flat modules such as `equity_lake.run_pipeline`, `equity_lake.pipeline`, `equity_lake.feature_jobs`, `equity_lake.ml_jobs`, `equity_lake.fetch_macro`, `equity_lake.backfill_data`
- `equity_lake.core.dag`, `equity_lake.core.storage`, `equity_lake.core.runtime`
- `equity_lake.cli.news`, `equity_lake.cli.sentiment`, `equity_lake.cli.signal`, `equity_lake.cli.config`, `equity_lake.cli.loader`
- `equity_lake.pipelines/` (removed)
- `equity_lake.loaders.options_flow_loader`, `equity_lake.loaders.reddit_loader` (removed)
- `equity_lake.ml.training` (removed — split into `trainer.py` + `feature_loader.py`)

---

## System Layers

### 1. Ingestion Layer

**Purpose**: Fetch market data from external APIs and write to local storage

**Locations**:

- runtime orchestration: `src/equity_lake/ingestion/orchestrator.py`
- market routing: `src/equity_lake/ingestion/router.py`
- backfill: `src/equity_lake/ingestion/backfill.py`
- parallel execution: `src/equity_lake/ingestion/parallel.py`
- source adapters: `src/equity_lake/sources/`
- CLI entrypoint: `src/equity_lake/cli/commands/data.py`

**Key Components**:

#### Base Fetcher (`src/equity_lake/sources/base.py`)
```python
class MarketDataFetcher:
    def fetch(self, trading_date: date) -> pl.DataFrame:
        """Fetch data for a specific date (Polars; ADR-0003)."""

    def _retry_on_failure(self, func, *args, **kwargs):
        """Exponential-backoff retry via tenacity — the decorator is built by
        core/retry.build_retry_decorator(...). Only TransientError
        (network/timeout/5xx/408/429) is retried; other errors propagate."""
```
Fetchers return `pl.DataFrame` (pandas appears only inside third-party library
boundaries and is standardized immediately). `standardize_columns()` — a
module-level function in `sources/base.py`, not an inheritance hook —
lowercases, renames, normalizes temporal columns, and selects the known schema.

#### Market-Specific Fetchers
- **USEquityFetcher** (`sources/us.py`): US market via yfinance, batch downloads, rate limiting
- **CNAshareFetcher** (`sources/cn.py`): China A-shares via akshare, column mapping
- **HKSGEquityFetcher** (`sources/hk_sg.py`): HK/SG markets via yfinance
- **CNHybridFetcher** (`sources/cn_hybrid.py`): akshare + efinance fallback, uses `core/ticker_utils.cn_to_yahoo_symbol`
- **JPXEquityFetcher** (`sources/jpx.py`): Japan exchange via yfinance
- **KRXEquityFetcher** (`sources/krx.py`): Korea exchange via FinanceDataReader (lazily imported)
- **MacroFetcher** (`sources/macro.py`): Macro indicators (FRED, DXY, VIX, yields, commodities). Standalone class — does not extend `MarketDataFetcher`.

#### Router (`ingestion/router.py`)
- Maps market identifiers to concrete fetcher classes
- Provides `fetch_market_data()` / `fetch_market_data_with_config()` entry points
- Routes macro through `MacroFetcher`, all equity markets through `MarketDataFetcher` subclasses

#### Orchestrator (`ingestion/orchestrator.py`)
- `run_daily_ingestion()`: Main entry point for a single trading day
- Delegates to `router.fetch_market_data()` for each market
- Parallel market fetch via `parallel.fetch_markets_parallel()`
- Delta-aware skip-existing checks and writes
- Macro data routed through router (no special-cased write branch) — every market, including `macro`, resolves its target through `ingestion/types.MARKET_DIR_MAP` and writes via `writers.upsert_dataset()`

#### Backfill (`ingestion/backfill.py`)
- `backfill_date_range()`: Iterates date range, calling `run_daily_ingestion()` per trading day
- No duplicated fetcher logic — pure delegation to orchestrator
- CLI: `uv run equity backfill --start 2023-04-06 --end 2026-04-05`

#### Parallel Utilities (`ingestion/parallel.py`)
- `fetch_markets_parallel()`: ThreadPoolExecutor for concurrent market fetching
- `fetch_items_parallel()`: Generic parallel item fetcher with sequential fallback and rate limiting
  - Used by `sources/news.py` and `sources/sentiment.py`
- `FetchResult`: Structured result with success/error/duration metadata
- `summarize_results()`: Aggregate fetch results

#### Parallel Fetching

`equity ingest` always runs with `parallel=True` (hardcoded in
`cli/commands/data.py`); `run_daily_ingestion(parallel=...)` defaults to
sequential when called as a library. In parallel mode the orchestrator wraps
the whole fan-out in one `correlation_context()` (so all markets share a single
correlation ID) and hands `fetch_markets_parallel()` a per-market
`fetch_func_map`. That helper submits one future per market to a
`ThreadPoolExecutor` — `max_workers` defaults to `len(markets)` when not set —
and collects a `FetchResult` (success, error, `duration_seconds`) per market as
futures complete. A single market's failure is captured on its `FetchResult`
without aborting the others; `summarize_results()` then aggregates
total/succeeded/failed counts into the `parallel_ingestion_summary` log event.
Correlation IDs are attached by the `add_correlation_id` structlog processor on
every log line regardless of execution mode.

**Data Flow**:
```
CLI Request → Orchestrator → Router → sources/* → Validation → writers.upsert_dataset() → Delta
                                     ↘ MacroFetcher → writers.upsert_dataset() → 01_bronze/macro
```

---

### 2. Storage Layer

**Purpose**: Persist and retrieve market data efficiently

**Location**: `src/equity_lake/storage/` (`duckdb.py`, `delta.py`, `s3_sync.py`, `lake_reader.py`, `examples.py`)

#### S3 Sync Module (`s3_sync.py`)
One-time bootstrap of US historical data from S3. Auto-detects sync tool (s5cmd > AWS CLI). The bucket is a root URL whose remote tree mirrors the numbered medallion layout (`<bucket>/01_bronze/market_data/<market_dir>`); each equity market is synced separately so s5cmd can parallelize per market.

#### Delta Storage (`delta.py`)
Runtime tables live under numbered medallion paths such as
`data/lake/01_bronze/market_data/us_equity/`. They are Delta tables
partitioned by `date`; their data files are Parquet and use the standardized
OHLCV schema. All ingestion writes go through `merge_delta()` — the canonical
writer entry point is `ingestion/writers.upsert_dataset()`.

#### Writers (`ingestion/writers.py`)
`upsert_dataset()` is the single canonical writer for every dataset — market
data, macro, news, and predictions alike. It converts to Polars, runs
`validate_schema()` (a required-column presence / all-null check), optionally
runs pointblank quality validation (`validate_quality=True`, ADR-0007), and
merges into the date-partitioned Delta table keyed by per-dataset dedupe
columns. There are no per-dataset writer functions such as
`write_market_data()` or `write_macro_data()`.

#### DuckDB Module (`duckdb.py`)
```python
class EquityDataDB:
    def __init__(self, db_path: str | Path | None = ":memory:"):
        self.con = duckdb.connect(self.db_path)

    def query(self, sql: str) -> pl.DataFrame:
        """Execute SQL and return a Polars DataFrame"""

    def run_named_query(self, name: str, **kwargs) -> pl.DataFrame:
        """Execute a predefined named query (delegates to storage/examples.py)"""

    def run_all_queries(self) -> dict[str, pl.DataFrame]:
        """Run all predefined queries and return results dict"""
```

#### Query Examples (`examples.py`)
`QueryExamples` holds the predefined analytical queries (latest summary, top
volume, gainers/losers, cross-market, moving averages, volatility, market
stats, price range) plus `benchmark_queries()`. `EquityDataDB.QUERY_MAP` maps
CLI-friendly names (`top_volume`, `gainers_losers`, …) onto these methods,
powering `equity query --query <name>` and the no-argument "run all" mode.

#### Lake Reader (`lake_reader.py`)
```python
def duckdb_scan_for(market_path: Path) -> str:
    """Return DuckDB scan expression — tries delta_scan first, falls back to read_parquet."""
```
Used by `FeatureLoader`, `features/engineering.py`, and `ml/forecasting.py` for transparent Delta/Parquet access.

---

### 3. Feature Engineering Layer

**Purpose**: Transform raw OHLCV into analytical features

**Location**: `src/equity_lake/features/` (`pipeline.py`, `engineering.py`, `indicators.py`, and the `dag/` package below)

**Key Features**:
- Moving averages (SMA, EMA)
- Price momentum (RSI, MACD)
- Volatility metrics (Bollinger Bands, ATR)
- Volume indicators (OBV, VWAP)
- Price returns (daily, weekly, monthly)
- Cross-modal sentiment features

**Implementation**: Hamilton-backed Polars pipeline with pandas retained only at narrow third-party boundaries. Feature outputs carry a schema version and can optionally merge news and social sentiment.

#### Hamilton DAG (`features/dag/`)

The feature pipeline is a Hamilton DAG split into four medallion-layered
modules:

```
features/dag/
├── __init__.py           # Package init
├── raw_01.py             # Bronze: OHLCV column extraction (@check_output)
├── clean_02.py           # Silver: returns + validated_ohlcv boundary node
├── features_03.py        # Gold: technical indicators (@parameterize, @check_output)
├── enrichments_04.py     # Gold: external data joins (sentiment, analyst, SEC, macro)
├── schemas.py            # Pydantic models for layer boundary validation
└── polars_validators.py  # Custom Hamilton validators for pl.Series dtypes & ranges
```

##### Two-Phase Execution

The DAG executes in two phases:

1. **Phase 1 — Per-ticker technical indicators**
   (`FeaturePipeline.compute_technical`): runs the Bronze→Silver→Gold subgraph
   for each ticker independently; outputs are concatenated vertically.
   `include_target=True` appends `TARGET_FEATURES` (`["next_day_return"]`,
   `features/pipeline.py`) for training; it is omitted by default to prevent
   look-ahead leakage at inference time.

2. **Phase 2 — Batch external-data enrichments**
   (`FeaturePipeline.compute_enriched`): runs the `enriched_features` node once
   for all tickers. The DuckDB connection and date bounds are injected as DAG
   inputs. SQL merges interpolate only the lake scan expression via f-string;
   ticker and date values bind through `?` placeholders.

##### `@parameterize`

Hand-written `roc_5`/`roc_10`/`roc_20` and `return_1d`/`return_5d`/
`return_10d`/`return_20d` functions are replaced by two `@parameterize`
decorated functions (`roc_pct`, `pct_return`). Hamilton generates nodes with
identical names, so downstream consumers are unaffected.

##### Boundary Validation

- **Bronze→Silver**: `@check_output(data_type=float)` on `close` and `volume`
  in `raw_01.py`; the `validated_ohlcv` node in `clean_02.py` applies Pydantic
  schema validation and filters invalid rows
- **Silver→Gold**: `@check_output(range=(0.0, 100.0))` on `rsi_14` in
  `features_03.py`; the `validated_features` node enforces the Pydantic schema
  on the final feature frame
- **Write boundaries**: `validate_schema()` in `ingestion/writers.py` is a
  required-column presence / all-null check; pointblank contracts are enforced
  by `upsert_dataset(validate_quality=True)` via
  `validation/pipeline.ValidationPipeline` (ADR-0007)
- **Platinum**: `validate_predictions()` (`ml/__init__.py`) uses pointblank to
  enforce probability range, direction values, and non-null keys; intercepts
  writes to `04_platinum/predictions/`
- **Custom validators**: `polars_validators.py` provides
  `PolarsDataTypeValidator` and `PolarsRangeValidator`, wired into
  `@check_output` via `default_validator_candidates` so dtype/range constraints
  work on `pl.Series`

---

### 4. ML Pipeline

**Purpose**: Train and run price direction classifiers with meta-labeling

**Location**: `src/equity_lake/ml/`

**Architecture** (split from monolithic `forecasting.py`):

```
PriceForecaster (forecasting.py)
  ├── FeatureLoader (feature_loader.py)     — DuckDB connection + feature view setup
  │     └── duckdb_scan_for (lake_reader.py)
  ├── trainer.compute_class_weights()       — class imbalance handling
  ├── trainer.compute_shap_importance()      — SHAP feature attribution
  ├── trainer.optimize_threshold()           — F1/precision threshold search
  ├── candidates.py                         — candidate event generation
  ├── labeling.py                           — triple-barrier labeling
  └── validation.py                         — purged walk-forward validation
```

---

### 5. Signal Generation Layer

**Purpose**: Generate trading signals from features

**Location**: `src/equity_lake/signals/`

**Signal Types**:
- Trend following (moving average crossovers)
- Mean reversion (RSI overbought/oversold)
- Breakout (price channel breaks)
- Momentum (relative strength)
- ML-based: `v1_direction`, `v2_meta_label`

ML validation uses purged and embargoed walk-forward splits.

---

### 6. Sources Layer

**Purpose**: External API adapters for all data types

**Location**: `src/equity_lake/sources/`

**Shared Utilities**:
- `base.py`: `MarketDataFetcher` base class; retry is tenacity via `core/retry.build_retry_decorator`, wrapped by `_retry_on_failure` (only `TransientError` is retried)
- `news.py` / `sentiment.py`: Use `fetch_items_parallel()` from `ingestion/parallel.py`
- `macro.py`: `MacroFetcher` + `MacroIndicatorFetcher` hierarchy for FRED/yfinance macro data

---

## Pipeline Orchestration

```
Stage 1: Ingestion     → run_daily_ingestion()  → write to 01_bronze/
Stage 2: Features      → run_feature_job()      → write to 03_gold/features/
Stage 3: ML            → run_prediction_job()   → write to 04_platinum/predictions/
```

The module-level `execute_eod_pipeline()` in `src/equity_lake/pipeline.py`
chains all three stages and returns a per-stage results dict. Each stage is
independently callable via the `equity` CLI. Predictions carry a
`feature_schema_version` tag so downstream consumers can detect feature-set
drift between training and inference.

## Data Catalog

A Hamilton-powered catalog (`src/equity_lake/catalog/`) generates
`data/catalog.jsonl` from the DAG topology plus static dataset definitions,
then renders as an interactive Astro + React Flow site.

```
catalog/
├── models.py      # Pydantic: Catalog, DatasetEntry, NodeEntry, EdgeEntry, ColumnInfo
├── datasets.py    # 15 static DatasetEntry definitions (Bronze/Silver/Gold/Platinum)
├── builder.py     # Builds Hamilton driver, extracts nodes/edges from DAG tags
└── writer.py      # JSONL serialization (one object per line)
```

### Generation Flow

1. `build_catalog()` constructs a Hamilton `Driver` from the four DAG modules and calls `list_available_variables()`.
2. Each tagged node becomes a `NodeEntry` (tags supply `layer`, `category`, `produces`, `validators`). Nodes without a `layer` tag (Hamilton inputs like `price_data`) and internal wrappers (`*_raw`, `*_data_type_validator`, `*_range_validator`) are filtered out; `module` and `hamilton.*` tags are stripped from output.
3. `what_is_upstream_of()` traces edges into `EdgeEntry` records (self-references and duplicates removed).
4. Static `DatasetEntry` records from `datasets.py` anchor each medallion layer with paths, schemas, and descriptions.
5. `write_catalog_jsonl()` emits one JSON object per line (`type`: `catalog | dataset | node | edge`) for clean git diffs.

### CLI & Deployment

```bash
uv run equity catalog-generate               # regenerate data/catalog.jsonl
uv run equity catalog-generate -o /tmp.jsonl # custom output path
```

- **JSONL format** (`data/catalog.jsonl`): 15 datasets, 52 nodes, 100 edges. Git-tracked (source of truth for the frontend). Never edit it directly — regenerate via `equity catalog-generate`.
- **Astro frontend** (`docs/catalog/`): static site built at build time from `catalog.jsonl`, uses `@xyflow/react` v12 for DAG visualization.
- **GitHub Pages**: deployed via `.github/workflows/catalog-deploy.yml` (official `actions/deploy-pages`, base path `/equity_lake`).
- **Freshness CI** (`.github/workflows/catalog-check.yml`): on PRs touching `features/dag/**`, `catalog/**`, `core/schemas.py`, `core/paths.py`, `ingestion/writers.py`, or `data/catalog.jsonl`, regenerates the catalog and fails if `data/catalog.jsonl` would change — preventing stale catalogs from merging.

---

## Design Patterns

### 1. Strategy Pattern — Market Fetchers
`MarketDataFetcher` base with per-market implementations. `router.py` maps market strings to concrete fetcher classes.

### 2. Layered Base Classes — Market Fetchers
`MarketDataFetcher` provides concrete shared behavior (tenacity-backed `_retry_on_failure`, config-driven ticker resolution, default `fetch_range()` loop); `YFinanceBaseFetcher` adds yfinance batching and MultiIndex handling; market subclasses supply market name, fallback tickers, and rename overrides. Column standardization is the module-level `standardize_columns()` function in `sources/base.py`, not an inheritance hook.

### 3. Composition — ML Pipeline
`PriceForecaster` composes `FeatureLoader` (DuckDB lifecycle) and delegates to `trainer.py` functions, rather than inheriting from a shared base.

### 4. Protocol — Pluggable Seams
`ModelBackend` (`ml/backends.py`) and `Alerter` (`monitoring/alerting.py`) are `typing.Protocol` interfaces for swappable implementations.

### 5. Transparent Storage — Lake Reader
`duckdb_scan_for()` abstracts Delta vs. Parquet access, letting callers use a single scan expression without knowing the underlying format.

### 6. Delegation — Backfill
`backfill_date_range()` delegates to `run_daily_ingestion()` per date instead of reimplementing fetch logic.

---

## Data Flow

### Daily Ingestion Flow

```
┌──────────────────────────────────────────────────────────────┐
│ 1. CLI Command                                               │
│    uv run equity ingest --date 2024-12-01                   │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Orchestrator                                              │
│    run_daily_ingestion(date, markets)                        │
│    - Resolve trading day (exchange-calendars)                │
│    - Setup logging + config                                  │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Router                                                    │
│    fetch_market_data(market, date)                           │
│    - Select fetcher class per market                         │
│    - Route macro → MacroFetcher                              │
│    - Route equity → MarketDataFetcher subclass               │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. Parallel Fetch                                            │
│    fetch_markets_parallel() via ThreadPoolExecutor           │
│    Each market fetcher: fetch(date) → Polars DataFrame       │
│    Macro fetcher: fetch indicator data                       │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. Validation & Write                                        │
│    writers.validate_schema() — required-column check         │
│    writers.upsert_dataset() — dedupe + Delta merge           │
│      (optional pointblank validation via validate_quality)   │
│    Delta-aware skip-existing checks                          │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. Summary & Reporting                                       │
│    summarize_results() — per-market success/failure counts   │
│    structlog JSON output with correlation IDs                │
└──────────────────────────────────────────────────────────────┘
```

### Backfill Flow

```
backfill_date_range(start, end, markets)
  │
  ├── Resolve trading calendar for date range
  │
  └── For each trading day:
        └── run_daily_ingestion(day, markets)
              └── (same flow as daily ingestion above)
```

---

## Entry Points

### CLI Entry Points

#### Daily Ingestion
```bash
uv run equity ingest --date 2024-12-01 --markets us,cn
```

#### Backfill
```bash
uv run equity backfill --start 2023-04-06 --end 2026-04-05
uv run equity backfill --days-back 365 --markets us
```

#### S3 Sync
```bash
uv run equity sync --bucket s3://my-bucket --workers 32
```
`--bucket` is a root URL whose remote tree mirrors the local numbered
medallion layout (`<bucket>/01_bronze/market_data/<market_dir>`).

#### Query Interface
```bash
uv run equity query                    # Run all predefined queries
uv run equity query --query top_volume # Run a named query
```

#### Full Pipeline
```bash
uv run equity pipeline    # ingest → features → ML
```

---

## Fault Tolerance

### Failure Modes

1. **API Failure**: Retry with exponential backoff (tenacity, `TransientError` only), continue other markets, log errors
2. **Storage Failure**: Validate Parquet integrity post-write, retry, alert on persistent failures
3. **Query Failure**: Validate SQL, check missing partitions, helpful error messages

### Recovery
- **Idempotent Operations**: Re-run without side effects
- **Incremental Updates**: Only fetch new data (Delta-aware skip-existing)
- **Data Validation**: pointblank schemas enforced at ingestion write boundaries via `upsert_dataset(validate_quality=True)` (ADR-0007)

---

**Entry Points**: 20 flat CLI commands (`ingest`, `backfill`, `auto-backfill`, `sync`, `macro`, `delta-vacuum`, `delta-compact`, `delta-migrate`, `news`, `sentiment`, `transcripts`, `ratings`, `sec`, `financials`, `query`, `monitor`, `forecast`, `backtest`, `pipeline`, `catalog-generate`) plus 10 sub-apps (`signal`, `dashboard`, `bootstrap`, `config`, `validate`, `arena`, `report`, `demo`, `ml`, `api`)
**Core Components**: 6 layers (Ingestion, Storage, Query, Features, ML, Signals)
**Design Patterns**: Strategy, Layered Base Classes, Composition, Protocol, Delegation, Transparent Storage
