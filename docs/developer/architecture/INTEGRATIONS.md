# Integrations

**Last Updated**: 2026-03-05
**Project**: Equity EOD Data Pipeline

## External APIs

### yfinance API

**Purpose**: Fetch EOD market data for US, Hong Kong, and Singapore equities

**Usage Locations**:
- `src/equity_lake/ingestion/sources/yfinance_source.py` (287 lines)
- `src/equity_lake/ingestion/sources/us_equity.py` (USEquityFetcher)
- `src/equity_lake/ingestion/sources/hk_sg_equity.py` (HKSGEquityFetcher)

**Implementation Details**:
```python
import yfinance as yf

# Single ticker download
data = yf.download('AAPL', start='2024-12-01', end='2024-12-02')

# Batch download (multiple tickers)
tickers = ['AAPL', 'GOOGL', 'MSFT']
data = yf.download(tickers, start=start, end=end, group_by='ticker')
```

**Rate Limiting**:
- Built-in delay: 0.5-1 second between requests
- Exponential backoff on failures (3 retries)
- Batch downloads to reduce API calls

**Data Retrieved**:
- OHLCV (Open, High, Low, Close, Volume)
- Adjusted close prices
- Date range queries
- Ticker metadata

**Error Handling**:
- Network timeout handling
- Retry logic with exponential backoff
- Graceful degradation on API failures

**Dependencies**:
- `yfinance>=0.2.50`
- Internet connectivity required
- No API key needed (free public API)

---

### akshare API

**Purpose**: Fetch China A-shares EOD market data

**Usage Locations**:
- `src/equity_lake/ingestion/sources/akshare_source.py` (171 lines)
- `src/equity_lake/ingestion/sources/cn_ashare.py` (CNAshareFetcher)
- `src/equity_lake/ingestion/sources/cn_hybrid.py` (CNHybridFetcher)

**Implementation Details**:
```python
import akshare as ak

# Fetch stock list
stock_list = ak.stock_info_a_code_name()

# Fetch historical data
df = ak.stock_zh_a_hist(
    symbol='000001',
    period='daily',
    start_date='20241201',
    end_date='20241202',
    adjust='qfq'  # Forward-adjusted prices
)
```

**Column Mapping Required**:
- Chinese column names → English
- '开盘' → 'open'
- '最高' → 'high'
- '最低' → 'low'
- '收盘' → 'close'
- '成交量' → 'volume'

**Rate Limiting**:
- Delay: 0.1 second between stock requests
- Retry logic: 3 attempts with exponential backoff
- Batch processing for multiple tickers

**Data Retrieved**:
- OHLCV for A-shares (Shanghai + Shenzhen)
- Stock list and metadata
- Adjusted prices (前复权)
- Trading calendar

**Error Handling**:
- Connection error handling
- VPN requirements for China access
- Fallback to alternative sources

**Dependencies**:
- `akshare>=1.15.0`
- May require VPN for mainland China access
- No API key needed (free public API)

---

### efinance API

**Purpose**: Alternative Chinese market data source

**Usage Locations**:
- `src/equity_lake/ingestion/sources/efinance_source.py`

**Implementation Details**:
```python
import efinance as ef

# Fetch stock data
df = ef.stock.get_quote_history()
```

**Use Case**:
- Backup/fallback for akshare
- Faster for certain queries
- Different data coverage

**Dependencies**:
- `efinance` package
- Similar network requirements as akshare

---

## Databases

### DuckDB

**Purpose**: SQL query engine for analytics and data exploration

**Usage Locations**:
- `src/equity_lake/storage/duckdb.py` (main query interface)
- `src/equity_lake/cli/query.py` (CLI entry point)

**Implementation Details**:
```python
import duckdb

# Create connection
con = duckdb.connect(':memory:')

# Query Parquet files directly
df = con.execute("""
    SELECT ticker, close, volume
    FROM 'data/lake/us_equity/date=*.parquet'
    WHERE date >= '2024-01-01'
""").df()

# Create unified view
con.execute("""
    CREATE OR REPLACE VIEW equity_all AS
    SELECT *, 'us' as market FROM 'data/lake/us_equity/date=*/*.parquet'
    UNION ALL
    SELECT *, 'cn' as market FROM 'data/lake/cn_ashare/date=*/*.parquet'
""")
```

**Features Used**:
- Zero-copy Parquet reading
- Hive partitioning support
- SQL query optimization
- In-memory processing

**Performance Optimizations**:
- Partition pruning (date filtering)
- Column projection (SELECT specific columns)
- Materialized views for frequent queries
- Parallel query execution

**Integration Pattern**:
- Read-only access to Parquet files
- No database server needed
- Embedded in Python process

---

### Parquet Data Lake

**Purpose**: Primary storage for EOD market data

**Storage Structure**:
```
data/lake/
├── us_equity/
│   ├── date=2024-12-01/
│   │   └── 2024-12-01.parquet
│   ├── date=2024-12-02/
│   │   └── 2024-12-02.parquet
│   └── ...
├── cn_ashare/
│   └── ... (same structure)
└── hk_sg_equity/
    └── ... (same structure)
```

**Implementation**:
- **Format**: Apache Parquet (columnar storage)
- **Compression**: Snappy (default)
- **Partitioning**: Hive-style by date
- **Library**: pyarrow for read/write operations

**Code Locations**:
- `src/equity_lake/storage/parquet.py` (read/write utilities)
- `scripts/ingest_daily.py` (write operations)
- `scripts/query_example.py` (read operations)

**Schema**:
```python
STANDARD_COLUMNS = [
    'ticker',      # STRING
    'date',        # DATE (partition key)
    'open',        # FLOAT64
    'high',        # FLOAT64
    'low',         # FLOAT64
    'close',       # FLOAT64
    'volume',      # INT64
    'adj_close'    # FLOAT64 (optional)
]
```

**Write Operations**:
```python
import pyarrow as pa
import pyarrow.parquet as pq

# Write to partitioned directory
table = pa.Table.from_pandas(df)
pq.write_table(
    table,
    f'data/lake/us_equity/date={date}/{date}.parquet',
    compression='snappy'
)
```

**Read Operations**:
```python
import pandas as pd

# Read single file
df = pd.read_parquet('data/lake/us_equity/date=2024-12-01/2024-12-01.parquet')

# Read multiple partitions with DuckDB
df = con.execute("""
    SELECT * FROM 'data/lake/us_equity/date=*/*.parquet'
    WHERE date >= '2024-12-01'
""").df()
```

---

## Cloud Storage

### AWS S3

**Purpose**: Bootstrap historical US equity data (one-time sync)

**Usage Locations**:
- `src/equity_lake/storage/s3_sync.py` (S3 sync orchestration)
- `scripts/sync_from_s3.py` (legacy sync script)
- `src/equity_lake/cli/sync.py` (CLI entry point)

**Authentication**:
```bash
# AWS credentials (from environment or ~/.aws/credentials)
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_DEFAULT_REGION=us-east-1
```

**Configuration**:
```python
# Environment variables
S3_BUCKET = os.getenv('S3_BUCKET', 's3://default-bucket/us_equity/')
AWS_PROFILE = os.getenv('AWS_PROFILE', 'default')
```

**Implementation**:
- **Tool 1**: s5cmd (preferred, high-performance)
  ```bash
  s5cmd cp --workers 32 s3://bucket/us_equity/* data/lake/us_equity/
  ```

- **Tool 2**: AWS CLI (fallback)
  ```bash
  aws s3 sync s3://bucket/us_equity/ data/lake/us_equity/
  ```

- **Tool 3**: boto3 (Python SDK)
  ```python
  import boto3
  s3 = boto3.client('s3')
  paginator = s3.get_paginator('list_objects_v2')
  ```

**Data Sync Pattern**:
```
S3 Bucket (Historical)
    ↓ One-time sync
Local Parquet Lake
    ↓ Daily appends
Query via DuckDB
```

**Error Handling**:
- Access denied handling
- Network timeout retries
- Integrity verification (Parquet validation)
- Partial sync resume capability

**Dependencies**:
- `boto3` (AWS SDK)
- `s5cmd` (external binary, optional)
- AWS CLI (external binary, fallback)

---

## Authentication & Security

### AWS Credentials

**Configuration Sources** (in order of precedence):
1. Environment variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`)
2. AWS credentials file (`~/.aws/credentials`)
3. IAM role (for EC2 instances)
4. S3 bucket policy (public buckets)

**Best Practices**:
- Never commit credentials to git
- Use `.env` file for local development (git-ignored)
- Rotate credentials regularly
- Use IAM roles for production
- Principle of least privilege

---

### API Keys

**Current Status**: No API keys required for core functionality
- yfinance: Free public API, no key needed
- akshare: Free public API, no key needed
- efinance: Free public API, no key needed

**Future Integrations** (if needed):
- Alpha Vantage (requires API key)
- Finnhub (requires API key)
- FRED Economic Data (requires API key)
- Polygon.io (requires API key)

**Configuration Pattern** (for future APIs):
```python
# .env file (git-ignored)
ALPHA_VANTAGE_API_KEY=your_key_here
FINNHUB_API_KEY=your_key_here

# Load in Python
from dotenv import load_dotenv
load_dotenv()

api_key = os.getenv('ALPHA_VANTAGE_API_KEY')
```

---

## Scheduling & Automation

### Cron Jobs

**Purpose**: Schedule daily EOD data ingestion

**Implementation Options**:

1. **System Cron** (Linux/macOS):
   ```crontab
   # Run daily at 6:00 PM (after market close)
   0 18 * * 1-5 cd /path/to/equity_lake && dotenvx run -- uv run equity ingest >> logs/cron.log 2>&1
   ```

2. **Docker Cron**:
   ```yaml
   # docker-compose.yml
   services:
     scheduler:
       image: equity-lake:latest
       command: cron -f
       volumes:
         - ./data:/app/data
   ```

3. **Python Schedule** (alternative):
   ```python
   import schedule
   schedule.every().day.at("18:00").do(run_daily_ingestion)
   ```

**Current Implementation**: Manual execution via CLI
```bash
# Run daily ingestion manually
make daily
# or
uv run equity ingest
```

---

## Data Sources Summary

### Primary Data Sources

| Market | Source | API | Cost | Coverage |
|--------|--------|-----|------|----------|
| US Equities | yfinance | Yahoo Finance | Free | 7000+ stocks |
| China A-shares | akshare | Various Chinese exchanges | Free | 5000+ stocks |
| Hong Kong | yfinance | Yahoo Finance | Free | 2000+ stocks |
| Singapore | yfinance | Yahoo Finance | Free | 600+ stocks |

### Bootstrap Source

| Data Type | Source | Method | Frequency |
|-----------|--------|--------|-----------|
| US Historical | AWS S3 | One-time sync | Once |
| US Daily | yfinance | API fetch | Daily |
| China Daily | akshare | API fetch | Daily |
| HK/SG Daily | yfinance | API fetch | Daily |

---

## Error Handling & Resilience

### API Failure Handling

**Retry Strategy**:
- Exponential backoff: 1s, 2s, 4s delays
- Max retries: 3 attempts
- Timeout: 30 seconds per request

**Graceful Degradation**:
```python
# Continue processing other markets if one fails
try:
    us_data = fetch_us_market(date)
except Exception as e:
    logger.error(f"US market failed: {e}")
    us_data = None

# Process successful fetches only
if cn_data is not None:
    write_to_partition(cn_data, 'cn_ashare')
```

**Logging**:
- All API errors logged to `logs/ingest_daily.log`
- Structured logging with correlation IDs
- Error metrics and statistics

---

## Network Requirements

### Connectivity

**Required Endpoints**:
- `query1.finance.yahoo.com` (yfinance)
- `akshare.akfamily.xyz` (akshare)
- `*.amazonaws.com` (S3, if using)

**Firewall Considerations**:
- Outbound HTTPS (443) required
- No inbound ports needed
- May need VPN for China sources

**Bandwidth**:
- Initial S3 sync: ~5-10 GB (US historical)
- Daily updates: ~5-50 MB per market
- Query operations: Local (no network needed)

---

## Integration Testing

### Mock Strategy

**External APIs**:
- Mock yfinance responses: `tests/unit/sources/test_yfinance_source.py`
- Mock akshare responses: `tests/unit/sources/test_akshare_source.py`
- Mock S3 operations: `tests/unit/storage/test_s3_sync.py`

**Example**:
```python
from unittest.mock import patch

@patch('yfinance.download')
def test_us_fetcher(mock_download):
    mock_download.return_value = sample_dataframe
    fetcher = USEquityFetcher()
    df = fetcher.fetch(date(2024, 12, 1))
    assert not df.empty
```

---

## Monitoring & Observability

### Logging

**Log Files**:
- `logs/ingest_daily.log`: Daily ingestion logs
- `logs/sync_from_s3.log`: S3 sync logs
- `logs/query.log`: Query operation logs

**Log Format**:
- Structured JSON logs (via structlog)
- Include: timestamp, level, message, context
- Correlation IDs for request tracking

**Metrics to Track**:
- API success/failure rates
- Data latency (time to fetch)
- Row counts per market
- Query performance

---

## Future Integrations

### Potential Additions

1. **Workflow Orchestration**:
   - Apache Airflow
   - Prefect
   - Dagster

2. **Caching Layer**:
   - Redis (API response caching)
   - Memcached

3. **Message Queue**:
   - RabbitMQ (async task processing)
   - Redis Queue (lightweight alternative)

4. **Additional Data Sources**:
   - Crypto (CoinGecko API)
   - Commodities (Alpha Vantage)
   - Economic indicators (FRED API)

5. **Notification**:
   - Slack webhooks (alerts)
   - Email notifications (failures)

---

**Total Integrations**: 4 external APIs, 2 databases, 1 cloud storage service
**Authentication**: AWS credentials (optional, for S3 sync)
**Scheduling**: Manual/cron (future: Airflow/Prefect)
