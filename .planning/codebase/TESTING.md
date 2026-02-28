# TESTING.md - Testing Strategy & Framework

## Overview

The equity_lake project uses pytest as the primary testing framework with a focus on unit tests, integration tests, and code coverage.

## Test Framework

### pytest

**Version**: >=8.0.0

**Configuration**: `pyproject.toml [tool.pytest.ini_options]`

**Key Settings**:
- Test path: `tests/`
- Python path: `src/`
- Markers: `slow`, `integration`, `unit`
- Output:Verbose with `-v` flag

## Test Structure

### Directory Layout

```
tests/
├── __init__.py
├── conftest.py                 # Shared fixtures and configuration
│
├── fixtures/                   # Test data and mock files
│   ├── sample_data.parquet
│   └── ticker_lists.yaml
│
├── unit/                       # Unit tests (fast, isolated)
│   ├── test_ingestion_orchestrator.py
│   ├── test_macro_sources.py
│   └── test_ml_jobs.py
│
└── integration/                # Integration tests (slower, real resources)
    ├── test_duckdb_queries.py
    └── test_pipeline_orchestrator.py
```

### Test Categories

#### Unit Tests (`@pytest.mark.unit`)

**Purpose**: Test individual functions and classes in isolation

**Characteristics**:
- Fast execution (< 1 second per test)
- No external dependencies (mocked)
- Test specific functionality

**Examples**:
- Test fetcher class methods
- Test data transformation logic
- Test validation functions

**Location**: `tests/unit/`

#### Integration Tests (`@pytest.mark.integration`)

**Purpose**: Test multiple components working together

**Characteristics**:
- Slower execution (1-10 seconds per test)
- May use real resources (test database, test files)
- Test workflows and pipelines

**Examples**:
- Test full ingestion pipeline
- Test database queries
- Test file I/O operations

**Location**: `tests/integration/`

#### Slow Tests (`@pytest.mark.slow`)

**Purpose**: Tests that are time-consuming

**Characteristics**:
- Very slow execution (> 10 seconds)
- May download data or perform heavy computation
- Disabled by default in CI

**Examples**:
- Test large dataset processing
- Test S3 sync operations
- Test ML model training

**Location**: Can be in either `unit/` or `integration/`

## Fixtures

### Shared Fixtures (`conftest.py`)

**Location**: `tests/conftest.py`

**Available Fixtures**:

#### Data Fixtures

```python
@pytest.fixture
def sample_ohlcv_data() -> pd.DataFrame:
    """Create sample OHLCV data for testing."""
    # Returns DataFrame with standard columns
    ...

@pytest.fixture
def sample_multi_day_data() -> pd.DataFrame:
    """Create sample multi-day OHLCV data."""
    # Returns multi-day DataFrame
    ...
```

#### Directory Fixtures

```python
@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create temporary data directory structure."""
    # Creates temp market directories
    ...

@pytest.fixture
def temp_partitioned_parquet(temp_data_dir, sample_multi_day_data) -> Path:
    """Create temporary Hive-partitioned Parquet files."""
    # Creates test Parquet files
    ...
```

#### Mock Fixtures

```python
@pytest.fixture
def mock_yfinance_download(monkeypatch):
    """Mock yfinance.download function."""
    # Mocks yfinance API calls
    ...

@pytest.fixture
def mock_akshare_stock_zh_a_hist(monkeypatch):
    """Mock akshare.stock_zh_a_hist function."""
    # Mocks akshare API calls
    ...
```

#### Database Fixtures

```python
@pytest.fixture
def temp_duckdb_db(tmp_path: Path) -> str:
    """Create temporary DuckDB database."""
    # Returns path to temp database
    ...
```

### Using Fixtures in Tests

```python
def test_fetch_with_sample_data(sample_ohlcv_data):
    """Test using sample data fixture."""
    assert not sample_ohlcv_data.empty
    assert "ticker" in sample_ohlcv_data.columns

def test_with_database(temp_duckdb_db):
    """Test using database fixture."""
    con = duckdb.connect(temp_duckdb_db)
    # Test database operations
```

## Mocking

### Mocking External APIs

**Library**: pytest-mock (built-in monkeypatch)

**Example**:

```python
def test_yfinance_fetcher(monkeypatch):
    """Test US equity fetcher with mocked yfinance."""
    def mock_download(*args, **kwargs):
        return pd.DataFrame({
            'Open': [150.0],
            'Close': [152.0],
            'Volume': [1000000]
        })

    # Mock the yfinance.download function
    monkeypatch.setattr('yfinance.download', mock_download)

    # Now test the fetcher
    fetcher = USEquityFetcher()
    result = fetcher.fetch(date(2024, 1, 1))
    assert result['close'].iloc[0] == 152.0
```

### Mocking File System

```python
def test_parquet_writer(tmp_path):
    """Test Parquet writer with temporary directory."""
    # Use tmp_path fixture for temp directory
    test_file = tmp_path / "test.parquet"

    # Write test data
    df = pd.DataFrame({'a': [1, 2, 3]})
    df.to_parquet(test_file)

    # Verify file exists
    assert test_file.exists()
```

### Mocking Environment Variables

```python
def test_config_with_env_vars(monkeypatch):
    """Test config loading with custom env vars."""
    monkeypatch.setenv('DATA_DIR', '/tmp/test_data')
    monkeypatch.setenv('LOG_LEVEL', 'DEBUG')

    config = load_config()
    assert config.data_dir == Path('/tmp/test_data')
    assert config.log_level == 'DEBUG'
```

## Test Markers

### Running Marked Tests

```bash
# Run only unit tests
pytest -m unit

# Run only integration tests
pytest -m integration

# Exclude slow tests
pytest -m "not slow"

# Run multiple markers
pytest -m "unit and not slow"
```

### Custom Markers

**Defined in**: `conftest.py` and `pyproject.toml`

```python
# In conftest.py
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks tests as integration")
    config.addinivalue_line("markers", "unit: marks tests as unit")
```

**Usage in tests**:

```python
@pytest.mark.slow
def test_large_dataset_processing():
    ...

@pytest.mark.integration
def test_full_pipeline():
    ...

@pytest.mark.unit
def test_specific_function():
    ...
```

## Code Coverage

### Configuration

**Tool**: pytest-cov

**Settings**: `pyproject.toml [tool.coverage]`

**Coverage Target**: 80% minimum

### Running Coverage

```bash
# Run tests with coverage
make test
# or
pytest --cov=src/equity_lake --cov-report=html --cov-report=term

# Generate HTML report
pytest --cov=src/equity_lake --cov-report=html
open htmlcov/index.html
```

### Coverage Exclusions

**In `pyproject.toml`**:

```toml
[tool.coverage.run]
omit = [
    "*/tests/*",
    "*/test_*",
    "setup.py",
]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "if self.debug:",
    "raise AssertionError",
    "raise NotImplementedError",
    "if 0:",
    "if __name__ == .__main__.:",
    "class .*\\bProtocol\\):",
    "@(abc\\.)?abstractmethod",
]
```

## Test Writing Guidelines

### Test Structure (AAA Pattern)

```python
def test_fetcher_returns_dataframe():
    """Test that fetcher returns valid DataFrame."""
    # Arrange - Set up test data and conditions
    fetcher = USEquityFetcher()
    test_date = date(2024, 1, 1)

    # Act - Execute the function under test
    result = fetcher.fetch(test_date)

    # Assert - Verify expected outcomes
    assert isinstance(result, pd.DataFrame)
    assert not result.empty
    assert "ticker" in result.columns
```

### Test Naming

- **Descriptive**: `test_<what>_<expected>`
- **Present tense**: `test_returns_dataframe` not `test_returned_dataframe`
- **Specific**: `test_fetcher_with_invalid_date_raises_error`

```python
# Good
def test_fetcher_with_empty_ticker_list_returns_empty_dataframe():
    ...

# Bad
def test_fetcher():
    ...
```

### Assertions

```python
# Specific assertions
assert result['close'].iloc[0] == 152.0
assert len(result) == 100

# Exception testing
with pytest.raises(ValueError):
    fetcher.fetch(date(2024, 1, 1))

# Approximate equality
assert result == pytest.approx(expected, rel=1e-3)
```

## Running Tests

### Make Commands

```bash
# Run all tests
make test

# Run unit tests only
make test-unit

# Run integration tests only
make test-integration

# Run slow tests
make test-slow
```

### pytest Commands

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_fetcher.py

# Run specific test function
pytest tests/unit/test_fetcher.py::test_fetch_returns_dataframe

# Stop on first failure
pytest -x

# Run until first failure, then drop into debugger
pytest -x --pdb

# Show print statements
pytest -s
```

## Continuous Integration

### CI Pipeline

```yaml
# Example CI configuration
test:
  script:
    - uv sync --group dev
    - uv run pytest -v --cov=src/equity_lake
    - uv run ruff check src/ tests/
    - uv run mypy src/equity_lake
```

### Pre-commit Hooks

**Tool**: pre-commit

**Configuration**: `.pre-commit-config.yaml`

```yaml
repos:
  - repo: local
    hooks:
      - id: pytest
        name: Run tests
        entry: uv run pytest -x
        language: system
        pass_filenames: false
```

## Test Data Management

### Fixtures Directory

**Location**: `tests/fixtures/`

**Purpose**: Store test data files

**Examples**:
- Sample Parquet files
- Test ticker lists
- Configuration files

### Test Data Generation

**Module**: `src/equity_lake/devtools/test_data.py`

**Usage**:

```python
from equity_lake.devtools.test_data import generate_ohlcv_data

def test_with_generated_data():
    """Test using generated test data."""
    df = generate_ohlcv_data(
        tickers=['AAPL', 'GOOGL'],
        dates=[date(2024, 1, 1), date(2024, 1, 2)],
        num_rows=100
    )
    assert len(df) == 100
```

## Troubleshooting Tests

### Common Issues

#### Import Errors

```bash
# Error: ModuleNotFoundError
# Solution: Ensure PYTHONPATH includes src/
pytest --pythonpath=src/
```

#### Fixture Not Found

```bash
# Error: fixture 'xyz' not found
# Solution: Ensure fixture is in conftest.py or imported
```

#### Database Lock

```bash
# Error: duckdb.IOError: Database is locked
# Solution: Use temp database fixture for each test
@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test.duckdb")
```

### Debugging Tests

```bash
# Drop into debugger on failure
pytest --pdb

# Drop into debugger on specific test
pytest --pdb tests/unit/test_fetcher.py::test_specific_function

# Show detailed output
pytest -vv -s
```

## Best Practices

1. **Isolation**: Each test should be independent
2. **Speed**: Keep unit tests fast (< 1 second)
3. **Clarity**: Test names should describe what they test
4. **Maintenance**: Update tests when code changes
5. **Coverage**: Aim for 80%+ code coverage
6. **Mocking**: Mock external dependencies
7. **Fixtures**: Use fixtures for common test data
8. **Cleanup**: Use `teardown` or context managers for resources

## Test Documentation

### Docstrings in Tests

```python
def test_fetcher_with_invalid_date_returns_empty_dataframe():
    """Test that fetcher returns empty DataFrame for invalid date.

    This test verifies that when a non-trading date (weekend or holiday)
    is provided, the fetcher gracefully returns an empty DataFrame
    rather than raising an error.

    Regression test for: https://github.com/user/repo/issues/123
    """
    ...
```

### Comments in Tests

```python
def test_complex_calculation():
    """Test complex calculation with multiple steps."""
    # Step 1: Prepare input data
    data = ...

    # Step 2: Apply calculation
    result = complex_function(data)

    # Step 3: Verify intermediate results
    assert result['intermediate'] == expected

    # Step 4: Verify final output
    assert result['final'] == expected_final
```
