# STACK.md - Technology Stack

## Overview

Local-first equity EOD (End-of-Day) data pipeline with S3 bootstrap and daily incremental updates.

## Language & Runtime

- **Language**: Python 3.12+
- **Runtime**: CPython (3.12, 3.13 supported)
- **Package Manager**: [uv](https://github.com/astral-sh/uv) - Ultra-fast Python package manager
- **Build System**: Hatchling (via pyproject.toml)

## Core Dependencies

### Data Sources
- **yfinance** (>=0.2.50) - US/HK/SG market data from Yahoo Finance API
- **akshare** (>=1.15.0) - China A-shares market data
- **fredapi** (>=0.5.2) - FRED economic indicators (macro data)

### Storage & Query
- **DuckDB** (>=1.0.0) - SQL query engine with native Parquet support
- **pandas** (>=2.2.0) - Data manipulation and analysis
- **PyArrow** (>=18.0.0) - Parquet I/O and Arrow data structures

### Utilities
- **python-dotenv** (>=1.0.0) - Environment variable management
- **requests** (>=2.31.0) - HTTP client for API calls
- **numpy** (>=1.24.0) - Numerical computations
- **tqdm** (>=4.66.0) - Progress bars
- **pydantic** (>=2.5.0) - Data validation and settings management
- **structlog** (>=24.1.0) - Structured logging
- **PyYAML** (>=6.0.2) - YAML configuration parsing

## Development Dependencies

### Testing
- **pytest** (>=8.0.0) - Test framework
- **pytest-cov** (>=5.0.0) - Coverage reporting
- **pytest-mock** (>=3.12.0) - Mocking support

### Code Quality
- **ruff** (>=0.8.0) - Linting and formatting (replaces flake8, black, isort)
- **mypy** (>=1.11.0) - Static type checking
- **pre-commit** (>=3.6.0) - Git hooks for code quality
- **black** (>=24.0.0) - Additional code formatting
- **isort** (>=5.13.0) - Import sorting

### Development Tools
- **Jupyter** (>=1.0.0) - Notebook support
- **ipykernel** (>=6.29.0) - Jupyter kernel for Python

## Optional Dependencies

### S3 Operations
- **boto3** (>=1.34.0) - AWS SDK for Python
- **s5cmd** (>=0.3.3) - Fast S3 sync tool (binary)

### ML & Analytics
- **xgboost** (>=3.1.3) - Gradient boosting
- **scikit-learn** (>=1.8.0) - Machine learning algorithms
- **shap** (>=0.49.1) - Model interpretability
- **pandas-ta** (>=0.4.71b0) - Technical analysis indicators
- **seaborn** (>=0.13.2) - Statistical visualization
- **networkx** (>=3.6.1) - Graph algorithms
- **scipy** (>=1.17.0) - Scientific computing
- **statsmodels** (>=0.14.6) - Statistical modeling

### Visualization
- **matplotlib** (>=3.8.0) - Plotting library
- **plotly** (>=6.5.0) - Interactive visualizations

## Deployment & Infrastructure

### Containerization
- **Docker** - Container runtime
- **docker-compose** - Multi-container orchestration
- **Base Image**: `python:3.11-slim`
- **Package Installer**: uv (in-container)

### Build & Release
- **Build Tool**: Hatchling
- **Package Format**: Wheel
- **Source Layout**: `src/equity_lake/`

## Environment Configuration

### Environment Variables
```bash
# AWS (for S3 sync)
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
S3_BUCKET

# Data & Logging
DATA_DIR=data
LOG_DIR=logs
DB_PATH=equity_data.duckdb

# Markets
MARKETS=us,cn,hk,sg

# API Behavior
API_RETRY_ATTEMPTS=3
API_RETRY_DELAY=1.0

# Logging
LOG_LEVEL=INFO

# Development
DEV_MODE=false
USE_TEST_DATA=false

# FRED API
FRED_API_KEY
ENABLE_MACRO_INDICATORS=true
MACRO_INDICATORS=dxy,treasury_10y,tips_yield,...
```

### Configuration Files
- `.env` - Local environment (git-ignored)
- `.env.example` - Template
- `config/tickers.yaml` - Market ticker lists
- `pyproject.toml` - Project config
- `Makefile` - Development commands

## Build & Runtime Requirements

### Minimum Requirements
- Python 3.12+
- 500MB disk space for dependencies
- 2GB RAM (4GB recommended for large datasets)

### Recommended for Production
- Python 3.12
- 8GB RAM
- SSD storage (for Parquet I/O)
- Stable internet connection (for API calls)

## CLI Entry Points

Installed via `pyproject.toml [project.scripts]`:

```bash
equity-daily              # Daily EOD ingestion
equity-sync               # S3 bootstrap sync
equity-query              # DuckDB queries
equity-pipeline           # Full pipeline orchestration
equity-monitor            # Health checks
equity-backfill           # Historical backfill
equity-macro              # Macro indicator fetching
equity-generate-test-data # Test data generation
equity-price-forecast     # Price forecasting
```

## Development Workflow

```bash
# Setup
uv venv
source .venv/bin/activate
uv sync

# Development
uv sync --group dev
make format
make lint
make check
make test

# Running CLI
uv run equity-daily
# or
make daily
```

## Version Control

- **Git** - Version control
- **pre-commit** - Git hooks (code quality on commit)
- **.gitignore** - Standard Python ignores (venv, __pycache__, *.pyc, .env)

## Documentation

- **README.md** - Project overview
- **CLAUDE.md** - AI development guide
- **docs/** - Comprehensive documentation (guides, architecture, decisions)
- **QUICKSTART.md** - Getting started guide
- **PIPELINE_USAGE.md** - Pipeline operations guide
