# ARCHITECTURE.md - System Architecture

## Overview

Local-first equity EOD data pipeline with modular architecture supporting multiple markets, extensible data sources, and unified SQL access.

## Architectural Principles

1. **Local-First**: After initial S3 bootstrap, all operations are local
2. **Modular Design**: Clear separation between ingestion, storage, and query layers
3. **Configuration-Driven**: Ticker lists and markets managed via YAML
4. **Graceful Degradation**: Pipeline continues if one market or source fails
5. **Idempotent Operations**: Safe to re-run without data duplication
6. **Hive Partitioning**: Efficient time-range queries via date partitioning

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI Entry Points                        │
│  (daily, sync, query, pipeline, monitor, backfill, macro)  │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                  Orchestrator Layer                         │
│  - Coordinates multi-market ingestion                       │
│  - Parallel execution support                              │
│  - Error aggregation and reporting                         │
└─────────────────────────┬───────────────────────────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
┌───────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
│ US Market    │  │ CN Market   │  │ HK/SG Market│
│ (yfinance)   │  │ (akshare)   │  │ (yfinance)  │
└───────┬──────┘  └──────┬──────┘  └──────┬──────┘
        │                 │                 │
        └─────────────────┼─────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                    Writer Layer                             │
│  - Schema validation                                        │
│  - Hive partition creation                                  │
│  - Parquet file writing                                     │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                   Storage Layer                             │
│  - Parquet files (Hive-partitioned)                        │
│  - DuckDB (query engine)                                    │
│  - S3 sync (bootstrap only)                                │
└─────────────────────────────────────────────────────────────┘
```

## Layer Breakdown

### 1. CLI Layer

**Location**: `src/equity_lake/cli/`

**Responsibilities**:
- Parse command-line arguments
- Load configuration
- Invoke orchestrators
- Display results

**Entry Points**:
- `daily.py` - Daily EOD ingestion
- `sync.py` - S3 bootstrap
- `query.py` - SQL queries
- `pipeline.py` - Full pipeline orchestration
- `monitor.py` - Health checks
- `backfill.py` - Historical backfill
- `macro.py` - Macro indicator fetching
- `generate_test_data.py` - Test data generation
- `price_forecaster.py` - Price forecasting

**Pattern**: Command pattern - Each CLI module implements `main()` function

### 2. Configuration Layer

**Location**: `src/equity_lake/config/`

**Modules**:
- `models.py` - Pydantic models for configuration
- `loader.py` - Load from YAML and environment
- `selectors.py` - Ticker list selection
- `validation.py` - Configuration validation

**Data Flow**:
```
config/tickers.yaml → ConfigLoader → Pydantic Models → Application
.env files           ↓
                    Environment variables
```

**Configuration Objects**:
- `MarketConfig` - Per-market settings
- `TickerConfig` - Ticker lists
- `PipelineConfig` - Pipeline orchestration settings

### 3. Ingestion Layer

**Location**: `src/equity_lake/ingestion/`

**Sub-layers**:

#### 3.1 Sources Layer (`ingestion/sources/`)

**Base Class**: `BaseMarketDataFetcher`
- Retry logic with exponential backoff
- Abstract `fetch()` method
- Error handling and logging

**Implementations**:
- `USEquityFetcher` - Yahoo Finance (US market)
- `CNAshareFetcher` - Akshare (China A-shares)
- `HKSGEquityFetcher` - Yahoo Finance (HK/SG markets)
- `MacroSourceFetcher` - FRED API (macro indicators)

**Pattern**: Strategy pattern - Each fetcher implements the same interface

#### 3.2 Orchestrator (`ingestion/orchestrator.py`)

**Responsibilities**:
- Coordinate multiple markets
- Parallel execution support
- Error aggregation
- Progress tracking

**Key Methods**:
- `orchestrate_ingestion()` - Run multi-market ingestion
- `_run_market_ingestion()` - Execute single market
- `_aggregate_results()` - Collect and report results

**Pattern**: Facade pattern - Simplifies complex multi-source operations

#### 3.3 Parallel Execution (`ingestion/parallel.py`)

**Responsibilities**:
- Thread-based parallel fetching
- Concurrency limiting
- Result aggregation

**Pattern**: Executor pattern - Uses `concurrent.futures.ThreadPoolExecutor`

#### 3.4 Filters (`ingestion/filters.py`)

**Responsibilities**:
- Data validation
- Null value filtering
- Outlier detection

**Pattern**: Filter chain - Composable filter functions

#### 3.5 Gap Detection (`ingestion/gap_detection.py`)

**Responsibilities**:
- Detect missing dates in data
- Identify gaps in time series
- Generate gap reports

**Pattern**: Analysis pattern - Scan and analyze existing data

#### 3.6 Models (`ingestion/models.py`)

**Data Structures**:
- `IngestionResult` - Result of ingestion operation
- `MarketData` - Standardized market data model
- `GapInfo` - Gap detection result

**Pattern**: Data transfer objects - Immutable data containers

#### 3.7 Writers (`ingestion/writers.py`)

**Responsibilities**:
- Write to Hive-partitioned Parquet
- Schema validation
- Partition directory creation

**Pattern**: Repository pattern - Abstraction over storage

### 4. Storage Layer

**Location**: `src/equity_lake/storage/`

#### 4.1 Parquet Storage (`storage/parquet.py`)

**Responsibilities**:
- Read/write Parquet files
- Schema management
- Partition handling

**Key Methods**:
- `write_to_partitioned_parquet()` - Write with Hive partitioning
- `read_parquet_with_filter()` - Read with date filter
- `validate_parquet_schema()` - Verify schema compliance

#### 4.2 DuckDB Storage (`storage/duckdb.py`)

**Responsibilities**:
- Database connection management
- View creation
- Query execution

**Key Methods**:
- `_setup_views()` - Create unified `equity_all` view
- `query()` - Execute SQL and return DataFrame
- `create_materialized_view()` - Cache frequently used queries

**Pattern**: Active Record pattern - Database operations encapsulated in objects

#### 4.3 S3 Sync (`storage/s3_sync.py`)

**Responsibilities**:
- Download from S3
- Tool detection (s5cmd vs AWS CLI)
- Integrity validation

**Key Methods**:
- `sync_with_s5cmd()` - Fast parallel sync
- `sync_with_aws_cli()` - Fallback sync method
- `verify_download()` - Validate Parquet structure

**Pattern**: Adapter pattern - Unified interface over multiple sync tools

### 5. Feature Engineering Layer

**Location**: `src/equity_lake/features/`

**Modules**:
- `engineering.py` - Feature computation (SMA, EMA, RSI, etc.)
- `jobs.py` - Feature generation orchestration
- `__init__.py` - Public API

**Features**:
- Moving averages (SMA, EMA)
- Momentum indicators (RSI, MACD)
- Volatility measures (Bollinger Bands)
- Volume indicators

**Pattern**: Builder pattern - Build features incrementally

### 6. Machine Learning Layer

**Location**: `src/equity_lake/ml/`

**Modules**:
- `forecasting.py` - Price forecasting models
- `training.py` - Model training pipeline
- `jobs.py` - ML orchestration

**Algorithms**:
- XGBoost
- scikit-learn models
- SHAP for interpretability

**Pattern**: Pipeline pattern - Sequential ML operations

### 7. Monitoring Layer

**Location**: `src/equity_lake/monitoring/`

**Modules**:
- `health.py` - Health checks

**Checks**:
- Data directory existence
- Parquet file integrity
- Database connectivity
- Log file health

**Pattern**: Observer pattern - Monitor system state

### 8. Core Layer

**Location**: `src/equity_lake/core/`

**Modules**:
- `constants.py` - Application constants
- `logging.py` - Structured logging setup
- `paths.py` - Path resolution
- `runtime.py` - Runtime configuration

**Responsibilities**:
- Shared utilities
- Logging configuration
- Path management

### 9. Devtools Layer

**Location**: `src/equity_lake/devtools/`

**Modules**:
- `test_data.py` - Realistic test data generation

**Responsibilities**:
- Generate sample data for testing
- Create test scenarios

## Data Flow

### Daily Ingestion Flow

```
User runs: equity-daily
    │
    ├─► Load config (tickers.yaml, .env)
    │
    ├─► Orchestrator.orchestrate_ingestion()
    │       │
    │       ├─► USEquityFetcher.fetch(date)
    │       │       └─► yfinance.download()
    │       │       └─► Schema validation
    │       │       └─► Return DataFrame
    │       │
    │       ├─► CNAshareFetcher.fetch(date)
    │       │       └─► akshare.stock_zh_a_hist()
    │       │       └─► Column mapping
    │       │       └─► Schema validation
    │       │       └─► Return DataFrame
    │       │
    │       └─► HKSGEquityFetcher.fetch(date)
    │               └─► yfinance.download()
    │               └─► Schema validation
    │               └─► Return DataFrame
    │
    ├─► Filter and validate data
    │
    ├─► WriteToPartitionedParquet()
    │       ├─► Create date=YYYY-MM-DD/ directory
    │       ├─► Write YYYY-MM-DD.parquet file
    │       └─► Validate schema
    │
    └─► Update DuckDB views
```

### Query Flow

```
User runs: equity-query
    │
    ├─► Load config
    │
    ├─► DuckDB connection setup
    │
    ├─► Create unified view (equity_all)
    │       ├─► Scan all Parquet files
    │       ├─► Union across markets
    │       └─► Add market column
    │
    └─► Execute SQL query
            ├─► Partition pruning (by date)
            ├─► Column projection
            └─► Return DataFrame
```

### Pipeline Flow

```
User runs: equity-pipeline
    │
    ├─► Stage 1: Ingestion
    │       └─► [Daily Ingestion Flow]
    │
    ├─► Stage 2: Gap Detection
    │       └─► Detect missing dates
    │
    ├─► Stage 3: Feature Engineering
    │       ├─► Compute technical indicators
    │       └─► Write to feature store
    │
    ├─► Stage 4: ML Training (optional)
    │       ├─► Load training data
    │       ├─► Train models
    │       └─► Save model artifacts
    │
    └─► Stage 5: Forecasting (optional)
            ├─► Load models
            ├─► Generate predictions
            └─► Save predictions
```

## Abstractions & Interfaces

### Key Abstract Classes

1. **BaseMarketDataFetcher** (`ingestion/sources/base.py`)
   - Abstract method: `fetch(date) -> DataFrame`
   - Built-in retry logic
   - Error handling template

2. **EquityDataDB** (`storage/duckdb.py`)
   - Database connection management
   - View creation and maintenance
   - Query execution interface

### Data Transfer Objects

1. **IngestionResult** - Result of market ingestion
2. **MarketData** - Standardized OHLCV data
3. **GapInfo** - Gap detection results
4. **HealthCheckResult** - Health check status

## Design Patterns Used

- **Strategy Pattern**: Market fetchers (US, CN, HK/SG)
- **Facade Pattern**: Orchestrator simplifies complex operations
- **Repository Pattern**: Storage abstractions
- **Builder Pattern**: Feature engineering pipeline
- **Factory Pattern**: Fetcher instantiation
- **Observer Pattern**: Monitoring and health checks
- **Command Pattern**: CLI entry points
- **Adapter Pattern**: S3 sync tool abstraction

## Error Handling Strategy

1. **Retry Logic**: All external API calls have exponential backoff
2. **Graceful Degradation**: Continue if one market fails
3. **Error Aggregation**: Collect all errors before reporting
4. **Logging**: Structured logging for debugging
5. **Validation**: Schema validation at multiple stages

## Concurrency Model

- **Thread-Based**: `concurrent.futures.ThreadPoolExecutor`
- **I/O-Bound**: Parallel fetching from multiple markets
- **No Async**: Currently all synchronous operations
- **Future Enhancement**: Could migrate to asyncio for I/O operations

## State Management

- **Immutable State**: DataFrames are immutable
- **Local Storage**: All state is local files
- **No Database**: DuckDB is query-only, no stateful writes
- **Configuration**: Loaded at startup, not mutated

## Extensibility Points

1. **New Markets**: Add new fetcher class inheriting from `BaseMarketDataFetcher`
2. **New Features**: Extend `FeatureEngineering` class
3. **New ML Models**: Add to `ml/` module
4. **New Storage Backends**: Implement storage interface
5. **New Queries**: Add to CLI query module

## Trade-offs & Design Decisions

1. **Why Not Async?**: Simplicity, easier debugging, sufficient performance
2. **Why Parquet?**: Columnar format, efficient compression, DuckDB native
3. **Why Hive Partitioning?**: Standard, tool support, partition pruning
4. **Why Local-First?**: Privacy, cost, performance after bootstrap
5. **Why DuckDB?**: Zero-copy, fast analytics, SQL compatibility
