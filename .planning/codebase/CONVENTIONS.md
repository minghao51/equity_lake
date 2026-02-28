# CONVENTIONS.md - Code Conventions & Standards

## Overview

This document describes the coding conventions, patterns, and standards used in the equity_lake codebase.

## Code Style

### Formatting

**Tool**: [ruff](https://docs.astral.sh/ruff/) (replaces flake8, black, isort)

**Configuration**: `pyproject.toml [tool.ruff]`

**Key Settings**:
- Line length: 88 characters
- Indent: 4 spaces
- Quotes: Double quotes (`"`)
- Import style: isort-compatible

**Formatting Commands**:
```bash
# Format code
make format
# or
uv run ruff format src/ tests/

# Auto-fix linting issues
uv run ruff check --fix src/ tests/
```

### Type Hints

**Tool**: mypy

**Configuration**: `pyproject.toml [tool.mypy]`

**Standards**:
- **Required**: All functions must have type hints
- **Strict mode**: Enabled (`disallow_untyped_defs = true`)
- **Return types**: Always specified
- **Parameter types**: Always specified

**Example**:
```python
from datetime import date
import pandas as pd

def fetch_market_data(
    trading_date: date,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Fetch market data for given date.

    Args:
        trading_date: Date to fetch data for
        tickers: Optional list of tickers (uses default if None)

    Returns:
        DataFrame with OHLCV data
    """
    ...
```

**Special Cases**:
- Use `|` for unions (Python 3.10+ style): `str | None` not `Optional[str]`
- Use `list[str]` not `List[str]` (PEP 585)
- Use `dict[str, int]` not `Dict[str, int]`

### Naming Conventions

#### Variables and Functions

**Pattern**: `snake_case`

```python
trading_date = date.today()
market_tickers = ["AAPL", "GOOGL"]
def fetch_market_data() -> pd.DataFrame:
    ...
```

#### Classes

**Pattern**: `PascalCase`

```python
class USEquityFetcher:
    ...

class BaseMarketDataFetcher:
    ...
```

#### Constants

**Pattern**: `UPPERCASE_WITH_UNDERSCORES`

```python
STANDARD_COLUMNS = ["ticker", "date", "open", "high", "low", "close", "volume"]
LAKE_DIR = Path("data/lake")
MAX_RETRIES = 3
```

#### Private Members

**Pattern**: Prefix with `_`

```python
def _retry_on_failure(func):
    """Private function with retry logic."""
    ...

class Fetcher:
    def __init__(self):
        self._cache = {}  # Private attribute
```

#### Module Names

**Pattern**: `lowercase_with_underscores`

```python
# Good
ingestion/orchestrator.py
storage/duckdb.py

# Bad
ingestion/Orchestrator.py
storage/DuckDB.py
```

## Docstrings

### Standard

**Format**: Google style docstrings (preferred)

```python
def fetch_market_data(
    trading_date: date,
    tickers: list[str] | None = None,
) -> pd.DataFrame:
    """Fetch market data for a given trading date.

    This function retrieves end-of-day market data from the configured
    data source for the specified date and tickers.

    Args:
        trading_date: The trading date to fetch data for.
        tickers: Optional list of ticker symbols. If None, uses the
            default ticker list from configuration.

    Returns:
        A DataFrame containing OHLCV data with columns:
        ticker, date, open, high, low, close, volume, adj_close

    Raises:
        ConnectionError: If unable to connect to data source
        ValueError: If no data is returned for the given date

    Examples:
        >>> fetch_market_data(date(2024, 1, 1))
           ticker       date   open   high    low  close  volume  adj_close
        0   AAPL 2024-01-01  150.0  155.0  148.0  152.0  1000000     152.0
    """
    ...
```

### Class Docstrings

```python
class USEquityFetcher(BaseMarketDataFetcher):
    """Fetcher for US equity market data using yfinance.

    This fetcher retrieves end-of-day OHLCV data for US equities
    from Yahoo Finance via the yfinance library.

    Attributes:
        retry_attempts: Number of retry attempts for failed requests
        retry_delay: Base delay between retries in seconds

    Example:
        >>> fetcher = USEquityFetcher()
        >>> data = fetcher.fetch(date(2024, 1, 1))
    """
    ...
```

## Import Conventions

### Import Order

```python
# 1. Standard library imports
from datetime import date, timedelta
from pathlib import Path
import logging

# 2. Third-party imports
import pandas as pd
import yfinance as yf
from structlog import get_logger

# 3. Local imports
from equity_lake.core.logging import get_logger
from equity_lake.ingestion.sources.base import BaseMarketDataFetcher
```

### Import Aliases

```python
# Standard aliases
import pandas as pd
import numpy as np
import yfinance as yf
from structlog import get_logger
```

### Relative vs Absolute Imports

**Within package**: Use relative imports

```python
# In ingestion/sources/cn.py
from .base import BaseMarketDataFetcher
from ..storage import ParquetStorage
```

**From outside package**: Use absolute imports

```python
# In tests/
from equity_lake.ingestion.sources.cn import CNAshareFetcher
```

## Error Handling

### Exception Hierarchy

```python
# Custom exceptions (in core/exceptions.py if needed)
class EquityLakeError(Exception):
    """Base exception for equity_lake."""

class DataFetchError(EquityLakeError):
    """Raised when data fetching fails."""

class ValidationError(EquityLakeError):
    """Raised when data validation fails."""
```

### Error Handling Patterns

#### With Retry Logic

```python
from equity_lake.core.logging import get_logger

logger = get_logger(__name__)

def fetch_with_retry(url: str, max_retries: int = 3) -> dict:
    """Fetch data with exponential backoff retry."""
    for attempt in range(max_retries):
        try:
            response = requests.get(url)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            if attempt == max_retries - 1:
                logger.error("Failed after retries", url=url, error=str(e))
                raise
            delay = 2 ** attempt
            logger.warning("Retry attempt", attempt=attempt, delay=delay)
            time.sleep(delay)
```

#### Graceful Degradation

```python
def fetch_multiple_markets(date: date) -> dict[str, pd.DataFrame]:
    """Fetch from all markets, continue on failures."""
    results = {}
    markets = ["us", "cn", "hk_sg"]

    for market in markets:
        try:
            results[market] = fetch_market(market, date)
        except Exception as e:
            logger.error("Market fetch failed", market=market, error=str(e))
            results[market] = None
            # Continue with other markets

    return results
```

#### Validation Errors

```python
def validate_dataframe(df: pd.DataFrame, market: str) -> None:
    """Validate DataFrame schema.

    Raises:
        ValidationError: If schema is invalid
    """
    required_columns = ["ticker", "date", "open", "high", "low", "close", "volume"]
    missing = [col for col in required_columns if col not in df.columns]

    if missing:
        raise ValidationError(
            f"Missing required columns for {market}: {missing}"
        )
```

## Logging

### Structured Logging

**Tool**: structlog

**Configuration**: `core/logging.py`

**Usage**:

```python
from structlog import get_logger

logger = get_logger(__name__)

# Structured logging with context
logger.info("Starting data fetch",
            market="us",
            date="2024-01-01",
            ticker_count=10)

logger.error("Fetch failed",
             market="cn",
             ticker="000001",
             error=str(e),
             retry_attempt=2)
```

### Log Levels

- **DEBUG**: Detailed diagnostic information
- **INFO**: General informational messages (default)
- **WARNING**: Unexpected but recoverable issues
- **ERROR**: Errors that prevent operation but don't crash
- **CRITICAL**: Critical errors that crash the application

### Logging Patterns

```python
# Entry/Exit logging
def process_data(date: date) -> pd.DataFrame:
    logger.info("Processing data", date=date.isoformat())
    try:
        # ... processing ...
        logger.info("Processing complete",
                   date=date.isoformat(),
                   rows_processed=len(df))
        return df
    except Exception as e:
        logger.error("Processing failed",
                    date=date.isoformat(),
                    error=str(e))
        raise
```

## Testing Conventions

### Test Structure

```python
# tests/unit/test_fetcher.py
import pytest
from datetime import date
from equity_lake.ingestion.sources.us import USEquityFetcher

class TestUSEquityFetcher:
    """Test suite for US equity fetcher."""

    @pytest.fixture
    def fetcher(self):
        """Create fetcher instance."""
        return USEquityFetcher()

    def test_fetch_returns_dataframe(self, fetcher):
        """Test that fetch returns a DataFrame."""
        result = fetcher.fetch(date(2024, 1, 1))
        assert isinstance(result, pd.DataFrame)
        assert not result.empty

    def test_fetch_has_required_columns(self, fetcher):
        """Test that result has required columns."""
        result = fetcher.fetch(date(2024, 1, 1))
        assert "ticker" in result.columns
        assert "close" in result.columns
```

### Test Naming

- **Test files**: `test_<module>.py`
- **Test classes**: `Test<ClassName>`
- **Test functions**: `test_<what>_<expected>`

```python
def test_fetch_with_invalid_date_raises_error():
    ...

def test_dataframe_has_correct_schema():
    ...
```

## Code Organization

### Module Structure

```python
# 1. Module docstring
"""Module description."""

# 2. Imports
from datetime import date
import pandas as pd

# 3. Constants
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3

# 4. Global exceptions
class CustomError(Exception):
    pass

# 5. Classes
class MyClass:
    pass

# 6. Functions
def my_function():
    pass

# 7. Main guard
if __name__ == "__main__":
    ...
```

### Class Organization

```python
class MyClass:
    """Class docstring."""

    # 1. Class attributes
    CLASS_ATTR = "value"

    # 2. __init__
    def __init__(self, param: str):
        """Constructor docstring."""
        self.param = param

    # 3. Properties
    @property
    def computed_value(self) -> int:
        """Property docstring."""
        return len(self.param)

    # 4. Public methods
    def public_method(self) -> None:
        """Method docstring."""
        pass

    # 5. Private methods
    def _private_method(self) -> None:
        """Private method docstring."""
        pass

    # 6. Special methods
    def __repr__(self) -> str:
        return f"MyClass(param={self.param!r})"
```

## Design Patterns

### Context Managers

```python
from contextlib import contextmanager

@contextmanager
def database_connection(db_path: str):
    """Context manager for database connections."""
    conn = duckdb.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
```

### Decorators

```python
def retry_on_failure(max_retries: int = 3):
    """Decorator for retry logic."""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    time.sleep(2 ** attempt)
        return wrapper
    return decorator
```

## Code Quality Standards

### Before Committing

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

### CI Checks

All code must pass:
1. **Ruff linting**: No unfixable issues
2. **mypy type checking**: Strict mode
3. **pytest tests**: All tests pass
4. **Coverage**: Minimum 80% (target)

## Performance Conventions

### DataFrame Operations

```python
# Good: Vectorized operations
df['returns'] = df['close'].pct_change()

# Bad: Row-by-row iteration
for i in range(len(df)):
    df.loc[i, 'returns'] = (df.loc[i, 'close'] / df.loc[i-1, 'close']) - 1
```

### Memory Management

```python
# Process in chunks for large datasets
for chunk in pd.read_parquet('large_file.parquet', chunksize=10000):
    process(chunk)
```

## Security Conventions

### Credential Management

```python
# Good: Use environment variables
import os
api_key = os.getenv('API_KEY')

# Bad: Hardcoded credentials
api_key = 'sk_live_abc123'
```

### Input Validation

```python
def fetch_data(ticker: str) -> pd.DataFrame:
    # Validate input
    if not ticker or not isinstance(ticker, str):
        raise ValueError("Invalid ticker")
    ...
```

## Documentation Standards

### README Standards

- Overview
- Installation
- Quick start
- Usage examples
- Configuration
- Troubleshooting
- Contributing

### Docstring Coverage

- All public functions: Required
- All classes: Required
- Private functions: Optional but recommended
