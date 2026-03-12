# Tech Stack

**Last Updated**: 2026-03-05
**Project**: Equity EOD Data Pipeline

## Language & Runtime

### Python
- **Version**: 3.11+ (specified in `.python-version`)
- **Package Manager**: uv (ultra-fast Python package manager written in Rust)
- **Virtual Environment**: uv-managed `.venv`

### Why uv?
- 10-100x faster dependency resolution than pip
- Zero-config virtual environment management
- Built-in dependency caching
- Compatible with existing `pyproject.toml` and `requirements.txt`

## Core Dependencies

### Data Acquisition
- **yfinance** (>=0.2.50): Yahoo Finance API for US/HK/SG market data
- **akshare** (>=1.15.0): China A-shares market data
- **efinance**: Alternative Chinese market data source

### Data Processing
- **pandas** (>=2.2.0): Data manipulation and analysis
- **numpy**: Numerical computing foundation
- **pyarrow** (>=18.0.0): Parquet I/O and columnar storage

### Storage & Query
- **duckdb** (>=1.0.0): SQL query engine for analytics
- **fastparquet**: Alternative Parquet reader/writer

### Cloud & Storage
- **boto3**: AWS SDK for Python (S3 operations)
- **s5cmd**: High-performance S3 sync tool (external binary)

### Utilities
- **python-dotenv** (>=1.0.0): Environment variable management
- **requests**: HTTP client library
- **typer**: CLI framework for modern command-line interfaces
- **rich**: Terminal formatting and progress bars

## Development Tools

### Code Quality
- **ruff** (>=0.8.0): Ultra-fast Python linter and formatter
  - Replaces flake8, black, isort
  - Configuration in `pyproject.toml`
- **mypy** (>=1.11.0): Static type checker
  - Strict mode enabled
  - Comprehensive type checking

### Testing
- **pytest** (>=8.0.0): Testing framework
- **pytest-cov** (>=5.0.0): Coverage plugin
- **pytest-mock**: Mocking utilities
- **pytest-asyncio**: Async test support

### Build & Automation
- **make**: Build automation via Makefile
- **pre-commit**: Git hooks for pre-commit checks
- **docker**: Containerization
- **docker-compose**: Multi-container orchestration

## Configuration Files

### Project Configuration
- **`pyproject.toml`**: Primary project configuration
  - Dependencies (production and dev)
  - Ruff configuration
  - Mypy settings
  - Project metadata
  - CLI entry points

### Python Configuration
- **`.python-version`**: Specifies Python 3.12
- **`requirements.txt`**: Legacy pip-compatible dependency list

### Container Configuration
- **`Dockerfile`**: Multi-stage Python container image
- **`docker-compose.yml`**: Container orchestration for pipeline services

### Development Configuration
- **`Makefile`**: Convenience commands for common operations
  - `make setup`: Initialize development environment
  - `make daily`: Run daily ingestion
  - `make test`: Run test suite
  - `make lint`: Run ruff linting
  - `make format`: Format code with ruff

### Git Configuration
- **`.gitignore`**: Excludes artifacts (`.venv/`, `data/`, `__pycache__/`)
- **`.pre-commit-config.yaml`**: Pre-commit hook definitions

## Frameworks & Patterns

### Architecture Patterns
- **ETL Pipeline**: Extract-Transform-Load pattern for data ingestion
- **Data Lake**: Hive-partitioned Parquet storage
- **Strategy Pattern**: Pluggable market data fetchers
- **Template Method**: Base classes with customizable fetch logic

### Code Organization
- **Modular Design**: Clear separation of concerns
  - `src/equity_lake/core/`: Shared utilities and constants
  - `src/equity_lake/ingestion/`: Data fetchers and orchestration
  - `src/equity_lake/storage/`: Data persistence and querying
  - `src/equity_lake/features/`: Feature engineering
  - `src/equity_lake/signals/`: Trading signal generation

### Design Principles
- **Local-First**: After initial S3 sync, run completely locally
- **Idempotent Operations**: Safe to re-run without side effects
- **Graceful Degradation**: Continue processing if one market fails
- **Observable Operations**: Comprehensive logging for debugging

## Data Stack

### Storage Format
- **Parquet**: Columnar storage format
  - Compression: Snappy
  - Partitioning: Hive-style by date (`date=YYYY-MM-DD/`)

### Query Engine
- **DuckDB**: In-memory SQL database
  - Zero-copy Parquet reading
  - Hive partitioning support
  - Python API integration

### Data Lake Structure
```
data/lake/
├── us_equity/         # US stocks (from S3)
├── cn_ashare/         # China A-shares (local fetch)
└── hk_sg_equity/      # HK/SG stocks (local fetch)
```

## CLI Entry Points

### Primary Commands
- **`equity-daily`**: Run daily EOD ingestion (`src/equity_lake/cli/daily.py`)
- **`equity-sync`**: S3 historical data sync (`src/equity_lake/cli/sync.py`)
- **`equity-query`**: DuckDB query interface (`src/equity_lake/cli/query.py`)

### Installation
```bash
# Using uv
uv pip install -e .

# Or traditional pip
pip install -e .
```

## Version Management

### Dependency Strategy
- **Pin Major Versions**: Production dependencies pinned to major versions
- **Dev Dependencies**: Latest compatible versions allowed
- **uv Lock File**: `.uv.lock` for reproducible builds

### Compatibility
- **Python**: 3.11+ required
- **Platform**: macOS, Linux (WSL supported)
- **Architecture**: x86_64, arm64 (Apple Silicon)

## Performance Optimizations

### Parallel Processing
- **S3 Sync**: s5cmd with 32 parallel workers
- **Data Fetching**: Concurrent API calls where possible
- **DuckDB**: Automatic query parallelization

### Memory Efficiency
- **Chunked Reading**: Process large datasets in chunks
- **Lazy Loading**: DuckDB zero-copy Parquet reading
- **Streaming**: Minimize in-memory data retention

## Development Workflow

### Quick Start
```bash
# 1. Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Create venv and install dependencies
uv venv && source .venv/bin/activate
uv sync

# 3. Run tests
make test

# 4. Run linting
make lint
```

### Code Quality Pipeline
```bash
# Format code
make format

# Run linter
make lint

# Type check
make check

# Run tests with coverage
make test
```

## Known Limitations

### Platform-Specific
- **s5cmd**: Linux/macOS only (Windows uses AWS CLI fallback)
- **Cron**: Native cron on Linux/macOS (Windows Task Scheduler equivalent)

### API Limitations
- **yfinance**: Rate limiting, occasional downtime
- **akshare**: May require VPN for China access
- **Free APIs**: No service level guarantees

## Future Stack Considerations

### Potential Additions
- **Apache Airflow**: Workflow orchestration
- **Prefect/Dask**: Modern workflow alternatives
- **Redis**: Caching layer for API responses
- **PostgreSQL**: Metadata and config storage
- **FastAPI**: REST API for data access

### Migration Path
- Current stack optimized for local development
- Cloud-native migration path available via Docker
- Microservices architecture possible if needed

---

**Total Dependencies**: 20+ production packages, 10+ dev packages
**Python Version**: 3.11+ (tested on 3.12)
**Package Manager**: uv (primary), pip (fallback)
