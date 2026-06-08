# Conventions

**Last Updated**: 2026-03-05
**Project**: Equity EOD Data Pipeline

## Code Style

### Linting & Formatting

**Tool**: Ruff (ultra-fast Python linter and formatter)

**Configuration** (`pyproject.toml`):
```toml
[tool.ruff]
line-length = 88
target-version = "py311"

select = [
    "E",   # pycodestyle errors
    "F",   # pyflakes
    "UP",  # pyupgrade
    "B",   # flake8-bugbear
    "SIM", # flake8-simplify
    "I",   # isort
]

ignore = [
    "E501",  # Line too long (handled by formatter)
]
```

**Formatting Rules**:
- **Line Length**: 88 characters (Black-compatible)
- **Quotes**: Double quotes for strings (`"hello"`, not `'hello'`)
- **Indentation**: 4 spaces (no tabs)
- **Imports**: Sorted and grouped (isort integration)
- **Trailing Commas**: Multi-line lists/dicts

**Enforcement**:
```bash
# Check linting
make lint
# or
uv run ruff check src/ tests/

# Auto-fix issues
uv run ruff check --fix src/ tests/

# Format code
make format
# or
uv run ruff format src/ tests/
```

---

### Type Hints

**Tool**: mypy (static type checker)

**Configuration** (`pyproject.toml`):
```toml
[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

**Type Annotation Standards**:

#### Functions
```python
from typing import Optional, List, Dict
from datetime import date
import pandas as pd

def fetch_market_data(
    trading_date: date,
    markets: Optional[List[str]] = None
) -> Dict[str, pd.DataFrame]:
    """
    Fetch market data for specified date.

    Args:
        trading_date: Date to fetch data for
        markets: List of market codes ('us', 'cn', 'hk_sg')

    Returns:
        Dictionary mapping market codes to DataFrames
    """
    if markets is None:
        markets = ['us', 'cn', 'hk_sg']

    results = {}
    for market in markets:
        results[market] = _fetch_single_market(market, trading_date)

    return results
```

#### Classes
```python
from abc import ABC, abstractmethod

class MarketDataFetcher(ABC):
    """Abstract base class for market data fetchers."""

    @abstractmethod
    def fetch(self, trading_date: date) -> pd.DataFrame:
        """
        Fetch EOD data for specific date.

        Args:
            trading_date: Date to fetch data for

        Returns:
            DataFrame with OHLCV data
        """
        pass
```

#### Type Aliases
```python
from typing import TypedDict

class OHLCVData(TypedDict):
    """Type hint for OHLCV data structure."""
    ticker: str
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: Optional[float]

def process_data(data: List[OHLCVData]) -> pd.DataFrame:
    """Process typed data into DataFrame."""
    pass
```

**Best Practices**:
- All functions must have type hints
- Use `Optional` for nullable arguments
- Use `List`, `Dict`, `TypedDict` for complex types
- Return types should always be specified
- Use `# type: ignore` sparingly with explanations

---

## Naming Conventions

### Modules and Packages

**Pattern**: `snake_case`

**Examples**:
- `orchestrator.py` ✓
- `yfinance_source.py` ✓
- `market_data_fetcher.py` ✓
- `Orchestrator.py` ✗
- `yfinanceSource.py` ✗

**Rationale**: Follows PEP 8, consistent with Python stdlib

---

### Classes

**Pattern**: `PascalCase`

**Examples**:
- `MarketDataFetcher` ✓
- `USEquityFetcher` ✓
- `CNAshareFetcher` ✓
- `EquityDataDB` ✓
- `S3Syncer` ✓
- `market_data_fetcher` ✗
- `marketDataFetcher` ✗

**Rationale**: Follows PEP 8, distinguishes classes from functions

---

### Functions and Methods

**Pattern**: `snake_case`

**Examples**:
- `fetch_market_data()` ✓
- `write_to_partitioned_parquet()` ✓
- `validate_schema()` ✓
- `_retry_on_failure()` ✓ (private)
- `fetchMarketData()` ✗
- `Fetch_Market_Data()` ✗

**Verb-Noun Pattern** (for functions that do something):
- `fetch_data()` (not `data()`)
- `validate_schema()` (not `schema()`)
- `write_parquet()` (not `parquet()`)

---

### Constants

**Pattern**: `UPPER_SNAKE_CASE`

**Examples**:
```python
STANDARD_COLUMNS = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
US_EQUITY_DIR = Path('data/lake/us_equity')
MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30
```

**Locations**:
- Centralized in `src/equity_lake/core/constants.py`
- Or at module level for module-specific constants

---

### Private Members

**Pattern**: `_leading_underscore`

**Examples**:
- `_retry_on_failure()` (private method)
- `_standardize_columns()` (internal function)
- `_MAX_WORKERS` (module-private constant)

**Rationale**: Indicates internal use, not part of public API

---

### Variables

**Pattern**: `snake_case`

**Examples**:
```python
trading_date = date(2024, 12, 1)
market_data = fetcher.fetch(trading_date)
partition_dir = f"data/lake/{market}/date={trading_date}/"
```

**Descriptive Names**:
```python
# Good
trading_date = date.today()
market_fetcher = USEquityFetcher()
partition_path = Path(f"data/lake/{market}/date={date}/")

# Bad
d = date.today()
f = USEquityFetcher()
p = Path(f"data/lake/{m}/date={d}/")
```

---

## Error Handling

### Structured Logging

**Tool**: structlog (structured JSON logging)

**Configuration** (`src/equity_lake/core/logging.py`):
```python
import structlog

def setup_logging() -> structlog.stdlib.BoundLogger:
    """Configure structured logging."""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return structlog.get_logger()
```

**Usage Pattern**:
```python
from equity_lake.core.logging import setup_logging

logger = setup_logging()

def fetch_market_data(trading_date: date):
    logger.info("Fetching market data", date=str(trading_date))

    try:
        data = _fetch_from_api(trading_date)
        logger.info("Successfully fetched data", row_count=len(data))
        return data
    except Exception as e:
        logger.error("Failed to fetch data", error=str(e))
        raise
```

**Log Levels**:
- **DEBUG**: Detailed diagnostic information
- **INFO**: General informational messages (default)
- **WARNING**: Something unexpected but not critical
- **ERROR**: Error occurred but operation can continue
- **CRITICAL**: Critical failure, operation cannot continue

---

### Exception Handling Patterns

#### Try-Except-Else-Finally
```python
def write_to_parquet(df: pd.DataFrame, path: Path):
    """Write DataFrame to Parquet with error handling."""
    try:
        # Validate input
        if df.empty:
            raise ValueError("Cannot write empty DataFrame")

        # Write to Parquet
        df.to_parquet(path, compression='snappy')

    except ValueError as e:
        logger.error("Validation failed", error=str(e), path=str(path))
        raise

    except Exception as e:
        logger.error("Failed to write Parquet", error=str(e), path=str(path))
        raise

    else:
        logger.info("Successfully wrote Parquet", path=str(path), rows=len(df))

    finally:
        # Cleanup (if needed)
        pass
```

#### Retry with Exponential Backoff
```python
import time
from functools import wraps

def _retry_on_failure(func, max_retries: int = 3, base_delay: float = 1.0):
    """Retry function with exponential backoff."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error("Max retries exceeded", error=str(e))
                    raise

                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Attempt failed, retrying",
                    attempt=attempt + 1,
                    delay=delay,
                    error=str(e)
                )
                time.sleep(delay)
    return wrapper
```

#### Graceful Degradation
```python
def fetch_all_markets(trading_date: date):
    """Fetch all markets, continue if one fails."""
    results = {}
    markets = ['us', 'cn', 'hk_sg']

    for market in markets:
        try:
            fetcher = FetcherFactory.create_fetcher(market)
            results[market] = fetcher.fetch(trading_date)
            logger.info("Successfully fetched", market=market)
        except Exception as e:
            logger.error("Failed to fetch market", market=market, error=str(e))
            # Continue processing other markets
            results[market] = None

    # Filter out failures
    successful = {k: v for k, v in results.items() if v is not None}
    return successful
```

---

### Custom Exceptions

**Pattern**: Create domain-specific exceptions

**Example**:
```python
class EquityLakeError(Exception):
    """Base exception for equity-lake package."""
    pass

class DataValidationError(EquityLakeError):
    """Raised when data validation fails."""
    pass

class SchemaMismatchError(DataValidationError):
    """Raised when schema doesn't match expected format."""
    pass

class FetchError(EquityLakeError):
    """Raised when data fetching fails."""
    pass

# Usage
def validate_schema(df: pd.DataFrame, market: str):
    """Validate DataFrame schema."""
    required_columns = STANDARD_COLUMNS
    missing = set(required_columns) - set(df.columns)

    if missing:
        raise SchemaMismatchError(
            f"Missing columns for {market}: {missing}"
        )
```

---

## Logging Patterns

### Structured Logging

**Key Features**:
- JSON format for machine parsing
- Contextual information (correlation IDs, timing)
- Consistent field names

**Example**:
```python
logger.info(
    "Data ingestion completed",
    market="us",
    date="2024-12-01",
    row_count=7500,
    duration_seconds=45.2,
    status="success"
)
```

---

### Correlation IDs

**Purpose**: Track requests across multiple components

**Implementation**:
```python
import uuid

from contextvars import ContextVar

correlation_id: ContextVar[str] = ContextVar('correlation_id')

def setup_correlation_id():
    """Generate or retrieve correlation ID."""
    cid = correlation_id.get(None)
    if cid is None:
        cid = str(uuid.uuid4())
        correlation_id.set(cid)
    return cid

# Usage in logger
logger.info(
    "Processing request",
    correlation_id=setup_correlation_id(),
    operation="fetch_data"
)
```

---

### Timing Context Manager

**Purpose**: Measure and log operation duration

**Implementation**:
```python
from contextlib import contextmanager
import time

@contextmanager
def timed_operation(operation_name: str):
    """Context manager for timing operations."""
    start_time = time.time()
    logger.info("Operation started", operation=operation_name)

    try:
        yield

    finally:
        duration = time.time() - start_time
        logger.info(
            "Operation completed",
            operation=operation_name,
            duration_seconds=round(duration, 2)
        )

# Usage
with timed_operation("S3 sync"):
    s3_syncer.sync_with_s5cmd(bucket, destination)
# Logs: "Operation completed", duration_seconds=123.45
```

---

### Decorator for Timing

**Alternative**: Decorator pattern for function timing

```python
import functools
import time

def timed(func):
    """Decorator to time function execution."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        logger.info("Function started", function=func.__name__)

        try:
            result = func(*args, **kwargs)
            return result

        finally:
            duration = time.time() - start
            logger.info(
                "Function completed",
                function=func.__name__,
                duration_seconds=round(duration, 2)
            )

    return wrapper

# Usage
@timed
def fetch_market_data(trading_date: date):
    # Function execution is timed automatically
    pass
```

---

## Docstring Conventions

### Google Style Docstrings

**Pattern**: Google-style docstrings with type hints

**Example**:
```python
def fetch_market_data(
    trading_date: date,
    markets: Optional[List[str]] = None
) -> Dict[str, pd.DataFrame]:
    """
    Fetch market data for specified date.

    This function orchestrates fetching EOD data from multiple markets
    (US, China, Hong Kong, Singapore) using market-specific fetchers.
    Each market is fetched independently with retry logic.

    Args:
        trading_date: Date to fetch data for.
        markets: Optional list of market codes. Defaults to all markets.
            Valid codes: 'us', 'cn', 'hk_sg'.

    Returns:
        Dictionary mapping market codes to DataFrames with OHLCV data.
        Failed markets are not included in the result.

    Raises:
        ValueError: If invalid market code provided.
        FetchError: If all markets fail to fetch.

    Example:
        >>> data = fetch_market_data(date(2024, 12, 1), ['us', 'cn'])
        >>> us_data = data['us']
        >>> print(us_data['close'].mean())
        154.23
    """
    pass
```

**Sections**:
1. **Summary**: One-line description
2. **Detailed Description**: Multi-line explanation (optional)
3. **Args**: Function parameters with types
4. **Returns**: Return value description
5. **Raises**: Exceptions that may be raised
6. **Example**: Usage example (optional)

---

### Class Docstrings

**Example**:
```python
class USEquityFetcher(MarketDataFetcher):
    """
    Fetch US equity market data using yfinance API.

    This fetcher retrieves OHLCV data for US stocks from Yahoo Finance.
    It supports batch downloads and implements rate limiting to avoid
    API throttling.

    Attributes:
        ticker_list: List of US stock tickers to fetch.
        retry_attempts: Number of retry attempts for failed requests.

    Example:
        >>> fetcher = USEquityFetcher()
        >>> df = fetcher.fetch(date(2024, 12, 1))
        >>> print(df.head())
           ticker       date   open   high    low  close  volume
        0   AAPL 2024-12-01  150.0  155.0  149.0  154.0  1000000
    """

    def __init__(self, ticker_list: Optional[List[str]] = None):
        """Initialize US equity fetcher.

        Args:
            ticker_list: Optional list of tickers. Defaults to all S&P 500.
        """
        pass
```

---

## Import Organization

### Import Order

**Standard** (enforced by ruff):
1. Standard library imports
2. Third-party imports
3. Local application imports

**Example**:
```python
# 1. Standard library
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Dict

# 2. Third-party
import pandas as pd
import yfinance as yf
import structlog

# 3. Local
from equity_lake.core.constants import STANDARD_COLUMNS
from equity_lake.sources.base import MarketDataFetcher
from equity_lake.storage.parquet import write_to_partitioned_parquet
```

---

### Import Aliases

**Common Aliases**:
```python
import pandas as pd
import numpy as np
import yfinance as yf
import duckdb
import structlog
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple
```

**Rationale**: Community conventions for popular libraries

---

### Relative vs. Absolute Imports

**Rule**: Use absolute imports for clarity

**Good**:
```python
from equity_lake.core.constants import STANDARD_COLUMNS
from equity_lake.sources.base import MarketDataFetcher
```

**Avoid**:
```python
from ..core.constants import STANDARD_COLUMNS
from .base import MarketDataFetcher
```

**Exception**: Relative imports acceptable in package `__init__.py` files

---

## Constants and Shared Utilities

### Centralized Constants

**Location**: `src/equity_lake/core/constants.py`

**Examples**:
```python
# Standard OHLCV schema
STANDARD_COLUMNS = [
    'ticker',
    'date',
    'open',
    'high',
    'low',
    'close',
    'volume',
    'adj_close'  # Optional
]

# Directory paths
LAKE_DIR = Path('data/lake')
US_EQUITY_DIR = LAKE_DIR / 'us_equity'
CN_ASHARE_DIR = LAKE_DIR / 'cn_ashare'
HK_SG_EQUITY_DIR = LAKE_DIR / 'hk_sg_equity'

# Market codes
MARKETS = ['us', 'cn', 'hk_sg']

# Retry configuration
MAX_RETRIES = 3
BASE_DELAY = 1.0
```

---

### Path Utilities

**Location**: `src/equity_lake/core/runtime.py`

**Examples**:
```python
from pathlib import Path

def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.parent.parent

def get_lake_dir(market: str) -> Path:
    """Get data lake directory for market."""
    root = get_project_root()
    return root / 'data' / 'lake' / market

def get_partition_path(market: str, trading_date: date) -> Path:
    """Get partition path for date."""
    lake_dir = get_lake_dir(market)
    return lake_dir / f"date={trading_date.strftime('%Y-%m-%d')}"
```

---

### Validation Utilities

**Location**: `src/equity_lake/core/validation.py`

**Examples**:
```python
import pandas as pd
from datetime import date

def validate_ohlcv_schema(df: pd.DataFrame, market: str) -> bool:
    """Validate DataFrame has OHLCV schema."""
    required = {'ticker', 'date', 'open', 'high', 'low', 'close', 'volume'}

    if not required.issubset(df.columns):
        missing = required - set(df.columns)
        raise ValueError(f"Missing columns for {market}: {missing}")

    # Validate data types
    if not pd.api.types.is_datetime64_any_dtype(df['date']):
        raise ValueError(f"Date column must be datetime type for {market}")

    # Validate no null prices
    if df[['open', 'high', 'low', 'close']].isnull().any().any():
        raise ValueError(f"Null prices found for {market}")

    return True

def validate_trading_date(trading_date: date) -> bool:
    """Validate trading date is not in the future."""
    if trading_date > date.today():
        raise ValueError(f"Trading date cannot be in the future: {trading_date}")
    return True
```

---

## Code Patterns

### Context Managers

**Pattern**: Use context managers for resource management

**Example**:
```python
from contextlib import contextmanager

@contextmanager
def duckdb_connection(database_path: str = ':memory:'):
    """Context manager for DuckDB connections."""
    import duckdb

    con = duckdb.connect(database_path)
    try:
        yield con
    finally:
        con.close()

# Usage
with duckdb_connection() as con:
    df = con.execute("SELECT * FROM equity_all").df()
```

---

### Factory Pattern

**Pattern**: Factory functions for object creation

**Example**:
```python
class FetcherFactory:
    """Factory for creating market-specific fetchers."""

    _fetchers = {
        'us': USEquityFetcher,
        'cn': CNAshareFetcher,
        'hk_sg': HKSGEquityFetcher,
    }

    @classmethod
    def create_fetcher(cls, market: str) -> MarketDataFetcher:
        """Create fetcher for market."""
        fetcher_class = cls._fetchers.get(market)

        if not fetcher_class:
            raise ValueError(f"Unknown market: {market}")

        return fetcher_class()

# Usage
fetcher = FetcherFactory.create_fetcher('us')
df = fetcher.fetch(date.today())
```

---

### Decorator Pattern

**Pattern**: Decorators for cross-cutting concerns

**Examples**:
```python
# Timing decorator
@timed
def fetch_market_data(trading_date: date):
    pass

# Retry decorator
@retry(max_attempts=3, base_delay=1.0)
def fetch_from_api(url: str):
    pass

# Logging decorator
@log_inputs_outputs
def process_data(df: pd.DataFrame):
    pass
```

---

## Testing Conventions

### Test Structure

**Pattern**: Arrange-Act-Assert (AAA)

**Example**:
```python
def test_us_fetcher_returns_valid_dataframe():
    """Test that USEquityFetcher returns valid DataFrame."""
    # Arrange
    fetcher = USEquityFetcher()
    test_date = date(2024, 12, 1)

    # Act
    df = fetcher.fetch(test_date)

    # Assert
    assert not df.empty
    assert all(col in df.columns for col in STANDARD_COLUMNS)
    assert df['date'].iloc[0] == test_date
```

---

### Test Fixtures

**Location**: `tests/conftest.py`

**Examples**:
```python
import pytest
from datetime import date
import pandas as pd

@pytest.fixture
def sample_ohlcv_df():
    """Return sample OHLCV DataFrame."""
    return pd.DataFrame({
        'ticker': ['AAPL', 'GOOGL'],
        'date': [date(2024, 12, 1), date(2024, 12, 1)],
        'open': [150.0, 140.0],
        'high': [155.0, 145.0],
        'low': [149.0, 139.0],
        'close': [154.0, 144.0],
        'volume': [1000000, 900000]
    })

@pytest.fixture
def temp_lake_dir(tmp_path):
    """Return temporary data lake directory."""
    lake_dir = tmp_path / "data" / "lake"
    lake_dir.mkdir(parents=True)
    return lake_dir
```

---

### Test Markers

**Markers**: Categorize tests by type

```python
import pytest

@pytest.mark.unit
def test_fetcher_initialization():
    """Unit test: No external dependencies."""
    pass

@pytest.mark.integration
def test_full_ingestion_workflow():
    """Integration test: Requires data lake."""
    pass

@pytest.mark.slow
def test_large_dataset_processing():
    """Slow test: Takes > 1 second."""
    pass
```

**Usage**:
```bash
# Run only unit tests
uv run pytest -m unit

# Skip slow tests
uv run pytest -m "not slow"

# Run integration tests
uv run pytest -m integration
```

---

## Summary

**Code Style**:
- Ruff for linting and formatting
- 88-character line length
- Double quotes for strings

**Type Hints**:
- Mypy strict mode
- All functions must have type hints
- Use `Optional`, `List`, `Dict`, `TypedDict`

**Naming**:
- Modules: `snake_case`
- Classes: `PascalCase`
- Functions: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private: `_leading_underscore`

**Error Handling**:
- Structured logging with structlog
- Try-except-else-finally pattern
- Retry with exponential backoff
- Graceful degradation

**Logging**:
- JSON format with contextual fields
- Correlation IDs for request tracking
- Timing decorators and context managers

**Documentation**:
- Google-style docstrings
- Type hints in docstrings
- Usage examples

**Testing**:
- AAA pattern (Arrange-Act-Assert)
- Shared fixtures in `conftest.py`
- Test markers (unit, integration, slow)
- Mock external APIs

**Constants**:
- Centralized in `core/` modules
- Use type aliases for complex types
- Path utilities for consistent paths
