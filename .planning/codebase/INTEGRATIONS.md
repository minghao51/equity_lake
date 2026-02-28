# INTEGRATIONS.md - External Integrations

## Overview

The pipeline integrates with multiple external data sources, storage systems, and services to fetch, store, and query equity market data.

## Data Source Integrations

### Yahoo Finance API (via yfinance)

**Purpose**: Fetch EOD data for US, Hong Kong, and Singapore markets

**Library**: `yfinance>=0.2.50`

**Usage Locations**:
- `src/equity_lake/ingestion/sources/us.py` - US equities
- `src/equity_lake/ingestion/sources/hk_sg.py` - Hong Kong and Singapore equities

**Data Retrieved**:
- OHLCV (Open, High, Low, Close, Volume)
- Adjusted close price
- Date stamps

**Ticker Formats**:
- US: `AAPL`, `GOOGL`, `MSFT`
- Hong Kong: `0700.HK`, `9988.HK`
- Singapore: `D05.SI`, `O39.SI`

**Rate Limits**:
- No official rate limit (best-effort)
- Implements exponential backoff retry logic (3 attempts)
- 500ms delay between requests

**Error Handling**:
- Retry on connection errors
- Retry on timeout
- Continue with fallback ticker list on API failures

**Example**:
```python
import yfinance as yf
data = yf.download('AAPL', start='2024-01-01', end='2024-01-02')
```

### Akshare API (China A-shares)

**Purpose**: Fetch EOD data for China A-shares market

**Library**: `akshare>=1.15.0`

**Usage Location**: `src/equity_lake/ingestion/sources/cn.py`

**Data Retrieved**:
- OHLCV (Chinese column names: 开盘, 最高, 最低, 收盘, 成交量)
- Date stamps

**Ticker Format**:
- 6-digit codes: `000001`, `600000`, `002415`

**Column Mapping**:
```python
'开盘' → 'open'
'最高' → 'high'
'最低' → 'low'
'收盘' → 'close'
'成交量' → 'volume'
```

**Rate Limits**:
- No official rate limit
- Implements retry logic with exponential backoff
- 100ms delay between stock requests

**Error Handling**:
- Retry on connection errors
- Graceful degradation (skip failed stocks)
- Network requirement: May need VPN for China access

**Example**:
```python
import akshare as ak
df = ak.stock_zh_a_hist(symbol='000001', period='daily', start_date='20240101', end_date='20240102')
```

### FRED API (Federal Reserve Economic Data)

**Purpose**: Fetch macroeconomic indicators for analysis

**Library**: `fredapi>=0.5.2`

**Usage Location**: `src/equity_lake/ingestion/sources/macro.py`

**API Key**: Required (free registration at https://fred.stlouisfed.org/docs/api/api_key.html)

**Data Retrieved**:
- DXY (Dollar Index)
- 10-Year Treasury Yield
- TIPS Yield
- Breakeven Inflation
- VIX (CBOE Volatility Index)
- Gold ETF prices (GLD, IAU)
- Economic Policy Uncertainty

**Environment Variables**:
```bash
FRED_API_KEY=your_api_key_here
ENABLE_MACRO_INDICATORS=true
MACRO_INDICATORS=dxy,treasury_10y,tips_yield,...
```

**Rate Limits**:
- 120 requests per minute (free tier)
- Implements retry logic

**Example**:
```python
from fredapi import Fred
fred = Fred(api_key='your_key')
dxy = fred.get_series('DTWEXBGS')
```

## Storage Integrations

### AWS S3 (Bootstrap)

**Purpose**: One-time download of historical US equity data

**Library**: `boto3>=1.34.0`, AWS CLI, s5cmd

**Usage Location**: `src/equity_lake/storage/s3_sync.py`, `src/equity_lake/cli/sync.py`

**Operations**:
- Download from S3 bucket
- Parallel sync with s5cmd (32 workers default)
- Integrity validation

**Environment Variables**:
```bash
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
S3_BUCKET=s3://your-bucket/path/
```

**Bucket Structure** (expected):
```
s3://bucket/
└── us_equity/
    ├── date=2020-01-01/
    │   └── 2020-01-01.parquet
    ├── date=2020-01-02/
    │   └── 2020-01-02.parquet
    └── ...
```

**Tools**:
- **s5cmd** (preferred): Fast parallel sync (10-100x AWS CLI)
- **AWS CLI** (fallback): `aws s3 cp --recursive`

**Example**:
```bash
# Using s5cmd
s5cmd cp s3://bucket/us_equity/date=*/*.parquet data/lake/us_equity/

# Using AWS CLI
aws s3 cp s3://bucket/us_equity/ data/lake/us_equity/ --recursive
```

### Local Parquet Files

**Purpose**: Primary storage for all market data

**Library**: `PyArrow>=18.0.0`, `pandas>=2.2.0`

**Storage Structure**:
```
data/lake/
├── us_equity/
│   ├── date=2024-01-01/
│   │   └── 2024-01-01.parquet
│   └── date=2024-01-02/
│       └── 2024-01-02.parquet
├── cn_ashare/
│   └── date=2024-01-01/
│       └── 2024-01-01.parquet
└── hk_sg_equity/
    └── date=2024-01-01/
        └── 2024-01-01.parquet
```

**Partitioning**: Hive-style (`date=YYYY-MM-DD/`)

**Schema**:
```
ticker: string
date: date
open: float64
high: float64
low: float64
close: float64
volume: int64
adj_close: float64 (optional)
```

**Usage**:
```python
import pandas as pd
df = pd.read_parquet('data/lake/us_equity/date=2024-01-01/2024-01-01.parquet')
```

### DuckDB (Query Engine)

**Purpose**: SQL query engine for analytics

**Library**: `duckdb>=1.0.0`

**Usage Location**: `src/equity_lake/storage/duckdb.py`

**Features**:
- Zero-copy Parquet queries
- Hive partition pruning (by date)
- Unified view across markets
- Materialized views

**Database File**: `equity_data.duckdb` (local)

**Example**:
```python
import duckdb
con = duckdb.connect('equity_data.duckdb')
df = con.execute("""
    SELECT ticker, close, volume
    FROM read_parquet('data/lake/**/*.parquet')
    WHERE date >= '2024-01-01'
""").df()
```

**Views Created**:
- `equity_all` - Unified view of all markets
- Market-specific views (us_equity, cn_ashare, hk_sg_equity)

## Configuration Integrations

### YAML Configuration Files

**Purpose**: Manage ticker lists and market configurations

**Library**: `PyYAML>=6.0.2`

**Location**: `config/tickers.yaml`

**Usage**:
```yaml
us_equity:
  fallback:
    - AAPL
    - GOOGL
    - MSFT

cn_ashare:
  fallback:
    - "000001"
    - "600000"
```

**Accessed via**: `src/equity_lake/config/` modules

### Environment Variables

**Library**: `python-dotenv>=1.0.0`

**File**: `.env` (git-ignored)

**Usage**: `src/equity_lake/config/loader.py`

## Development Integrations

### Pre-commit Hooks

**Purpose**: Code quality enforcement on git commit

**Framework**: `pre-commit>=3.6.0`

**Hooks configured** (in `.pre-commit-config.yaml`):
- ruff (linting and formatting)
- mypy (type checking)

**Installation**:
```bash
pre-commit install
```

### Logging

**Library**: `structlog>=24.1.0`

**Usage**: Structured logging throughout the pipeline

**Configuration**: `src/equity_lake/core/logging.py`

**Log Files**: `logs/` directory
- `ingest_daily.log`
- `sync_from_s3.log`
- `run_pipeline.log`
- `fetch_macro.log`

## Monitoring & Health

No external monitoring integrations currently. Health checks are local:

**Location**: `src/equity_lake/monitoring/health.py`

**Checks**:
- Data directory existence
- Parquet file integrity
- Database connectivity
- Log file health

## Deployment Integrations

### Docker

**Purpose**: Containerize the pipeline for production deployment

**Orchestration**: `docker-compose.yml`

**Services**:
- **sync** - One-time S3 bootstrap
- **daily** - Cron-based daily ingestion
- **query** - Interactive query interface
- **dev** - Development environment
- **jupyter** - Jupyter Lab for exploration

**Example**:
```bash
docker compose --profile daily up -d
```

## Authentication & Authorization

### AWS S3

**Method**: Access key authentication

**Credentials**:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`

**Storage**: `.env` file (git-ignored)

**No IAM roles** configured (local-only deployment)

### FRED API

**Method**: API key in query string

**Credential**: `FRED_API_KEY` environment variable

**No OAuth** required

## Error Handling & Retry Logic

All external integrations implement retry logic:

**Configuration**:
```bash
API_RETRY_ATTEMPTS=3
API_RETRY_DELAY=1.0  # Exponential backoff
```

**Implementation**:
- `src/equity_lake/ingestion/sources/base.py` - Base retry decorator
- Applied to all fetcher classes

## Security Considerations

- **No hardcoded secrets** - All credentials via environment variables
- **.env git-ignored** - Prevents accidental credential commits
- **.env.example** provided - Template without actual values
- **Read-only operations** - No write operations to external systems
- **No data exfiltration** - All data stays local

## Future Integrations (Planned)

- Additional data sources (Bloomberg, Reuters)
- Time-series databases (TimescaleDB)
- Message queues (RabbitMQ, Redis)
- Monitoring (Prometheus, Grafana)
- Alerting (PagerDuty, Slack webhooks)
