# 📚 Data Pipeline Concepts Explained

**Purpose**: Educational guide for understanding core data engineering concepts used in this equity EOD pipeline.

---

## 🏗️ ETL vs ELT Patterns

### Traditional ETL (Extract-Transform-Load)
```
Source → Transform → Target
```

**Characteristics**:
- Transform data before loading into target
- Complex transformations during extraction
- Target system receives clean, processed data
- High transformation requirements upfront

**Use Cases**:
- Data warehouses with strict schemas
- Complex business logic applied early
- When target system has limited processing power

### Modern ELT (Extract-Load-Transform)
```
Source → Raw → Transform (in target)
```

**Our Pipeline Uses ELT**:
```
S3/yfinance/akshare → Raw Parquet → DuckDB Transformations
```

**Why ELT for This Project**:
- ✅ **Raw data preservation**: Keep original market data
- ✅ **Flexibility**: Transform after loading into DuckDB
- ✅ **Performance**: Leverage DuckDB's query engine
- ✅ **Simplicity**: Load fast, transform on-demand

**Benefits**:
1. **Fast ingestion**: Raw Parquet loading is minimal
2. **Schema flexibility**: DuckDB handles schema evolution
3. **Query flexibility**: Transform data in SQL as needed
4. **Debugging**: Raw data always available for inspection

---

## 📂 Data Lakehouse Architecture

### Traditional Data Warehouse
```
Data Sources → ETL → Data Warehouse → Analytics
```
- Expensive storage
- Rigid schemas
- Vendor lock-in
- Limited scalability

### Data Lakehouse (Our Approach)
```
Data Sources → Raw Files → Query Engine → Analytics
```
- Cost-effective storage (local files)
- Flexible schemas (Parquet)
- Open formats (no vendor lock-in)
- Scalable ( DuckDB scales with data)

### Our Lakehouse Components

#### Storage Layer: Parquet Files
```python
# Hive-partitioned structure
data/lake/
├── us_equity/date=2024-12-01/2024-12-01.parquet
├── cn_ashare/date=2024-12-01/2024-12-01.parquet
└── hk_sg_equity/date=2024-12-01/2024-12-01.parquet
```

**Why Parquet?**:
- ✅ **Columnar storage**: Efficient for analytics (read only needed columns)
- ✅ **Compression**: 5-10x smaller than CSV
- ✅ **Schema evolution**: Add columns without breaking existing data
- ✅ **Performance**: Predicate pushdown, statistics, indexes
- ✅ **Standard**: Supported by all modern data tools

#### Query Layer: DuckDB
```sql
-- Zero-copy queries on Parquet
SELECT ticker, close, volume
FROM read_parquet('data/lake/*/date=2024-12-01/*.parquet', hive_partitioning=1)
WHERE market = 'us' AND volume > 1000000;
```

**Why DuckDB?**:
- ✅ **Zero-copy**: Reads Parquet directly without loading
- ✅ **SQLite simplicity**: Single file, embedded, no server
- ✅ **PostgreSQL compatibility**: Standard SQL syntax
- ✅ **Performance**: Vectorized execution, parallel processing
- ✅ **Python integration**: Native Python API

---

## 🗂️ Hive Partitioning Explained

### Concept
Hive partitioning organizes data by creating directory structures based on column values.

### Traditional Structure (Inefficient)
```
data/
├── all_data_2024.parquet       # 10GB file
└── all_data_2023.parquet       # 10GB file
```
**Problems**:
- Must scan entire files for date filtering
- Slow queries on specific dates
- Inefficient storage for growing data

### Hive-Partitioned Structure (Our Approach)
```
data/lake/us_equity/
├── date=2024-01-01/2024-01-01.parquet    # 50MB
├── date=2024-01-02/2024-01-02.parquet    # 48MB
├── date=2024-01-03/2024-01-03.parquet    # 52MB
└── ...
```

**Benefits**:
1. **Partition Pruning**: DuckDB only reads relevant directories
   ```sql
   -- Only reads 2024-12-01 data, not the entire dataset
   SELECT * FROM equity_all WHERE date = '2024-12-01';
   ```

2. **Efficient Management**: Easy to add/remove data by date
   ```bash
   # Remove old data
   rm -rf data/lake/us_equity/date=2020-*/
   ```

3. **Parallel Processing**: Different dates can be processed in parallel

4. **Scalability**: Adding new data doesn't affect existing queries

### Implementation
```python
# Writing with Hive partitioning
df.to_parquet(
    'data/lake/us_equity/date=2024-12-01/2024-12-01.parquet',
    partition_cols=['date'],  # Creates date=YYYY-MM-DD/ directories
    index=False
)

# Querying with Hive partitioning
duckdb.sql("""
    SELECT * FROM read_parquet(
        'data/lake/us_equity/date=*/*.parquet',
        hive_partitioning=1  # Tell DuckDB to parse directory names
    )
""")
```

---

## 🔄 Data Ingestion Patterns

### Batch Processing (Our Pattern)
Process data in discrete batches (daily).

**Characteristics**:
- Scheduled runs (cron, daily at market close)
- Large data volumes per batch
- Focus on throughput over latency
- Simpler error handling

**Our Implementation**:
```python
def ingest_daily():
    yesterday = datetime.now() - timedelta(days=1)

    # Batch download all markets
    us_data = fetch_us_data(yesterday)
    cn_data = fetch_cn_data(yesterday)
    hk_data = fetch_hk_data(yesterday)

    # Batch write to Parquet
    write_to_parquet(us_data, 'us_equity', yesterday)
    write_to_parquet(cn_data, 'cn_ashare', yesterday)
    write_to_parquet(hk_data, 'hk_sg_equity', yesterday)
```

### Streaming Processing (Alternative)
Process data as it arrives.

**Characteristics**:
- Real-time processing
- Small data volumes continuously
- Focus on latency over throughput
- Complex state management

**When to Use**:
- Real-time trading systems
- Alerting on price movements
- Live dashboards

---

## 📊 Schema Design Principles

### Star Schema vs Snowflake Schema

#### Star Schema (Our Approach)
```
Fact Table: equity_prices (ticker, date, open, high, low, close, volume)
Dimension Tables: markets, tickers (simplified)
```

**Benefits**:
- Simple joins
- Fast queries
- Easy to understand
- Good for this use case

#### Snowflake Schema (More Complex)
```
Fact Table: equity_prices
Dimension Tables: markets, exchanges, tickers, industries, sectors...
```

**Benefits**:
- Normalized data
- Less storage duplication
- Better for complex organizations

**Why Star Schema for This Project**:
- Simplicity: We primarily analyze price data
- Performance: Fewer joins = faster queries
- Maintenance: Easier to manage and extend
- Use Case: Financial analysis doesn't need complex dimensions

### Slowly Changing Dimensions (SCDs)

Not applicable to our market data since:
- Historical prices don't change (immutable)
- Company information changes infrequently
- We focus on time-series price data

---

## 🎯 Data Quality Considerations

### Data Validation Rules

#### Completeness Checks
```python
def validate_completeness(df, date):
    """Check if all expected tickers have data for given date"""
    expected_tickers = get_active_tickers(date)
    actual_tickers = set(df['ticker'].unique())

    missing = expected_tickers - actual_tickers
    if missing:
        log_warning(f"Missing data for {len(missing)} tickers on {date}")

    return len(missing) == 0
```

#### Accuracy Checks
```python
def validate_price_logic(df):
    """Ensure OHLC relationships make sense"""
    # High should be >= Open, Close, Low
    # Low should be <= Open, Close, High
    # Volume should be non-negative

    invalid_high = df[df['high'] < df[['open', 'close', 'low']].max(axis=1)]
    invalid_low = df[df['low'] > df[['open', 'close', 'high']].min(axis=1)]
    negative_volume = df[df['volume'] < 0]

    return len(invalid_high) == 0 and len(invalid_low) == 0 and len(negative_volume) == 0
```

#### Consistency Checks
```python
def validate_cross_day_consistency(df_previous, df_current):
    """Check for reasonable day-to-day price changes"""
    # Prices shouldn't change more than 50% in one day (splits excluded)
    max_change = 0.5

    merged = df_previous.merge(df_current, on='ticker', suffixes=('_prev', '_curr'))
    price_change = abs(merged['close_curr'] - merged['close_prev']) / merged['close_prev']

    extreme_changes = merged[price_change > max_change]
    if not extreme_changes.empty:
        log_warning(f"Extreme price changes detected: {len(extreme_changes)} tickers")

    return len(extreme_changes) == 0
```

### Data Lineage Tracking

```python
# Add metadata to track data source and processing
def add_lineage_metadata(df, source, processing_date):
    df['data_source'] = source  # 'yfinance', 'akshare'
    df['ingestion_timestamp'] = datetime.now()
    df['processing_date'] = processing_date
    return df
```

---

## 🔧 Performance Optimization Techniques

### 1. Partition Pruning
```sql
-- Good: Uses partition pruning (only reads specific date)
SELECT * FROM equity_all WHERE date >= '2024-12-01' AND date <= '2024-12-07';

-- Bad: Scans all data
SELECT * FROM equity_all WHERE EXTRACT(MONTH FROM date) = 12;
```

### 2. Column Pruning
```sql
-- Good: Only reads needed columns
SELECT ticker, close FROM equity_all WHERE ticker = 'AAPL';

-- Bad: Reads all columns
SELECT * FROM equity_all WHERE ticker = 'AAPL';
```

### 3. Query Optimization
```sql
-- Use indexes for frequent filters
CREATE INDEX idx_ticker_date ON equity_all(ticker, date);

-- Materialize frequently used aggregations
CREATE MATERIALIZED VIEW daily_summary AS
SELECT
    date,
    market,
    COUNT(*) as ticker_count,
    SUM(volume) as total_volume,
    AVG(close) as avg_close
FROM equity_all
GROUP BY date, market;
```

### 4. Storage Optimization
```python
# Use appropriate compression
df.to_parquet(
    'data.parquet',
    compression='snappy',  # Good balance of speed vs compression
    index=False
)

# Use appropriate data types
df['volume'] = df['volume'].astype('int32')  # Not int64 unless needed
df['price'] = df['price'].astype('float32')  # Not float64 unless needed
```

---

## 🚀 Scalability Patterns

### Vertical Scaling (Bigger Machine)
- More CPU cores for parallel processing
- More RAM for larger query caches
- Faster SSD for I/O operations
- Good up to a certain point

### Horizontal Scaling (More Machines)
- Not used in this project (single-machine focus)
- Would require distributed query engines (Spark, Presto)
- More complex architecture

### Data Partitioning for Scale
```python
# Partition by market for parallel processing
data/
├── us_equity/     # Process on machine 1
├── cn_ashare/     # Process on machine 2
└── hk_sg_equity/  # Process on machine 3
```

### Time-based Partitioning
```python
# Archive old data
data/
├── current/       # Last 2 years (frequently accessed)
└── archive/       # Older data (rarely accessed)
    ├── 2020/
    ├── 2021/
    └── 2022/
```

---

## 🔒 Security Considerations

### Data Privacy
- Market data is public information
- No PII (Personally Identifiable Information)
- Still follow good security practices

### Access Control
```python
# Use environment variables for credentials
import os
aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
aws_secret_key = os.getenv('AWS_SECRET_ACCESS_KEY')

# Never commit credentials to git
# Use .gitignore for sensitive files
```

### Data Integrity
```python
# Verify checksums for large downloads
import hashlib

def verify_file_integrity(file_path, expected_checksum):
    with open(file_path, 'rb') as f:
        file_hash = hashlib.md5(f.read()).hexdigest()

    return file_hash == expected_checksum
```

---

## 📈 Monitoring & Observability

### Key Metrics to Track
1. **Data Freshness**: How recent is the data?
2. **Data Completeness**: What percentage of expected data arrived?
3. **Query Performance**: Average query response time
4. **Storage Usage**: Disk space consumption
5. **Error Rates**: Failed API calls, write errors

### Logging Strategy
```python
import logging

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='{"timestamp": "%(asctime)s", "level": "%(levelname)s", "message": "%(message)s"}',
    handlers=[
        logging.FileHandler('logs/ingestion.log'),
        logging.StreamHandler()
    ]
)

# Use structured logs for easy parsing
logger.info({
    'event': 'data_ingestion_completed',
    'date': '2024-12-01',
    'market': 'us',
    'records_processed': 8500,
    'records_failed': 12,
    'duration_seconds': 45
})
```

### Alerting
```python
# Set up alerts for critical issues
def check_data_quality_and_alert():
    issues = []

    # Check for missing data
    if not validate_completeness(df, yesterday):
        issues.append("Missing tickers detected")

    # Check for processing failures
    if error_rate > 0.05:  # 5% error rate threshold
        issues.append("High error rate in data processing")

    if issues:
        send_alert(f"Data quality issues: {', '.join(issues)}")
```

---

## 🎓 Key Takeaways

1. **ELT Pattern**: Load raw data first, transform later - provides flexibility and preserves original data
2. **Lakehouse Architecture**: Cost-effective, scalable alternative to traditional data warehouses
3. **Hive Partitioning**: Essential for performance with time-series data
4. **Parquet Format**: Optimal storage format for analytical workloads
5. **Data Quality**: Implement validation at ingestion time, not just query time
6. **Performance**: Use partition pruning, column pruning, and appropriate indexes
7. **Monitoring**: Track data freshness, completeness, and performance metrics
8. **Security**: Treat even public data with good security practices

These concepts form the foundation of modern data engineering and are directly applicable to our equity EOD data pipeline.

---

**Further Reading**:
- [DuckDB Documentation](https://duckdb.org/docs/)
- [Apache Parquet Specification](https://parquet.apache.org/docs/)
- [Data Lakehouse Architecture](https://delta.io/)
- [Modern Data Stack Guide](https://www.modern-data-stack.com/)