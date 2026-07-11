# Architecture

**Last Updated**: 2026-07-11
**Project**: Equity EOD Data Pipeline

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

### Key Design Principles

1. **Local-First**: After initial S3 bootstrap, all operations run locally
2. **Idempotent**: Safe to re-run any operation without side effects
3. **Failure Isolation**: Optional enrichments may degrade; required price failures block dependent outputs
4. **Partitioned Storage**: Numbered medallion Delta tables partitioned by date
5. **Zero-Copy Queries**: DuckDB reads Parquet/Delta files directly without loading
6. **Single Canonical Path**: no supported duplicate source tree or package-root shims
7. **Composition over Inheritance**: ML pipeline decomposed into `FeatureLoader` + `trainer` utilities

## Canonical Module Boundaries

- `src/equity_lake/sources/`: all market and external-data source adapters (including `macro.py`)
- `src/equity_lake/ingestion/`: authoritative ingestion runtime (`orchestrator.py`, `router.py`, `backfill.py`, `writers.py`, `parallel.py`)
- `src/equity_lake/storage/`: persistence layer (`duckdb.py`, `delta.py`, `s3_sync.py`, `compaction.py`, `lake_reader.py`)
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
class MarketDataFetcher(ABC):
    @abstractmethod
    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch data for specific date"""
        pass

    def _retry_on_failure(self, func, *args, **kwargs):
        """Exponential backoff retry logic"""
        pass
```

#### Market-Specific Fetchers
- **USEquityFetcher** (`sources/us.py`): US market via yfinance, batch downloads, rate limiting
- **CNAshareFetcher** (`sources/cn.py`): China A-shares via akshare, column mapping
- **HKSGEquityFetcher** (`sources/hk_sg.py`): HK/SG markets via yfinance
- **CNHybridFetcher** (`sources/cn_hybrid.py`): akshare + efinance fallback, uses `core/ticker_utils.cn_to_yahoo_symbol`
- **JPXEquityFetcher** (`sources/jpx.py`): Japan exchange via yfinance
- **KRXFetcher** (`sources/krx.py`): Korea exchange via efinance
- **MacroFetcher** (`sources/macro.py`): Macro indicators (FRED, DXY, VIX, yields, commodities). Standalone class — does not extend `MarketDataFetcher`.

#### Router (`ingestion/router.py`)
- Maps market identifiers to concrete fetcher classes
- Provides `fetch_market_data()` / `fetch_market_data_with_config()` entry points
- Routes macro through `MacroFetcher`, all equity markets through `MarketDataFetcher` subclasses
- Schema validation via `writers.validate_schema()`

#### Orchestrator (`ingestion/orchestrator.py`)
- `run_daily_ingestion()`: Main entry point for a single trading day
- Delegates to `router.fetch_market_data()` for each market
- Parallel market fetch via `parallel.fetch_markets_parallel()`
- Delta-aware skip-existing checks and writes
- Macro data routed through router (no special-cased write branch)

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

**Data Flow**:
```
CLI Request → Orchestrator → Router → sources/* → Validation → writers.py → Parquet/Delta
                                     ↘ MacroFetcher → writers.py → macro_indicators/
```

---

### 2. Storage Layer

**Purpose**: Persist and retrieve market data efficiently

**Location**: `src/equity_lake/storage/`

#### S3 Sync Module (`s3_sync.py`)
One-time bootstrap of US historical data from S3. Auto-detects sync tool (s5cmd > AWS CLI > boto3).

#### Delta Storage
Runtime tables live under numbered medallion paths such as
`data/lake/01_bronze/market_data/us_equity/`. They are Delta tables
partitioned by `date`; their data files are Parquet and use the standardized
OHLCV schema.

#### DuckDB Module (`duckdb.py`)
```python
class EquityDataDB:
    def __init__(self):
        self.con = duckdb.connect(':memory:')
        self._setup_views()

    def query(self, sql, params=None):
        """Execute SQL and return DataFrame"""
        pass

    def run_named_query(self, name, **params):
        """Execute a predefined named query with parameters"""
        pass

    def run_all_queries(self):
        """Run all predefined queries and return results dict"""
        pass
```

#### Lake Reader (`lake_reader.py`)
```python
def duckdb_scan_for(market_path: Path) -> str:
    """Return DuckDB scan expression — tries delta_scan first, falls back to read_parquet."""
```
Used by `FeatureLoader`, `features/engineering.py`, and `ml/forecasting.py` for transparent Delta/Parquet access.

---

### 3. Feature Engineering Layer

**Purpose**: Transform raw OHLCV into analytical features

**Location**: `src/equity_lake/features/`

**Key Features**:
- Moving averages (SMA, EMA)
- Price momentum (RSI, MACD)
- Volatility metrics (Bollinger Bands, ATR)
- Volume indicators (OBV, VWAP)
- Price returns (daily, weekly, monthly)
- Cross-modal sentiment features

**Implementation**: Hamilton-backed Polars pipeline with pandas retained only at narrow third-party boundaries. Feature outputs carry a schema version and can optionally merge news and social sentiment.

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
- `base.py`: `MarketDataFetcher` ABC with retry logic (`_retry_on_failure`)
- `news.py` / `sentiment.py`: Use `fetch_items_parallel()` from `ingestion/parallel.py`
- `macro.py`: `MacroFetcher` + `MacroIndicatorFetcher` hierarchy for FRED/yfinance macro data

---

## Design Patterns

### 1. Strategy Pattern — Market Fetchers
`MarketDataFetcher` ABC with per-market implementations. `router.py` maps market strings to concrete fetcher classes.

### 2. Template Method — Base Fetcher
`MarketDataFetcher.fetch()` → `_fetch_from_source()` (abstract) → `_standardize_columns()` (common) → `_validate_data()` (common).

### 3. Composition — ML Pipeline
`PriceForecaster` composes `FeatureLoader` (DuckDB lifecycle) and delegates to `trainer.py` functions, rather than inheriting from a shared base.

### 4. Transparent Storage — Lake Reader
`duckdb_scan_for()` abstracts Delta vs. Parquet access, letting callers use a single scan expression without knowing the underlying format.

### 5. Delegation — Backfill
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
│    writers.validate_schema() — OHLCV column check            │
│    writers.write_market_data() — dedup + partitioned Parquet │
│    writers.write_macro_data() — macro-indicator Parquet      │
│    Delta-aware skip-existing checks                          │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. Summary & Reporting                                       │
│    summarize_results() — per-market row counts, errors       │
│    structlog JSON output                                     │
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
uv run equity sync --bucket s3://my-bucket/us_equity/ --workers 32
```

#### Query Interface
```bash
uv run equity query                    # Run all predefined queries
uv run equity query --query top_volume # Run a named query
uv run equity query --days 14 --output results.csv
```

#### Full Pipeline
```bash
uv run equity pipeline    # ingest → features → ML
```

---

## Fault Tolerance

### Failure Modes

1. **API Failure**: Retry with exponential backoff (tenacity), continue other markets, log errors
2. **Storage Failure**: Validate Parquet integrity post-write, retry, alert on persistent failures
3. **Query Failure**: Validate SQL, check missing partitions, helpful error messages

### Recovery
- **Idempotent Operations**: Re-run without side effects
- **Incremental Updates**: Only fetch new data (Delta-aware skip-existing)
- **Data Validation**: pointblank schemas enforced at ingestion write boundaries

---

**Entry Points**: 12+ CLI commands (ingest, backfill, sync, query, pipeline, monitor, signal, backtest, config, dashboard, loader, update, etc.)
**Core Components**: 6 layers (Ingestion, Storage, Query, Features, ML, Signals)
**Design Patterns**: Strategy, Template Method, Composition, Protocol, Delegation, Transparent Storage
