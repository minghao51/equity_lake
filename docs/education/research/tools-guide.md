# 🛠️ Tools & Libraries Guide

**Purpose**: Comprehensive guide to the technology stack used in the equity EOD data pipeline.

---

## 📦 Package Manager: uv

### Overview
**uv** is an ultra-fast Python package manager written in Rust, created by Astral (the same company behind Ruff).

### Why uv over pip/poetry?
```bash
# pip: 30-60 seconds for medium-sized projects
# uv: 1-5 seconds for same project
# Speed improvement: 10-100x faster
```

### Installation
```bash
# One-time installation
curl -LsSf https://astral.sh/uv/install.sh | sh

# Reload shell to update PATH
source ~/.bashrc  # or ~/.zshrc
```

### Basic Usage
```bash
# Create virtual environment
uv venv                    # Creates .venv/
source .venv/bin/activate   # Activate environment

# Install dependencies
uv pip install pandas yfinance duckdb

# Install from requirements.txt
uv pip install -r requirements.txt

# Install development dependencies
uv pip install -r requirements.txt -e ".[dev]"

# Run commands in environment
uv run python scripts/ingest_daily.py
uv run pytest tests/

# Sync from pyproject.toml
uv sync                    # Install all dependencies
uv sync --group dev        # Install + dev dependencies
```

### pyproject.toml Configuration
```toml
[project]
name = "equity-edo-data-pipeline"
version = "0.1.0"
description = "Equity EOD data pipeline with local-first architecture"
requires-python = ">=3.11"
dependencies = [
    "yfinance>=0.2.50",
    "akshare>=1.15.0",
    "duckdb>=1.0.0",
    "pandas>=2.2.0",
    "pyarrow>=18.0.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-cov>=5.0.0",
    "ruff>=0.8.0",
    "mypy>=1.11.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

### Performance Tips
```bash
# Use uv for all Python operations
uv pip install package     # Not: pip install package
uv run python script.py     # Not: python script.py

# Cache management
uv cache clean             # Clear old packages
uv cache info             # Check cache usage
```

---

## 📈 Data Sources: yfinance

### Overview
**yfinance** is a popular open-source library for accessing historical market data from Yahoo! Finance.

### Market Coverage
- **US Markets**: NYSE, NASDAQ, AMEX (5,000+ tickers)
- **Hong Kong**: HKEX (2,000+ tickers)
- **Singapore**: SGX (700+ tickers)
- **Other**: UK, Germany, Australia, India, and more

### Installation & Setup
```bash
# Install with uv
uv pip install yfinance>=0.2.50

# Basic usage
import yfinance as yf
```

### Fetching Historical Data
```python
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta

def fetch_single_ticker(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch data for a single ticker"""
    data = yf.download(ticker, start=start_date, end=end_date, progress=False)

    if data.empty:
        return pd.DataFrame()

    # Standardize column names
    data.columns = [col.lower() for col in data.columns]
    data = data.reset_index()
    data['ticker'] = ticker

    return data[['ticker', 'date', 'open', 'high', 'low', 'close', 'adj close', 'volume']]

def fetch_multiple_tickers(tickers: list, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch data for multiple tickers efficiently"""
    data = yf.download(tickers, start=start_date, end=end_date, group_by='ticker', progress=False)

    if data.empty:
        return pd.DataFrame()

    # Flatten multi-level columns
    data.columns = [f"{col[0]}_{col[1]}" if col[1] else col[0] for col in data.columns]
    data = data.reset_index()

    # Transform to long format (one row per ticker per date)
    result = []
    for ticker in tickers:
        ticker_data = data[['date'] + [col for col in data.columns if ticker in col]].copy()
        ticker_data['ticker'] = ticker

        # Extract OHLCV columns
        ticker_data = ticker_data.rename(columns={
            f'{ticker}_open': 'open',
            f'{ticker}_high': 'high',
            f'{ticker}_low': 'low',
            f'{ticker}_close': 'close',
            f'{ticker}_adj close': 'adj_close',
            f'{ticker}_volume': 'volume'
        })

        result.append(ticker_data[['ticker', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume']])

    return pd.concat(result, ignore_index=True).dropna()

# Example usage
us_tickers = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA']
yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
data = fetch_multiple_tickers(us_tickers, yesterday, yesterday)
print(f"Fetched data for {len(data)} tickers")
```

### Hong Kong & Singapore Tickers
```python
def fetch_hk_data(tickers: list, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch Hong Kong market data"""
    hk_tickers = [f"{ticker}.HK" for ticker in tickers]  # Add .HK suffix
    data = fetch_multiple_tickers(hk_tickers, start_date, end_date)
    data['ticker'] = data['ticker'].str.replace('.HK', '')  # Remove suffix for consistency
    data['market'] = 'hk'
    return data

def fetch_sg_data(tickers: list, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch Singapore market data"""
    sg_tickers = [f"{ticker}.SI" for ticker in tickers]  # Add .SI suffix
    data = fetch_multiple_tickers(sg_tickers, start_date, end_date)
    data['ticker'] = data['ticker'].str.replace('.SI', '')  # Remove suffix for consistency
    data['market'] = 'sg'
    return data

# Example usage
hk_tickers = ['0700', '9988', '1299', '2318']  # Tencent, Alibaba, AIA, China Construction Bank
sg_tickers = ['D05', 'O39', 'U11', 'Z74']     # DBS, OCBC, UOB, Keppel
```

### Rate Limiting & Error Handling
```python
import time
from typing import List
import logging

logger = logging.getLogger(__name__)

def fetch_with_retry(fetch_fn, tickers: List[str], max_retries: int = 3, delay: float = 0.5):
    """Fetch data with retry logic and rate limiting"""
    for attempt in range(max_retries):
        try:
            # Add delay between attempts to respect rate limits
            if attempt > 0:
                time.sleep(delay * (2 ** attempt))  # Exponential backoff

            result = fetch_fn(tickers)

            if not result.empty:
                logger.info(f"Successfully fetched data for {len(result)} records")
                return result
            else:
                logger.warning(f"Empty result on attempt {attempt + 1}")

        except Exception as e:
            logger.error(f"Attempt {attempt + 1} failed: {e}")
            if attempt == max_retries - 1:
                logger.error(f"All {max_retries} attempts failed for tickers: {tickers}")
                raise

    return pd.DataFrame()  # Return empty DataFrame if all attempts fail

# Usage with rate limiting
def fetch_multiple_batches(tickers: List[str], batch_size: int = 50):
    """Process tickers in batches to avoid rate limits"""
    all_data = []

    for i in range(0, len(tickers), batch_size):
        batch = tickers[i:i + batch_size]
        logger.info(f"Processing batch {i//batch_size + 1}: {len(batch)} tickers")

        batch_data = fetch_with_retry(fetch_multiple_tickers, batch)
        if not batch_data.empty:
            all_data.append(batch_data)

        # Rate limiting between batches
        time.sleep(1.0)

    return pd.concat(all_data, ignore_index=True) if all_data else pd.DataFrame()
```

### Common Issues & Solutions

#### Issue: Empty DataFrame
```python
# Problem: Some tickers may not exist or be delisted
def filter_valid_tickers(tickers: List[str]) -> List[str]:
    """Filter out invalid tickers"""
    valid_tickers = []

    for ticker in tickers[:5]:  # Test first few
        try:
            data = yf.Ticker(ticker).info
            if data and 'shortName' in data:
                valid_tickers.append(ticker)
        except:
            logger.warning(f"Invalid ticker: {ticker}")

    return valid_tickers
```

#### Issue: Missing Data for Holidays
```python
# Problem: Markets closed on holidays
def handle_missing_dates(tickers: List[str], date: str) -> pd.DataFrame:
    """Handle cases where markets are closed"""
    try:
        data = fetch_multiple_tickers(tickers, date, date)
        if data.empty:
            logger.info(f"No data available for {date} (likely holiday or weekend)")
        return data
    except Exception as e:
        logger.error(f"Error fetching data for {date}: {e}")
        return pd.DataFrame()
```

---

## 🇨🇳 China A-shares: akshare

### Overview
**akshare** is a comprehensive Python library for accessing Chinese financial data, including A-shares from Shanghai and Shenzhen exchanges.

### Market Coverage
- **Shanghai Stock Exchange (SSE)**: ~2,200 A-shares
- **Shenzhen Stock Exchange (SZSE)**: ~2,800 A-shares
- **Total A-shares**: ~5,000+ active stocks

### Installation & Setup
```bash
# Install with uv
uv pip install akshare>=1.15.0

# Basic usage
import akshare as ak
```

### Fetching A-share Data
```python
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta

def fetch_cn_ashare_single_day(trade_date: str) -> pd.DataFrame:
    """Fetch A-share data for a specific trading day"""
    try:
        # Get all A-share stock codes
        stock_info = ak.stock_info_a_code_name()
        stock_codes = stock_info['code'].tolist()

        # Fetch daily data
        # Note: akshare has multiple functions for different data types
        daily_data = []

        for exchange in ['sh', 'sz']:  # Shanghai and Shenzhen
            try:
                # Get real-time data (this returns current day's data)
                data = ak.stock_zh_a_spot_em()

                # Filter by exchange and standardize format
                if exchange == 'sh':
                    # Shanghai stocks start with 6
                    filtered = data[data['代码'].str.startswith('6')]
                else:
                    # Shenzhen stocks start with 0, 2, 3
                    filtered = data[data['代码'].str.match(r'^[023]')]

                # Rename and standardize columns
                filtered = filtered.rename(columns={
                    '代码': 'ticker',
                    '名称': 'name',
                    '最新价': 'close',
                    '今开': 'open',
                    '最高': 'high',
                    '最低': 'low',
                    '成交量': 'volume',
                    '涨跌幅': 'pct_change'
                })

                # Add metadata
                filtered['date'] = trade_date
                filtered['exchange'] = exchange
                filtered['market'] = 'cn'

                # Select and reorder columns
                filtered = filtered[['ticker', 'name', 'date', 'open', 'high', 'low', 'close', 'volume', 'pct_change', 'exchange']]
                daily_data.append(filtered)

            except Exception as e:
                print(f"Error fetching {exchange} data: {e}")
                continue

        if daily_data:
            result = pd.concat(daily_data, ignore_index=True)
            print(f"Successfully fetched {len(result)} A-share records for {trade_date}")
            return result
        else:
            print(f"No data fetched for {trade_date}")
            return pd.DataFrame()

    except Exception as e:
        print(f"Error fetching A-share data for {trade_date}: {e}")
        return pd.DataFrame()

def fetch_cn_ashare_history(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch historical data for a single A-share ticker"""
    try:
        # akshare has different functions for historical data
        data = ak.stock_zh_a_hist(symbol=ticker, period="daily",
                                start_date=start_date.replace('-', ''),
                                end_date=end_date.replace('-', ''),
                                adjust="qfq")  # Forward adjusted for splits/dividends

        if data.empty:
            return pd.DataFrame()

        # Standardize column names
        data = data.rename(columns={
            '日期': 'date',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'volume'
        })

        data['ticker'] = ticker
        data['market'] = 'cn'

        return data[['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']]

    except Exception as e:
        print(f"Error fetching historical data for {ticker}: {e}")
        return pd.DataFrame()
```

### Alternative A-share Data Sources
```python
def fetch_cn_ashare_alternative(trade_date: str) -> pd.DataFrame:
    """Alternative method using different akshare functions"""
    try:
        # Method 1: Use individual stock data fetching
        # This is more reliable but slower

        # Get all stock codes (may need to be hardcoded for reliability)
        major_stocks = [
            # Shanghai (6xxxx)
            '600519',  # Kweichow Moutai
            '600036',  # China Merchants Bank
            '600276',  # Hengrui Medicine
            '600900',  # Yangtze River Power

            # Shenzhen (0xxxx, 2xxxx, 3xxxx)
            '000001',  # Ping An Bank
            '000002',  # Vanke
            '300059',  # Oriental Fortune
            '300750',  # Ningde Era
        ]

        all_data = []

        for ticker in major_stocks:
            try:
                stock_data = ak.stock_zh_a_spot_em()
                stock_row = stock_data[stock_data['代码'] == ticker]

                if not stock_row.empty:
                    row = stock_row.iloc[0]
                    formatted_data = {
                        'ticker': ticker,
                        'date': trade_date,
                        'open': row['今开'],
                        'high': row['最高'],
                        'low': row['最低'],
                        'close': row['最新价'],
                        'volume': row['成交量'],
                        'market': 'cn'
                    }
                    all_data.append(formatted_data)

                # Add delay to avoid rate limiting
                time.sleep(0.1)

            except Exception as e:
                print(f"Error fetching data for {ticker}: {e}")
                continue

        if all_data:
            df = pd.DataFrame(all_data)
            print(f"Successfully fetched {len(df)} major A-shares for {trade_date}")
            return df
        else:
            return pd.DataFrame()

    except Exception as e:
        print(f"Error in alternative A-share fetching: {e}")
        return pd.DataFrame()
```

### Data Quality Validation for A-shares
```python
def validate_cn_ashare_data(df: pd.DataFrame) -> bool:
    """Validate A-share data for quality issues"""
    if df.empty:
        return True  # Empty data is valid (no trading day)

    # Check for required columns
    required_columns = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
    if not all(col in df.columns for col in required_columns):
        print("Missing required columns")
        return False

    # Check ticker format (6 digits)
    invalid_tickers = df[~df['ticker'].str.match(r'^\d{6}$', na=False)]
    if not invalid_tickers.empty:
        print(f"Invalid ticker formats: {invalid_tickers['ticker'].tolist()}")
        return False

    # Check price relationships
    invalid_high = df[df['high'] < df[['open', 'close', 'low']].max(axis=1)]
    invalid_low = df[df['low'] > df[['open', 'close', 'high']].min(axis=1)]

    if not invalid_high.empty or not invalid_low.empty:
        print("Invalid price relationships detected")
        return False

    # Check for negative values
    negative_values = df[(df[['open', 'high', 'low', 'close', 'volume']] < 0).any(axis=1)]
    if not negative_values.empty:
        print("Negative values detected")
        return False

    print("A-share data validation passed")
    return True
```

### Common Issues with akshare

#### Issue: API Access Restrictions
```python
# Problem: akshare may have network restrictions in some regions
def test_akshare_connectivity():
    """Test if akshare can access Chinese financial APIs"""
    try:
        # Simple test - try to get stock list
        test_data = ak.stock_info_a_code_name()
        if not test_data.empty:
            print("akshare connectivity test: PASSED")
            return True
        else:
            print("akshare connectivity test: FAILED - Empty data")
            return False
    except Exception as e:
        print(f"akshare connectivity test: FAILED - {e}")
        return False

# Fallback strategy
def get_cn_ashare_data_with_fallback(trade_date: str) -> pd.DataFrame:
    """Get A-share data with fallback strategies"""

    # Strategy 1: Primary akshare method
    if test_akshare_connectivity():
        data = fetch_cn_ashare_single_day(trade_date)
        if not data.empty and validate_cn_ashare_data(data):
            return data

    # Strategy 2: Alternative akshare method
    print("Trying alternative akshare method...")
    data = fetch_cn_ashare_alternative(trade_date)
    if not data.empty and validate_cn_ashare_data(data):
        return data

    # Strategy 3: Use cached data or skip
    print("All A-share fetching methods failed, returning empty DataFrame")
    return pd.DataFrame()
```

#### Issue: Data Format Inconsistencies
```python
# Problem: Different akshare functions return data in different formats
def standardize_akshare_data(df: pd.DataFrame, market: str = 'cn') -> pd.DataFrame:
    """Standardize akshare data to consistent format"""
    if df.empty:
        return df

    # Common column name mappings
    column_mappings = {
        '代码': 'ticker',
        '日期': 'date',
        '开盘': 'open',
        '最高': 'high',
        '最低': 'low',
        '收盘': 'close',
        '成交量': 'volume',
        '最新价': 'close',
        '今开': 'open'
    }

    # Rename columns
    df = df.rename(columns=column_mappings)

    # Ensure required columns exist
    required_columns = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
    for col in required_columns:
        if col not in df.columns:
            if col == 'date':
                df[col'] = datetime.now().strftime('%Y-%m-%d')
            else:
                df[col] = 0.0  # Default value

    # Add market column
    df['market'] = market

    # Select and reorder columns
    df = df[required_columns + ['market']]

    # Convert data types
    df['ticker'] = df['ticker'].astype(str)
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    df[['open', 'high', 'low', 'close']] = df[['open', 'high', 'low', 'close']].astype(float)
    df['volume'] = df['volume'].astype(int)

    return df
```

---

## 🦆 Query Engine: DuckDB

### Overview
**DuckDB** is an in-process SQL OLAP database management system, often called "SQLite for analytics".

### Key Features
- **Zero-Copy Parquet Reading**: Query Parquet files directly without loading
- **Columnar Execution**: Vectorized query processing
- **SQLite-like Simplicity**: Embedded, no server required
- **PostgreSQL Compatibility**: Standard SQL syntax
- **Python Integration**: Native Python API
- **Hive Partitioning Support**: Automatic partition pruning

### Installation & Setup
```bash
# Install with uv
uv pip install duckdb>=1.0.0

# Basic usage
import duckdb
```

### Basic DuckDB Operations
```python
import duckdb
import pandas as pd

# Create in-memory database
conn = duckdb.connect(':memory:')

# Create persistent database
conn = duckdb.connect('equity_data.duckdb')

# Execute SQL queries
result = conn.execute("""
    SELECT ticker, date, close, volume
    FROM read_parquet('data/lake/us_equity/date=2024-12-01/*.parquet')
    WHERE volume > 1000000
    ORDER BY volume DESC
    LIMIT 10
""").fetchall()

# Convert to pandas DataFrame
df = conn.execute("""
    SELECT * FROM read_parquet('data/lake/*/date=2024-12-01/*.parquet', hive_partitioning=1)
""").df()
```

### Reading Partitioned Parquet Files
```python
def create_unified_view():
    """Create a unified view across all markets"""
    conn = duckdb.connect('equity_data.duckdb')

    # Create view that reads from all markets
    conn.execute("""
        CREATE OR REPLACE VIEW equity_all AS
        SELECT *, 'us' as market
        FROM read_parquet('data/lake/us_equity/date=*/*.parquet', hive_partitioning=1)

        UNION ALL

        SELECT *, 'cn' as market
        FROM read_parquet('data/lake/cn_ashare/date=*/*.parquet', hive_partitioning=1)

        UNION ALL

        SELECT *, 'hk' as market
        FROM read_parquet('data/lake/hk_sg_equity/date=*/*.parquet', hive_partitioning=1)
    """)

    print("Unified view 'equity_all' created successfully")

# Usage
create_unified_view()

# Query the unified view
result = conn.execute("""
    SELECT market, COUNT(*) as ticker_count, AVG(volume) as avg_volume
    FROM equity_all
    WHERE date >= '2024-12-01'
    GROUP BY market
    ORDER BY avg_volume DESC
""").fetchdf()
```

### Advanced DuckDB Queries
```python
def setup_duckdb_database():
    """Set up DuckDB database with indexes and materialized views"""
    conn = duckdb.connect('equity_data.duckdb')

    # Create unified view
    create_unified_view()

    # Create indexes for frequently queried columns
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ticker_date ON equity_all(ticker, date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_market_date ON equity_all(market, date)")

    # Create materialized views for common aggregations
    conn.execute("""
        CREATE OR REPLACE MATERIALIZED VIEW daily_market_summary AS
        SELECT
            date,
            market,
            COUNT(*) as ticker_count,
            SUM(volume) as total_volume,
            AVG(close) as avg_close,
            MAX(volume) as max_volume,
            MIN(close) as min_close
        FROM equity_all
        GROUP BY date, market
        ORDER BY date, market
    """)

    # Create view for top performers
    conn.execute("""
        CREATE OR REPLACE VIEW top_gainers AS
        WITH daily_changes AS (
            SELECT
                ticker,
                market,
                date,
                close,
                LAG(close) OVER (PARTITION BY ticker ORDER BY date) as prev_close,
                (close - LAG(close) OVER (PARTITION BY ticker ORDER BY date)) / LAG(close) OVER (PARTITION BY ticker ORDER BY date) * 100 as pct_change
            FROM equity_all
            WHERE prev_close IS NOT NULL
        )
        SELECT * FROM daily_changes WHERE pct_change > 5
        ORDER BY pct_change DESC
    """)

    print("DuckDB database setup complete")

# Example analytical queries
def run_analytical_queries():
    """Run common analytical queries"""
    conn = duckdb.connect('equity_data.duckdb')

    queries = {
        "Top Volume Stocks": """
            SELECT ticker, market, close, volume
            FROM equity_all
            WHERE date = (SELECT MAX(date) FROM equity_all WHERE market = 'us')
            ORDER BY volume DESC
            LIMIT 10
        """,

        "Cross-Market Comparison": """
            SELECT
                market,
                COUNT(*) as ticker_count,
                SUM(volume) as total_volume,
                AVG(close) as avg_close
            FROM equity_all
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY market
        """,

        "Weekly Best Performers": """
            SELECT ticker, market, AVG(pct_change) as avg_weekly_change
            FROM top_gainers
            WHERE date >= CURRENT_DATE - INTERVAL '7 days'
            GROUP BY ticker, market
            ORDER BY avg_weekly_change DESC
            LIMIT 10
        """,

        "Volume Trends": """
            SELECT
                date,
                market,
                SUM(volume) as daily_volume
            FROM equity_all
            WHERE date >= CURRENT_DATE - INTERVAL '30 days'
            GROUP BY date, market
            ORDER BY date, market
        """
    }

    for query_name, query in queries.items():
        print(f"\n=== {query_name} ===")
        try:
            result = conn.execute(query).fetchdf()
            print(result.to_string(index=False))
        except Exception as e:
            print(f"Error executing {query_name}: {e}")
```

### Performance Optimization
```python
def optimize_duckdb_performance():
    """Optimize DuckDB for better performance"""
    conn = duckdb.connect('equity_data.duckdb')

    # Set memory limits
    conn.execute("SET memory_limit='8GB'")
    conn.execute("SET threads TO 4")

    # Enable parallel processing
    conn.execute("SET enable_progress_bar=false")
    conn.execute("SET preserve_insertion_order=false")

    # Optimize for analytical workloads
    conn.execute("SET force_parallelism=true")

    print("DuckDB performance optimizations applied")

def query_with_partition_pruning(start_date: str, end_date: str):
    """Example of efficient query with partition pruning"""
    conn = duckdb.connect('equity_data.duckdb')

    # This query will only read the date partitions in the specified range
    query = f"""
        SELECT
            ticker,
            market,
            date,
            close,
            volume
        FROM equity_all
        WHERE date >= '{start_date}' AND date <= '{end_date}'
          AND volume > 1000000
        ORDER BY volume DESC
        LIMIT 100
    """

    result = conn.execute(f"EXPLAIN ANALYZE {query}").fetchdf()
    print("Query Execution Plan:")
    print(result.to_string())

    # Execute the actual query
    return conn.execute(query).fetchdf()
```

### DuckDB Python API Integration
```python
import duckdb
import pandas as pd
from typing import Dict, List, Optional

class EquityDataAnalyzer:
    """Wrapper class for DuckDB equity data operations"""

    def __init__(self, db_path: str = 'equity_data.duckdb'):
        self.conn = duckdb.connect(db_path)
        self._setup_database()

    def _setup_database(self):
        """Initialize database with views and indexes"""
        setup_duckdb_database()
        optimize_duckdb_performance()

    def get_ticker_history(self, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        """Get historical data for a specific ticker"""
        query = f"""
            SELECT * FROM equity_all
            WHERE ticker = '{ticker}'
              AND date >= '{start_date}'
              AND date <= '{end_date}'
            ORDER BY date
        """
        return self.conn.execute(query).fetchdf()

    def get_top_volume_stocks(self, market: str, date: str, limit: int = 10) -> pd.DataFrame:
        """Get top volume stocks for a specific market and date"""
        query = f"""
            SELECT ticker, close, volume
            FROM equity_all
            WHERE market = '{market}' AND date = '{date}'
            ORDER BY volume DESC
            LIMIT {limit}
        """
        return self.conn.execute(query).fetchdf()

    def get_market_summary(self, date: str) -> Dict[str, pd.DataFrame]:
        """Get comprehensive market summary for a date"""
        queries = {
            'volume_leaders': f"""
                SELECT market, ticker, close, volume
                FROM equity_all
                WHERE date = '{date}'
                ORDER BY volume DESC
                LIMIT 20
            """,

            'price_movers': f"""
                WITH price_changes AS (
                    SELECT
                        ticker,
                        market,
                        close,
                        LAG(close) OVER (PARTITION BY ticker ORDER BY date) as prev_close,
                        (close - LAG(close) OVER (PARTITION BY ticker ORDER BY date)) /
                        LAG(close) OVER (PARTITION BY ticker ORDER BY date) * 100 as pct_change
                    FROM equity_all
                    WHERE date <= '{date}' AND date >= DATE_SUB('{date}', INTERVAL '7 days')
                )
                SELECT * FROM price_changes
                WHERE prev_close IS NOT NULL
                ORDER BY ABS(pct_change) DESC
                LIMIT 20
            """,

            'market_overview': f"""
                SELECT
                    market,
                    COUNT(*) as ticker_count,
                    SUM(volume) as total_volume,
                    AVG(close) as avg_close,
                    STDDEV(close) as volatility
                FROM equity_all
                WHERE date = '{date}'
                GROUP BY market
                ORDER BY total_volume DESC
            """
        }

        results = {}
        for name, query in queries.items():
            results[name] = self.conn.execute(query).fetchdf()

        return results

    def export_to_csv(self, query: str, output_path: str):
        """Export query results to CSV"""
        result = self.conn.execute(query).fetchdf()
        result.to_csv(output_path, index=False)
        print(f"Exported {len(result)} rows to {output_path}")

# Usage example
analyzer = EquityDataAnalyzer()

# Get Apple stock history
apple_history = analyzer.get_ticker_history('AAPL', '2024-11-01', '2024-12-01')

# Get market summary for latest date
latest_date = analyzer.conn.execute("SELECT MAX(date) FROM equity_all").fetchone()[0]
market_summary = analyzer.get_market_summary(latest_date)

for table_name, data in market_summary.items():
    print(f"\n=== {table_name.replace('_', ' ').title()} ===")
    print(data.to_string(index=False))
```

### Common DuckDB Patterns
```python
# Pattern 1: Rolling calculations
conn.execute("""
    SELECT
        ticker,
        date,
        close,
        AVG(close) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) as ma5,
        AVG(close) OVER (PARTITION BY ticker ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as ma20
    FROM equity_all
    WHERE ticker = 'AAPL' AND date >= CURRENT_DATE - INTERVAL '60 days'
""")

# Pattern 2: Time-based aggregations
conn.execute("""
    SELECT
        EXTRACT(YEAR FROM date) as year,
        EXTRACT(MONTH FROM date) as month,
        market,
        SUM(volume) as total_volume,
        AVG(close) as avg_close
    FROM equity_all
    WHERE date >= CURRENT_DATE - INTERVAL '2 years'
    GROUP BY year, month, market
    ORDER BY year, month, total_volume DESC
""")

# Pattern 3: Cross-market correlations
conn.execute("""
    WITH daily_returns AS (
        SELECT
            ticker,
            market,
            date,
            close,
            LAG(close) OVER (PARTITION BY ticker ORDER BY date) as prev_close,
            (close - LAG(close) OVER (PARTITION BY ticker ORDER BY date)) / LAG(close) OVER (PARTITION BY ticker ORDER BY date) as daily_return
        FROM equity_all
        WHERE date >= CURRENT_DATE - INTERVAL '30 days'
    )
    SELECT
        a.ticker as ticker_a,
        b.ticker as ticker_b,
        a.market as market_a,
        b.market as market_b,
        CORR(a.daily_return, b.daily_return) as correlation
    FROM daily_returns a
    JOIN daily_returns b ON a.date = b.date AND a.ticker != b.ticker
    GROUP BY ticker_a, ticker_b, market_a, market_b
    HAVING COUNT(*) >= 20  -- At least 20 days of data
    ORDER BY ABS(correlation) DESC
    LIMIT 20
""")
```

---

## 📊 Data Format: Apache Parquet

### Overview
**Apache Parquet** is a columnar storage format optimized for analytical workloads.

### Key Advantages
- **Columnar Storage**: Read only needed columns
- **Compression**: 5-10x smaller than CSV
- **Schema Evolution**: Add columns without breaking existing data
- **Predicate Pushdown**: Filter data during file scan
- **Statistics**: Metadata for query optimization

### Parquet Operations with pandas
```python
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

def write_to_parquet(df: pd.DataFrame, file_path: str, compression: str = 'snappy'):
    """Write DataFrame to Parquet with optimal settings"""
    # Ensure date column is proper datetime
    if 'date' in df.columns:
        df['date'] = pd.to_datetime(df['date'])

    # Convert to optimal data types
    df = df.astype({
        'ticker': 'category' if df['ticker'].nunique() < len(df) else 'string',
        'market': 'category',
        'open': 'float32',
        'high': 'float32',
        'low': 'float32',
        'close': 'float32',
        'volume': 'int32'
    })

    # Write with partitioning
    df.to_parquet(
        file_path,
        engine='pyarrow',
        compression=compression,
        index=False,
        partition_cols=['date'] if 'date' in df.columns else None
    )

def read_parquet_with_filters(file_path: str, filters: dict = None):
    """Read Parquet file with optional filters for better performance"""
    return pd.read_parquet(
        file_path,
        engine='pyarrow',
        filters=filters,
        dtype_backend='pyarrow'  # Use Arrow dtypes for better performance
    )

# Example usage
df = pd.DataFrame({
    'ticker': ['AAPL', 'GOOGL', 'MSFT'],
    'date': pd.date_range('2024-12-01', periods=3),
    'close': [150.0, 140.0, 380.0],
    'volume': [1000000, 800000, 500000],
    'market': 'us'
})

# Write partitioned by date
write_to_parquet(df, 'data/lake/us_equity/test.parquet')

# Read with filters (predicate pushdown)
filtered_data = read_parquet_with_filters(
    'data/lake/us_equity/',
    filters=[('date', '>=', '2024-12-01')]
)
```

### Parquet File Structure
```python
def create_sample_parquet_structure():
    """Create sample Hive-partitioned Parquet structure"""
    import numpy as np
    from datetime import datetime, timedelta

    # Generate sample data
    dates = pd.date_range('2024-11-01', periods=7)
    tickers = ['AAPL', 'GOOGL', 'MSFT', 'AMZN', 'TSLA']

    all_data = []
    for date in dates:
        for ticker in tickers:
            # Simulate realistic price data
            base_price = np.random.uniform(50, 500)
            high = base_price * np.random.uniform(1.0, 1.05)
            low = base_price * np.random.uniform(0.95, 1.0)
            close = base_price * np.random.uniform(0.97, 1.03)
            volume = np.random.randint(100000, 5000000)

            all_data.append({
                'ticker': ticker,
                'date': date.strftime('%Y-%m-%d'),
                'open': base_price,
                'high': high,
                'low': low,
                'close': close,
                'volume': volume,
                'market': 'us'
            })

    df = pd.DataFrame(all_data)

    # Write with Hive partitioning
    write_to_parquet(df, 'data/lake/us_equity/')

    print("Sample Parquet structure created:")
    print("data/lake/us_equity/")
    print("├── date=2024-11-01/")
    print("│   └── part-0.parquet")
    print("├── date=2024-11-02/")
    print("│   └── part-0.parquet")
    print("└── ...")

# Verify Parquet file structure
def inspect_parquet_file(file_path: str):
    """Inspect Parquet file metadata"""
    # Read file metadata
    parquet_file = pq.ParquetFile(file_path)

    print(f"File: {file_path}")
    print(f"Number of rows: {parquet_file.metadata.num_rows}")
    print(f"Number of row groups: {parquet_file.num_row_groups}")
    print(f"Schema: {parquet_file.schema}")

    # Read first few rows
    sample_df = pd.read_parquet(file_path)
    print(f"\nSample data:")
    print(sample_df.head())

    # Check file size
    import os
    file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
    print(f"\nFile size: {file_size_mb:.2f} MB")
```

---

## 🔧 Additional Tools

### Environment Management: python-dotenv
```python
# .env file
S3_BUCKET=s3://your-equity-bucket/us_equity/
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
DB_PATH=equity_data.duckdb
LOG_LEVEL=INFO

# Usage in Python
from dotenv import load_dotenv
import os

load_dotenv()

s3_bucket = os.getenv('S3_BUCKET')
aws_access_key = os.getenv('AWS_ACCESS_KEY_ID')
```

### Code Quality: ruff
```bash
# Install
uv pip install ruff

# Lint
ruff check scripts/

# Auto-fix
ruff check --fix scripts/

# Format
ruff format scripts/
```

### Type Checking: mypy
```bash
# Install
uv pip install mypy

# Check types
mypy scripts/

# Configuration in pyproject.toml
[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

---

## 🚀 Best Practices Summary

### Performance
1. **Use partitioning** for time-series data (Hive format)
2. **Leverage column pruning** in DuckDB queries
3. **Choose appropriate compression** (snappy for balance)
4. **Use data types wisely** (float32 vs float64, category for strings)

### Reliability
1. **Implement retry logic** for API calls
2. **Validate data** before writing to Parquet
3. **Use structured logging** for debugging
4. **Handle missing data** gracefully

### Maintainability
1. **Standardize schemas** across markets
2. **Use type hints** throughout codebase
3. **Write comprehensive tests**
4. **Document data sources** and transformation logic

### Security
1. **Never commit credentials** to version control
2. **Use environment variables** for configuration
3. **Validate inputs** and sanitize outputs
4. **Monitor API usage** and respect rate limits

This comprehensive toolkit provides everything needed to build a robust, scalable equity data pipeline.

---

**Next Steps**:
1. Install and configure uv environment
2. Set up basic data fetching with yfinance
3. Create first Parquet files with test data
4. Set up DuckDB for querying
5. Implement daily ingestion pipeline