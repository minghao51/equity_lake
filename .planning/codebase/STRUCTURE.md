# STRUCTURE.md - Directory Structure & Organization

## Project Root Layout

```
equity_lake/
├── .planning/              # Planning documents (git-ignored)
│   └── codebase/          # Codebase documentation (this file)
├── config/                # Configuration files
│   ├── example.env        # Environment template
│   └── tickers.yaml       # Market ticker lists
├── data/                  # Data storage (git-ignored)
│   ├── lake/              # Parquet data lake
│   │   ├── us_equity/     # US market data
│   │   ├── cn_ashare/     # China A-shares data
│   │   └── hk_sg_equity/  # HK/SG market data
│   └── models/            # ML model artifacts
├── docs/                  # Documentation
│   ├── architecture/      # Architecture docs
│   ├── decisions/         # Architecture decision records
│   ├── design/            # Design documents
│   ├── developer-guide/   # Developer guides
│   ├── education/         # Educational content
│   ├── getting-started/   # Getting started guides
│   ├── guides/            # User guides
│   ├── implementations/   # Implementation notes
│   ├── planning/          # Planning documents
│   ├── reports/           # Project reports
│   └── user-guide/        # User documentation
├── examples/              # Example scripts
│   └── parallel_logging_demo.py
├── logs/                  # Application logs (git-ignored)
│   ├── backfill_data.log
│   ├── feature_engineering.log
│   ├── fetch_macro.log
│   ├── ingest_daily.log
│   ├── monitor_pipeline.log
│   ├── price_forecaster.log
│   └── run_pipeline.log
├── src/                   # Source code
│   └── equity_lake/       # Main package
├── tests/                 # Test suite
│   ├── fixtures/          # Test fixtures
│   ├── integration/       # Integration tests
│   ├── unit/              # Unit tests
│   ├── conftest.py        # Pytest configuration
│   └── __init__.py
├── .env.example           # Environment template
├── .gitignore             # Git ignore rules
├── .python-version        # Python version (3.12)
├── CLAUDE.md              # AI development guide
├── Dockerfile             # Docker image
├── docker-compose.yml     # Docker orchestration
├── Makefile               # Development commands
├── pyproject.toml         # Project configuration
├── README.md              # Project overview
├── requirements-dev.txt   # Development dependencies
├── requirements.txt       # Production dependencies
└── uv.lock               # Dependency lock file
```

## Source Code Structure

### `src/equity_lake/` - Main Package

```
src/equity_lake/
├── __init__.py                 # Package initialization
├── backfill_data.py            # Historical backfill script
├── fetch_macro.py              # Macro indicator fetcher
├── feature_jobs.py             # Feature engineering jobs
├── ingestion_jobs.py           # Ingestion orchestration
├── ml_jobs.py                  # ML orchestration
├── pipeline.py                 # Pipeline orchestration
├── price_forecaster.py         # Price forecasting
├── run_pipeline.py             # Pipeline runner
├── validators.py               # Data validators
│
├── cli/                        # CLI Entry Points
│   ├── __init__.py
│   ├── backfill.py             # Backfill CLI
│   ├── daily.py                # Daily ingestion CLI
│   ├── generate_test_data.py   # Test data generation CLI
│   ├── macro.py                # Macro indicators CLI
│   ├── monitor.py              # Health check CLI
│   ├── pipeline.py             # Pipeline CLI
│   ├── price_forecaster.py     # Price forecasting CLI
│   ├── query.py                # Query CLI
│   └── sync.py                 # S3 sync CLI
│
├── config/                     # Configuration Management
│   ├── __init__.py
│   ├── loader.py               # Config loading logic
│   ├── models.py               # Pydantic config models
│   ├── selectors.py            # Ticker selection logic
│   └── validation.py           # Config validation
│
├── core/                       # Core Utilities
│   ├── __init__.py
│   ├── constants.py            # Application constants
│   ├── logging.py              # Structured logging setup
│   ├── paths.py                # Path resolution
│   └── runtime.py              # Runtime configuration
│
├── devtools/                   # Development Tools
│   ├── __init__.py
│   └── test_data.py            # Test data generation
│
├── features/                   # Feature Engineering
│   ├── __init__.py
│   ├── engineering.py          # Feature computation
│   └── jobs.py                 # Feature jobs
│
├── ingestion/                  # Data Ingestion
│   ├── __init__.py
│   ├── filters.py              # Data filters
│   ├── gap_detection.py        # Gap detection
│   ├── models.py               # Ingestion data models
│   ├── orchestrator.py         # Ingestion orchestration
│   ├── parallel.py             # Parallel execution
│   ├── writers.py              # Data writers
│   └── sources/                # Data Source Fetchers
│       ├── __init__.py
│       ├── base.py             # Base fetcher class
│       ├── cn.py               # China A-shares (akshare)
│       ├── hk_sg.py            # HK/SG markets (yfinance)
│       ├── macro.py            # Macro indicators (FRED)
│       └── us.py               # US market (yfinance)
│
├── ml/                         # Machine Learning
│   ├── __init__.py
│   ├── forecasting.py          # Forecasting models
│   ├── jobs.py                 # ML orchestration
│   └── training.py             # Model training
│
├── monitoring/                 # Monitoring
│   ├── __init__.py
│   └── health.py               # Health checks
│
└── storage/                    # Storage Layer
    ├── __init__.py
    ├── duckdb.py               # DuckDB operations
    ├── parquet.py              # Parquet operations
    └── s3_sync.py              # S3 synchronization
```

## Test Structure

### `tests/` - Test Suite

```
tests/
├── __init__.py
├── conftest.py                 # Pytest fixtures and configuration
│
├── fixtures/                   # Test Fixtures
│   └── (test data files)
│
├── unit/                       # Unit Tests
│   ├── test_ingestion_orchestrator.py
│   ├── test_macro_sources.py
│   └── test_ml_jobs.py
│
└── integration/                # Integration Tests
    ├── test_duckdb_queries.py
    └── test_pipeline_orchestrator.py
```

## Key File Locations

### Configuration Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Project metadata, dependencies, tool config |
| `requirements.txt` | Production dependencies |
| `requirements-dev.txt` | Development dependencies |
| `.env.example` | Environment variable template |
| `config/tickers.yaml` | Market ticker configuration |
| `Makefile` | Development commands |

### Entry Points

| File | Purpose |
|------|---------|
| `src/equity_lake/cli/daily.py` | Daily ingestion |
| `src/equity_lake/cli/sync.py` | S3 bootstrap |
| `src/equity_lake/cli/query.py` | SQL queries |
| `src/equity_lake/cli/pipeline.py` | Full pipeline |

### Core Logic

| File | Purpose |
|------|---------|
| `src/equity_lake/ingestion/sources/base.py` | Base fetcher class |
| `src/equity_lake/ingestion/orchestrator.py` | Ingestion coordination |
| `src/equity_lake/storage/duckdb.py` | Database operations |
| `src/equity_lake/storage/parquet.py` | Parquet operations |

### Data Storage

| Directory | Purpose |
|-----------|---------|
| `data/lake/us_equity/` | US market Parquet files |
| `data/lake/cn_ashare/` | China A-shares Parquet files |
| `data/lake/hk_sg_equity/` | HK/SG Parquet files |
| `equity_data.duckdb` | DuckDB database file |

### Logs

| File | Purpose |
|------|---------|
| `logs/ingest_daily.log` | Daily ingestion logs |
| `logs/sync_from_s3.log` | S3 sync logs |
| `logs/run_pipeline.log` | Pipeline logs |
| `logs/fetch_macro.log` | Macro indicator logs |

## Naming Conventions

### Directories

- **Lowercase with underscores**: `ingestion/`, `devtools/`, `storage/`
- **Plural for collections**: `sources/`, `features/`, `tests/`
- **Singular for single-purpose**: `config/`, `core/`, `cli/`

### Files

- **Lowercase with underscores**: `orchestrator.py`, `gap_detection.py`
- **CLI modules**: `<command>.py` (e.g., `daily.py`, `sync.py`)
- **Test files**: `test_<module>.py` (e.g., `test_ingestion.py`)

### Classes

- **PascalCase**: `USEquityFetcher`, `BaseMarketDataFetcher`, `IngestionOrchestrator`
- **Suffixes**:
  - `*Fetcher` - Data source fetchers
  - `*Orchestrator` - Coordination logic
  - `*Writer` - Storage writers
  - `*Config` - Configuration models

### Functions

- **lowercase_with_underscores**: `fetch_market_data()`, `write_to_parquet()`
- **Private functions**: Prefix with `_`: `_retry_on_failure()`, `_validate_schema()`

### Constants

- **UPPERCASE_WITH_UNDERSCORES**: `STANDARD_COLUMNS`, `LAKE_DIR`, `LOGS_DIR`
- **Location**: `core/constants.py` or module-level constants

### CLI Entry Points

- **Hyphenated**: `equity-daily`, `equity-sync`, `equity-query`
- **Defined in**: `pyproject.toml [project.scripts]`

## Module Organization

### Layered Architecture

```
CLI Layer (cli/)
    ↓
Orchestrator Layer (ingestion/orchestrator.py, pipeline.py)
    ↓
Business Logic Layer (ingestion/, features/, ml/)
    ↓
Data Access Layer (storage/, ingestion/sources/)
    ↓
Infrastructure Layer (core/, monitoring/)
```

### Package Responsibilities

| Package | Responsibility |
|---------|---------------|
| `cli/` | Command-line interface, argument parsing |
| `config/` | Configuration loading and validation |
| `core/` | Shared utilities (logging, paths, constants) |
| `ingestion/` | Data fetching and writing |
| `features/` | Feature engineering |
| `ml/` | Machine learning models |
| `monitoring/` | Health checks and monitoring |
| `storage/` | Data storage abstraction |
| `devtools/` | Development utilities |

## Import Patterns

### Relative Imports

```python
# Within equity_lake package
from .base import BaseMarketDataFetcher
from ..storage import ParquetStorage
from ..core.logging import get_logger
```

### Absolute Imports

```python
# From external packages
import yfinance as yf
import akshare as ak
import duckdb
import pandas as pd
```

### Import Grouping

```python
# 1. Standard library imports
from datetime import date, timedelta
from pathlib import Path

# 2. Third-party imports
import pandas as pd
import yfinance as yf

# 3. Local imports
from equity_lake.core.logging import get_logger
from equity_lake.ingestion.sources.base import BaseMarketDataFetcher
```

## Data Partition Structure

### Hive-Style Partitioning

```
data/lake/us_equity/
├── date=2024-01-01/
│   └── 2024-01-01.parquet
├── date=2024-01-02/
│   └── 2024-01-02.parquet
└── date=2024-01-03/
    └── 2024-01-03.parquet
```

**Pattern**: `<key>=<value>/` (Hive standard)
**Partition Key**: `date` (YYYY-MM-DD format)
**File Naming**: `<date>.parquet` (matches partition key)

## Documentation Structure

### `docs/` Organization

```
docs/
├── QUICKSTART.md              # Quick start guide
├── PIPELINE_USAGE.md          # Pipeline operations
├── README.md                  # Docs overview
│
├── architecture/              # Architecture docs
├── decisions/                 # Architecture Decision Records (ADRs)
├── design/                    # Design documents
├── developer-guide/           # Developer guides
├── education/                 # Educational content
│   └── research/              # Research notes
├── getting-started/           # Getting started guides
├── guides/                    # User guides
├── implementations/           # Implementation notes
├── planning/                  # Planning documents
│   └── development-log.md     # Development log
├── reports/                   # Project reports
└── user-guide/                # User documentation
```

## Generated Files

### Build Artifacts

- **uv.lock** - Dependency lock file (auto-generated)
- **.coverage** - Coverage report
- **htmlcov/** - HTML coverage report
- **.pytest_cache/** - Pytest cache
- **.ruff_cache/** - Ruff cache
- **.mypy_cache/** - MyPy cache

### Runtime Files

- **equity_data.duckdb** - DuckDB database (created on first run)
- **logs/*.log** - Application logs
- **data/lake/**/*.parquet** - Data files

## Docker Structure

### Multi-Stage Build

```
base (python:3.11-slim + uv)
    ↓
dependencies (install requirements.txt)
    ↓
production (copy application code)
    ↓
development (+dev tools, s5cmd)
```

### Service Profiles

- **sync** - One-time S3 sync
- **daily** - Cron-based daily ingestion
- **query** - Interactive query interface
- **dev** - Development environment
- **jupyter** - Jupyter Lab notebook server

## File Size Guidelines

| File Type | Recommended Size | Notes |
|-----------|----------------|-------|
| Python module | <500 lines | Split if larger |
| Class | <200 lines | Extract methods |
| Function | <50 lines | Extract helper functions |
| Test file | <300 lines | Split by feature |

## Configuration Hierarchy

1. **pyproject.toml** - Project metadata and tool config
2. **.env** - Environment-specific settings (git-ignored)
3. **config/tickers.yaml** - Ticker lists
4. **CLI arguments** - Runtime overrides

## Path Management

### Centralized Paths (`core/paths.py`)

```python
LAKE_DIR = Path("data/lake")
US_EQUITY_DIR = LAKE_DIR / "us_equity"
CN_ASHARE_DIR = LAKE_DIR / "cn_ashare"
HK_SG_EQUITY_DIR = LAKE_DIR / "hk_sg_equity"
LOGS_DIR = Path("logs")
```

### Usage Pattern

```python
from equity_lake.core.paths import US_EQUITY_DIR, LOGS_DIR

# Use path constants instead of hardcoded strings
log_file = LOGS_DIR / "ingest_daily.log"
data_dir = US_EQUITY_DIR / f"date={trading_date}"
```
