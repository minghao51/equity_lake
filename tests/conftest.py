"""
Pytest configuration and shared fixtures for Equity EOD Data Pipeline tests.
"""

import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Generator

import pandas as pd
import pytest
import pyarrow as pa
import pyarrow.parquet as pq

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent))


# =============================================================================
# Pytest Configuration
# =============================================================================

def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests"
    )


# =============================================================================
# Fixtures for Test Data
# =============================================================================

@pytest.fixture
def sample_ohlcv_data() -> pd.DataFrame:
    """Create sample OHLCV data for testing."""
    data = {
        'ticker': ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA'],
        'date': [date(2024, 1, 1)] * 5,
        'open': [150.0, 380.0, 140.0, 180.0, 250.0],
        'high': [155.0, 385.0, 145.0, 185.0, 255.0],
        'low': [148.0, 378.0, 138.0, 178.0, 248.0],
        'close': [152.0, 382.0, 142.0, 182.0, 252.0],
        'volume': [1000000, 800000, 1200000, 1500000, 900000],
        'adj_close': [152.0, 382.0, 142.0, 182.0, 252.0]
    }
    return pd.DataFrame(data)


@pytest.fixture
def sample_multi_day_data() -> pd.DataFrame:
    """Create sample multi-day OHLCV data."""
    tickers = ['AAPL', 'MSFT', 'GOOGL']
    dates = [date(2024, 1, i) for i in range(1, 6)]  # 5 days

    data = []
    for ticker in tickers:
        for i, dt in enumerate(dates):
            base_price = 150.0 if ticker == 'AAPL' else (380.0 if ticker == 'MSFT' else 140.0)
            price_variation = i * 2.0

            data.append({
                'ticker': ticker,
                'date': dt,
                'open': base_price + price_variation,
                'high': base_price + price_variation + 5,
                'low': base_price + price_variation - 2,
                'close': base_price + price_variation + 3,
                'volume': 1000000 + i * 10000,
                'adj_close': base_price + price_variation + 3
            })

    return pd.DataFrame(data)


# =============================================================================
# Fixtures for Temporary Directories
# =============================================================================

@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create temporary data directory structure."""
    # Create market directories
    for market in ['us_equity', 'cn_ashare', 'hk_sg_equity']:
        market_dir = tmp_path / 'lake' / market
        market_dir.mkdir(parents=True, exist_ok=True)

    yield tmp_path / 'lake'


@pytest.fixture
def temp_partitioned_parquet(temp_data_dir: Path, sample_multi_day_data: pd.DataFrame) -> Path:
    """Create temporary Hive-partitioned Parquet files."""
    us_dir = temp_data_dir / 'us_equity'

    # Group by date and create partitions
    for dt, group in sample_multi_day_data.groupby('date'):
        partition_dir = us_dir / f'date={dt}'
        partition_dir.mkdir(parents=True, exist_ok=True)

        parquet_file = partition_dir / f'{dt}.parquet'
        group.to_parquet(parquet_file, index=False)

    return us_dir


# =============================================================================
# Fixtures for Environment Configuration
# =============================================================================

@pytest.fixture
def mock_env_vars(monkeypatch) -> dict:
    """Set mock environment variables."""
    env_vars = {
        'AWS_ACCESS_KEY_ID': 'test_key_id',
        'AWS_SECRET_ACCESS_KEY': 'test_secret_key',
        'S3_BUCKET': 's3://test-bucket/us_equity/',
        'DATA_DIR': '/tmp/test_data',
        'LOG_LEVEL': 'DEBUG',
    }

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    return env_vars


# =============================================================================
# Fixtures for Mocking External Services
# =============================================================================

@pytest.fixture
def mock_yfinance_download(monkeypatch):
    """Mock yfinance.download function."""
    def mock_download(*args, **kwargs):
        # Return empty DataFrame with expected structure
        dates = pd.date_range('2024-01-01', periods=1)
        df = pd.DataFrame(
            index=dates,
            data={
                'Open': [150.0],
                'High': [155.0],
                'Low': [148.0],
                'Close': [152.0],
                'Adj Close': [152.0],
                'Volume': [1000000]
            }
        )
        return df

    monkeypatch.setattr('yfinance.download', mock_download)


@pytest.fixture
def mock_akshare_stock_zh_a_hist(monkeypatch):
    """Mock akshare.stock_zh_a_hist function."""
    def mock_hist(*args, **kwargs):
        return pd.DataFrame({
            '日期': ['2024-01-01'],
            '开盘': [10.5],
            '最高': [10.8],
            '最低': [10.4],
            '收盘': [10.7],
            '成交量': [1000000],
        })

    monkeypatch.setattr('akshare.stock_zh_a_hist', mock_hist)


# =============================================================================
# Logging Fixtures
# =============================================================================

@pytest.fixture
def capture_logs(caplog):
    """Capture log messages during tests."""
    caplog.set_level('DEBUG')
    return caplog


# =============================================================================
# Database Fixtures
# =============================================================================

@pytest.fixture
def temp_duckdb_db(tmp_path: Path) -> str:
    """Create temporary DuckDB database."""
    db_path = tmp_path / 'test.duckdb'
    return str(db_path)


# =============================================================================
# Helper Functions
# =============================================================================

def create_test_parquet_file(path: Path, data: pd.DataFrame) -> None:
    """Helper to create test Parquet file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data.to_parquet(path, index=False)


def count_parquet_files(directory: Path) -> int:
    """Helper to count Parquet files in directory."""
    return len(list(directory.rglob('*.parquet')))
