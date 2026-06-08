# Structure

> **Note (2026-04):** This file is stale. It references deleted `scripts/`
> directories, lists only 3 CLI entrypoints (the repo now has 12+), and
> describes a `docs/api/` directory that does not exist. For an accurate,
> concise view of the current project layout, see
> [Project Structure](../../developer-guide/project-structure.md).

**Last Updated**: 2026-03-05
**Project**: Equity EOD Data Pipeline

## Directory Layout

```
equity_lake/
├── .planning/                 # (removed — see docs/developer/)
│
├── data/                     # Data directory (git-ignored)
│   └── lake/                # Hive-partitioned Parquet data lake
│       ├── us_equity/       # US stocks (from S3)
│       │   ├── date=2024-12-01/
│       │   │   └── 2024-12-01.parquet
│       │   └── date=2024-12-02/
│       │       └── 2024-12-02.parquet
│       ├── cn_ashare/       # China A-shares (local fetch)
│       └── hk_sg_equity/    # HK/SG stocks (local fetch)
│
├── logs/                    # Application logs (git-ignored)
│   ├── ingest_daily.log    # Daily ingestion logs
│   ├── sync_from_s3.log    # S3 sync logs
│   └── query.log           # Query operation logs
│
├── scripts/                 # Legacy scripts (being migrated to src/)
│   ├── ingest_daily.py     # Daily EOD ingestion (637 lines)
│   ├── sync_from_s3.py     # S3 historical sync (398 lines)
│   ├── query_example.py    # DuckDB query examples (594 lines)
│   └── generate_test_data.py # Test data generator
│
├── src/
│   └── equity_lake/        # Main package
│       ├── __init__.py     # Package initialization
│       │
│       ├── cli/            # CLI entry points
│       │   ├── __init__.py
│       │   ├── daily.py    # Daily ingestion CLI
│       │   ├── sync.py     # S3 sync CLI
│       │   └── query.py    # Query interface CLI
│       │
│       ├── core/           # Core utilities and constants
│       │   ├── __init__.py
│       │   ├── runtime.py  # Runtime configuration
│       │   ├── logging.py  # Logging setup (structlog)
│       │   └── constants.py # Standardized column names
│       │
│       ├── ingestion/      # Data ingestion layer
│       │   ├── __init__.py
│       │   ├── orchestrator.py   # Multi-market coordination (880 lines)
│       │   └── sources/          # Market-specific fetchers
│       │       ├── __init__.py
│       │       ├── base.py       # Abstract base class
│       │       ├── us_equity.py  # US market (yfinance)
│       │       ├── cn_ashare.py  # China A-shares (akshare)
│       │       ├── hk_sg_equity.py # HK/SG markets
│       │       └── cn_hybrid.py  # China with fallback
│       │
│       ├── storage/        # Data persistence layer
│       │   ├── __init__.py
│       │   ├── s3_sync.py  # S3 sync orchestration
│       │   ├── parquet.py  # Parquet read/write utilities
│       │   └── duckdb.py   # DuckDB query interface
│       │
│       ├── features/       # Feature engineering (optional)
│       │   ├── __init__.py
│       │   ├── engineering.py # Feature calculations (712 lines)
│       │   └── indicators.py   # Technical indicators
│       │
│       └── signals/        # Trading signal generation (optional)
│           ├── __init__.py
│           ├── scanner.py  # Signal scanner
│           └── strategies.py # Trading strategies
│
├── tests/                  # Test suite
│   ├── __init__.py
│   ├── conftest.py         # Shared test fixtures
│   │
│   ├── unit/              # Unit tests
│   │   ├── sources/       # Test fetchers
│   │   │   ├── test_yfinance_source.py
│   │   │   ├── test_akshare_source.py
│   │   │   └── test_base_fetcher.py
│   │   ├── storage/       # Test storage layer
│   │   │   ├── test_parquet.py
│   │   │   ├── test_duckdb.py
│   │   │   └── test_s3_sync.py
│   │   └── test_orchestrator.py
│   │
│   ├── integration/       # Integration tests
│   │   ├── test_full_ingestion.py
│   │   ├── test_s3_workflow.py
│   │   └── test_query_workflow.py
│   │
│   └── signals/           # Signal tests
│       ├── test_scanner.py
│       └── test_strategies.py
│
├── docs/                   # Documentation
│   ├── getting-started/
│   │   └── quickstart.md  # Quick start guide
│   ├── user-guide/
│   │   ├── pipeline.md    # Pipeline usage
│   │   └── query-guide.md # Query examples
│   └── api/               # API documentation
│
├── .github/               # GitHub-specific files
│   └── workflows/         # CI/CD workflows
│       └── test.yml       # GitHub Actions test workflow
│
├── .env.example           # Environment variables template
├── .gitignore             # Git ignore rules
├── .python-version        # Python version (3.12)
├── CLAUDE.md              # AI assistant guide
├── README.md              # Project overview
├── pyproject.toml         # Project configuration
├── requirements.txt       # (removed — deps in pyproject.toml)
├── Makefile               # Common commands
├── Dockerfile             # Container image
└── docker-compose.yml     # Container orchestration
```

---

## File Count & Size

### Summary
- **Total Python Files**: 105
- **Total Lines of Code**: 17,731
- **Test Files**: 23
- **Test Lines**: 3,966
- **Documentation Files**: 15+ MD files

### Largest Files
1. `src/equity_lake/ingestion/orchestrator.py` - 880 lines
2. `src/equity_lake/features/engineering.py` - 712 lines
3. `scripts/ingest_daily.py` - 637 lines (legacy)
4. `scripts/query_example.py` - 594 lines (legacy)
5. `scripts/sync_from_s3.py` - 398 lines (legacy)

---

## Key Locations & Purposes

### Entry Points

#### CLI Commands
- **`src/equity_lake/cli/daily.py`**: Daily EOD ingestion
  - Parses date and market arguments
  - Invokes orchestrator
  - Reports results

- **`src/equity_lake/cli/sync.py`**: S3 historical sync
  - Configures S3 bucket and workers
  - Runs sync process
  - Validates downloads

- **`src/equity_lake/cli/query.py`**: DuckDB query interface
  - Executes SQL queries
  - Returns or exports results

#### Makefile Commands
```makefile
make setup          # Initialize dev environment
make daily          # Run daily ingestion
make sync           # S3 sync
make query          # Run query examples
make test           # Run tests with coverage
make lint           # Run ruff linting
make format         # Format code with ruff
make check          # Run mypy type checking
make clean          # Clean cache and temp files
make docker-up      # Start Docker containers
```

---

### Configuration Files

#### `pyproject.toml`
**Purpose**: Primary project configuration

**Key Sections**:
```toml
[project]
name = "equity-lake"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "yfinance>=0.2.50",
    "akshare>=1.15.0",
    "duckdb>=1.0.0",
    # ... etc
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "ruff>=0.8.0",
    "mypy>=1.11.0",
]

[project.scripts]
equity = "equity_lake.cli.__main__:app"

[tool.ruff]
line-length = 88
target-version = "py311"

[tool.mypy]
python_version = "3.11"
strict = true
```

---

#### `.python-version`
**Purpose**: Specify Python version for uv

**Content**:
```
3.12
```

---

#### `.env.example`
**Purpose**: Environment variables template

**Key Variables**:
```bash
# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_key_here
AWS_SECRET_ACCESS_KEY=your_secret_here
AWS_DEFAULT_REGION=us-east-1
S3_BUCKET=s3://your-bucket/us_equity/

# Optional API Keys
ALPHA_VANTAGE_API_KEY=your_key_here
FINNHUB_API_KEY=your_key_here
```

---

#### `Makefile`
**Purpose**: Convenience commands for common operations

**Key Targets**:
```makefile
.PHONY: setup test lint format check clean

setup:
	uv venv
	source .venv/bin/activate
	uv sync

daily:
	uv run equity ingest

test:
	uv run pytest --cov=src/equity_lake --cov-report=html

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/

check:
	uv run mypy src/equity_lake
```

---

#### `.gitignore`
**Purpose**: Exclude artifacts from git

**Key Exclusions**:
```
# Python
__pycache__/
*.py[cod]
*$py.class
.venv/

# Data
data/lake/
*.parquet

# Logs
logs/*.log

# Environment
.env

# IDE
.vscode/
.idea/

# Testing
.pytest_cache/
.coverage
htmlcov/

# uv
.uv/
```

---

### Data Directory Structure

#### Hive Partitioning Layout

```
data/lake/
├── us_equity/                      # US market data
│   ├── date=2024-12-01/           # December 1, 2024 partition
│   │   └── 2024-12-01.parquet     # Daily OHLCV data
│   ├── date=2024-12-02/
│   │   └── 2024-12-02.parquet
│   └── ...
│
├── cn_ashare/                      # China A-shares
│   ├── date=2024-12-01/
│   │   └── 2024-12-01.parquet
│   └── ...
│
└── hk_sg_equity/                   # Hong Kong & Singapore
    ├── date=2024-12-01/
    │   └── 2024-12-01.parquet
    └── ...
```

**Partition Format**:
- **Pattern**: `date=YYYY-MM-DD/` (Hive-style)
- **Files**: `{date}.parquet` inside partition
- **Date Column**: `date` type (not string) in Parquet

**Benefits**:
- Efficient time-range queries (partition pruning)
- Easy to manage daily updates
- Compatible with DuckDB, Spark, AWS Athena

---

### Log Directory Structure

```
logs/
├── ingest_daily.log    # Daily ingestion logs
├── sync_from_s3.log    # S3 sync logs
└── query.log           # Query operation logs
```

**Log Format**:
- **Structured JSON logs** (via structlog)
- **Fields**: timestamp, level, message, correlation_id
- **Rotation**: Manual (user manages log file size)

---

### Test Structure

#### Unit Tests (`tests/unit/`)

**Purpose**: Test individual components in isolation

**Structure**:
```
tests/unit/
├── sources/              # Test market fetchers
│   ├── test_yfinance_source.py
│   ├── test_akshare_source.py
│   └── test_base_fetcher.py
│
├── storage/              # Test storage layer
│   ├── test_parquet.py
│   ├── test_duckdb.py
│   └── test_s3_sync.py
│
└── test_orchestrator.py  # Test coordination logic
```

**Patterns**:
- Mock external APIs (yfinance, akshare)
- Use pytest fixtures for sample data
- Fast execution (< 1 second each)

---

#### Integration Tests (`tests/integration/`)

**Purpose**: Test end-to-end workflows

**Structure**:
```
tests/integration/
├── test_full_ingestion.py   # Test complete ingestion workflow
├── test_s3_workflow.py      # Test S3 sync + query
└── test_query_workflow.py   # Test query operations
```

**Patterns**:
- Use temporary directories for data
- Real Parquet file operations
- Slower execution, marked with `@pytest.mark.integration`

---

#### Test Fixtures (`tests/conftest.py`)

**Purpose**: Shared test data and utilities

**Key Fixtures**:
```python
@pytest.fixture
def sample_ohlcv_df():
    """Return sample OHLCV DataFrame"""
    return pd.DataFrame({
        'ticker': ['AAPL', 'GOOGL'],
        'date': [date(2024, 12, 1), date(2024, 12, 1)],
        'open': [150.0, 140.0],
        'high': [155.0, 145.0],
        'low': [149.0, 139.0],
        'close': [154.0, 144.0],
        'volume': [1000000, 900000]
    })

@pytest.fixture
def temp_data_dir(tmp_path):
    """Return temporary data directory"""
    data_dir = tmp_path / "data" / "lake"
    data_dir.mkdir(parents=True)
    return data_dir
```

---

## Naming Conventions

### Modules & Packages

**Pattern**: `snake_case`

**Examples**:
- `orchestrator.py` (not `Orchestrator.py`)
- `yfinance_source.py` (not `YFinanceSource.py`)
- `s3_sync.py` (not `s3_sync.py`)

**Rationale**: Follows PEP 8 conventions for Python modules

---

### Classes

**Pattern**: `PascalCase`

**Examples**:
- `MarketDataFetcher` (base class)
- `USEquityFetcher` (US market fetcher)
- `CNAshareFetcher` (China A-shares fetcher)
- `EquityDataDB` (DuckDB wrapper)
- `S3Syncer` (S3 synchronization)

**Rationale**: Follows PEP 8 conventions for classes

---

### Functions & Methods

**Pattern**: `snake_case`

**Examples**:
- `fetch_market_data()`
- `write_to_partitioned_parquet()`
- `validate_schema()`
- `run_daily_ingestion()`

**Rationale**: Follows PEP 8 conventions for functions

---

### Constants

**Pattern**: `UPPER_SNAKE_CASE`

**Examples**:
- `STANDARD_COLUMNS` (required OHLCV columns)
- `US_EQUITY_DIR` (US data directory path)
- `LOGS_DIR` (logs directory path)
- `MAX_RETRIES` (maximum retry attempts)

**Locations**:
- `src/equity_lake/core/constants.py`
- `src/equity_lake/core/runtime.py`

---

### Private Methods

**Pattern**: `_leading_underscore`

**Examples**:
- `_retry_on_failure()` (internal retry logic)
- `_standardize_columns()` (internal column mapping)
- `_validate_data()` (internal validation)
- `_fetch_from_source()` (abstract method implementation)

**Rationale**: Indicates internal use only (not part of public API)

---

### CLI Commands

**Pattern**: `kebab-case` (command name), `snake_case` (entry point)

**Examples**:
- **Command**: `equity ingest`
- **Entry Point**: `equity_lake.cli.__main__:app`
- **Command**: `equity sync`
- **Entry Point**: `equity_lake.cli.__main__:app`

**Rationale**: Follows CLI conventions for user-facing commands

---

## Data File Naming

### Parquet Files

**Pattern**: `{date}.parquet`

**Examples**:
- `2024-12-01.parquet`
- `2024-12-02.parquet`

**Location**: Inside partition directory
```
data/lake/us_equity/date=2024-12-01/2024-12-01.parquet
```

---

### Partition Directories

**Pattern**: `date={YYYY-MM-DD}/`

**Examples**:
- `date=2024-12-01/`
- `date=2024-12-02/`

**Rationale**: Hive-style partitioning for compatibility with DuckDB, Spark, AWS Athena

---

## Import Organization

### Standard Convention

**Order**:
1. Standard library imports
2. Third-party imports
3. Local application imports

**Example**:
```python
# 1. Standard library
import os
from datetime import date, timedelta
from pathlib import Path

# 2. Third-party
import pandas as pd
import yfinance as yf
import structlog

# 3. Local
from equity_lake.core.constants import STANDARD_COLUMNS
from equity_lake.sources.base import MarketDataFetcher
```

---

### Import Aliases

**Common Aliases**:
```python
import pandas as pd
import numpy as np
import yfinance as yf
import duckdb
import structlog
from datetime import date, timedelta
from pathlib import Path
```

**Rationale**: Follows community conventions for popular libraries

---

## Module Organization

### Core Module (`src/equity_lake/core/`)

**Purpose**: Shared utilities and constants

**Files**:
- `runtime.py`: Runtime configuration, path constants
- `logging.py`: Structured logging setup
- `constants.py`: Standardized column names, schemas

**Dependencies**: None (minimal dependencies)

---

### Ingestion Module (`src/equity_lake/ingestion/`)

**Purpose**: Data fetching and orchestration

**Files**:
- `orchestrator.py`: Multi-market coordination
- `sources/base.py`: Abstract base class
- `sources/us_equity.py`: US market fetcher
- `sources/cn_ashare.py`: China A-shares fetcher
- `sources/hk_sg_equity.py`: HK/SG markets fetcher
- `sources/cn_hybrid.py`: China with fallback

**Dependencies**:
- `core` (constants, logging)
- External APIs (yfinance, akshare)

---

### Storage Module (`src/equity_lake/storage/`)

**Purpose**: Data persistence and querying

**Files**:
- `s3_sync.py`: S3 synchronization
- `parquet.py`: Parquet read/write utilities
- `duckdb.py`: DuckDB query interface

**Dependencies**:
- `core` (constants, logging)
- External tools (boto3, pyarrow, duckdb)

---

### Features Module (`src/equity_lake/features/`)

**Purpose**: Feature engineering (optional)

**Files**:
- `engineering.py`: Feature calculations
- `indicators.py`: Technical indicators

**Dependencies**:
- `storage` (for reading data)
- pandas, numpy

---

### Signals Module (`src/equity_lake/signals/`)

**Purpose**: Trading signal generation (optional)

**Files**:
- `scanner.py`: Signal scanner
- `strategies.py`: Trading strategies

**Dependencies**:
- `features` (for using calculated features)
- pandas, numpy

---

## Documentation Structure

### Markdown Documentation (`docs/`)

**Structure**:
```
docs/
├── getting-started/
│   └── quickstart.md          # Quick start guide
├── user-guide/
│   ├── pipeline.md            # Pipeline usage
│   └── query-guide.md         # Query examples
└── api/                       # API documentation
```

**Purpose**: User-facing documentation

---

### AI Assistant Documentation (`CLAUDE.md`)

**Purpose**: Guide for AI assistants (Claude Code)

**Contents**:
- Project overview and objectives
- Architecture context
- AI assistant workflow guide
- Key scripts reference
- Troubleshooting guide
- Data schema reference
- Testing guidelines
- Development workflow

**Audience**: AI assistants working on the codebase

---

### Planning Documentation (`.planning/` — removed)

**Purpose**: Development planning and analysis

**Contents**:
- `codebase/`: Codebase analysis (this file)
- Implementation plans
- Architecture decision records

**Audience**: Developers and AI assistants

---

## Legacy vs. Modern Structure

### Migration Status

**Legacy Scripts** (`scripts/`):
- `ingest_daily.py` → Migrating to `src/equity_lake/cli/daily.py`
- `sync_from_s3.py` → Migrating to `src/equity_lake/cli/sync.py`
- `query_example.py` → Migrating to `src/equity_lake/cli/query.py`

**Modern Structure** (`src/equity_lake/`):
- Modular package structure
- CLI entry points via `pyproject.toml`
- Better separation of concerns
- Easier to test and maintain

**Timeline**: Legacy scripts still work, but new development should use `src/` structure

---

## Docker Structure

### `Dockerfile`

**Purpose**: Container image for deployment

**Key Stages**:
```dockerfile
# Base image
FROM python:3.11-slim

# Install uv
RUN pip install uv

# Install dependencies
COPY pyproject.toml .
RUN uv pip install .

# Copy application
COPY src/ src/equity_lake/

# Entry point
CMD ["equity", "ingest"]
```

---

### `docker-compose.yml`

**Purpose**: Multi-container orchestration

**Services**:
- **scheduler**: Cron-like scheduler for daily ingestion
- **api**: REST API for data access (optional)
- **worker**: Background job processor (optional)

---

## CI/CD Structure

### GitHub Actions (`.github/workflows/`)

**Test Workflow** (`.github/workflows/test.yml`):
```yaml
name: Test
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh
      - name: Run tests
        run: uv run pytest --cov
```

---

## Summary

**Total Directories**: 20+
**Total Files**: 150+ (including tests and docs)
**Code Files**: 105 Python modules
**Test Files**: 23 test files
**Documentation**: 15+ markdown files
**Configuration**: 10+ config files (pyproject.toml, Makefile, Dockerfile, etc.)

**Key Principles**:
- Clear separation of concerns (core, ingestion, storage, features, signals)
- Modular design (easy to add new markets/sources)
- Hive partitioning for efficient queries
- Comprehensive test coverage
- Well-documented for AI assistants and developers
