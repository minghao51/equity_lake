# 🤖 CLAUDE.md - AI Development Guide

This document provides context and guidance for Claude Code and other AI assistants working on this equity EOD data pipeline project.

> **📌 For Users:** If you're looking for how to **use** the pipeline, see [Quick Start Guide](docs/getting-started/quickstart.md) or [Pipeline Usage Guide](docs/user-guide/pipeline.md). This document is for AI assistants and developers.

---

## 📋 Project Overview & Objectives

**Project Type**: Data Engineering Pipeline
**Language**: Python 3.11+
**Package Manager**: uv (ultra-fast Python package manager)
**Data Stack**: yfinance, akshare, DuckDB, Parquet
**Deployment**: Local cron + Docker

### Primary Objectives

1. **Bootstrap Historical Data**: One-time sync from S3 (partitioned Parquet) to local storage
2. **Daily Incremental Updates**: Fetch and append only yesterday's EOD data (multi-market support)
3. **Local-First Architecture**: After initial sync, operate 100% locally without cloud dependencies
4. **Unified SQL Access**: Query all markets via DuckDB with Hive partitioning for performance
5. **Market Coverage**: US Equities (S3), China A-shares, Hong Kong, Singapore (local fetch)

### Key Design Principles

- **Minimal Cloud Dependency**: Use S3 only for initial bootstrap, then run locally
- **Partitioned Storage**: Hive-style partitioning (`date=YYYY-MM-DD/`) for efficient time-range queries
- **Idempotent Operations**: Scripts should be safe to re-run without data duplication
- **Graceful Degradation**: Continue processing other markets if one fails
- **Observable Operations**: Comprehensive logging for debugging and monitoring

---

## 🏗️ Architecture Context

### Data Flow

```
S3 Historical → One-time Sync → Local Parquet Lake → Daily Append → DuckDB Query
```

### Key Design Decisions

1. **Why S3 + Local Hybrid?**
   - S3: One-time download of full historical data (multi-year, multi-GB)
   - Local: Daily appends are tiny (MBs), no need for cloud storage
   - Result: Best of both worlds - cloud scale + local speed

2. **Why Parquet + Hive Partitioning?**
   - Columnar format = fast analytics
   - Partitioning by date = efficient time-range queries
   - DuckDB native support = zero-copy queries

3. **Why uv instead of pip/poetry?**
   - 10-100x faster dependency resolution and installation
   - Built in Rust, zero-config virtual environments
   - Compatible with existing requirements.txt
   - Native support for pyproject.toml

4. **Why yfinance + akshare?**
   - yfinance: Free, reliable for US/HK/SG markets
   - akshare: Best free source for China A-shares
   - Both are lightweight (no heavy dependencies like pandas-datareader)

---

## 🤖 AI Assistant Workflow Guide

### When the user asks "What should I work on?"

**Recommended Actions:**
1. Check for failing tests: `make test` or `uv run pytest -xvs`
2. Review recent git history: `git log --oneline -10`
3. Check for TODO/FIXME comments: `grep -r "TODO\|FIXME" scripts/ tests/`
4. Review implementation plans in `plans/` directory
5. Check logs for recent errors: `ls -lt logs/*.log | head -5`

### Common Development Tasks

#### Task 1: Adding a New Data Source

**Steps:**
1. Create new fetcher class inheriting from `MarketDataFetcher` (scripts/ingest_daily.py:52)
2. Implement `fetch(self, trading_date: date) -> pd.DataFrame` method
3. Ensure schema compliance with `STANDARD_COLUMNS` (scripts/__init__.py)
4. Add retry logic using `_retry_on_failure` (scripts/ingest_daily.py:63)
5. Register in `fetch_market_data()` function (scripts/ingest_daily.py:429)
6. Add tests in `tests/test_ingest.py`
7. Update documentation in README.md

**Example:**
```python
class NewMarketFetcher(MarketDataFetcher):
    def fetch(self, trading_date: date) -> pd.DataFrame:
        # Fetch logic here
        # Must return DataFrame with STANDARD_COLUMNS
        pass
```

#### Task 2: Debugging Data Ingestion Issues

**Diagnostic Checklist:**
1. Check logs: `tail -100 logs/ingest_daily.log`
2. Verify date format: `date +%Y-%m-%d`
3. Test API access manually:
   - US: `yf.download('AAPL', start='2024-12-01', end='2024-12-02')`
   - CN: `ak.stock_zh_a_hist(symbol='000001', period='daily', ...)`
4. Check network connectivity and rate limits
5. Verify data directories exist: `ls -la data/lake/*/`
6. Validate Parquet files: `python -c "import pandas as pd; pd.read_parquet('path/to/file.parquet')"`

#### Task 3: Adding New Query Templates

**Steps:**
1. Add method to `QueryExamples` class (scripts/query_example.py:151)
2. Follow naming convention: `query_N_descriptive_name`
3. Include docstring with description and parameters
4. Use parameterized SQL (f-strings) for flexibility
5. Add to `query_map` in main() (scripts/query_example.py:528)
6. Update help text with query description
7. Test with: `uv run python scripts/query_example.py --query your_new_query`

#### Task 4: Performance Optimization

**Before optimizing:**
1. Run benchmarks: `uv run python scripts/query_example.py --query benchmark`
2. Use DuckDB EXPLAIN: `EXPLAIN SELECT ...`
3. Check Parquet file sizes: `du -sh data/lake/*/date=*`

**Common optimizations:**
- Add date filtering to leverage partition pruning
- Use column projection instead of `SELECT *`
- Create materialized views for frequently accessed data
- Increase s5cmd workers for S3 sync: `--workers 32`
- Batch API calls instead of individual ticker requests

### Code Review Guidelines

When reviewing code changes, check for:

**Functionality:**
- [ ] Schema compliance: All DataFrames have `STANDARD_COLUMNS`
- [ ] Error handling: Try-except blocks with logging
- [ ] Idempotency: Safe to re-run without side effects
- [ ] Graceful degradation: Continues on partial failures

**Code Quality:**
- [ ] Type hints: All functions have proper type annotations
- [ ] Logging: Appropriate log levels (DEBUG, INFO, WARNING, ERROR)
- [ ] Documentation: Docstrings for classes and public methods
- [ ] Testing: Unit tests added for new functionality

**Performance:**
- [ ] Rate limiting: Time delays between API calls
- [ ] Efficient queries: Uses partition pruning and column projection
- [ ] Memory: Handles large datasets without OOM errors

**Security:**
- [ ] No hardcoded credentials in code
- [ ] Environment variables for sensitive data
- [ ] Input validation for user-provided parameters

---

### Using uv (Recommended)

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create and activate venv
uv venv
source .venv/bin/activate

# Install dependencies
uv sync

# Or if using pyproject.toml
uv sync
```

### Project Structure

```
equity-eod/
├── data/lake/              # Data lake (git-ignored)
│   ├── us_equity/          # US stocks (from S3)
│   ├── cn_ashare/          # China A-shares (local)
│   └── hk_sg_equity/       # HK/SG stocks (local)
├── scripts/
│   ├── sync_from_s3.sh     # Initial S3 sync
│   ├── ingest_daily.py     # Daily EOD append
│   ├── query.sql           # SQL templates
│   └── query_example.py    # Python query examples
├── tests/                  # Unit tests
├── logs/                   # Application logs
├── .python-version         # Python version (3.11+)
├── pyproject.toml          # uv project config
├── requirements.txt        # Dependencies
├── Makefile                # Common commands
└── docker-compose.yml      # Docker orchestration
```

---

## 📦 Dependencies

### Core Libraries

```toml
[project]
dependencies = [
    "yfinance>=0.2.50",      # US/HK/SG market data
    "akshare>=1.15.0",       # China A-shares data
    "duckdb>=1.0.0",         # SQL query engine
    "pandas>=2.2.0",         # Data manipulation
    "pyarrow>=18.0.0",       # Parquet I/O
    "python-dotenv>=1.0.0",  # Environment variables
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.8.0",           # Linting
    "mypy>=1.11.0",          # Type checking
]
```

### Installing Dependencies

```bash
# Production dependencies
uv pip install -r requirements.txt

# Development dependencies
uv pip install -r requirements.txt -e ".[dev]"

# Or using pyproject.toml
uv sync --group dev
```

---

## 🎯 Key Scripts Reference

### Core Scripts

#### 1. `scripts/ingest_daily.py` - Daily EOD Data Ingestion

**Location**: `scripts/ingest_daily.py` (637 lines)
**Purpose**: Fetch and append yesterday's EOD data for all markets

**Key Classes:**
- `MarketDataFetcher` (line 52): Base class with retry logic
- `USEquityFetcher` (line 82): Fetches US market data via yfinance
- `CNAshareFetcher` (line 163): Fetches China A-shares via akshare
- `HKSGEquityFetcher` (line 253): Fetches HK/SG markets via yfinance

**Key Functions:**
- `fetch_market_data()` (line 429): Router for market-specific fetchers
- `run_daily_ingestion()` (line 468): Orchestrates multi-market ingestion
- `write_to_partitioned_parquet()` (line 333): Writes to Hive-partitioned Parquet
- `validate_schema()` (line 399): Ensures OHLCV schema compliance

**Usage Examples:**
```bash
# Fetch yesterday's data for all markets
make daily
# or
uv run python scripts/ingest_daily.py

# Fetch specific date
uv run python scripts/ingest_daily.py --date 2024-12-01

# Fetch only US and China markets
uv run python scripts/ingest_daily.py --markets us,cn

# Dry run (test without writing)
uv run python scripts/ingest_daily.py --dry-run --verbose
```

**Error Handling:**
- Retry logic with exponential backoff (default: 3 attempts)
- Continues processing other markets if one fails
- Logs all errors to `logs/ingest_daily.log`
- Returns exit code 1 if any market fails

#### 2. `scripts/sync_from_s3.py` - S3 Historical Data Sync

**Location**: `scripts/sync_from_s3.py` (398 lines)
**Purpose**: One-time bootstrap of historical US equity data from S3

**Key Classes:**
- `S3Syncer` (line 45): Handles S3 to local synchronization
  - Auto-detects s5cmd or AWS CLI
  - Supports parallel downloads (configurable workers)
  - Validates download integrity

**Key Methods:**
- `_detect_tool()` (line 74): Auto-selects best available sync tool
- `_test_s3_access()` (line 107): Verifies bucket accessibility
- `sync_with_s5cmd()` (line 138): Fast parallel sync with s5cmd
- `sync_with_aws_cli()` (line 176): Fallback using AWS CLI
- `verify_download()` (line 214): Validates Parquet file structure

**Usage Examples:**
```bash
# Sync from default S3 bucket
make sync
# or
uv run python scripts/sync_from_s3

# Sync from custom bucket
uv run python scripts/sync_from_s3 --bucket s3://my-bucket/us_equity/

# Use s5cmd with 32 workers
uv run python scripts/sync_from_s3 --tool s5cmd --workers 32

# Dry run (test without downloading)
uv run python scripts/sync_from_s3 --dry-run --verbose
```

**Expected Output Structure:**
```
data/lake/us_equity/
├── date=2020-01-01/
│   └── 2020-01-01.parquet
├── date=2020-01-02/
│   └── 2020-01-02.parquet
└── ...
```

#### 3. `scripts/query_example.py` - DuckDB Query Templates

**Location**: `scripts/query_example.py` (594 lines)
**Purpose**: Example queries and unified view management

**Key Classes:**
- `EquityDataDB` (line 47): DuckDB connection manager
  - `_setup_views()` (line 61): Creates unified `equity_all` view
  - `query()` (line 130): Executes SQL and returns DataFrame

- `QueryExamples` (line 151): Collection of analytical queries
  - `query_1_latest_data_summary()` (line 157): Latest data by market
  - `query_2_top_volume_stocks()` (line 176): Volume leaders
  - `query_3_top_gainers_losers()` (line 205): Price performance
  - `query_6_volatility_analysis()` (line 291): Most volatile stocks
  - `query_7_market_summary_stats()` (line 332): Market statistics

**Usage Examples:**
```bash
# Run all queries
make query
# or
uv run python scripts/query_example.py

# Run specific query
uv run python scripts/query_example.py --query top_volume --days 14

# Query specific ticker
uv run python scripts/query_example.py --query moving_avg --ticker AAPL --days 20

# Export results to CSV
uv run python scripts/query_example.py --query gainers_losers --output results.csv

# Benchmark performance
uv run python scripts/query_example.py --query benchmark
```

**Available Queries:**
- `latest_summary`: Latest data summary by market
- `top_volume`: Top stocks by volume (last N days)
- `gainers_losers`: Top gainers and losers
- `volatility`: Most volatile stocks
- `market_stats`: Market summary statistics
- `price_range`: Price range analysis
- `benchmark`: Performance benchmarks

#### 4. `scripts/generate_test_data.py` - Test Data Generator

**Purpose**: Generate realistic test data for development and testing

**Usage:**
```bash
make generate-test-data
# or
uv run python scripts/generate_test_data.py
```

### Utility Modules

#### `scripts/__init__.py`

**Key Exports:**
- `STANDARD_COLUMNS`: List of required OHLCV columns
- `LAKE_DIR`, `US_EQUITY_DIR`, `CN_ASHARE_DIR`, `HK_SG_EQUITY_DIR`: Path constants
- `LOGS_DIR`: Log file directory
- `get_project_config()`: Loads configuration from environment/pyproject.toml
- `setup_logging()`: Configures logging with file and console handlers

### Configuration Files

#### `pyproject.toml`

**Project Metadata:**
- Python version: 3.11+
- Dependencies: yfinance, akshare, duckdb, pandas, pyarrow
- Dev dependencies: pytest, ruff, mypy

**Entry Points:**
```bash
equity-daily    # Runs equity_lake.cli.daily:main
equity-sync     # Runs equity_lake.cli.sync:main
equity-query    # Runs equity_lake.cli.query:main
```

**Ruff Configuration:**
- Line length: 88 characters
- Target version: Python 3.11
- Auto-fixable rules enabled

#### `Makefile`

**Common Commands:**
```bash
make setup          # Create venv and install dependencies
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

## 🔧 Troubleshooting Guide

### Common Issues and Solutions

#### Issue 1: "No files found matching pattern" in DuckDB

**Symptoms:**
```
Catalog Error: No files found matching pattern "data/lake/us_equity/date=*/*.parquet"
```

**Diagnosis:**
```bash
# Check if data directories exist
ls -la data/lake/

# Check if Parquet files are present
find data/lake/ -name "*.parquet" | head -10

# Verify partition structure
ls -la data/lake/us_equity/ | grep "date="
```

**Solutions:**
1. Run initial sync: `make sync` or `uv run python scripts/sync_from_s3`
2. Generate test data: `make generate-test-data`
3. Verify partition format is `date=YYYY-MM-DD/*.parquet` (not `date=YYYY-MM-DD*.parquet`)

#### Issue 2: yfinance Rate Limiting

**Symptoms:**
```
Too Many Requests for URL
```

**Solutions:**
```python
# Add rate limiting in fetcher (already implemented in ingest_daily.py:209)
import time
time.sleep(0.5)  # 500ms delay between requests

# Or use batch downloads
tickers = ['AAPL', 'GOOGL', 'MSFT']
data = yf.download(tickers, start=start, end=end, group_by='ticker')
```

#### Issue 3: akshare Connection Errors

**Symptoms:**
```
requests.exceptions.ConnectionError: HTTPSConnectionPool
```

**Diagnosis:**
```python
# Test akshare connectivity
import akshare as ak
try:
    df = ak.stock_info_a_code_name()
    print(f"✅ akshare OK: {len(df)} stocks")
except Exception as e:
    print(f"❌ akshare error: {e}")
```

**Solutions:**
1. Check network connectivity (may need VPN for China)
2. Verify akshare version: `uv pip list | grep akshare`
3. Retry with exponential backoff (already implemented in CNAshareFetcher)

#### Issue 4: S3 Sync Fails with "Access Denied"

**Symptoms:**
```
An error occurred (403) when calling the HeadObject operation
```

**Diagnosis:**
```bash
# Test AWS credentials
aws configure list

# Test bucket access
aws s3 ls s3://your-bucket/

# For public buckets, test with --no-sign-request
aws s3 ls s3://public-bucket/ --no-sign-request
```

**Solutions:**
1. Configure AWS credentials: `aws configure`
2. Use IAM role for EC2 instances
3. For public buckets, ensure `--no-sign-request` flag is used
4. Check bucket policy permissions

#### Issue 5: "Module not found" Errors

**Symptoms:**
```
ModuleNotFoundError: No module named 'yfinance'
```

**Diagnosis:**
```bash
# Check if venv is activated
which python

# Check if packages are installed
uv pip list | grep -E "(yfinance|akshare|duckdb)"
```

**Solutions:**
```bash
# Activate venv
source .venv/bin/activate

# Reinstall dependencies
uv sync

# Or install with pip
uv pip install -r requirements.txt
```

#### Issue 6: Parquet Schema Mismatch

**Symptoms:**
```
Invalid: Error while scanning parquet file
```

**Diagnosis:**
```python
# Check Parquet schema
import pandas as pd
df = pd.read_parquet('data/lake/us_equity/date=2024-12-01/2024-12-01.parquet')
print(df.columns.tolist())
print(df.dtypes)
```

**Solutions:**
1. Ensure all columns match `STANDARD_COLUMNS`
2. Check date column is `date` type, not string
3. Verify numeric columns are float64/int64
4. Re-run ingestion with corrected schema

#### Issue 7: Docker Build Failures

**Symptoms:**
```
ERROR [builder] failed to solve
```

**Diagnosis:**
```bash
# Check Dockerfile syntax
docker compose config

# Build with no cache
docker compose build --no-cache
```

**Solutions:**
1. Verify Dockerfile has correct base image (python:3.11-slim)
2. Check uv installation in Dockerfile
3. Ensure requirements.txt is accessible during build
4. Check for network issues during dependency installation

#### Issue 8: Tests Fail Due to Missing Data

**Symptoms:**
```
AssertionError: Directory not found: data/lake/us_equity
```

**Solutions:**
```bash
# Generate test data before running tests
make generate-test-data

# Or skip integration tests
uv run pytest tests/ -m "not integration"

# Or run only unit tests
make test-unit
```

### Debug Mode Checklist

When troubleshooting, enable verbose logging:

```bash
# For daily ingestion
uv run python scripts/ingest_daily.py --verbose --dry-run

# For S3 sync
uv run python scripts/sync_from_s3.py --verbose --dry-run

# For queries
uv run python scripts/query_example.py --verbose

# Check logs
tail -f logs/ingest_daily.log
tail -f logs/sync_from_s3.log
```

### Getting Help

1. **Check logs first**: `ls -lt logs/*.log | head -5`
2. **Run diagnostics**: `make validate`
3. **Test in isolation**: Run specific script with `--verbose`
4. **Review error messages**: Full stack traces are in log files
5. **Check dependencies**: `uv pip list | grep -E "(yfinance|akshare|duckdb)"`
6. **Verify data integrity**: `find data/lake/ -name "*.parquet" | wc -l`

---

## 📊 Data Schema Reference

### Standard OHLCV Schema

All markets MUST conform to this schema (defined in `scripts/__init__.py`):

```python
STANDARD_COLUMNS = [
    'ticker',      # STRING: Stock symbol (e.g., 'AAPL', '600000')
    'date',        # DATE: Trading date (partition key)
    'open',        # FLOAT64: Opening price
    'high',        # FLOAT64: Highest price
    'low',         # FLOAT64: Lowest price
    'close',       # FLOAT64: Closing price
    'volume',      # INT64: Trading volume
    'adj_close'    # FLOAT64: Adjusted close (optional)
]
```

### Market-Specific Notes

**US Equities (yfinance):**
- Ticker format: 'AAPL', 'GOOGL', 'MSFT'
- Volume in shares
- Adjusted close available by default
- Currency: USD

**China A-shares (akshare):**
- Ticker format: '000001', '600000' (6-digit code)
- Column name mapping required:
  - '开盘' → 'open'
  - '最高' → 'high'
  - '最低' → 'low'
  - '收盘' → 'close'
  - '成交量' → 'volume'
- Volume in lots (100 shares)
- Currency: CNY

**Hong Kong (yfinance):**
- Ticker format: '0700.HK', '9988.HK'
- Currency: HKD

**Singapore (yfinance):**
- Ticker format: 'D05.SI', 'O39.SI'
- Currency: SGD

### Hive Partitioning Structure

```
data/lake/
├── us_equity/
│   ├── date=2024-12-01/
│   │   └── 2024-12-01.parquet
│   ├── date=2024-12-02/
│   │   └── 2024-12-02.parquet
│   └── ...
├── cn_ashare/
│   ├── date=2024-12-01/
│   │   └── 2024-12-01.parquet
│   └── ...
└── hk_sg_equity/
    ├── date=2024-12-01/
    │   └── 2024-12-01.parquet
    └── ...
```

**Key Requirements:**
- Partition format: `date=YYYY-MM-DD/` (exact format)
- File naming: `{date}.parquet` inside partition directory
- Date column in Parquet: `date` type (not string)
- All Parquet files in same market must have identical schema

---

## 🧪 Testing Guidelines

### Running Tests

```bash
# Run all tests
make test
# or
uv run pytest -v

# Run specific test file
uv run pytest tests/test_ingest.py -v

# Run with coverage
uv run pytest --cov=scripts --cov-report=html --cov-report=term

# Run specific test markers
make test-unit          # Unit tests only
make test-integration   # Integration tests only
make test-slow          # Slow tests only

# Exclude slow tests
uv run pytest -m "not slow"
```

### Writing Tests

**Test Structure:**
```python
# tests/test_ingest.py
import pytest
from datetime import date, timedelta
from equity_lake.ingestion import USEquityFetcher, validate_schema

def test_us_fetcher_returns_dataframe():
    """Test that USEquityFetcher returns valid DataFrame."""
    fetcher = USEquityFetcher()
    yesterday = date.today() - timedelta(days=1)
    df = fetcher.fetch(yesterday)

    assert not df.empty
    assert all(col in df.columns for col in ['ticker', 'date', 'close', 'volume'])
    assert df['date'].iloc[0] == yesterday

def test_schema_validation():
    """Test schema validation with valid data."""
    import pandas as pd

    valid_df = pd.DataFrame({
        'ticker': ['AAPL', 'GOOGL'],
        'date': [date(2024, 12, 1), date(2024, 12, 1)],
        'open': [150.0, 140.0],
        'high': [155.0, 145.0],
        'low': [149.0, 139.0],
        'close': [154.0, 144.0],
        'volume': [1000000, 900000]
    })

    assert validate_schema(valid_df, 'test_market') == True

def test_schema_validation_missing_columns():
    """Test schema validation with missing columns."""
    import pandas as pd

    invalid_df = pd.DataFrame({
        'ticker': ['AAPL'],
        'date': [date(2024, 12, 1)],
        # Missing required columns
    })

    assert validate_schema(invalid_df, 'test_market') == False
```

### Test Markers

Use pytest markers to categorize tests:

```python
@pytest.mark.unit
def test_fetcher_initialization():
    pass

@pytest.mark.integration
def test_full_ingestion_pipeline():
    pass

@pytest.mark.slow
def test_large_dataset_processing():
    pass
```

---

## 📝 Development Workflow

### Making Changes

1. **Create feature branch:**
   ```bash
   git checkout -b feature/new-market-support
   ```

2. **Make changes and test:**
   ```bash
   # Format code
   make format

   # Run linting
   make lint

   # Run type checking
   make check

   # Run tests
   make test
   ```

3. **Commit changes:**
   ```bash
   git add .
   git commit -m "feat: add support for Japanese market"
   ```

4. **Run full CI checks:**
   ```bash
   make ci
   ```

### Code Quality Standards

**Before committing, ensure:**
- [ ] All tests pass: `make test`
- [ ] Linting passes: `make lint`
- [ ] Type checking passes: `make check`
- [ ] Code formatted: `make format`
- [ ] Documentation updated
- [ ] Log files checked for errors

---

## 🚀 Performance Best Practices

### S3 Sync Optimization

```bash
# Use s5cmd with maximum workers
uv run python scripts/sync_from_s3.py --tool s5cmd --workers 32

# For very large datasets, split sync by date range
uv run python scripts/sync_from_s3.py --bucket "s3://bucket/us_equity/date=2024*/"
```

### DuckDB Query Optimization

```python
# Bad: Select all columns
con.execute("SELECT * FROM equity_all WHERE date >= '2024-01-01'")

# Good: Select only needed columns
con.execute("SELECT ticker, close, volume FROM equity_all WHERE date >= '2024-01-01'")

# Bad: Scan all partitions
con.execute("SELECT * FROM equity_all")

# Good: Use date filter for partition pruning
con.execute("SELECT * FROM equity_all WHERE date >= '2024-01-01'")
```

### API Rate Limiting

```python
# Implemented in fetchers
# CNAshareFetcher: time.sleep(0.1) between stocks
# USEquityFetcher: Batch downloads with group_by='ticker'

# For custom fetchers, use:
import time
time.sleep(0.5)  # 500ms delay
```

---

## 🔒 Security & Best Practices

### Credential Management

**Never commit:**
- AWS credentials
- API keys
- Database passwords

**Use environment variables:**
```bash
# .env file (git-ignored)
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
S3_BUCKET=s3://your-bucket/

# Load in Python
from dotenv import load_dotenv
load_dotenv()
```

### Data Privacy

- EOD market data is public information
- No PII in this project
- Still use `.gitignore` for data/ directory (large files)

---

## 🎓 Learning Resources

### Official Documentation
- [DuckDB Python API](https://duckdb.org/docs/api/python/overview)
- [uv Documentation](https://github.com/astral-sh/uv)
- [yfinance GitHub](https://github.com/ranaroussi/yfinance)
- [akshare Documentation](https://akshare.readthedocs.io/zh-cn/latest/)

### Relevant Concepts
- [Hive Partitioning](https://docs.aws.amazon.com/emr/latest/DevelopmentGuide/emr-hive.html)
- [Parquet Format](https://parquet.apache.org/docs/)
- [PyArrow](https://arrow.apache.org/docs/python/)

---

## 📞 Support and Contribution

### Getting Help

1. **Check logs first**: `ls -lt logs/*.log | head -5`
2. **Review this document**: Look for relevant troubleshooting section
3. **Run diagnostics**: `make validate`
4. **Check test outputs**: `uv run pytest -v`

### Contributing

When contributing new features:

1. Follow the code review guidelines in this document
2. Add tests for new functionality
3. Update documentation (CLAUDE.md and README.md)
4. Ensure all CI checks pass: `make ci`

---

**Last Updated**: 2025-01-23
**Project Version**: 0.1.0
**Maintained by**: AI-assisted development with Claude Code

**Document Structure:**
- Project Overview & Objectives
- Architecture Context
- AI Assistant Workflow Guide
- Key Scripts Reference
- Troubleshooting Guide
- Data Schema Reference
- Testing Guidelines
- Development Workflow
- Performance Best Practices
- Security & Best Practices
- Learning Resources
- Support and Contribution
