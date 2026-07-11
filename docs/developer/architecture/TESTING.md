# Testing

**Last Updated**: 2026-03-05
**Project**: Equity EOD Data Pipeline

## Testing Framework

### Core Stack

**Primary Framework**: pytest (>=8.0.0)

**Key Plugins**:
- **pytest-cov** (>=5.0.0): Coverage reporting
- **pytest-mock**: Mocking utilities
- **pytest-asyncio**: Async test support

**Configuration** (`pyproject.toml`):
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "-v",                    # Verbose output
    "--strict-markers",      # Error on unknown markers
    "--cov=src/equity_lake", # Coverage
    "--cov-report=term",     # Terminal coverage report
    "--cov-report=html",     # HTML coverage report
    "--cov-report=term-missing" # Show missing lines
]

markers = [
    "unit: Unit tests (no external dependencies)",
    "integration: Integration tests (requires data lake)",
    "slow: Slow tests (> 1 second)",
]
```

---

### Running Tests

#### Run All Tests
```bash
make test
# or
uv run pytest
```

#### Run Specific Test File
```bash
uv run pytest tests/unit/sources/test_yfinance_source.py
```

#### Run Specific Test Function
```bash
uv run pytest tests/unit/sources/test_yfinance_source.py::test_fetcher_returns_dataframe
```

#### Run by Marker
```bash
# Unit tests only
uv run pytest -m unit

# Integration tests only
uv run pytest -m integration

# Skip slow tests
uv run pytest -m "not slow"

# Run only slow tests
uv run pytest -m slow
```

#### With Coverage
```bash
uv run pytest --cov=src/equity_lake --cov-report=html --cov-report=term
```

#### Verbose Output
```bash
uv run pytest -v
```

#### Stop on First Failure
```bash
uv run pytest -x
```

#### Run Failed Tests Only
```bash
uv run pytest --lf
```

---

## Test Structure

### Directory Layout

```
tests/
├── __init__.py
├── conftest.py                 # Shared fixtures
│
├── unit/                       # Unit tests
│   ├── sources/                # Test market fetchers
│   │   ├── test_yfinance_source.py
│   │   ├── test_akshare_source.py
│   │   └── test_base_fetcher.py
│   ├── storage/                # Test storage layer
│   │   ├── test_parquet.py
│   │   ├── test_duckdb.py
│   │   └── test_s3_sync.py
│   └── test_orchestrator.py    # Test coordination logic
│
├── integration/                # Integration tests
│   ├── test_full_ingestion.py
│   ├── test_s3_workflow.py
│   └── test_query_workflow.py
│
└── signals/                    # Signal tests
    ├── test_scanner.py
    └── test_strategies.py
```

---

### Test Organization

#### Unit Tests (`tests/unit/`)

**Purpose**: Test individual components in isolation

**Characteristics**:
- No external dependencies (network, filesystem)
- Mock external APIs (yfinance, akshare)
- Fast execution (< 1 second each)
- High coverage of logic paths

**Example**:
```python
import pytest
from datetime import date
from unittest.mock import patch, MagicMock
import pandas as pd

from equity_lake.sources.us import USEquityFetcher

@pytest.mark.unit
def test_us_fetcher_returns_valid_dataframe():
    """Test that USEquityFetcher returns valid DataFrame."""
    # Arrange
    fetcher = USEquityFetcher()
    test_date = date(2024, 12, 1)

    # Act
    with patch('yfinance.download') as mock_download:
        mock_download.return_value = pd.DataFrame({
            'Open': [150.0],
            'High': [155.0],
            'Low': [149.0],
            'Close': [154.0],
            'Volume': [1000000]
        })

        df = fetcher.fetch(test_date)

    # Assert
    assert not df.empty
    assert 'ticker' in df.columns
    assert 'close' in df.columns
    assert df['date'].iloc[0] == test_date
```

---

#### Integration Tests (`tests/integration/`)

**Purpose**: Test end-to-end workflows

**Characteristics**:
- Real filesystem operations
- Temporary directories for data
- May use real Parquet files
- Slower execution (seconds to minutes)
- Test component interactions

**Example**:
```python
import pytest
from datetime import date
from pathlib import Path

from equity_lake.ingestion.orchestrator import run_daily_ingestion

@pytest.mark.integration
def test_full_ingestion_workflow(temp_lake_dir):
    """Test complete ingestion workflow."""
    # Arrange
    test_date = date(2024, 12, 1)

    # Act
    results = run_daily_ingestion(
        trading_date=test_date,
        markets=['us'],
        output_dir=temp_lake_dir
    )

    # Assert
    assert results['us'] is not None
    assert len(results['us']) > 0

    # Verify Parquet files created
    parquet_file = temp_lake_dir / 'us_equity' / f'date={test_date}' / f'{test_date}.parquet'
    assert parquet_file.exists()
```

---

### Test Markers

#### Markers Overview

**Purpose**: Categorize tests by type

**Available Markers**:
- `@pytest.mark.unit`: Unit tests (no external dependencies)
- `@pytest.mark.integration`: Integration tests (requires data lake)
- `@pytest.mark.slow`: Slow tests (> 1 second)

**Usage**:
```python
import pytest

@pytest.mark.unit
def test_fast_logic():
    """Unit test: Fast, no external dependencies."""
    pass

@pytest.mark.integration
def test_database_workflow():
    """Integration test: Requires database."""
    pass

@pytest.mark.slow
def test_large_dataset():
    """Slow test: Processes large dataset."""
    pass
```

**Running by Marker**:
```bash
# Run only unit tests
uv run pytest -m unit

# Run unit + integration (exclude slow)
uv run pytest -m "unit or integration"

# Skip slow tests
uv run pytest -m "not slow"
```

---

## Mocking Patterns

### Mocking External APIs

#### yfinance Mock

**Example**:
```python
from unittest.mock import patch
import pandas as pd

@pytest.mark.unit
def test_us_fetcher_with_mocked_yfinance():
    """Test USEquityFetcher with mocked yfinance."""
    # Mock data
    mock_data = pd.DataFrame({
        'Open': [150.0, 140.0],
        'High': [155.0, 145.0],
        'Low': [149.0, 139.0],
        'Close': [154.0, 144.0],
        'Volume': [1000000, 900000]
    }, index=pd.date_range('2024-12-01', periods=2))

    with patch('yfinance.download') as mock_download:
        mock_download.return_value = mock_data

        # Test fetcher
        fetcher = USEquityFetcher()
        df = fetcher.fetch(date(2024, 12, 1))

        # Assertions
        assert mock_download.called
        assert len(df) == 2
        assert df['close'].iloc[0] == 154.0
```

---

#### akshare Mock

**Example**:
```python
from unittest.mock import patch
import pandas as pd

@pytest.mark.unit
def test_cn_fetcher_with_mocked_akshare():
    """Test CNAshareFetcher with mocked akshare."""
    # Mock data (Chinese column names)
    mock_data = pd.DataFrame({
        '股票代码': ['000001', '000002'],
        '开盘': [10.0, 20.0],
        '最高': [11.0, 21.0],
        '最低': [9.0, 19.0],
        '收盘': [10.5, 20.5],
        '成交量': [1000000, 2000000]
    })

    with patch('akshare.stock_zh_a_hist') as mock_fetch:
        mock_fetch.return_value = mock_data

        # Test fetcher
        fetcher = CNAshareFetcher()
        df = fetcher.fetch(date(2024, 12, 1))

        # Assertions
        assert mock_fetch.called
        assert 'open' in df.columns  # Column mapped to English
        assert df['close'].iloc[0] == 10.5
```

---

### Mocking Filesystem Operations

#### Temporary Directories

**Example**:
```python
import pytest
from pathlib import Path

@pytest.fixture
def temp_lake_dir(tmp_path):
    """Create temporary data lake directory."""
    lake_dir = tmp_path / "data" / "lake"
    lake_dir.mkdir(parents=True)
    return lake_dir

@pytest.mark.integration
def test_write_parquet(temp_lake_dir):
    """Test Parquet writing with temporary directory."""
    # Use temp_lake_dir for test
    output_path = temp_lake_dir / 'test.parquet'

    df = pd.DataFrame({'a': [1, 2, 3]})
    df.to_parquet(output_path)

    assert output_path.exists()
```

---

#### Mocking Path Operations

**Example**:
```python
from unittest.mock import patch
from pathlib import Path

@pytest.mark.unit
def test_path_resolution():
    """Test path resolution with mocked filesystem."""
    with patch('pathlib.Path.exists') as mock_exists:
        mock_exists.return_value = True

        path = Path('data/lake/01_bronze/market_data/us_equity')
        assert path.exists()

        mock_exists.assert_called_once()
```

---

### Mocking Database Operations

#### DuckDB Mock

**Example**:
```python
from unittest.mock import MagicMock

@pytest.mark.unit
def test_duckdb_query():
    """Test DuckDB query with mocked connection."""
    # Mock DuckDB connection
    mock_con = MagicMock()
    mock_con.execute.return_value.df.return_value = pd.DataFrame({
        'ticker': ['AAPL'],
        'close': [154.0]
    })

    # Test with mocked connection
    df = mock_con.execute("SELECT * FROM equity_all").df()

    # Assertions
    assert mock_con.execute.called
    assert len(df) == 1
    assert df['close'].iloc[0] == 154.0
```

---

## Test Fixtures

### Shared Fixtures (`tests/conftest.py`)

#### OHLCV Data Fixture

```python
import pytest
from datetime import date
import pandas as pd

@pytest.fixture
def sample_ohlcv_df():
    """Return sample OHLCV DataFrame."""
    return pd.DataFrame({
        'ticker': ['AAPL', 'GOOGL', 'MSFT'],
        'date': [date(2024, 12, 1), date(2024, 12, 1), date(2024, 12, 1)],
        'open': [150.0, 140.0, 380.0],
        'high': [155.0, 145.0, 385.0],
        'low': [149.0, 139.0, 378.0],
        'close': [154.0, 144.0, 383.0],
        'volume': [1000000, 900000, 800000],
        'adj_close': [154.0, 144.0, 383.0]
    })
```

**Usage**:
```python
def test_with_sample_data(sample_ohlcv_df):
    """Test using shared OHLCV fixture."""
    assert len(sample_ohlcv_df) == 3
    assert 'AAPL' in sample_ohlcv_df['ticker'].values
```

---

#### Temporary Directory Fixture

```python
import pytest
from pathlib import Path

@pytest.fixture
def temp_lake_dir(tmp_path):
    """Create temporary data lake directory."""
    lake_dir = tmp_path / "data" / "lake"
    lake_dir.mkdir(parents=True)

    # Create market subdirectories
    (lake_dir / 'us_equity').mkdir()
    (lake_dir / 'cn_ashare').mkdir()
    (lake_dir / 'hk_sg_equity').mkdir()

    return lake_dir
```

**Usage**:
```python
def test_with_temp_dir(temp_lake_dir):
    """Test using temporary directory."""
    assert temp_lake_dir.exists()
    assert (temp_lake_dir / 'us_equity').exists()
```

---

#### Mock Fetcher Fixture

```python
import pytest
from equity_lake.sources.base import MarketDataFetcher
import pandas as pd
from datetime import date

@pytest.fixture
def mock_fetcher():
    """Return mock fetcher with sample data."""
    class MockFetcher(MarketDataFetcher):
        def fetch(self, trading_date: date) -> pd.DataFrame:
            return pd.DataFrame({
                'ticker': ['TEST'],
                'date': [trading_date],
                'open': [100.0],
                'high': [105.0],
                'low': [99.0],
                'close': [104.0],
                'volume': [1000000]
            })

    return MockFetcher()
```

**Usage**:
```python
def test_with_mock_fetcher(mock_fetcher):
    """Test using mock fetcher."""
    df = mock_fetcher.fetch(date(2024, 12, 1))
    assert df['ticker'].iloc[0] == 'TEST'
```

---

### Parametrized Fixtures

**Purpose**: Run tests with multiple inputs

**Example**:
```python
@pytest.mark.parametrize(
    "market,expected_ticker",
    [
        ('us', 'AAPL'),
        ('cn', '000001'),
        ('hk_sg', '0700.HK'),
    ]
)
def test_market_specific_ticker(market, expected_ticker):
    """Test ticker format for each market."""
    assert is_valid_ticker_format(expected_ticker, market)
```

---

## Coverage Goals

### Coverage Configuration

**Target**: 90%+ line coverage

**Configuration** (`pyproject.toml`):
```toml
[tool.coverage.run]
source = ["src/equity_lake"]
omit = [
    "*/tests/*",
    "*/__init__.py",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
    "if TYPE_CHECKING:",
]
```

---

### Running Coverage

#### Generate Coverage Report
```bash
uv run pytest --cov=src/equity_lake --cov-report=html --cov-report=term
```

#### View HTML Report
```bash
open htmlcov/index.html
```

#### Minimum Coverage Threshold
```bash
uv run pytest --cov=src/equity_lake --cov-fail-under=90
```

**Output**:
```
Name                              Stmts   Miss  Cover   Missing
---------------------------------------------------------------
src/equity_lake/cli/daily.py         45      2    96%   23-24
src/equity_lake/ingestion/orchestrator.py  120     10    92%   45-50
src/equity_lake/storage/duckdb.py      80      5    94%   12-13
---------------------------------------------------------------
TOTAL                                245     17    93%
```

---

### Coverage Exclusions

**Rationale**:
- `__init__.py`: Minimal package initialization
- Debug/development code
- Abstract methods (implemented in subclasses)
- Error handling paths for external failures

**Example**:
```python
def __repr__(self):  # pragma: no cover
    return f"Fetcher(market={self.market})"

if __name__ == "__main__":  # pragma: no cover
    main()
```

---

## Integration vs. Unit Testing

### Unit Tests

**Focus**: Individual functions/classes in isolation

**Characteristics**:
- Fast execution (< 1 second each)
- No external dependencies
- Mock all external interactions
- Test logic paths and edge cases

**When to Use**:
- Testing business logic
- Validating data transformations
- Testing error handling
- Edge case testing

**Example**:
```python
@pytest.mark.unit
def test_column_mapping():
    """Test Chinese to English column mapping."""
    df = pd.DataFrame({'开盘': [10.0], '收盘': [10.5]})
    mapped = map_chinese_columns(df)

    assert 'open' in mapped.columns
    assert 'close' in mapped.columns
```

---

### Integration Tests

**Focus**: End-to-end workflows

**Characteristics**:
- Slower execution (seconds to minutes)
- Real filesystem operations
- Real Parquet file operations
- Test component interactions

**When to Use**:
- Testing complete workflows
- Validating file I/O
- Testing database operations
- Verifying integration points

**Example**:
```python
@pytest.mark.integration
def test_full_ingestion_to_parquet(temp_lake_dir):
    """Test complete workflow: fetch → validate → write."""
    # Fetch
    fetcher = USEquityFetcher()
    df = fetcher.fetch(date(2024, 12, 1))

    # Validate
    assert validate_schema(df, 'us')

    # Write
    path = temp_lake_dir / 'test.parquet'
    df.to_parquet(path)

    # Verify
    assert path.exists()
    result = pd.read_parquet(path)
    assert len(result) == len(df)
```

---

## Best Practices

### AAA Pattern (Arrange-Act-Assert)

**Structure**:
1. **Arrange**: Set up test data and mocks
2. **Act**: Call the function being tested
3. **Assert**: Verify expected outcomes

**Example**:
```python
def test_fetcher_returns_data():
    # Arrange
    fetcher = USEquityFetcher()
    test_date = date(2024, 12, 1)

    with patch('yfinance.download') as mock_download:
        mock_download.return_value = sample_data

        # Act
        result = fetcher.fetch(test_date)

        # Assert
        assert not result.empty
        assert result['date'].iloc[0] == test_date
```

---

### Test Isolation

**Principle**: Tests should not depend on each other

**Good**:
```python
def test_fetcher_with_valid_date():
    fetcher = USEquityFetcher()
    df = fetcher.fetch(date(2024, 12, 1))
    assert not df.empty

def test_fetcher_with_another_date():
    fetcher = USEquityFetcher()
    df = fetcher.fetch(date(2024, 12, 2))
    assert not df.empty
```

**Bad** (tests depend on order):
```python
def test_write_data():
    global_df = pd.DataFrame({'a': [1]})
    global_df.to_parquet('test.parquet')

def test_read_data():
    # Assumes test_write_data ran first
    df = pd.read_parquet('test.parquet')
    assert len(df) == 1
```

---

### Descriptive Test Names

**Pattern**: `test_<function>_<condition>_<expected_result>`

**Examples**:
```python
def test_fetcher_with_valid_date_returns_dataframe():
    """Test that fetcher returns DataFrame for valid date."""
    pass

def test_fetcher_with_future_date_raises_error():
    """Test that fetcher raises error for future date."""
    pass

def test_validator_with_missing_columns_returns_false():
    """Test that validator returns False for missing columns."""
    pass
```

---

### Edge Case Testing

**Common Edge Cases**:
- Empty DataFrames
- Missing columns
- Null values
- Future dates
- Weekends/holidays
- Network failures
- API errors

**Example**:
```python
@pytest.mark.unit
def test_validator_with_empty_dataframe():
    """Test validator rejects empty DataFrame."""
    df = pd.DataFrame()
    assert not validate_schema(df, 'test')

def test_validator_with_null_prices():
    """Test validator rejects null prices."""
    df = pd.DataFrame({
        'ticker': ['AAPL'],
        'date': [date(2024, 12, 1)],
        'open': [None],  # Null price
        'high': [155.0],
        'low': [149.0],
        'close': [154.0],
        'volume': [1000000]
    })
    assert not validate_schema(df, 'test')
```

---

## Test Data Generation

### Test Data Generator Script

**Location**: `src/equity_lake/devtools/test_data.py`

**Purpose**: Generate realistic test data for development

**Usage**:
```bash
make generate-test-data
# or
uv run equity bootstrap sample
```

**Output**:
- Creates sample Parquet files in `data/lake/`
- Generates OHLCV data with realistic distributions
- Useful for testing queries and analytics

---

### Manual Test Data

**Creating Test Data**:
```python
import pandas as pd
from datetime import date, timedelta

def generate_test_data(start_date, days=10):
    """Generate test data for multiple days."""
    dates = [start_date + timedelta(days=i) for i in range(days)]
    tickers = ['AAPL', 'GOOGL', 'MSFT']

    data = []
    for date in dates:
        for ticker in tickers:
            base_price = 150.0 if ticker == 'AAPL' else 140.0
            data.append({
                'ticker': ticker,
                'date': date,
                'open': base_price,
                'high': base_price + 5,
                'low': base_price - 5,
                'close': base_price + 2,
                'volume': 1000000
            })

    return pd.DataFrame(data)

df = generate_test_data(date(2024, 12, 1), days=5)
df.to_parquet('test_data.parquet')
```

---

## Performance Testing

### Benchmarking

**Example**:
```python
import pytest
import time

@pytest.mark.slow
def test_query_performance():
    """Test query performance with large dataset."""
    # Setup: Load large dataset
    df = pd.read_parquet('data/lake/01_bronze/market_data/us_equity/date=*/*.parquet')

    # Benchmark query
    start_time = time.time()
    result = df[df['date'] >= '2024-01-01']
    duration = time.time() - start_time

    # Assert query completes in reasonable time
    assert duration < 5.0  # Should complete in < 5 seconds
```

---

## Continuous Integration

### GitHub Actions Workflow

**Location**: `.github/workflows/test.yml`

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

      - name: Install dependencies
        run: uv sync

      - name: Run tests
        run: uv run pytest --cov=src/equity_lake --cov-report=xml

      - name: Upload coverage
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
```

---

## Summary

**Testing Framework**: pytest with coverage
**Test Organization**: Unit tests (fast, mocked) vs Integration tests (slow, real operations)
**Mocking**: Mock external APIs (yfinance, akshare), filesystem, database
**Fixtures**: Shared OHLCV data, temporary directories, mock fetchers
**Coverage**: 90%+ target, exclude `__init__.py`, debug code
**Best Practices**: AAA pattern, test isolation, descriptive names, edge cases
**Test Data**: Generator script for development, manual creation for specific cases
**CI/CD**: GitHub Actions for automated testing
