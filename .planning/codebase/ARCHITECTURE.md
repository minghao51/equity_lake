# Architecture

**Last Updated**: 2026-03-05
**Project**: Equity EOD Data Pipeline

## Overall Architecture Pattern

### Hybrid ETL Pipeline with Local-First Design

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Cloud (S3)    │    │   Local APIs    │    │   Local APIs    │
│ US Historical   │    │  US/HK/SG Data  │    │  China A-shares │
└────────┬────────┘    └────────┬────────┘    └────────┬────────┘
         │                      │                      │
         │ One-time sync        │ Daily fetch          │ Daily fetch
         ▼                      ▼                      ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Ingestion Layer                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ S3 Syncer   │  │ yfinance    │  │ akshare     │              │
│  │ (bootstrap) │  │ Fetcher     │  │ Fetcher     │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
│         │                │                    │                  │
│         └────────────────┴────────────────────┘                  │
│                          │                                       │
│                   Orchestrator                                   │
│                   (retry, validation)                            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Storage Layer                               │
│                  Hive-Partitioned Parquet                        │
│  data/lake/{market}/date=YYYY-MM-DD/*.parquet                   │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Query Layer                                │
│                    DuckDB SQL Engine                             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ Raw Queries │  │ Views       │  │ Analytics   │              │
│  └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

### Key Design Principles

1. **Local-First**: After initial S3 bootstrap, all operations run locally
2. **Idempotent**: Safe to re-run any operation without side effects
3. **Graceful Degradation**: Continue processing if one market/source fails
4. **Partitioned Storage**: Hive-style date partitioning for efficient queries
5. **Zero-Copy Queries**: DuckDB reads Parquet files directly without loading

---

## System Layers

### 1. Ingestion Layer

**Purpose**: Fetch market data from external APIs and write to local storage

**Location**: `src/equity_lake/ingestion/`

**Key Components**:

#### Base Fetcher (`sources/base.py`)
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
- **USEquityFetcher** (`sources/us_equity.py`):
  - Fetches US market data via yfinance
  - Batch downloads for efficiency
  - Rate limiting: 0.5s delay between requests

- **CNAshareFetcher** (`sources/cn_ashare.py`):
  - Fetches China A-shares via akshare
  - Column mapping: Chinese → English
  - Rate limiting: 0.1s delay between stocks

- **HKSGEquityFetcher** (`sources/hk_sg_equity.py`):
  - Fetches HK/SG markets via yfinance
  - Separate ticker formats (.HK, .SI)

- **CNHybridFetcher** (`sources/cn_hybrid.py`):
  - Combines akshare + efinance
  - Fallback logic for reliability

#### Orchestrator (`orchestrator.py`)
- **Purpose**: Coordinate multi-market ingestion
- **Key Functions**:
  - `run_daily_ingestion()`: Main entry point
  - `fetch_market_data()`: Router for market-specific fetchers
  - `validate_schema()`: Ensure OHLCV compliance
  - `write_to_partitioned_parquet()`: Write to data lake

**Data Flow**:
```
CLI Request → Orchestrator → Market Fetchers → Validation → Partitioned Parquet
```

---

### 2. Storage Layer

**Purpose**: Persist and retrieve market data efficiently

**Location**: `src/equity_lake/storage/`

#### S3 Sync Module (`s3_sync.py`)
```python
class S3Syncer:
    def sync_with_s5cmd(self, bucket, destination):
        """High-performance parallel sync"""
        pass

    def sync_with_aws_cli(self, bucket, destination):
        """Fallback sync method"""
        pass

    def verify_download(self):
        """Validate Parquet structure"""
        pass
```

**Purpose**: One-time bootstrap of US historical data from S3

**Pattern**:
1. Auto-detect available tool (s5cmd > AWS CLI > boto3)
2. Parallel download with configurable workers
3. Integrity verification post-download

#### Parquet Module (`parquet.py`)
```python
def write_to_partitioned_parquet(df, market, trading_date):
    """Write DataFrame to Hive-partitioned directory"""
    partition_dir = f"data/lake/{market}/date={trading_date}/"
    os.makedirs(partition_dir, exist_ok=True)
    df.to_parquet(f"{partition_dir}/{trading_date}.parquet")

def read_partitioned_parquet(market, date_range=None):
    """Read Parquet partitions with optional filtering"""
    pass
```

**Storage Pattern**:
- **Format**: Parquet (columnar, compressed)
- **Partitioning**: Hive-style by date
- **Compression**: Snappy (balance of speed/ratio)
- **Schema**: Standardized OHLCV across all markets

#### DuckDB Module (`duckdb.py`)
```python
class EquityDataDB:
    def __init__(self):
        self.con = duckdb.connect(':memory:')
        self._setup_views()

    def _setup_views(self):
        """Create unified equity_all view"""
        pass

    def query(self, sql, params=None):
        """Execute SQL and return DataFrame"""
        pass
```

**Purpose**: SQL query interface for analytics

---

### 3. Query Layer

**Purpose**: Provide SQL access to data lake for analytics

**Location**: `src/equity_lake/storage/duckdb.py`

**Unified View**:
```sql
CREATE OR REPLACE VIEW equity_all AS
SELECT
    ticker, date, open, high, low, close, volume, adj_close,
    'us' as market
FROM 'data/lake/us_equity/date=*/*.parquet'

UNION ALL

SELECT
    ticker, date, open, high, low, close, volume,
    NULL as adj_close,  -- China markets don't have adj_close
    'cn' as market
FROM 'data/lake/cn_ashare/date=*/*.parquet'

UNION ALL

SELECT
    ticker, date, open, high, low, close, volume, adj_close,
    'hk_sg' as market
FROM 'data/lake/hk_sg_equity/date=*/*.parquet'
```

**Query Patterns**:
- **Time-series**: `SELECT * FROM equity_all WHERE ticker = 'AAPL'`
- **Cross-market**: `SELECT * FROM equity_all WHERE date >= '2024-01-01'`
- **Aggregations**: `SELECT market, AVG(volume) FROM equity_all GROUP BY market`
- **Joins**: Self-joins for moving averages, technical indicators

---

### 4. Feature Engineering Layer (Optional)

**Purpose**: Transform raw OHLCV into analytical features

**Location**: `src/equity_lake/features/`

**Key Features**:
- Moving averages (SMA, EMA)
- Price momentum (RSI, MACD)
- Volatility metrics (Bollinger Bands, ATR)
- Volume indicators (OBV, VWAP)
- Price returns (daily, weekly, monthly)

**Pattern**:
```python
def calculate_sma(df, window=20):
    """Calculate simple moving average"""
    return df['close'].rolling(window=window).mean()

def calculate_rsi(df, window=14):
    """Calculate Relative Strength Index"""
    pass
```

---

### 5. Signal Generation Layer (Optional)

**Purpose**: Generate trading signals from features

**Location**: `src/equity_lake/signals/`

**Signal Types**:
- Trend following (moving average crossovers)
- Mean reversion (RSI overbought/oversold)
- Breakout (price channel breaks)
- Momentum (relative strength)

**Pattern**:
```python
def generate_ma_cross_signals(df):
    """Generate moving average crossover signals"""
    df['signal'] = np.where(
        df['sma_short'] > df['sma_long'],
        'buy',
        'sell'
    )
    return df
```

---

## Design Patterns

### 1. Strategy Pattern

**Context**: Market-specific data fetching

**Implementation**:
```python
# Base strategy (abstract)
class MarketDataFetcher(ABC):
    @abstractmethod
    def fetch(self, trading_date: date) -> pd.DataFrame:
        pass

# Concrete strategies
class USEquityFetcher(MarketDataFetcher):
    def fetch(self, trading_date: date) -> pd.DataFrame:
        # US-specific implementation
        pass

class CNAshareFetcher(MarketDataFetcher):
    def fetch(self, trading_date: date) -> pd.DataFrame:
        # China-specific implementation
        pass

# Strategy selector
fetcher_map = {
    'us': USEquityFetcher(),
    'cn': CNAshareFetcher(),
    'hk_sg': HKSGEquityFetcher()
}

fetcher = fetcher_map[market]
df = fetcher.fetch(date)
```

**Benefits**:
- Easy to add new markets (new fetcher class)
- Consistent interface across markets
- Isolated market-specific logic

---

### 2. Template Method Pattern

**Context**: Base fetcher with common workflow

**Implementation**:
```python
class MarketDataFetcher(ABC):
    def fetch(self, trading_date: date) -> pd.DataFrame:
        # Template method
        df = self._fetch_from_source(trading_date)  # Abstract
        df = self._standardize_columns(df)          # Common
        df = self._validate_data(df)                # Common
        return df

    @abstractmethod
    def _fetch_from_source(self, trading_date: date) -> pd.DataFrame:
        pass

    def _standardize_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        # Common column mapping logic
        pass

    def _validate_data(self, df: pd.DataFrame) -> pd.DataFrame:
        # Common validation logic
        pass
```

**Benefits**:
- Reusable workflow steps
- Consistent data processing
- Flexible implementation in subclasses

---

### 3. Factory Pattern

**Context**: Creating market-specific fetchers

**Implementation**:
```python
class FetcherFactory:
    @staticmethod
    def create_fetcher(market: str) -> MarketDataFetcher:
        fetchers = {
            'us': USEquityFetcher,
            'cn': CNAshareFetcher,
            'hk_sg': HKSGEquityFetcher
        }
        fetcher_class = fetchers.get(market)
        if not fetcher_class:
            raise ValueError(f"Unknown market: {market}")
        return fetcher_class()

# Usage
fetcher = FetcherFactory.create_fetcher('us')
df = fetcher.fetch(date)
```

**Benefits**:
- Centralized fetcher creation
- Easy to extend with new markets
- Error handling for invalid markets

---

### 4. Repository Pattern

**Context**: Data access abstraction

**Implementation**:
```python
class EquityRepository:
    def __init__(self, db: duckdb.DuckDBPyConnection):
        self.db = db

    def get_ticker_data(self, ticker: str, start_date, end_date):
        """Abstract SQL query behind method"""
        return self.db.execute("""
            SELECT * FROM equity_all
            WHERE ticker = ? AND date BETWEEN ? AND ?
        """, [ticker, start_date, end_date]).df()

    def get_latest_data(self, market: str):
        """Encapsulate complex query logic"""
        pass

# Usage
repo = EquityRepository(db)
aapl_data = repo.get_ticker_data('AAPL', start, end)
```

**Benefits**:
- Encapsulates SQL logic
- Type-safe interface
- Easier to test (mock repository)

---

## Data Flow

### Daily Ingestion Flow

```
┌──────────────────────────────────────────────────────────────┐
│ 1. CLI Command                                               │
│    uv run python -m equity_lake.cli.daily --date 2024-12-01 │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. Orchestrator Initialization                               │
│    - Parse date argument                                     │
│    - Initialize fetchers for each market                     │
│    - Setup logging                                           │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. Market Data Fetching (Parallel)                           │
│    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│    │ US Fetcher   │  │ CN Fetcher   │  │ HK/SG Fetcher│      │
│    │ (yfinance)   │  │ (akshare)    │  │ (yfinance)   │      │
│    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
│           │                 │                  │              │
│           └─────────────────┴──────────────────┘              │
│                              │                                │
│                              ▼                                │
│                    Collect all DataFrames                     │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. Validation & Transformation                               │
│    - Schema validation (STANDARD_COLUMNS)                    │
│    - Data type conversion                                    │
│    - Duplicate detection                                     │
│    - Quality checks (no null prices)                         │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. Partitioned Parquet Write                                 │
│    For each market:                                          │
│    - Create partition: data/lake/{market}/date=YYYY-MM-DD/   │
│    - Write: {date}.parquet                                   │
│    - Compress with Snappy                                    │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. Summary & Reporting                                       │
│    - Log row counts per market                               │
│    - Report any failures                                     │
│    - Return exit code (0=success, 1=failure)                 │
└──────────────────────────────────────────────────────────────┘
```

---

### Query Flow

```
┌──────────────────────────────────────────────────────────────┐
│ 1. CLI Command                                               │
│    uv run python -m equity_lake.cli.query --query top_volume │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 2. DuckDB Initialization                                     │
│    - Create in-memory connection                             │
│    - Setup unified view (equity_all)                         │
│    - Register Parquet datasets                               │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 3. SQL Execution                                             │
│    SELECT                                                    │
│      ticker, market, SUM(volume) as total_volume             │
│    FROM equity_all                                           │
│    WHERE date >= CURRENT_DATE - INTERVAL 7 DAYS              │
│    GROUP BY ticker, market                                   │
│    ORDER BY total_volume DESC                                │
│    LIMIT 10                                                  │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 4. DuckDB Query Processing                                   │
│    - Parse SQL query                                         │
│    - Optimize execution plan                                 │
│    - Apply partition pruning (date filter)                   │
│    - Read only needed columns (column projection)            │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 5. Parquet File Access                                       │
│    - Zero-copy read from Parquet files                       │
│    - Deserialize only required columns                       │
│    - Filter partitions by date                               │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 6. Result Compilation                                        │
│    - Collect filtered rows                                   │
│    - Apply aggregations                                      │
│    - Sort results                                            │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ 7. Return DataFrame                                          │
│    - Convert to pandas DataFrame                             │
│    - Display or export to CSV/JSON                           │
└──────────────────────────────────────────────────────────────┘
```

---

## Entry Points

### CLI Entry Points

#### 1. Daily Ingestion
```python
# src/equity_lake/cli/daily.py
def main():
    """Main entry point for daily EOD ingestion"""
    parser = typerCli()
    parser.command()(run_daily_ingestion)
```

**Usage**:
```bash
equity-daily --date 2024-12-01 --markets us,cn
```

**Flow**:
1. Parse CLI arguments
2. Initialize orchestrator
3. Fetch data for each market
4. Validate and write to Parquet
5. Report results

---

#### 2. S3 Sync
```python
# src/equity_lake/cli/sync.py
def main():
    """Main entry point for S3 historical data sync"""
    parser = typerCli()
    parser.command()(s3_sync_main)
```

**Usage**:
```bash
equity-sync --bucket s3://my-bucket/us_equity/ --workers 32
```

**Flow**:
1. Parse CLI arguments
2. Initialize S3 syncer
3. Detect sync tool (s5cmd > AWS CLI > boto3)
4. Download historical data
5. Verify integrity

---

#### 3. Query Interface
```python
# src/equity_lake/cli/query.py
def main():
    """Main entry point for DuckDB queries"""
    parser = typerCli()
    parser.command()(query_main)
```

**Usage**:
```bash
equity-query --query top_volume --days 14 --output results.csv
```

**Flow**:
1. Parse CLI arguments
2. Initialize DuckDB connection
3. Setup unified view
4. Execute SQL query
5. Return/export results

---

## Abstractions

### Core Abstractions

#### 1. MarketDataFetcher (Abstract Base)
**Purpose**: Define interface for all market fetchers

**Interface**:
```python
class MarketDataFetcher(ABC):
    @abstractmethod
    def fetch(self, trading_date: date) -> pd.DataFrame:
        """Fetch EOD data for specific date"""
        pass

    def _retry_on_failure(self, func, max_retries=3):
        """Common retry logic"""
        pass
```

**Implementations**:
- `USEquityFetcher`: US market via yfinance
- `CNAshareFetcher`: China A-shares via akshare
- `HKSGEquityFetcher`: HK/SG markets via yfinance
- `CNHybridFetcher`: China with fallback (akshare → efinance)

---

#### 2. Data Source Abstraction
**Purpose**: Encapsulate external API interactions

**Pattern**:
```python
class YFinanceSource:
    def download(self, symbols, start, end):
        """Abstract yfinance API calls"""
        pass

class AkshareSource:
    def get_stock_data(self, symbol, start, end):
        """Abstract akshare API calls"""
        pass
```

**Benefits**:
- Easy to mock for testing
- Centralized error handling
- Consistent response format

---

#### 3. Storage Abstraction
**Purpose**: Unified interface for data persistence

**Interface**:
```python
class DataStorage(ABC):
    @abstractmethod
    def write(self, df: pd.DataFrame, partition_key: str):
        pass

    @abstractmethod
    def read(self, partition_key: str) -> pd.DataFrame:
        pass

class ParquetStorage(DataStorage):
    def write(self, df, partition_key):
        df.to_parquet(f"{partition_key}.parquet")

    def read(self, partition_key):
        return pd.read_parquet(f"{partition_key}.parquet")
```

---

## Component Relationships

### Dependency Graph

```
CLI Layer (daily.py, sync.py, query.py)
    │
    ├─► Orchestrator (orchestrator.py)
    │       │
    │       ├─► USEquityFetcher
    │       ├─► CNAshareFetcher
    │       └─► HKSGEquityFetcher
    │
    ├─► S3Syncer (s3_sync.py)
    │
    └─► EquityDataDB (duckdb.py)
            │
            └─► ParquetStorage (parquet.py)
```

### Module Coupling

**Tight Coupling** (Intentional):
- Orchestrator → Market Fetchers (composition)
- Fetchers → API sources (yfinance, akshare)
- DuckDB → Parquet files (zero-copy read)

**Loose Coupling**:
- CLI → Orchestrator (dependency injection via args)
- Fetchers → Storage (via standardized DataFrames)
- Query → Storage (via SQL abstraction)

---

## Scaling Considerations

### Current Capacity
- **US Historical**: ~5-10 GB (S3 bootstrap)
- **Daily Updates**: ~5-50 MB per market
- **Query Performance**: Sub-second for filtered queries

### Bottlenecks
1. **API Rate Limits**: yfinance/akshare throttling
2. **Network Latency**: S3 sync time
3. **Memory**: Large dataset processing

### Scaling Strategies
1. **Parallel Fetching**: Concurrent API calls per market
2. **Partition Pruning**: Efficient time-range queries
3. **Caching**: Redis for frequently accessed data
4. **Batch Processing**: Accumulate and write in batches

---

## Fault Tolerance

### Failure Modes

1. **API Failure**:
   - Retry with exponential backoff
   - Continue processing other markets
   - Log errors for review

2. **Storage Failure**:
   - Validate Parquet integrity post-write
   - Retry write operations
   - Alert on persistent failures

3. **Query Failure**:
   - Validate SQL syntax
   - Check for missing partitions
   - Provide helpful error messages

### Recovery
- **Idempotent Operations**: Re-run without side effects
- **Incremental Updates**: Only fetch new data
- **Data Validation**: Schema and quality checks

---

**Total Files in Architecture**: 105 Python modules
**Core Components**: 5 layers (Ingestion, Storage, Query, Features, Signals)
**Design Patterns**: Strategy, Template Method, Factory, Repository
**Entry Points**: 3 CLI commands (daily, sync, query)
