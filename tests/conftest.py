"""
Pytest configuration and shared fixtures for Equity EOD Data Pipeline tests.
"""

import shutil
import sys
import tempfile
from collections.abc import Generator
from datetime import date
from importlib import import_module
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import polars as pl
import pytest

_TEST_RUNTIME_ROOT: Path | None = None


def pytest_configure(config: pytest.Config) -> None:
    """Redirect runtime data, model, and log paths before tests are imported."""
    global _TEST_RUNTIME_ROOT

    from equity_lake.core import paths  # type: ignore[import-untyped]

    # Catalog entries derive checkout-relative paths at import time. Load those
    # pure definitions before redirecting the runtime roots.
    import_module("equity_lake.catalog.datasets")

    workerinput = getattr(config, "workerinput", {})
    worker_id = workerinput.get("workerid", "main") if isinstance(workerinput, dict) else "main"
    _TEST_RUNTIME_ROOT = Path(tempfile.mkdtemp(prefix=f"equity-lake-pytest-{worker_id}-", dir="/tmp"))
    data_root = _TEST_RUNTIME_ROOT / "data"
    model_root = data_root / "models"
    logs_root = _TEST_RUNTIME_ROOT / "logs"

    project_root = paths.PROJECT_ROOT
    source_data_root = paths.DATA_DIR
    source_model_root = paths.MODELS_DIR
    source_logs_root = paths.LOGS_DIR
    catalog_path = project_root / "data" / "catalog.jsonl"

    def isolate_runtime_path(value: Path) -> Path:
        """Move runtime paths out of the checkout while preserving the tracked catalog."""
        if value == catalog_path:
            return value
        if value.is_relative_to(source_model_root):
            return model_root / value.relative_to(source_model_root)
        if value.is_relative_to(source_data_root):
            return data_root / value.relative_to(source_data_root)
        if value.is_relative_to(source_logs_root):
            return logs_root / value.relative_to(source_logs_root)
        return value

    # Update core constants and already-imported aliases/derived runtime paths.
    # The scan is restricted to application and test modules; PROJECT_ROOT,
    # CONFIG_DIR, and the tracked catalog remain anchored to the checkout.
    for module in tuple(sys.modules.values()):
        module_name = getattr(module, "__name__", "")
        if not module_name.startswith(("equity_lake", "tests.", "test_")):
            continue
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        for name, value in tuple(namespace.items()):
            if isinstance(value, Path):
                isolated = isolate_runtime_path(value)
                if isolated != value:
                    namespace[name] = isolated


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove only the temporary root created for this pytest process."""
    del config
    if _TEST_RUNTIME_ROOT is not None:
        shutil.rmtree(_TEST_RUNTIME_ROOT, ignore_errors=True)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Require explicit opt-in before collecting tests marked as network-dependent."""
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Allow tests marked network (may contact external services).",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-mark tests by directory and deselect network tests without opt-in."""
    for item in items:
        if "integration/" in item.nodeid:
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.slow)
        elif "unit/" in item.nodeid:
            item.add_marker(pytest.mark.unit)

    if config.getoption("--run-network"):
        return

    selected = []
    deselected = []
    for item in items:
        if item.get_closest_marker("network") is not None:
            deselected.append(item)
        else:
            selected.append(item)

    if deselected:
        items[:] = selected
        config.hook.pytest_deselected(items=deselected)


# =============================================================================
# Fixtures for Test Data
# =============================================================================


@pytest.fixture
def sample_ohlcv_data() -> pl.DataFrame:
    """Create sample OHLCV data for testing."""
    return pl.DataFrame(
        {
            "ticker": ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA"],
            "mic": ["XNAS"] * 5,
            "date": [date(2024, 1, 1)] * 5,
            "open": [150.0, 380.0, 140.0, 180.0, 250.0],
            "high": [155.0, 385.0, 145.0, 185.0, 255.0],
            "low": [148.0, 378.0, 138.0, 178.0, 248.0],
            "close": [152.0, 382.0, 142.0, 182.0, 252.0],
            "volume": [1000000, 800000, 1200000, 1500000, 900000],
            "adj_close": [152.0, 382.0, 142.0, 182.0, 252.0],
        },
        schema={
            "ticker": pl.Utf8,
            "mic": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "adj_close": pl.Float64,
        },
    )


@pytest.fixture
def sample_multi_day_data() -> pl.DataFrame:
    """Create sample multi-day OHLCV data."""
    tickers = ["AAPL", "MSFT", "GOOGL"]
    dates = [date(2024, 1, i) for i in range(1, 6)]

    data = []
    for ticker in tickers:
        for i, dt in enumerate(dates):
            base_price = 150.0 if ticker == "AAPL" else (380.0 if ticker == "MSFT" else 140.0)
            price_variation = i * 2.0

            data.append(
                {
                    "ticker": ticker,
                    "date": dt,
                    "open": base_price + price_variation,
                    "high": base_price + price_variation + 5,
                    "low": base_price + price_variation - 2,
                    "close": base_price + price_variation + 3,
                    "volume": 1000000 + i * 10000,
                    "adj_close": base_price + price_variation + 3,
                }
            )

    return pl.DataFrame(
        data,
        schema={
            "ticker": pl.Utf8,
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
            "adj_close": pl.Float64,
        },
    )


@pytest.fixture
def sample_us_tickers() -> list[str]:
    """Sample US ticker list for testing."""
    return ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "META", "NVDA", "JPM"]


@pytest.fixture
def sample_large_ticker_list() -> list[str]:
    """Large ticker list for testing batch functionality."""
    # Generate 1200 tickers to test batching (default batch size is 500)
    base_tickers = [
        "AAPL",
        "MSFT",
        "GOOGL",
        "AMZN",
        "TSLA",
        "META",
        "NVDA",
        "JPM",
        "V",
        "JNJ",
        "WMT",
        "PG",
        "UNH",
        "HD",
        "CVX",
        "MRK",
    ]
    return base_tickers * 75  # 1200 tickers total


@pytest.fixture
def sample_cn_tickers() -> list[str]:
    """Sample China ticker list for testing."""
    return ["000001", "000002", "600000", "600036", "601398"]


# =============================================================================
# Fixtures for Temporary Directories
# =============================================================================


@pytest.fixture
def temp_data_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Create temporary data directory structure."""
    # Create market directories
    for market in ["us_equity", "cn_ashare", "hk_sg_equity"]:
        market_dir = tmp_path / "lake" / market
        market_dir.mkdir(parents=True, exist_ok=True)

    yield tmp_path / "lake"


@pytest.fixture
def temp_partitioned_parquet(temp_data_dir: Path, sample_multi_day_data: pl.DataFrame) -> Path:
    """Create temporary Hive-partitioned Parquet files."""
    us_dir = temp_data_dir / "us_equity"

    for dt in sample_multi_day_data.get_column("date").unique().sort():
        partition = sample_multi_day_data.filter(pl.col("date") == dt)
        partition_dir = us_dir / f"date={dt}"
        partition_dir.mkdir(parents=True, exist_ok=True)
        partition.write_parquet(partition_dir / f"{dt}.parquet")

    return us_dir


# =============================================================================
# Fixtures for Environment Configuration
# =============================================================================


@pytest.fixture
def mock_env_vars(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set mock environment variables."""
    env_vars = {
        "DATA_DIR": "/tmp/test_data",
        "LOG_LEVEL": "DEBUG",
    }

    for key, value in env_vars.items():
        monkeypatch.setenv(key, value)

    return env_vars


# =============================================================================
# Fixtures for Mocking External Services
# =============================================================================


@pytest.fixture
def mock_yfinance_download(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock yfinance.download function."""

    def mock_download(*args: object, **kwargs: object) -> pd.DataFrame:
        # Return empty DataFrame with expected structure
        dates = pd.date_range("2024-01-01", periods=1)
        df = pd.DataFrame(
            index=dates,
            data={
                "Open": [150.0],
                "High": [155.0],
                "Low": [148.0],
                "Close": [152.0],
                "Adj Close": [152.0],
                "Volume": [1000000],
            },
        )
        return df

    monkeypatch.setattr("yfinance.download", mock_download)


@pytest.fixture
def mock_akshare_stock_zh_a_hist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock akshare.stock_zh_a_hist function."""

    def mock_hist(*args: object, **kwargs: object) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "日期": ["2024-01-01"],
                "开盘": [10.5],
                "最高": [10.8],
                "最低": [10.4],
                "收盘": [10.7],
                "成交量": [1000000],
            }
        )

    monkeypatch.setattr("akshare.stock_zh_a_hist", mock_hist)


@pytest.fixture
def mock_akshare_stock_info_a_code_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock akshare.stock_info_a_code_name function for stock list."""

    def mock_info() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "code": ["000001", "000002", "600000", "600036"],
                "name": ["平安银行", "万科A", "浦发银行", "招商银行"],
            }
        )

    monkeypatch.setattr("akshare.stock_info_a_code_name", mock_info)


@pytest.fixture
def mock_efinance_get_quote_history(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock efinance.stock.get_quote_history function."""

    def mock_history(stock_code: str, beg: str, end: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "股票代码": [stock_code],
                "日期": [beg],
                "开盘": [10.5],
                "收盘": [10.7],
                "最高": [10.8],
                "最低": [10.4],
                "成交量": [1000000],
                "成交额": [10700000],
            }
        )

    monkeypatch.setattr("efinance.stock.get_quote_history", mock_history)


@pytest.fixture
def mock_efinance_get_realtime_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock efinance.stock.get_realtime_quotes function for stock list."""

    def mock_quotes() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "股票代码": ["000001", "000002", "600000", "600036"],
                "股票名称": ["平安银行", "万科A", "浦发银行", "招商银行"],
                "最新价": [10.5, 8.3, 12.7, 35.2],
            }
        )

    monkeypatch.setattr("efinance.stock.get_realtime_quotes", mock_quotes)


@pytest.fixture
def mock_efinance_module(monkeypatch: pytest.MonkeyPatch) -> Generator[MagicMock, None, None]:
    """Mock entire efinance module for tests where efinance is not installed."""
    import sys
    from unittest.mock import MagicMock

    # Create a mock efinance module
    mock_efinance = MagicMock()
    mock_efinance.stock = MagicMock()

    # Only install if not already present
    if "efinance" not in sys.modules or sys.modules["efinance"] is None:
        sys.modules["efinance"] = mock_efinance

    yield mock_efinance

    # Clean up
    if "efinance" in sys.modules:
        del sys.modules["efinance"]


# =============================================================================
# Logging Fixtures
# =============================================================================


@pytest.fixture
def capture_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Capture log messages during tests."""
    caplog.set_level("DEBUG")
    return caplog


# =============================================================================
# Database Fixtures
# =============================================================================


@pytest.fixture
def temp_duckdb_db(tmp_path: Path) -> str:
    """Create temporary DuckDB database."""
    db_path = tmp_path / "test.duckdb"
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
    return len(list(directory.rglob("*.parquet")))


# =============================================================================
# HTTP Mocking Fixtures
# =============================================================================


@pytest.fixture
def mock_httpx_client() -> MagicMock:
    """A MagicMock that mimics an httpx.Client context manager.

    Pre-wires ``__enter__`` (returns self) and ``__exit__`` (returns False) so
    ``with httpx.Client() as c: c.get(...)`` works in tests. Set ``.get`` /
    ``.post`` return values or side effects on the returned object, then patch
    it into the target module, e.g.::

        def test_x(mock_httpx_client):
            mock_httpx_client.get.return_value = mock_response
            with patch("equity_lake.sources.reddit.httpx.Client", return_value=mock_httpx_client):
                ...
    """
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client
