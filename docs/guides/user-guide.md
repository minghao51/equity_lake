# User Guide - Equity EOD Data Pipeline

Comprehensive guide for using the equity EOD data pipeline.

---

## Table of Contents

1. [Daily Update Process](#daily-update-process)
2. [Example Queries](#example-queries)
3. [Troubleshooting](#troubleshooting)
4. [Cron Scheduling](#cron-scheduling)
5. [Advanced Configuration](#advanced-configuration)

---

## Daily Update Process

The `ingest_daily.py` script fetches and appends EOD data for multiple markets.

### How It Works

1. **Fetches yesterday's EOD data** from:
   - **US markets**: yfinance (NYSE, NASDAQ)
   - **HK/SG markets**: yfinance
   - **CN A-shares**: akshare

2. **Writes to partitioned Parquet** (`date=YYYY-MM-DD/`)

3. **Never modifies S3-synced historical data**

4. **Conflict-safe**: Creates new partitions only

### Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1: Fetch Market Data                                  │
├─────────────────────────────────────────────────────────────┤
│  • US Market (yfinance)    → ~5000 tickers                 │
│  • CN A-shares (akshare)   → ~100 tickers (configurable)   │
│  • HK/SG Markets (yfinance) → ~500 tickers                 │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 2: Validate & Transform                               │
├─────────────────────────────────────────────────────────────┤
│  • Standardize column names (OHLCV + ticker, date)         │
│  • Validate schema compliance                               │
│  • Add market metadata                                       │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 3: Write to Partitioned Parquet                       │
├─────────────────────────────────────────────────────────────┤
│  • data/lake/us_equity/date=2024-12-01/2024-12-01.parquet  │
│  • data/lake/cn_ashare/date=2024-12-01/2024-12-01.parquet  │
│  • data/lake/hk_sg_equity/date=2024-12-01/2024-12-01.parquet│
└─────────────────────────────────────────────────────────────┘
```

### Command Options

```bash
# Basic usage (fetch yesterday's data)
python -m scripts.ingest_daily

# Specify date
python -m scripts.ingest_daily --date 2024-12-01

# Select specific markets
python -m scripts.ingest_daily --markets us,cn

# Parallel mode (3x faster)
python -m scripts.ingest_daily --parallel

# Parallel with custom worker count
python -m scripts.ingest_daily --parallel --max-workers 2

# Dry run (test without writing)
python -m scripts.ingest_daily --dry-run --verbose

# Filter by ticker priority (requires config)
python -m scripts.ingest_daily --tags blue-chip --min-priority 8
```

---

## Example Queries

### Cross-Market Analysis

```sql
-- Compare average volume across markets
SELECT
    market,
    AVG(volume) as avg_volume,
    COUNT(DISTINCT ticker) as num_tickers
FROM equity_all
WHERE date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY market;
```

### Stock Performance

```sql
-- Top gainers last week
WITH weekly_change AS (
    SELECT
        ticker,
        market,
        (MAX(close) - MIN(close)) / MIN(close) * 100 as pct_change
    FROM equity_all
    WHERE date >= CURRENT_DATE - INTERVAL '7 days'
    GROUP BY ticker, market
)
SELECT * FROM weekly_change
ORDER BY pct_change DESC
LIMIT 10;
```

### Latest Data Summary

```sql
-- Most recent data by market
SELECT
    market,
    MAX(date) as latest_date,
    COUNT(DISTINCT ticker) as ticker_count
FROM equity_all
GROUP BY market
ORDER BY market;
```

### Top Volume Stocks

```sql
-- Top 20 stocks by volume (last 30 days)
SELECT
    ticker,
    market,
    AVG(volume) as avg_daily_volume,
    SUM(volume) as total_volume
FROM equity_all
WHERE date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY ticker, market
ORDER BY avg_daily_volume DESC
LIMIT 20;
```

### Price Range Analysis

```sql
-- High-low range analysis (last trading day)
SELECT
    ticker,
    market,
    close,
    high - low as daily_range,
    (high - low) / close * 100 as pct_range
FROM equity_all
WHERE date = (SELECT MAX(date) FROM equity_all)
ORDER BY pct_range DESC
LIMIT 10;
```

### Moving Averages

```sql
-- 20-day moving average crossover
WITH ma20 AS (
    SELECT
        ticker,
        date,
        close,
        AVG(close) OVER (
            PARTITION BY ticker
            ORDER BY date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) as ma20
    FROM equity_all
    WHERE ticker = 'AAPL'
    AND date >= CURRENT_DATE - INTERVAL '60 days'
)
SELECT *
FROM ma20
ORDER BY date DESC;
```

### Market Summary Statistics

```sql
-- Daily summary statistics by market
SELECT
    date,
    market,
    COUNT(DISTINCT ticker) as num_tickers,
    SUM(volume) as total_volume,
    AVG(close) as avg_close,
    AVG(high - low) as avg_daily_range
FROM equity_all
WHERE date >= CURRENT_DATE - INTERVAL '30 days'
GROUP BY date, market
ORDER BY date DESC, market;
```

---

## Troubleshooting

### S3 Sync Issues

**Problem**: S3 sync fails with access denied error

**Solutions**:
```bash
# Verify AWS credentials
aws s3 ls s3://your-bucket/

# Check credential configuration
aws configure list

# For public buckets, use --no-sign-request
aws s3 ls s3://public-bucket/ --no-sign-request
```

**Problem**: Sync is very slow

**Solutions**:
```bash
# Use s5cmd for faster sync (10x faster)
s5cmd --numworkers 32 sync s3://bucket/ data/lake/

# Reduce workers if network is limited
s5cmd --numworkers 8 sync s3://bucket/ data/lake/
```

### Missing Dependencies

**Problem**: ModuleNotFoundError

**Solution**:
```bash
# Reinstall all dependencies
uv pip install --reinstall -r requirements.txt

# Or use uv sync
uv sync
```

### DuckDB Query Errors

**Problem**: "No files found matching pattern"

**Solutions**:
```bash
# Check if data directories exist
ls -la data/lake/

# Verify Parquet files are present
find data/lake/ -name "*.parquet" | head -10

# Validate Parquet file
python -c "import pandas as pd; pd.read_parquet('data/lake/us_equity/date=2024-12-01/2024-12-01.parquet')"
```

**Problem**: Schema mismatch error

**Solution**:
```bash
# Check schema consistency
python -c "
import pandas as pd
import glob

files = glob.glob('data/lake/us_equity/date=*/*.parquet')
for f in files[:5]:
    df = pd.read_parquet(f)
    print(f'{f}: {df.columns.tolist()}')
"
```

### API Rate Limiting

**Problem**: yfinance or akshare rate limiting errors

**Solutions**:
```bash
# Use sequential mode instead of parallel
python -m scripts.ingest_daily  # (no --parallel flag)

# Reduce worker count
python -m scripts.ingest_daily --parallel --max-workers 1

# Add delays between requests (edit ingest_daily.py)
# Increase retry_delay parameter
```

### Data Quality Issues

**Problem**: Missing data for expected tickers

**Solutions**:
```bash
# Check logs for errors
tail -100 logs/ingest_daily.log | grep ERROR

# Verify data was written
ls -lh data/lake/us_equity/date=2024-12-01/

# Check Parquet content
python -c "
import pandas as pd
df = pd.read_parquet('data/lake/us_equity/date=2024-12-01/2024-12-01.parquet')
print(f'Rows: {len(df)}')
print(f'Tickers: {df[\"ticker\"].nunique()}')
print(f'Date range: {df[\"date\"].min()} to {df[\"date\"].max()}')
"
```

---

## Cron Scheduling

### Linux/macOS Cron Setup

1. **Edit crontab**:
```bash
crontab -e
```

2. **Add daily job** (runs at 8 AM every weekday):
```bash
# Daily ingestion at 8 AM (Monday-Friday)
0 8 * * 1-5 cd /path/to/equity-eod && /path/to/.venv/bin/python -m scripts.ingest_daily --parallel >> logs/daily.log 2>&1
```

3. **Alternative with email notifications**:
```bash
# Daily at 8 AM with email on error
0 8 * * * cd /path/to/equity-eod && /path/to/.venv/bin/python -m scripts.ingest_daily --parallel >> logs/daily.log 2>&1 || mail -s "Ingestion Failed" user@example.com < logs/daily.log
```

### Cron Schedule Examples

```bash
# Every day at midnight
0 0 * * * cd /path/to/equity-eod && python -m scripts.ingest_daily

# Every weekday at 6:30 PM (after market close)
30 18 * * 1-5 cd /path/to/equity-eod && python -m scripts.ingest_daily --parallel

# Every 6 hours
0 */6 * * * cd /path/to/equity-eod && python -m scripts.ingest_daily

# At 2 AM on Sundays (for weekend data)
0 2 * * 0 cd /path/to/equity-eod && python -m scripts.ingest_daily --date $(date -d "yesterday" +%Y-%m-%d)
```

### Verify Cron Jobs

```bash
# List current crontab
crontab -l

# Check cron logs
grep CRON /var/log/syslog | tail -20

# Test job manually first
cd /path/to/equity-eod && python -m scripts.ingest_daily --dry-run
```

---

## Advanced Configuration

### Custom Ticker Lists

Create `config/tickers.yaml`:

```yaml
markets:
  us:
    tickers:
      - symbol: AAPL
        name: Apple Inc.
        priority: 10
        tags: [tech, blue-chip]
      - symbol: GOOGL
        name: Alphabet Inc.
        priority: 9
        tags: [tech]

  cn:
    tickers:
      - symbol: "000001"
        name: Ping An Bank
        priority: 8
        tags: [finance]
```

Usage:
```bash
python -m scripts.ingest_daily --tags blue-chip --min-priority 9
```

### Environment Variables

Create `.env` file:

```bash
# AWS S3 Configuration
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
S3_BUCKET=s3://my-equity-lake/

# API Configuration
YFINANCE_RATE_LIMIT=0.5
AKSHARE_RATE_LIMIT=0.1

# Parallel Processing
MAX_WORKERS=10
DEFAULT_PARALLEL_MODE=true

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

Load in Python:
```python
from dotenv import load_dotenv
load_dotenv()
```

### Performance Tuning

**For faster ingestion**:
```bash
# More aggressive parallelism
python -m scripts.ingest_daily --parallel --max-workers 20

# Fetch more CN stocks
python -m scripts.ingest_daily --parallel --markets cn --stock-limit 500
```

**For more reliable fetching**:
```bash
# Conservative settings
python -m scripts.ingest_daily --parallel --max-workers 3

# Fewer stocks, more retries
python -m scripts.ingest_daily --stock-limit 50 --retry-attempts 5
```

---

## Monitoring & Logging

### View Logs

```bash
# Real-time monitoring
tail -f logs/ingest_daily.log

# Filter for errors
grep ERROR logs/ingest_daily.log

# View structured logs (JSON format)
tail -f logs/ingest_daily.log | jq '.'

# Filter by correlation ID
jq 'select(.correlation_id == "abc123")' logs/ingest_daily.log
```

### Performance Monitoring

```bash
# Check timing metrics
jq 'select(.event == "market_fetch_completed") | {market: .market, duration: .duration_seconds}' logs/ingest_daily.log

# Average fetch time by market
jq -s 'group_by(.market) | map({market: .[0].market, avg_time: map(.duration_seconds) | add / length})' logs/ingest_daily.log
```

### Data Quality Checks

```bash
# Check for missing dates
python -c "
import duckdb
con = duckdb.connect(':memory:')

# Find gaps in data
result = con.execute('''
    WITH date_range AS (
        SELECT generate_series::DATE AS date
        FROM generate_series(
            CURRENT_DATE - INTERVAL '30 days',
            CURRENT_DATE,
            INTERVAL '1 day'
        )
    ),
    existing_dates AS (
        SELECT DISTINCT date
        FROM read_parquet('data/lake/us_equity/**/*.parquet')
    )
    SELECT d.date
    FROM date_range d
    LEFT JOIN existing_dates e ON d.date = e.date
    WHERE e.date IS NULL
''').fetchall()

print('Missing dates:', [row[0] for row in result])
"
```

---

## Docker Tips

### Container Management

```bash
# View logs
docker compose logs -f daily

# Restart container
docker compose restart daily

# Execute command in container
docker compose exec daily python -m scripts.ingest_daily --dry-run

# Clean up old containers
docker compose down -v
docker system prune -a
```

### Resource Limits

Edit `docker-compose.yml`:

```yaml
services:
  daily:
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

---

## Backup & Recovery

### Backup Strategy

```bash
# Backup data lake
tar -czf backup-$(date +%Y%m%d).tar.gz data/lake/

# Backup to remote storage
rsync -avz data/lake/ user@backup-server:/backups/equity-eod/

# Incremental backup with rsync
rsync -avz --delete data/lake/ user@backup-server:/backups/equity-eod/
```

### Recovery

```bash
# Restore from backup
tar -xzf backup-20241201.tar.gz

# Restore from remote
rsync -avz user@backup-server:/backups/equity-eod/ data/lake/
```

---

## Best Practices

1. **Run in parallel mode** for 3-5x performance improvement
2. **Monitor logs** regularly for errors and performance issues
3. **Use cron** for automated daily updates
4. **Keep backups** of critical data
5. **Validate schema** before querying
6. **Test with --dry-run** before making changes
7. **Use structured logging** for better observability
8. **Set up alerts** for failed ingestion runs

---

## Additional Resources

- [DuckDB Python API](https://duckdb.org/docs/api/python/overview)
- [yfinance Usage Guide](https://github.com/ranaroussi/yfinance/wiki)
- [akshare Documentation](https://akshare.readthedocs.io/zh-cn/latest/)
- [Parquet Best Practices](https://parquet.apache.org/docs/file-organization/)
