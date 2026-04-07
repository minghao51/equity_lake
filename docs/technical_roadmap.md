# Equity Lake - Technical Roadmap

## Executive Summary

**Equity Lake** is a mature, local-first equity EOD data pipeline that has evolved into a comprehensive financial data platform. Currently at **v0.4.0**, the project already implements S3 bootstrap, multi-market daily ingestion (US, CN, HK, SG), DuckDB querying, ML pipelines, backtesting, and signal generation. This roadmap focuses on enhancing the platform's **configurability**, **extensibility**, and **data management** capabilities to become the definitive one-stop solution for free financial data ingestion.

---

## Current Project State

### ✅ What's Already Implemented

| Component | Status | Implementation |
|-----------|--------|----------------|
| **S3 Bootstrap** | ✅ Complete | One-time sync from S3 Parquet to local storage |
| **Multi-Market Ingestion** | ✅ Complete | US (yfinance), CN (akshare/efinance), HK/SG (yfinance) |
| **Storage Layer** | ✅ Complete | Hive-partitioned Parquet with DuckDB query engine |
| **CLI Tools** | ✅ Complete | 12 CLI entrypoints (daily, sync, query, pipeline, monitor, etc.) |
| **ML Pipeline** | ✅ Complete | XGBoost forecasting, 40+ technical indicators |
| **Backtesting** | ✅ Complete | vectorbt integration, strategy testing |
| **Signal Scanner** | ✅ Complete | Buy/sell/hold signals, multiple strategies |
| **Sentiment Analysis** | ✅ Complete | VADER sentiment, news integration |
| **Macro Indicators** | ✅ Complete | FRED API integration (fredapi) |
| **Monitoring** | ✅ Complete | Health checks, data freshness alerts |
| **Docker Deployment** | ✅ Complete | docker-compose.yml with cron scheduling |
| **Package Management** | ✅ Complete | uv for fast dependency management |

### Tech Stack Summary

```
┌─────────────────────────────────────────────────────────────────┐
│                    EQUITY LAKE v0.4.0                           │
├─────────────────────────────────────────────────────────────────┤
│  Package Manager:  uv (Rust-based, 10-100x faster than pip)    │
│  Language:         Python 3.12+                                 │
│  Query Engine:     DuckDB                                       │
│  Storage:          Hive-partitioned Parquet                     │
│  Config:           Pydantic + YAML + .env                       │
│  Logging:          structlog                                    │
├─────────────────────────────────────────────────────────────────┤
│  Data Sources:                                                   │
│  • US Equities:    yfinance (free)                             │
│  • China A-shares: akshare + efinance (free)                   │
│  • HK/SG:          yfinance (free)                             │
│  • Macro:          fredapi (FRED - free)                       │
│  • News:           Web scraping + VADER sentiment              │
├─────────────────────────────────────────────────────────────────┤
│  CLI Commands (12 entrypoints):                                 │
│  equity-daily, equity-sync, equity-query, equity-pipeline,     │
│  equity-monitor, equity-backfill, equity-macro,                │
│  equity-price-forecast, equity-backtest, equity-news,          │
│  equity-sentiment, equity-signal                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## Vision: One-Stop Data Ingestion Platform

### Goal Statement

Transform Equity Lake into the **definitive open-source platform for financial data ingestion** with:
1. **Easily Configurable Settings** - YAML-based, hot-reloadable configuration
2. **Slot-in Data Loaders** - Plugin architecture for new data sources
3. **Effortless Updates** - Smart incremental updates with conflict resolution
4. **Intuitive Data Management** - Next.js web dashboard + REST API

---

## Phase 1: Configuration System Enhancement

**Timeline**: Weeks 1-3
**Priority**: High
**Goal**: "Easily Configurable Settings"

### Libraries to Use

| Library | Purpose | Why |
|---------|---------|-----|
| **Pydantic v2** | Settings validation | Already in use, best-in-class for config validation |
| **Pydantic-settings** | Environment variables | Seamless .env integration with YAML |
| **watchdog** | File watching | Hot-reload configuration on file changes |
| **croniter** | Cron validation | Validate cron expressions in schedules |
| **PyYAML** | YAML parsing | Already in use |

### 1.1 Unified Configuration Architecture

Create a centralized, hierarchical configuration system that consolidates all settings.

```
config/
├── settings.yaml           # Main configuration (all settings)
├── sources/
│   ├── us_equity.yaml      # US market specific config
│   ├── cn_ashare.yaml      # China market specific config
│   ├── hk_sg.yaml          # HK/SG market specific config
│   └── macro.yaml          # FRED/macro indicators config
├── schedules/
│   ├── daily.yaml          # Daily ingestion schedules
│   └── backfill.yaml       # Backfill schedules
├── profiles/
│   ├── development.yaml    # Dev environment overrides
│   ├── production.yaml     # Production environment overrides
│   └── minimal.yaml        # Minimal subset for testing
└── secrets/
    └── .env.example        # Template for sensitive credentials
```

### 1.2 Implementation with Pydantic

```python
# src/equity_lake/config/settings.py
from pydantic import BaseModel, Field, validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Dict, List, Optional, Literal
from enum import Enum
from pathlib import Path
import yaml

class ScheduleType(str, Enum):
    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"

class RetryConfig(BaseModel):
    """Retry configuration for data fetching."""
    max_attempts: int = Field(default=3, ge=1, le=10)
    backoff: Literal["linear", "exponential", "constant"] = "exponential"
    base_delay: float = Field(default=5.0, ge=1.0, le=60.0)
    max_delay: float = Field(default=300.0, ge=10.0)

class RateLimitConfig(BaseModel):
    """Rate limiting configuration."""
    requests_per_minute: Optional[int] = None
    requests_per_day: Optional[int] = None
    requests_per_second: Optional[int] = None

class SymbolConfig(BaseModel):
    """Symbol list configuration."""
    type: Literal["file", "list", "dynamic"] = "file"
    path: Optional[str] = None  # For type="file"
    symbols: Optional[List[str]] = None  # For type="list"
    universe: Optional[str] = None  # For type="dynamic" (e.g., "sp500", "nasdaq100")

    @validator('path', always=True)
    def validate_path(cls, v, values):
        if values.get('type') == 'file' and not v:
            raise ValueError("path required when type='file'")
        return v

class DataSourceConfig(BaseModel):
    """Configuration for a data source."""
    enabled: bool = True
    provider: str
    schedule: str  # Cron expression
    symbols: SymbolConfig
    retry: RetryConfig = RetryConfig()
    rate_limit: Optional[RateLimitConfig] = None
    timeout: int = Field(default=30, ge=5, le=300)

    @validator('schedule')
    def validate_cron(cls, v):
        """Validate cron expression format."""
        from croniter import croniter
        if not croniter.is_valid(v):
            raise ValueError(f"Invalid cron expression: {v}")
        return v

class PipelineStageConfig(BaseModel):
    """Configuration for a pipeline stage."""
    enabled: bool = True
    parallel: bool = False
    max_workers: int = Field(default=3, ge=1, le=10)

class MLConfig(BaseModel):
    """ML pipeline configuration."""
    enabled: bool = True
    model: Literal["xgboost", "lightgbm", "random_forest"] = "xgboost"
    forecast_days: int = Field(default=5, ge=1, le=30)
    retrain_interval_days: int = Field(default=7, ge=1)

class MonitoringConfig(BaseModel):
    """Monitoring configuration."""
    enabled: bool = True
    data_stale_hours: int = Field(default=24, ge=1)
    quality_check: bool = True
    output_format: Literal["json", "yaml"] = "json"
    output_path: str = "./logs/monitoring/"

class StorageConfig(BaseModel):
    """Storage configuration."""
    base_path: str = Field(default="./data/lake")
    format: Literal["parquet"] = "parquet"
    compression: Literal["snappy", "gzip", "lz4", "zstd"] = "snappy"
    partition_columns: List[str] = ["date"]

class Settings(BaseModel):
    """Root settings model with full validation."""
    project: Dict[str, str]
    storage: StorageConfig = StorageConfig()
    data_sources: Dict[str, DataSourceConfig]
    pipeline: Dict[str, PipelineStageConfig]
    ml: MLConfig = MLConfig()
    monitoring: MonitoringConfig = MonitoringConfig()

    class Config:
        extra = "forbid"  # Reject unknown fields

class AppConfig(BaseSettings):
    """Application configuration with environment variable support."""
    model_config = SettingsConfigDict(
        env_prefix="EQUITY_LAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # Override with environment variables
    environment: Literal["development", "production", "testing"] = "development"
    config_path: str = "./config/settings.yaml"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # Secrets (from .env)
    fred_api_key: Optional[str] = None
    finnhub_api_key: Optional[str] = None
    alpha_vantage_api_key: Optional[str] = None

    def load_settings(self) -> Settings:
        """Load and validate settings from YAML."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        with open(config_file) as f:
            data = yaml.safe_load(f)

        return Settings(**data)

# Usage
config = AppConfig()
settings = config.load_settings()
```

### 1.3 Hot-Reloadable Configuration

```python
# src/equity_lake/core/config_watcher.py
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent
from pathlib import Path
from typing import Callable, Optional
import threading
import time
import logging

class ConfigWatcher:
    """
    Watch configuration files for changes and reload automatically.
    
    Usage:
        watcher = ConfigWatcher(
            config_path=Path("./config"),
            on_change=lambda path: print(f"Config changed: {path}")
        )
        watcher.start()
        # ... later
        watcher.stop()
    """

    def __init__(
        self,
        config_path: Path,
        on_change: Callable[[str], None],
        debounce_seconds: float = 1.0
    ):
        self.config_path = Path(config_path)
        self.on_change = on_change
        self.debounce_seconds = debounce_seconds
        self.observer = Observer()
        self._last_event_time: Dict[str, float] = {}
        self._running = False

    def start(self):
        """Start watching configuration files."""
        handler = _ConfigChangeHandler(self._handle_change)
        self.observer.schedule(
            handler,
            str(self.config_path.parent if self.config_path.is_file() else self.config_path),
            recursive=True
        )
        self.observer.start()
        self._running = True
        logging.info(f"Started watching config at {self.config_path}")

    def stop(self):
        """Stop watching configuration files."""
        self._running = False
        self.observer.stop()
        self.observer.join()
        logging.info("Stopped config watcher")

    def _handle_change(self, path: str):
        """Handle config file change with debouncing."""
        now = time.time()
        last_time = self._last_event_time.get(path, 0)

        if now - last_time < self.debounce_seconds:
            return  # Debounce

        self._last_event_time[path] = now
        logging.info(f"Config file changed: {path}")
        self.on_change(path)

class _ConfigChangeHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback

    def on_modified(self, event: FileModifiedEvent):
        if event.src_path.endswith(('.yaml', '.yml', '.env')):
            self.callback(event.src_path)

    def on_created(self, event):
        if event.src_path.endswith(('.yaml', '.yml')):
            self.callback(event.src_path)

# Integration with existing pipeline
class ConfigManager:
    """Manages configuration loading and hot-reloading."""

    def __init__(self, config_path: str = "./config/settings.yaml"):
        self.config_path = config_path
        self._settings: Optional[Settings] = None
        self._watcher: Optional[ConfigWatcher] = None
        self._lock = threading.Lock()

    @property
    def settings(self) -> Settings:
        """Get current settings, loading if necessary."""
        if self._settings is None:
            self._settings = self._load()
        return self._settings

    def _load(self) -> Settings:
        """Load settings from file."""
        app_config = AppConfig(config_path=self.config_path)
        return app_config.load_settings()

    def reload(self):
        """Force reload of settings."""
        with self._lock:
            self._settings = self._load()
            logging.info("Configuration reloaded")

    def start_watching(self):
        """Start watching for config changes."""
        if self._watcher is None:
            self._watcher = ConfigWatcher(
                config_path=Path(self.config_path).parent,
                on_change=lambda _: self.reload()
            )
            self._watcher.start()

    def stop_watching(self):
        """Stop watching for config changes."""
        if self._watcher:
            self._watcher.stop()
            self._watcher = None
```

### 1.4 YAML Configuration Example

```yaml
# config/settings.yaml
project:
  name: equity-lake
  version: 0.4.0
  environment: ${EQUITY_LAKE_ENVIRONMENT:development}

storage:
  base_path: ${EQUITY_LAKE_DATA:./data/lake}
  format: parquet
  compression: snappy
  partition_columns:
    - date

data_sources:
  us_equity:
    enabled: true
    provider: yfinance
    schedule: "0 18 * * 1-5"  # 6 PM ET weekdays
    symbols:
      type: file
      path: config/symbols/sp500.txt
    retry:
      max_attempts: 3
      backoff: exponential
      base_delay: 5.0
    rate_limit:
      requests_per_minute: 60
    timeout: 30

  cn_ashare:
    enabled: true
    provider: akshare
    schedule: "0 17 * * 1-5"  # 5 PM China time
    symbols:
      type: dynamic
      universe: all_a_shares
    retry:
      max_attempts: 3
      backoff: exponential
    rate_limit:
      requests_per_minute: 30

  hk_sg:
    enabled: true
    provider: yfinance
    markets: [HK, SG]
    schedule: "0 18 * * 1-5"
    symbols:
      type: file
      path: config/symbols/hk_sg.txt

  macro:
    enabled: true
    provider: fred
    schedule: "0 9 * * *"  # Daily at 9 AM
    symbols:
      type: list
      symbols: [DGS10, DGS2, DFF, UNRATE, CPIAUCSL]
    retry:
      max_attempts: 5

pipeline:
  ingestion:
    enabled: true
    parallel: true
    max_workers: 3
  features:
    enabled: true
    indicators:
      - rsi
      - macd
      - bollinger_bands
      - atr
      - sma_crossover
  ml:
    enabled: true
    model: xgboost
    forecast_days: 5

ml:
  enabled: true
  model: xgboost
  forecast_days: 5
  retrain_interval_days: 7

monitoring:
  enabled: true
  data_stale_hours: 24
  quality_check: true
  output_format: json
  output_path: ./logs/monitoring/
```

### 1.5 CLI Configuration Commands

```bash
# View current configuration
equity-config show

# Show specific section
equity-config show data_sources.us_equity

# Validate configuration
equity-config validate

# Set a configuration value
equity-config set data_sources.us_equity.enabled false

# Get a configuration value
equity-config get data_sources.us_equity.schedule

# Reload configuration (if hot-reload enabled)
equity-config reload

# Export configuration
equity-config export --format json --output config_backup.json

# Import configuration
equity-config import --file new_config.yaml
```

---

## Phase 2: Plugin Architecture for Data Loaders

**Timeline**: Weeks 4-6
**Priority**: High
**Goal**: "Slot-in Data Loaders"

### Libraries to Use

| Library | Purpose | Why |
|---------|---------|-----|
| **importlib.metadata** | Entry point discovery | Python standard library, no dependencies |
| **pluggy** | Plugin hooks (optional) | Used by pytest, battle-tested |
| **stevedore** | Plugin management | OpenStack's plugin framework |

### 2.1 Abstract Data Loader Interface

```python
# src/equity_lake/loaders/base.py
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from datetime import date
from pydantic import BaseModel, Field
import pandas as pd

class LoaderMetadata(BaseModel):
    """Metadata for a data loader."""
    name: str = Field(..., description="Unique loader identifier")
    version: str = Field(default="1.0.0", description="Loader version")
    description: str = Field(default="", description="Human-readable description")
    author: str = Field(default="Unknown", description="Loader author")
    supported_markets: List[str] = Field(default_factory=list, description="Supported market codes")
    supported_intervals: List[str] = Field(default=["1d"], description="Supported data intervals")
    rate_limit: Optional[Dict[str, int]] = Field(default=None, description="Rate limit configuration")
    requires_auth: bool = Field(default=False, description="Whether API key is required")
    data_types: List[str] = Field(default=["ohlcv"], description="Types of data provided")

class LoadResult(BaseModel):
    """Result of a data load operation."""
    success: bool
    data: Optional[pd.DataFrame] = None
    records_count: int = 0
    errors: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    loaded_at: str = Field(default_factory=lambda: pd.Timestamp.now().isoformat())

    class Config:
        arbitrary_types_allowed = True  # Allow pandas DataFrame

class BaseDataLoader(ABC):
    """
    Abstract base class for all data loaders.

    To create a new loader:
    1. Inherit from this class
    2. Set the `metadata` class attribute
    3. Implement all abstract methods
    4. Register via entry points in pyproject.toml

    Example:
        class MyLoader(BaseDataLoader):
            metadata = LoaderMetadata(name="my_loader", ...)

            def load(self, symbols, start_date, end_date, interval="1d"):
                # Fetch data and return LoadResult
                pass
    """

    metadata: LoaderMetadata  # Must be set in subclass

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self._validate_config()

    @abstractmethod
    def _validate_config(self) -> None:
        """
        Validate loader-specific configuration.

        Raises:
            ConfigValidationError: If configuration is invalid
        """
        pass

    @abstractmethod
    def load(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        interval: str = "1d"
    ) -> LoadResult:
        """
        Load data for specified symbols and date range.

        Args:
            symbols: List of ticker symbols
            start_date: Start date for data
            end_date: End date for data
            interval: Data interval (e.g., "1d", "1h", "5m")

        Returns:
            LoadResult with success status and data
        """
        pass

    @abstractmethod
    def get_available_symbols(self) -> List[str]:
        """
        Get list of available symbols from the source.

        Returns:
            List of symbol strings
        """
        pass

    @abstractmethod
    def validate_connection(self) -> bool:
        """
        Test connection to data source.

        Returns:
            True if connection successful, False otherwise
        """
        pass

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """
        Get real-time quote (optional, override if supported).

        Args:
            symbol: Ticker symbol

        Returns:
            Dictionary with quote data or None if not supported
        """
        return None

    def get_corporate_actions(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Get dividends, splits, etc. (optional).

        Args:
            symbol: Ticker symbol

        Returns:
            DataFrame with corporate actions or None
        """
        return None

    def normalize_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize data to standard schema.

        Standard columns: ticker, date, open, high, low, close, volume

        Args:
            df: Raw DataFrame from source

        Returns:
            Normalized DataFrame
        """
        # Default implementation - override for custom normalization
        required = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
        missing = [c for c in required if c not in df.columns]
        if missing:
            raise ValueError(f"Missing required columns: {missing}")
        return df[required]
```

### 2.2 Loader Registry with Entry Points

```python
# src/equity_lake/loaders/registry.py
from importlib.metadata import entry_points
from typing import Dict, Type, List, Optional
import logging
from .base import BaseDataLoader, LoaderMetadata

class LoaderRegistry:
    """
    Registry for data loader plugins using Python entry points.

    Usage:
        # Get singleton instance
        registry = LoaderRegistry()

        # List all loaders
        for metadata in registry.list():
            print(f"{metadata.name}: {metadata.description}")

        # Create a loader instance
        loader = registry.create("yfinance", {"timeout": 30})

        # Use the loader
        result = loader.load(["AAPL"], date(2024, 1, 1), date(2024, 1, 31))

    To create a plugin package:
        # In your plugin's pyproject.toml:
        [project.entry-points."equity_lake.loaders"]
        my_loader = "my_package.loader:MyLoader"
    """

    _instance: Optional['LoaderRegistry'] = None
    _loaders: Dict[str, Type[BaseDataLoader]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._discover_loaders()
        return cls._instance

    def _discover_loaders(self):
        """Discover loaders from entry points."""
        try:
            eps = entry_points(group="equity_lake.loaders")
            for ep in eps:
                try:
                    loader_class = ep.load()
                    if issubclass(loader_class, BaseDataLoader):
                        name = loader_class.metadata.name
                        self._loaders[name] = loader_class
                        logging.debug(f"Discovered loader: {name}")
                except Exception as e:
                    logging.warning(f"Failed to load loader {ep.name}: {e}")
        except Exception as e:
            logging.error(f"Error discovering loaders: {e}")

    def register(self, name: str, loader_class: Type[BaseDataLoader]):
        """
        Manually register a loader.

        Args:
            name: Loader name
            loader_class: Loader class (must inherit from BaseDataLoader)
        """
        if not issubclass(loader_class, BaseDataLoader):
            raise TypeError(f"Loader must inherit from BaseDataLoader")
        self._loaders[name] = loader_class
        logging.info(f"Registered loader: {name}")

    def unregister(self, name: str):
        """Remove a loader from registry."""
        if name in self._loaders:
            del self._loaders[name]
            logging.info(f"Unregistered loader: {name}")

    def get(self, name: str) -> Type[BaseDataLoader]:
        """
        Get a loader class by name.

        Args:
            name: Loader name

        Returns:
            Loader class

        Raises:
            KeyError: If loader not found
        """
        if name not in self._loaders:
            available = list(self._loaders.keys())
            raise KeyError(f"Loader '{name}' not found. Available: {available}")
        return self._loaders[name]

    def list(self) -> List[LoaderMetadata]:
        """
        List all available loaders with their metadata.

        Returns:
            List of LoaderMetadata objects
        """
        return [cls.metadata for cls in self._loaders.values()]

    def create(self, name: str, config: Dict) -> BaseDataLoader:
        """
        Create a loader instance with configuration.

        Args:
            name: Loader name
            config: Configuration dictionary

        Returns:
            Configured loader instance
        """
        loader_class = self.get(name)
        return loader_class(config)

    def exists(self, name: str) -> bool:
        """Check if a loader exists."""
        return name in self._loaders

# Global registry instance
registry = LoaderRegistry()
```

### 2.3 Built-in yfinance Loader

```python
# src/equity_lake/loaders/yfinance_loader.py
from .base import BaseDataLoader, LoaderMetadata, LoadResult
from datetime import date, datetime
from typing import Dict, List, Any, Optional
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class YFinanceLoader(BaseDataLoader):
    """
    yfinance data loader for US, HK, SG markets.

    Provides:
    - Historical OHLCV data
    - Dividends and splits
    - Real-time quotes
    - Options chain

    Usage:
        loader = YFinanceLoader({"timeout": 30})
        result = loader.load(["AAPL", "MSFT"], date(2024, 1, 1), date(2024, 1, 31))

        if result.success:
            df = result.data
            print(f"Loaded {len(df)} records")
    """

    metadata = LoaderMetadata(
        name="yfinance",
        version="1.0.0",
        description="Yahoo Finance data loader - free, no authentication required",
        author="Equity Lake Team",
        supported_markets=["US", "HK", "SG"],
        supported_intervals=["1m", "2m", "5m", "15m", "30m", "60m", "90m", "1h", "1d", "5d", "1wk", "1mo", "3mo"],
        rate_limit={"requests_per_minute": 60},
        requires_auth=False,
        data_types=["ohlcv", "dividends", "splits", "options"]
    )

    def _validate_config(self) -> None:
        """Validate yfinance-specific configuration."""
        self.timeout = self.config.get("timeout", 30)
        self.session = self.config.get("session", None)

    def load(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        interval: str = "1d"
    ) -> LoadResult:
        """Load historical price data from Yahoo Finance."""
        import yfinance as yf

        try:
            logger.info(f"Fetching {len(symbols)} symbols from {start_date} to {end_date}")

            # yfinance batch download
            data = yf.download(
                tickers=" ".join(symbols),
                start=start_date,
                end=end_date,
                interval=interval,
                group_by='ticker',
                progress=False,
                threads=True
            )

            if data.empty:
                return LoadResult(
                    success=False,
                    errors=["No data returned from yfinance"],
                    metadata={"symbols": symbols, "start": str(start_date), "end": str(end_date)}
                )

            # Normalize to standard format
            df = self._normalize_multi_ticker(data, symbols)

            return LoadResult(
                success=True,
                data=df,
                records_count=len(df),
                metadata={
                    "source": "yfinance",
                    "interval": interval,
                    "symbols": symbols
                }
            )

        except Exception as e:
            logger.error(f"yfinance load error: {e}")
            return LoadResult(
                success=False,
                errors=[str(e)],
                metadata={"symbols": symbols}
            )

    def _normalize_multi_ticker(self, data: pd.DataFrame, symbols: List[str]) -> pd.DataFrame:
        """Normalize multi-ticker yfinance output to standard schema."""
        records = []

        if len(symbols) == 1:
            # Single ticker - columns are not multi-index
            symbol = symbols[0]
            for idx, row in data.iterrows():
                records.append({
                    'ticker': symbol,
                    'date': idx.date() if hasattr(idx, 'date') else idx,
                    'open': row.get('Open', row.get('open')),
                    'high': row.get('High', row.get('high')),
                    'low': row.get('Low', row.get('low')),
                    'close': row.get('Close', row.get('close')),
                    'volume': row.get('Volume', row.get('volume'))
                })
        else:
            # Multi-ticker - columns are multi-index
            for symbol in symbols:
                if symbol in data.columns:
                    sym_data = data[symbol]
                    for idx, row in sym_data.iterrows():
                        records.append({
                            'ticker': symbol,
                            'date': idx.date() if hasattr(idx, 'date') else idx,
                            'open': row.get('Open', row.get('open')),
                            'high': row.get('High', row.get('high')),
                            'low': row.get('Low', row.get('low')),
                            'close': row.get('Close', row.get('close')),
                            'volume': row.get('Volume', row.get('volume'))
                        })

        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        return df.dropna(subset=['close'])

    def get_available_symbols(self) -> List[str]:
        """
        yfinance doesn't have a symbol list API.

        Returns empty list - use config file for symbols.
        """
        return []

    def validate_connection(self) -> bool:
        """Test connection to Yahoo Finance."""
        import yfinance as yf
        try:
            ticker = yf.Ticker("AAPL")
            hist = ticker.history(period="1d")
            return not hist.empty
        except Exception as e:
            logger.error(f"yfinance connection test failed: {e}")
            return False

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """Get real-time quote for a symbol."""
        import yfinance as yf
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            return {
                'symbol': symbol,
                'price': info.get('currentPrice', info.get('regularMarketPrice')),
                'change': info.get('regularMarketChange'),
                'change_percent': info.get('regularMarketChangePercent'),
                'volume': info.get('regularMarketVolume'),
                'timestamp': datetime.now().isoformat()
            }
        except Exception as e:
            logger.error(f"Quote fetch error for {symbol}: {e}")
            return None

    def get_corporate_actions(self, symbol: str) -> Optional[pd.DataFrame]:
        """Get dividends and splits for a symbol."""
        import yfinance as yf
        try:
            ticker = yf.Ticker(symbol)

            # Get dividends and splits
            dividends = ticker.dividends
            splits = ticker.splits

            actions = []
            for idx, div in dividends.items():
                actions.append({
                    'date': idx.date(),
                    'type': 'dividend',
                    'value': div
                })
            for idx, split in splits.items():
                actions.append({
                    'date': idx.date(),
                    'type': 'split',
                    'value': split
                })

            if actions:
                return pd.DataFrame(actions)
            return None

        except Exception as e:
            logger.error(f"Corporate actions fetch error: {e}")
            return None

# Register in pyproject.toml:
# [project.entry-points."equity_lake.loaders"]
# yfinance = "equity_lake.loaders.yfinance_loader:YFinanceLoader"
```

### 2.4 External Plugin Development Guide

```python
# Example: Custom Crypto Loader Plugin
# In a separate package: equity-lake-crypto

# my_crypto_loader/coingecko_loader.py
from equity_lake.loaders.base import BaseDataLoader, LoaderMetadata, LoadResult
from datetime import date
from typing import Dict, List, Optional
import pandas as pd
import requests

class CoinGeckoLoader(BaseDataLoader):
    """
    CoinGecko cryptocurrency data loader.

    Free tier limits:
    - 10-50 requests/minute
    - No authentication required for basic endpoints

    Installation:
        pip install equity-lake-crypto

    Usage:
        loader = CoinGeckoLoader({"api_base": "https://api.coingecko.com/api/v3"})
        result = loader.load(["bitcoin", "ethereum"], date(2024, 1, 1), date(2024, 1, 31))
    """

    metadata = LoaderMetadata(
        name="coingecko",
        version="1.0.0",
        description="CoinGecko cryptocurrency data loader (free tier)",
        author="Community Contributor",
        supported_markets=["CRYPTO"],
        supported_intervals=["1d"],
        rate_limit={"requests_per_minute": 10},
        requires_auth=False,
        data_types=["ohlcv"]
    )

    def _validate_config(self) -> None:
        self.api_base = self.config.get(
            "api_base",
            "https://api.coingecko.com/api/v3"
        )
        self.session = requests.Session()

    def load(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        interval: str = "1d"
    ) -> LoadResult:
        """Load historical crypto data from CoinGecko."""
        all_data = []
        errors = []

        for symbol in symbols:
            try:
                # CoinGecko uses coin IDs (e.g., "bitcoin" not "BTC")
                coin_id = self._get_coin_id(symbol)
                if not coin_id:
                    errors.append(f"Unknown symbol: {symbol}")
                    continue

                url = f"{self.api_base}/coins/{coin_id}/market_chart/range"
                params = {
                    'vs_currency': 'usd',
                    'from': int(start_date.strftime('%s')),
                    'to': int(end_date.strftime('%s'))
                }

                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()

                # Convert to DataFrame
                df = self._parse_response(data, symbol)
                all_data.append(df)

            except Exception as e:
                errors.append(f"{symbol}: {str(e)}")

        if all_data:
            combined = pd.concat(all_data, ignore_index=True)
            return LoadResult(
                success=len(errors) == 0,
                data=combined,
                records_count=len(combined),
                errors=errors
            )

        return LoadResult(success=False, errors=errors)

    def _get_coin_id(self, symbol: str) -> Optional[str]:
        """Convert symbol to CoinGecko coin ID."""
        symbol_map = {
            'BTC': 'bitcoin',
            'ETH': 'ethereum',
            'SOL': 'solana',
            'XRP': 'ripple',
            # Add more mappings...
        }
        return symbol_map.get(symbol.upper(), symbol.lower())

    def _parse_response(self, data: Dict, symbol: str) -> pd.DataFrame:
        """Parse CoinGecko response to standard format."""
        prices = data.get('prices', [])
        volumes = data.get('total_volumes', [])

        records = []
        for i, (timestamp, price) in enumerate(prices):
            records.append({
                'ticker': symbol.upper(),
                'date': pd.to_datetime(timestamp, unit='ms').date(),
                'open': price,  # CoinGecko only provides close
                'high': price,
                'low': price,
                'close': price,
                'volume': volumes[i][1] if i < len(volumes) else 0
            })

        return pd.DataFrame(records)

    def get_available_symbols(self) -> List[str]:
        """Get top 100 coins by market cap."""
        try:
            resp = self.session.get(
                f"{self.api_base}/coins/markets",
                params={'vs_currency': 'usd', 'per_page': 100}
            )
            resp.raise_for_status()
            coins = resp.json()
            return [c['symbol'].upper() for c in coins]
        except:
            return []

    def validate_connection(self) -> bool:
        """Test connection to CoinGecko API."""
        try:
            resp = self.session.get(f"{self.api_base}/ping", timeout=10)
            return resp.status_code == 200
        except:
            return False

# pyproject.toml for plugin package:
# [project]
# name = "equity-lake-crypto"
# version = "0.1.0"
# dependencies = ["equity-lake", "requests"]
#
# [project.entry-points."equity_lake.loaders"]
# coingecko = "my_crypto_loader.coingecko_loader:CoinGeckoLoader"
```

### 2.5 Loader Management CLI

```python
# src/equity_lake/cli/loader.py
import click
from rich.console import Console
from rich.table import Table
from ..loaders.registry import registry

console = Console()

@click.group()
def loader():
    """Data loader management commands."""
    pass

@loader.command()
def list():
    """List all available data loaders."""
    loaders = registry.list()

    table = Table(title="Available Data Loaders")
    table.add_column("Name", style="cyan")
    table.add_column("Description", style="green")
    table.add_column("Markets", style="yellow")
    table.add_column("Auth Required", style="red")

    for meta in loaders:
        table.add_row(
            meta.name,
            meta.description[:50] + "..." if len(meta.description) > 50 else meta.description,
            ", ".join(meta.supported_markets),
            "Yes" if meta.requires_auth else "No"
        )

    console.print(table)

@loader.command()
@click.argument('name')
def show(name: str):
    """Show detailed information about a loader."""
    try:
        loader_class = registry.get(name)
        meta = loader_class.metadata

        console.print(f"\n[bold cyan]{meta.name}[/bold cyan] v{meta.version}")
        console.print(f"[green]{meta.description}[/green]\n")

        console.print(f"[bold]Author:[/bold] {meta.author}")
        console.print(f"[bold]Markets:[/bold] {', '.join(meta.supported_markets)}")
        console.print(f"[bold]Intervals:[/bold] {', '.join(meta.supported_intervals)}")
        console.print(f"[bold]Data Types:[/bold] {', '.join(meta.data_types)}")

        if meta.rate_limit:
            console.print(f"[bold]Rate Limits:[/bold]")
            for key, value in meta.rate_limit.items():
                console.print(f"  - {key}: {value}")

    except KeyError:
        console.print(f"[red]Loader '{name}' not found[/red]")

@loader.command()
@click.argument('name')
def test(name: str):
    """Test connection to a data source."""
    try:
        loader = registry.create(name, {})
        console.print(f"Testing connection to [cyan]{name}[/cyan]...")

        if loader.validate_connection():
            console.print("[green]✓ Connection successful[/green]")
        else:
            console.print("[red]✗ Connection failed[/red]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

@loader.command()
@click.argument('name')
def symbols(name: str):
    """List available symbols for a loader."""
    try:
        loader = registry.create(name, {})
        console.print(f"Fetching symbols from [cyan]{name}[/cyan]...")

        symbols = loader.get_available_symbols()

        if symbols:
            console.print(f"[green]Found {len(symbols)} symbols[/green]")
            for symbol in symbols[:20]:  # Show first 20
                console.print(f"  - {symbol}")
            if len(symbols) > 20:
                console.print(f"  ... and {len(symbols) - 20} more")
        else:
            console.print("[yellow]No symbols returned (may require config file)[/yellow]")

    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

# Add to CLI main
# main.add_command(loader, name='loader')
```

---

## Phase 3: Smart Update Management

**Timeline**: Weeks 7-9
**Priority**: High
**Goal**: "Effortless Updates"

### Libraries to Use

| Library | Purpose | Why |
|---------|---------|-----|
| **APScheduler** | Job scheduling | Already used, reliable, cron support |
| **tenacity** | Retry logic | Better than manual retry, exponential backoff |
| **networkx** | Dependency graph | Model update dependencies between sources |

### 3.1 Intelligent Update Engine

```python
# src/equity_lake/updates/engine.py
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set
from enum import Enum
from pydantic import BaseModel, Field
import pandas as pd
import logging
import networkx as nx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

class UpdateStrategy(str, Enum):
    FULL = "full"              # Complete data refresh
    INCREMENTAL = "incremental" # Only new data
    DELTA = "delta"            # Only changed records
    SMART = "smart"            # Intelligent based on data age

class UpdateResult(BaseModel):
    """Result of an update operation."""
    success: bool
    source: str
    records_added: int = 0
    records_updated: int = 0
    records_skipped: int = 0
    errors: List[str] = Field(default_factory=list)
    duration_seconds: float = 0
    next_suggested_update: Optional[datetime] = None

    class Config:
        arbitrary_types_allowed = True

class UpdateEngine:
    """
    Intelligent update management system.

    Features:
    - Freshness-aware updates (skip if data is fresh enough)
    - Missing date detection
    - Dependency-aware execution
    - Retry with exponential backoff

    Usage:
        engine = UpdateEngine(storage, loaders)

        # Update a single source
        result = engine.update("us_equity", ["AAPL", "MSFT"])

        # Update all sources
        results = engine.update_all()

        # Backfill missing data
        engine.backfill("us_equity", "AAPL", date(2020, 1, 1), date(2023, 12, 31))
    """

    # Freshness rules: how long before data is considered stale
    FRESHNESS_RULES = {
        "us_equity": timedelta(hours=18),    # After market close
        "cn_ashare": timedelta(hours=17),    # After China market close
        "hk_sg": timedelta(hours=18),
        "macro": timedelta(hours=24),        # Daily is fine
        "crypto": timedelta(hours=1),        # More frequent for crypto
    }

    def __init__(self, storage, loaders: Dict[str, 'BaseDataLoader']):
        self.storage = storage
        self.loaders = loaders
        self.history = UpdateHistory()
        self._dependency_graph = self._build_dependency_graph()

    def _build_dependency_graph(self) -> nx.DiGraph:
        """Build dependency graph for update ordering."""
        G = nx.DiGraph()

        # Define dependencies
        # e.g., features depend on price data
        dependencies = {
            "features": ["us_equity", "cn_ashare", "hk_sg"],
            "ml": ["features"],
        }

        for source, deps in dependencies.items():
            G.add_node(source)
            for dep in deps:
                G.add_edge(dep, source)

        return G

    def needs_update(self, source: str, symbol: Optional[str] = None) -> bool:
        """
        Determine if data needs update based on freshness rules.

        Args:
            source: Data source name
            symbol: Optional specific symbol

        Returns:
            True if update needed, False if data is fresh
        """
        last_update = self.history.get_last_update(source, symbol)

        if last_update is None:
            return True  # Never updated

        max_age = self.FRESHNESS_RULES.get(source, timedelta(hours=24))
        age = datetime.now() - last_update

        return age > max_age

    def get_missing_dates(
        self,
        source: str,
        symbol: str,
        start_date: Optional[date] = None
    ) -> List[date]:
        """
        Find dates with missing data (excludes holidays/weekends).

        Args:
            source: Data source
            symbol: Ticker symbol
            start_date: Start checking from this date (default: 1 year ago)

        Returns:
            List of missing dates
        """
        if start_date is None:
            start_date = date.today() - timedelta(days=365)

        existing_dates = set(self.storage.get_dates(source, symbol))
        expected_dates = self._get_trading_dates(source, symbol, start_date)

        return sorted(set(expected_dates) - existing_dates)

    def _get_trading_dates(
        self,
        source: str,
        symbol: str,
        start_date: date
    ) -> List[date]:
        """Get expected trading dates (excluding holidays)."""
        # This could use pandas_market_calendars for accurate holiday handling
        # For now, simple weekday check
        end_date = date.today()
        dates = pd.date_range(start_date, end_date, freq='B')  # Business days
        return [d.date() for d in dates]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        reraise=True
    )
    def _fetch_with_retry(
        self,
        loader: 'BaseDataLoader',
        symbols: List[str],
        start_date: date,
        end_date: date
    ) -> 'LoadResult':
        """Fetch data with retry logic."""
        return loader.load(symbols, start_date, end_date)

    def update(
        self,
        source: str,
        symbols: Optional[List[str]] = None,
        strategy: UpdateStrategy = UpdateStrategy.SMART,
        force: bool = False
    ) -> UpdateResult:
        """
        Execute update for specified source and symbols.

        Args:
            source: Data source name
            symbols: List of symbols (None = all configured)
            strategy: Update strategy
            force: Force update even if data is fresh

        Returns:
            UpdateResult with status and metrics
        """
        start_time = datetime.now()
        loader = self.loaders.get(source)

        if not loader:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"Unknown source: {source}"]
            )

        # Get configured symbols if not specified
        if symbols is None:
            symbols = self._get_configured_symbols(source)

        records_added = 0
        records_updated = 0
        records_skipped = 0
        errors = []

        for symbol in symbols:
            # Skip if fresh (unless forced)
            if not force and not self.needs_update(source, symbol):
                records_skipped += 1
                logger.debug(f"Skipping {symbol} - data is fresh")
                continue

            # Determine date range based on strategy
            start_date, end_date = self._determine_date_range(
                source, symbol, strategy
            )

            try:
                # Fetch with retry
                result = self._fetch_with_retry(loader, [symbol], start_date, end_date)

                if result.success and result.data is not None:
                    # Merge with existing data
                    merge_result = self.storage.merge(source, result.data, symbol)
                    records_added += merge_result.get('added', 0)
                    records_updated += merge_result.get('updated', 0)

                    # Record in history
                    self.history.record(source, symbol)

                else:
                    errors.extend(result.errors)

            except Exception as e:
                errors.append(f"{symbol}: {str(e)}")
                logger.error(f"Update failed for {symbol}: {e}")

        duration = (datetime.now() - start_time).total_seconds()

        return UpdateResult(
            success=len(errors) == 0,
            source=source,
            records_added=records_added,
            records_updated=records_updated,
            records_skipped=records_skipped,
            errors=errors,
            duration_seconds=duration,
            next_suggested_update=self._suggest_next_update(source)
        )

    def update_all(self, force: bool = False) -> Dict[str, UpdateResult]:
        """
        Update all sources respecting dependencies.

        Args:
            force: Force update even if data is fresh

        Returns:
            Dict mapping source names to UpdateResults
        """
        results = {}

        # Get topological order respecting dependencies
        try:
            order = list(nx.topological_sort(self._dependency_graph))
        except nx.NetworkXUnfeasible:
            # Cycle detected, fall back to simple order
            order = list(self.loaders.keys())

        for source in order:
            if source in self.loaders:
                results[source] = self.update(source, force=force)

        return results

    def backfill(
        self,
        source: str,
        symbol: str,
        start_date: date,
        end_date: date,
        batch_days: int = 365
    ) -> UpdateResult:
        """
        Backfill historical data for a date range.

        Args:
            source: Data source
            symbol: Ticker symbol
            start_date: Start date
            end_date: End date
            batch_days: Days per batch (to avoid rate limits)

        Returns:
            UpdateResult with backfill status
        """
        loader = self.loaders.get(source)
        if not loader:
            return UpdateResult(
                success=False,
                source=source,
                errors=[f"Unknown source: {source}"]
            )

        total_added = 0
        errors = []

        # Process in batches
        current_start = start_date
        while current_start < end_date:
            current_end = min(current_start + timedelta(days=batch_days), end_date)

            try:
                result = loader.load([symbol], current_start, current_end)

                if result.success and result.data is not None:
                    self.storage.overwrite(source, result.data, symbol, current_start, current_end)
                    total_added += len(result.data)

            except Exception as e:
                errors.append(f"{current_start} to {current_end}: {str(e)}")

            current_start = current_end + timedelta(days=1)

        return UpdateResult(
            success=len(errors) == 0,
            source=source,
            records_added=total_added,
            errors=errors
        )

    def _determine_date_range(
        self,
        source: str,
        symbol: str,
        strategy: UpdateStrategy
    ) -> tuple[date, date]:
        """Determine date range based on strategy."""
        today = date.today()

        if strategy == UpdateStrategy.FULL:
            return today - timedelta(days=365), today

        elif strategy == UpdateStrategy.INCREMENTAL:
            last_date = self.storage.get_last_date(source, symbol)
            start = last_date + timedelta(days=1) if last_date else today - timedelta(days=30)
            return start, today

        elif strategy == UpdateStrategy.DELTA:
            # Re-fetch last 7 days to catch corrections
            return today - timedelta(days=7), today

        else:  # SMART
            missing = self.get_missing_dates(source, symbol, today - timedelta(days=30))
            if missing:
                return min(missing), max(missing)
            else:
                return today - timedelta(days=7), today

    def _suggest_next_update(self, source: str) -> datetime:
        """Suggest when the next update should run."""
        freshness = self.FRESHNESS_RULES.get(source, timedelta(hours=24))
        return datetime.now() + freshness

    def _get_configured_symbols(self, source: str) -> List[str]:
        """Get symbols from configuration for a source."""
        # This would read from the config system
        # Placeholder for now
        return []


class UpdateHistory:
    """Track update history for freshness checks."""

    def __init__(self, path: str = "./data/update_history.parquet"):
        self.path = path
        self._history: Optional[pd.DataFrame] = None

    def _load(self) -> pd.DataFrame:
        """Load history from storage."""
        import os
        if os.path.exists(self.path):
            return pd.read_parquet(self.path)
        return pd.DataFrame(columns=['source', 'symbol', 'updated_at'])

    @property
    def history(self) -> pd.DataFrame:
        if self._history is None:
            self._history = self._load()
        return self._history

    def get_last_update(self, source: str, symbol: Optional[str] = None) -> Optional[datetime]:
        """Get timestamp of last successful update."""
        df = self.history
        mask = df['source'] == source
        if symbol:
            mask &= df['symbol'] == symbol

        subset = df[mask]
        if subset.empty:
            return None

        return pd.to_datetime(subset['updated_at'].max())

    def record(self, source: str, symbol: str, records: int = 0):
        """Record an update in history."""
        new_row = pd.DataFrame([{
            'source': source,
            'symbol': symbol,
            'updated_at': datetime.now(),
            'records': records
        }])

        self._history = pd.concat([self.history, new_row], ignore_index=True)
        self._history.to_parquet(self.path, index=False)
```

---

## Phase 4: Data Quality Framework

**Timeline**: Weeks 10-12
**Priority**: High
**Goal**: Reliable, validated data

### Libraries to Use

| Library | Purpose | Why |
|---------|---------|-----|
| **Pandera** | Schema validation | Lightweight, Pydantic-style, DataFrame schemas |
| **whylogs** | Data profiling | Statistical profiling, drift detection |
| **great_expectations** | Advanced validation | Industry standard, extensive checks (optional) |

### 4.1 Schema Validation with Pandera

```python
# src/equity_lake/validation/schemas.py
import pandera as pa
from pandera import Column, DataFrameSchema, Check
from pandera.typing import Series
from datetime import date
from typing import Optional
import pandas as pd

class PriceDataSchema(pa.DataFrameModel):
    """
    Schema for price data validation.

    Validates:
    - Required columns present
    - Correct data types
    - Positive prices
    - Non-negative volume
    - No duplicate ticker+date combinations

    Usage:
        schema = PriceDataSchema()
        validated_df = schema.validate(df)  # Raises ValidationError if invalid
    """
    ticker: Series[str] = pa.Field(description="Ticker symbol")
    date: Series[pd.Timestamp] = pa.Field(description="Trading date")
    open: Series[float] = pa.Field(gt=0, description="Opening price (must be positive)")
    high: Series[float] = pa.Field(gt=0, description="High price (must be positive)")
    low: Series[float] = pa.Field(gt=0, description="Low price (must be positive)")
    close: Series[float] = pa.Field(gt=0, description="Closing price (must be positive)")
    volume: Series[int] = pa.Field(ge=0, description="Volume (non-negative)")

    # Additional optional columns
    adjusted_close: Optional[Series[float]] = pa.Field(gt=0, default=None)
    dividend_amount: Optional[Series[float]] = pa.Field(ge=0, default=None)

    @pa.check("high")
    def high_greater_than_low(cls, series: Series[float]) -> Series[bool]:
        """High must be >= Low for the same row."""
        # This is a column-level check; for row-level, use DataFrame checks
        return series >= 0  # Simplified; actual row-level check below

    @pa.dataframe_check
    def price_consistency(cls, df: pd.DataFrame) -> Series[bool]:
        """Validate price relationships: High >= Low, High >= Open, etc."""
        return (
            (df['high'] >= df['low']) &
            (df['high'] >= df['open']) &
            (df['high'] >= df['close']) &
            (df['low'] <= df['open']) &
            (df['low'] <= df['close'])
        )

    @pa.dataframe_check
    def no_duplicates(cls, df: pd.DataFrame) -> Series[bool]:
        """No duplicate ticker+date combinations."""
        duplicates = df.duplicated(subset=['ticker', 'date'])
        return ~duplicates

    class Config:
        coerce = True  # Automatically coerce types
        strict = False  # Allow extra columns


class MacroDataSchema(pa.DataFrameModel):
    """Schema for macro economic data."""
    series_id: Series[str] = pa.Field(description="FRED series ID")
    date: Series[pd.Timestamp] = pa.Field(description="Observation date")
    value: Series[float] = pa.Field(description="Series value")
    source: Series[str] = pa.Field(description="Data source")

    class Config:
        coerce = True


class FeatureDataSchema(pa.DataFrameModel):
    """Schema for feature-engineered data."""
    ticker: Series[str]
    date: Series[pd.Timestamp]
    close: Series[float] = pa.Field(gt=0)

    # Technical indicators
    rsi_14: Series[float] = pa.Field(ge=0, le=100)
    macd: Series[float]
    macd_signal: Series[float]
    bb_upper: Series[float] = pa.Field(gt=0)
    bb_lower: Series[float] = pa.Field(gt=0)
    atr_14: Series[float] = pa.Field(ge=0)

    @pa.dataframe_check
    def bb_consistency(cls, df: pd.DataFrame) -> Series[bool]:
        """Bollinger Band lower <= close <= upper."""
        return (
            (df['bb_lower'] <= df['close']) &
            (df['close'] <= df['bb_upper'])
        )

    class Config:
        coerce = True
        strict = False


# Legacy-style schema (alternative approach)
PRICE_SCHEMA = DataFrameSchema(
    columns={
        "ticker": Column(str, nullable=False),
        "date": Column(pa.DateTime, nullable=False),
        "open": Column(float, checks=Check.gt(0), nullable=False),
        "high": Column(float, checks=Check.gt(0), nullable=False),
        "low": Column(float, checks=Check.gt(0), nullable=False),
        "close": Column(float, checks=Check.gt(0), nullable=False),
        "volume": Column(int, checks=Check.ge(0), nullable=False),
    },
    checks=[
        # Row-level check: high >= low
        Check(
            lambda df: df["high"] >= df["low"],
            error="High must be >= Low",
        ),
        # No duplicates
        Check(
            lambda df: ~df.duplicated(subset=["ticker", "date"]),
            error="Duplicate ticker+date combinations found",
        ),
    ],
    strict=False,
    coerce=True,
)
```

### 4.2 Data Profiling with whylogs

```python
# src/equity_lake/validation/profiling.py
from typing import Dict, Optional
import pandas as pd
import whylogs as why
from whylogs.core import DatasetProfileView
from whylogs.viz import NotebookProfileVisualizer
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class DataProfiler:
    """
    Statistical profiling for data quality monitoring.

    Features:
    - Statistical profiles (mean, std, quantiles, etc.)
    - Drift detection between profiles
    - Data quality metrics
    - Profile comparison

    Usage:
        profiler = DataProfiler()

        # Profile new data
        profile = profiler.profile(df, name="us_equity_2024_01")

        # Compare with baseline
        drift_report = profiler.compare(profile, baseline_profile)

        # Check for drift
        if drift_report.has_drift:
            alert(f"Data drift detected in {drift_report.columns}")
    """

    def __init__(self, storage_path: str = "./data/profiles"):
        self.storage_path = storage_path
        self._profiles: Dict[str, DatasetProfileView] = {}

    def profile(
        self,
        df: pd.DataFrame,
        name: str,
        tags: Optional[Dict] = None
    ) -> DatasetProfileView:
        """
        Create a statistical profile of the data.

        Args:
            df: DataFrame to profile
            name: Profile name
            tags: Optional metadata tags

        Returns:
            DatasetProfileView
        """
        # Create profile
        profile = why.log(df, name=name)

        # Add metadata
        if tags:
            profile.set_dataset_metadata(tags)

        view = profile.view()

        # Store profile
        self._profiles[name] = view
        self._save_profile(view, name)

        logger.info(f"Created profile: {name}")
        return view

    def _save_profile(self, profile: DatasetProfileView, name: str):
        """Save profile to disk."""
        import os
        os.makedirs(self.storage_path, exist_ok=True)
        path = f"{self.storage_path}/{name}.bin"
        profile.write(path)

    def load_profile(self, name: str) -> Optional[DatasetProfileView]:
        """Load a saved profile."""
        path = f"{self.storage_path}/{name}.bin"
        import os
        if os.path.exists(path):
            return DatasetProfileView.read(path)
        return None

    def compare(
        self,
        profile1: DatasetProfileView,
        profile2: DatasetProfileView
    ) -> 'DriftReport':
        """
        Compare two profiles for drift detection.

        Args:
            profile1: First profile (e.g., current)
            profile2: Second profile (e.g., baseline)

        Returns:
            DriftReport with drift metrics
        """
        from whylogs.extras import add_metric
        from whylogs.core.metrics import ColumnCountsMetric

        # Get column profiles
        cols1 = profile1.get_columns()
        cols2 = profile2.get_columns()

        drift_metrics = {}
        has_drift = False

        for col_name in cols1.keys():
            if col_name in cols2:
                col1 = cols1[col_name]
                col2 = cols2[col_name]

                # Compare distributions
                # This is simplified; whylogs has more sophisticated drift detection
                mean1 = col1.to_summary_dict().get('distribution/mean', 0)
                mean2 = col2.to_summary_dict().get('distribution/mean', 0)

                if mean1 and mean2:
                    pct_change = abs(mean1 - mean2) / mean2 if mean2 != 0 else 0
                    if pct_change > 0.1:  # 10% threshold
                        has_drift = True
                        drift_metrics[col_name] = {
                            'mean_current': mean1,
                            'mean_baseline': mean2,
                            'pct_change': pct_change
                        }

        return DriftReport(
            has_drift=has_drift,
            columns=drift_metrics,
            profile1_name=getattr(profile1, 'name', 'current'),
            profile2_name=getattr(profile2, 'name', 'baseline')
        )

    def get_quality_metrics(
        self,
        profile: DatasetProfileView
    ) -> Dict[str, float]:
        """
        Extract data quality metrics from profile.

        Returns:
            Dict with metrics like completeness, uniqueness, etc.
        """
        metrics = {}
        cols = profile.get_columns()

        for col_name, col_profile in cols.items():
            summary = col_profile.to_summary_dict()

            # Completeness (fraction of non-null)
            total = summary.get('counts/n', 0)
            null = summary.get('counts/null', 0)
            completeness = (total - null) / total if total > 0 else 0

            # Uniqueness
            unique = summary.get('cardinality/est', 0)
            uniqueness = unique / total if total > 0 else 0

            metrics[col_name] = {
                'completeness': completeness,
                'uniqueness': uniqueness,
                'count': total,
                'null_count': null,
            }

            # Distribution stats for numeric columns
            if 'distribution/mean' in summary:
                metrics[col_name].update({
                    'mean': summary['distribution/mean'],
                    'std': summary.get('distribution/stddev'),
                    'min': summary.get('distribution/min'),
                    'max': summary.get('distribution/max'),
                })

        return metrics


class DriftReport:
    """Report on data drift between two profiles."""

    def __init__(
        self,
        has_drift: bool,
        columns: Dict,
        profile1_name: str = "current",
        profile2_name: str = "baseline"
    ):
        self.has_drift = has_drift
        self.columns = columns
        self.profile1_name = profile1_name
        self.profile2_name = profile2_name
        self.timestamp = datetime.now()

    def to_dict(self) -> Dict:
        return {
            'has_drift': self.has_drift,
            'columns': self.columns,
            'profile_current': self.profile1_name,
            'profile_baseline': self.profile2_name,
            'timestamp': self.timestamp.isoformat()
        }

    def __repr__(self) -> str:
        status = "DRIFT DETECTED" if self.has_drift else "No drift"
        return f"DriftReport({status}, {len(self.columns)} columns affected)"
```

### 4.3 Integrated Validation Pipeline

```python
# src/equity_lake/validation/pipeline.py
from typing import Dict, List, Optional, Callable
from pydantic import BaseModel, Field
import pandas as pd
import logging
from datetime import datetime

from .schemas import PriceDataSchema, MacroDataSchema, FeatureDataSchema
from .profiling import DataProfiler, DriftReport

logger = logging.getLogger(__name__)

class ValidationResult(BaseModel):
    """Result of a validation operation."""
    success: bool
    schema_valid: bool = True
    profile_valid: bool = True
    drift_detected: bool = False
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    metrics: Dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.now)

class ValidationPipeline:
    """
    Integrated validation pipeline combining schema and profile validation.

    Usage:
        pipeline = ValidationPipeline()

        # Validate with both schema and profiling
        result = pipeline.validate(df, "us_equity")

        if not result.success:
            print(f"Validation failed: {result.errors}")
    """

    SCHEMAS = {
        'price': PriceDataSchema,
        'macro': MacroDataSchema,
        'feature': FeatureDataSchema,
    }

    def __init__(
        self,
        profiler: Optional[DataProfiler] = None,
        strict: bool = False
    ):
        self.profiler = profiler or DataProfiler()
        self.strict = strict
        self._baselines: Dict[str, any] = {}

    def set_baseline(self, name: str, df: pd.DataFrame):
        """Set a baseline profile for drift detection."""
        self._baselines[name] = self.profiler.profile(df, f"baseline_{name}")

    def validate(
        self,
        df: pd.DataFrame,
        data_type: str = 'price',
        check_drift: bool = True,
        name: Optional[str] = None
    ) -> ValidationResult:
        """
        Validate DataFrame against schema and profile.

        Args:
            df: DataFrame to validate
            data_type: Type of data ('price', 'macro', 'feature')
            check_drift: Whether to check for drift against baseline
            name: Profile name

        Returns:
            ValidationResult with validation status
        """
        errors = []
        warnings = []
        metrics = {}

        # 1. Schema validation
        schema_valid = True
        schema_class = self.SCHEMAS.get(data_type)

        if schema_class:
            try:
                validated = schema_class.validate(df)
                logger.info(f"Schema validation passed for {data_type}")
            except Exception as e:
                schema_valid = False
                errors.append(f"Schema validation failed: {str(e)}")
                logger.error(f"Schema validation failed: {e}")

                if self.strict:
                    return ValidationResult(
                        success=False,
                        schema_valid=False,
                        errors=errors
                    )
        else:
            warnings.append(f"Unknown data type '{data_type}', skipping schema validation")

        # 2. Profile validation
        profile_valid = True
        drift_detected = False

        if name:
            try:
                profile = self.profiler.profile(df, name)
                quality_metrics = self.profiler.get_quality_metrics(profile)
                metrics['quality'] = quality_metrics

                # Check for drift if baseline exists
                if check_drift and name in self._baselines:
                    drift_report = self.profiler.compare(
                        profile, self._baselines[name]
                    )
                    drift_detected = drift_report.has_drift
                    metrics['drift'] = drift_report.to_dict()

                    if drift_detected:
                        warnings.append(
                            f"Data drift detected in columns: {list(drift_report.columns.keys())}"
                        )

            except Exception as e:
                profile_valid = False
                warnings.append(f"Profiling failed: {str(e)}")

        # 3. Custom checks
        custom_errors = self._run_custom_checks(df, data_type)
        errors.extend(custom_errors)

        return ValidationResult(
            success=len(errors) == 0,
            schema_valid=schema_valid,
            profile_valid=profile_valid,
            drift_detected=drift_detected,
            errors=errors,
            warnings=warnings,
            metrics=metrics
        )

    def _run_custom_checks(self, df: pd.DataFrame, data_type: str) -> List[str]:
        """Run additional custom validation checks."""
        errors = []

        # Check for completely empty DataFrame
        if df.empty:
            errors.append("DataFrame is empty")
            return errors

        # Check for all-null columns
        null_cols = df.columns[df.isnull().all()].tolist()
        if null_cols:
            errors.append(f"Columns with all null values: {null_cols}")

        # Check for very high null rates
        high_null = df.columns[df.isnull().mean() > 0.5].tolist()
        if high_null:
            errors.append(f"Columns with >50% null values: {high_null}")

        return errors

    def validate_and_fix(
        self,
        df: pd.DataFrame,
        data_type: str = 'price'
    ) -> tuple[pd.DataFrame, ValidationResult]:
        """
        Validate and attempt to fix common issues.

        Returns:
            Tuple of (fixed DataFrame, ValidationResult)
        """
        df_fixed = df.copy()

        # Remove duplicate rows
        before = len(df_fixed)
        df_fixed = df_fixed.drop_duplicates(subset=['ticker', 'date'], keep='last')
        if len(df_fixed) < before:
            logger.warning(f"Removed {before - len(df_fixed)} duplicate rows")

        # Remove rows with invalid prices
        if 'close' in df_fixed.columns:
            df_fixed = df_fixed[df_fixed['close'] > 0]

        # Fill missing volume with 0
        if 'volume' in df_fixed.columns:
            df_fixed['volume'] = df_fixed['volume'].fillna(0)

        # Validate the fixed DataFrame
        result = self.validate(df_fixed, data_type)

        return df_fixed, result
```

---

## Phase 5: Feature Store with Hamilton

**Timeline**: Weeks 13-15
**Priority**: High
**Goal**: Production-ready feature engineering

### Libraries to Use

| Library | Purpose | Why |
|---------|---------|-----|
| **Hamilton** | Feature DAG | Declarative features, auto-documentation, testable |
| **Feast** | Feature serving | Store and serve features to ML models |

### 5.1 Hamilton Feature Definitions

```python
# src/equity_lake/features/hamilton_features.py
"""
Hamilton feature definitions for equity data.

Each function is a node in the DAG. Hamilton automatically
resolves dependencies and executes in correct order.

Usage:
    from hamilton import driver
    import features.hamilton_features as feature_module

    dr = driver.Driver({}, feature_module)
    features = dr.execute(
        ['rsi_14', 'macd_histogram', 'volatility_20'],
        inputs={'price_data': df}
    )
"""
import pandas as pd
import pandas_ta as ta
import numpy as np
from typing import Dict, Any

# =============================================================================
# INPUT NODES - These receive data from outside
# =============================================================================

def price_data() -> pd.DataFrame:
    """
    Input node: Raw price data.
    This is a placeholder - actual data is passed via inputs.
    """
    pass

def close(price_data: pd.DataFrame) -> pd.Series:
    """Extract close prices."""
    return price_data['close']

def high(price_data: pd.DataFrame) -> pd.Series:
    """Extract high prices."""
    return price_data['high']

def low(price_data: pd.DataFrame) -> pd.Series:
    """Extract low prices."""
    return price_data['low']

def volume(price_data: pd.DataFrame) -> pd.Series:
    """Extract volume."""
    return price_data['volume']

def returns(close: pd.Series) -> pd.Series:
    """Daily returns."""
    return close.pct_change()

# =============================================================================
# TREND INDICATORS
# =============================================================================

def sma_20(close: pd.Series) -> pd.Series:
    """20-period Simple Moving Average."""
    return ta.sma(close, length=20)

def sma_50(close: pd.Series) -> pd.Series:
    """50-period Simple Moving Average."""
    return ta.sma(close, length=50)

def sma_200(close: pd.Series) -> pd.Series:
    """200-period Simple Moving Average."""
    return ta.sma(close, length=200)

def ema_12(close: pd.Series) -> pd.Series:
    """12-period Exponential Moving Average."""
    return ta.ema(close, length=12)

def ema_26(close: pd.Series) -> pd.Series:
    """26-period Exponential Moving Average."""
    return ta.ema(close, length=26)

def sma_crossover(sma_20: pd.Series, sma_50: pd.Series) -> pd.Series:
    """
    SMA crossover signal.
    1 when SMA20 > SMA50, 0 otherwise.
    """
    return (sma_20 > sma_50).astype(int)

def golden_cross(sma_50: pd.Series, sma_200: pd.Series) -> pd.Series:
    """
    Golden cross signal.
    1 when SMA50 crosses above SMA200.
    """
    cross = (sma_50 > sma_200).astype(int)
    return (cross.diff() == 1).astype(int)

# =============================================================================
# MOMENTUM INDICATORS
# =============================================================================

def rsi_14(close: pd.Series) -> pd.Series:
    """14-period Relative Strength Index."""
    return ta.rsi(close, length=14)

def rsi_7(close: pd.Series) -> pd.Series:
    """7-period RSI (more sensitive)."""
    return ta.rsi(close, length=7)

def rsi_21(close: pd.Series) -> pd.Series:
    """21-period RSI (less sensitive)."""
    return ta.rsi(close, length=21)

def rsi_overbought(rsi_14: pd.Series) -> pd.Series:
    """RSI overbought signal (>70)."""
    return (rsi_14 > 70).astype(int)

def rsi_oversold(rsi_14: pd.Series) -> pd.Series:
    """RSI oversold signal (<30)."""
    return (rsi_14 < 30).astype(int)

def macd(close: pd.Series) -> pd.DataFrame:
    """MACD indicator (12, 26, 9)."""
    return ta.macd(close, fast=12, slow=26, signal=9)

def macd_line(macd: pd.DataFrame) -> pd.Series:
    """MACD line."""
    return macd['MACD_12_26_9']

def macd_signal_line(macd: pd.DataFrame) -> pd.Series:
    """MACD signal line."""
    return macd['MACDs_12_26_9']

def macd_histogram(macd_line: pd.Series, macd_signal_line: pd.Series) -> pd.Series:
    """MACD histogram (MACD - Signal)."""
    return macd_line - macd_signal_line

def macd_crossover(macd_line: pd.Series, macd_signal_line: pd.Series) -> pd.Series:
    """MACD crossover signal."""
    diff = macd_line - macd_signal_line
    return (diff > 0).astype(int)

def stoch(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.DataFrame:
    """Stochastic oscillator."""
    return ta.stoch(high, low, close, k=14, d=3)

def stoch_k(stoch: pd.DataFrame) -> pd.Series:
    """Stochastic %K."""
    return stoch['STOCHk_14_3_3']

def stoch_d(stoch: pd.DataFrame) -> pd.Series:
    """Stochastic %D."""
    return stoch['STOCHd_14_3_3']

# =============================================================================
# VOLATILITY INDICATORS
# =============================================================================

def atr_14(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """14-period Average True Range."""
    return ta.atr(high, low, close, length=14)

def atr_7(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """7-period ATR (more sensitive)."""
    return ta.atr(high, low, close, length=7)

def volatility_20(returns: pd.Series) -> pd.Series:
    """20-day rolling volatility (annualized)."""
    return returns.rolling(20).std() * np.sqrt(252)

def volatility_60(returns: pd.Series) -> pd.Series:
    """60-day rolling volatility (annualized)."""
    return returns.rolling(60).std() * np.sqrt(252)

def bollinger_bands(close: pd.Series) -> pd.DataFrame:
    """Bollinger Bands (20, 2)."""
    return ta.bbands(close, length=20, std=2)

def bb_upper(bollinger_bands: pd.DataFrame) -> pd.Series:
    """Bollinger Band upper."""
    return bollinger_bands['BBU_20_2.0']

def bb_middle(bollinger_bands: pd.DataFrame) -> pd.Series:
    """Bollinger Band middle (SMA)."""
    return bollinger_bands['BBM_20_2.0']

def bb_lower(bollinger_bands: pd.DataFrame) -> pd.Series:
    """Bollinger Band lower."""
    return bollinger_bands['BBL_20_2.0']

def bb_width(bb_upper: pd.Series, bb_lower: pd.Series, bb_middle: pd.Series) -> pd.Series:
    """Bollinger Band width."""
    return (bb_upper - bb_lower) / bb_middle

def bb_position(close: pd.Series, bb_upper: pd.Series, bb_lower: pd.Series) -> pd.Series:
    """Position within Bollinger Bands (0-1)."""
    return (close - bb_lower) / (bb_upper - bb_lower)

# =============================================================================
# VOLUME INDICATORS
# =============================================================================

def volume_sma_20(volume: pd.Series) -> pd.Series:
    """20-day volume SMA."""
    return ta.sma(volume, length=20)

def volume_ratio(volume: pd.Series, volume_sma_20: pd.Series) -> pd.Series:
    """Volume relative to average."""
    return volume / volume_sma_20

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    return ta.obv(close, volume)

def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    """Volume Weighted Average Price (rolling)."""
    return ta.vwap(high, low, close, volume)

# =============================================================================
# PRICE PATTERNS
# =============================================================================

def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range."""
    return ta.true_range(high, low, close)

def gap_up(open_price: pd.Series, close: pd.Series) -> pd.Series:
    """Gap up signal."""
    prev_close = close.shift(1)
    return (open_price > prev_close * 1.01).astype(int)

def gap_down(open_price: pd.Series, close: pd.Series) -> pd.Series:
    """Gap down signal."""
    prev_close = close.shift(1)
    return (open_price < prev_close * 0.99).astype(int)

def inside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """Inside bar pattern."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    return ((high < prev_high) & (low > prev_low)).astype(int)

def outside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    """Outside bar pattern."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    return ((high > prev_high) & (low < prev_low)).astype(int)

# =============================================================================
# DERIVED FEATURES
# =============================================================================

def price_momentum_5(close: pd.Series) -> pd.Series:
    """5-day price momentum (%)."""
    return close.pct_change(5) * 100

def price_momentum_20(close: pd.Series) -> pd.Series:
    """20-day price momentum (%)."""
    return close.pct_change(20) * 100

def distance_from_sma20(close: pd.Series, sma_20: pd.Series) -> pd.Series:
    """Distance from SMA20 (%)."""
    return ((close - sma_20) / sma_20) * 100

def distance_from_high_20(close: pd.Series) -> pd.Series:
    """Distance from 20-day high (%)."""
    high_20 = close.rolling(20).max()
    return ((high_20 - close) / high_20) * 100

def distance_from_low_20(close: pd.Series) -> pd.Series:
    """Distance from 20-day low (%)."""
    low_20 = close.rolling(20).min()
    return ((close - low_20) / low_20) * 100

# =============================================================================
# AGGREGATE FEATURES
# =============================================================================

def trend_score(
    sma_crossover: pd.Series,
    macd_crossover: pd.Series,
    rsi_14: pd.Series,
    bb_position: pd.Series
) -> pd.Series:
    """
    Composite trend score (0-100).
    Combines multiple indicators into single score.
    """
    score = 0
    score += sma_crossover * 25  # 0 or 25
    score += macd_crossover * 25  # 0 or 25
    score += ((rsi_14 - 30) / 40 * 25).clip(0, 25)  # 0-25 based on RSI
    score += bb_position.clip(0, 1) * 25  # 0-25 based on BB position
    return score

def volatility_regime(volatility_20: pd.Series) -> pd.Series:
    """
    Volatility regime classification.
    0 = Low, 1 = Normal, 2 = High
    """
    q25 = volatility_20.rolling(252).quantile(0.25)
    q75 = volatility_20.rolling(252).quantile(0.75)

    regime = pd.Series(1, index=volatility_20.index)  # Normal
    regime[volatility_20 < q25] = 0  # Low
    regime[volatility_20 > q75] = 2  # High

    return regime
```

### 5.2 Hamilton Feature Pipeline

```python
# src/equity_lake/features/pipeline.py
from hamilton import driver, lifecycle
from typing import List, Dict, Optional
import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class FeaturePipeline:
    """
    Hamilton-based feature engineering pipeline.

    Features:
    - Declarative feature definitions
    - Automatic dependency resolution
    - Built-in caching
    - Testable features

    Usage:
        pipeline = FeaturePipeline()

        # Compute specific features
        features = pipeline.compute(
            price_data=df,
            features=['rsi_14', 'macd_histogram', 'volatility_20']
        )

        # Compute all features
        all_features = pipeline.compute_all(price_data=df)
    """

    def __init__(
        self,
        cache_dir: Optional[str] = "./data/feature_cache",
        enable_cache: bool = True
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.enable_cache = enable_cache

        # Initialize Hamilton driver
        self._driver = self._create_driver()

    def _create_driver(self):
        """Create Hamilton driver with optional caching."""
        import sys
        from pathlib import Path

        # Import feature module
        # This assumes features are in hamilton_features.py
        sys.path.insert(0, str(Path(__file__).parent))
        import hamilton_features

        # Create adapter for caching if enabled
        adapter = None
        if self.enable_cache and self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            adapter = lifecycle.LocalCache(
                cache_version="v1",
                cache_dir=str(self.cache_dir)
            )

        return driver.Driver({}, hamilton_features, adapter=adapter)

    def compute(
        self,
        price_data: pd.DataFrame,
        features: List[str],
        **kwargs
    ) -> pd.DataFrame:
        """
        Compute specified features.

        Args:
            price_data: DataFrame with OHLCV data
            features: List of feature names to compute
            **kwargs: Additional inputs

        Returns:
            DataFrame with computed features
        """
        inputs = {'price_data': price_data, **kwargs}

        try:
            result = self._driver.execute(features, inputs=inputs)
            return pd.DataFrame(result)
        except Exception as e:
            logger.error(f"Feature computation failed: {e}")
            raise

    def compute_all(
        self,
        price_data: pd.DataFrame,
        **kwargs
    ) -> pd.DataFrame:
        """
        Compute all defined features.

        Args:
            price_data: DataFrame with OHLCV data
            **kwargs: Additional inputs

        Returns:
            DataFrame with all features
        """
        # Get all available outputs
        all_features = self.list_features()

        return self.compute(price_data, all_features, **kwargs)

    def list_features(self) -> List[str]:
        """List all available feature names."""
        return list(self._driver.list_available_variables())

    def get_feature_dependencies(self, feature: str) -> List[str]:
        """Get dependencies for a feature."""
        return self._driver.list_inputs([feature])

    def visualize_dag(self, output_path: str = "feature_dag.png"):
        """Visualize the feature DAG."""
        # Hamilton has built-in visualization
        try:
            self._driver.visualize_execution(
                self.list_features(),
                output_file_path=output_path
            )
            logger.info(f"DAG visualization saved to {output_path}")
        except Exception as e:
            logger.warning(f"Could not visualize DAG: {e}")


# Convenience function
def compute_features(
    price_data: pd.DataFrame,
    features: Optional[List[str]] = None
) -> pd.DataFrame:
    """
    Compute features from price data.

    Args:
        price_data: DataFrame with OHLCV data
        features: Optional list of features (default: all)

    Returns:
        DataFrame with features

    Example:
        df = pd.read_parquet("prices.parquet")
        features = compute_features(df, ['rsi_14', 'macd_histogram'])
    """
    pipeline = FeaturePipeline()

    if features is None:
        return pipeline.compute_all(price_data)
    return pipeline.compute(price_data, features)
```

### 5.3 Feast Feature Store Integration

```python
# src/equity_lake/features/feast_store.py
"""
Feast feature store integration for serving features to ML models.

This module provides:
1. Feature definitions (Feature Views)
2. Feature serving (online and offline)
3. Feature registry

Setup:
    pip install feast

Usage:
    # Apply feature definitions
    feast apply

    # Materialize features to online store
    feast materialize-range 2024-01-01 2024-01-31

    # Get features for inference
    store = FeatureStore(repo_path=".")
    features = store.get_online_features(
        features=["price_features:rsi_14", "price_features:macd"],
        entity_rows=[{"ticker": "AAPL", "date": "2024-01-15"}]
    )
"""
from datetime import timedelta
from feast import (
    Entity, Feature, FeatureView, FileSource,
    ValueType, Field
)
from feast.types import Float32, Int64
from pydantic import BaseModel
from typing import List, Dict, Optional
import pandas as pd
from datetime import datetime

# =============================================================================
# Feast Feature Definitions (for feast apply)
# =============================================================================

# Define entities
ticker_entity = Entity(
    name="ticker",
    value_type=ValueType.STRING,
    description="Stock ticker symbol"
)

date_entity = Entity(
    name="date",
    value_type=ValueType.STRING,
    description="Trading date"
)

# Define data source (Parquet files)
price_source = FileSource(
    path="data/lake/us_equity/**/*.parquet",
    event_timestamp_column="date",
    file_format="parquet"
)

feature_source = FileSource(
    path="data/features/**/*.parquet",
    event_timestamp_column="date",
    file_format="parquet"
)

# Define feature views
price_features = FeatureView(
    name="price_features",
    entities=[ticker_entity],
    ttl=timedelta(days=365),
    schema=[
        Field(name="open", dtype=Float32),
        Field(name="high", dtype=Float32),
        Field(name="low", dtype=Float32),
        Field(name="close", dtype=Float32),
        Field(name="volume", dtype=Int64),
    ],
    source=price_source,
)

technical_features = FeatureView(
    name="technical_features",
    entities=[ticker_entity],
    ttl=timedelta(days=365),
    schema=[
        Field(name="rsi_14", dtype=Float32),
        Field(name="macd", dtype=Float32),
        Field(name="macd_signal", dtype=Float32),
        Field(name="macd_histogram", dtype=Float32),
        Field(name="atr_14", dtype=Float32),
        Field(name="bb_upper", dtype=Float32),
        Field(name="bb_lower", dtype=Float32),
        Field(name="sma_20", dtype=Float32),
        Field(name="sma_50", dtype=Float32),
        Field(name="volatility_20", dtype=Float32),
    ],
    source=feature_source,
)

# =============================================================================
# Feature Store Client
# =============================================================================

class FeatureStoreClient:
    """
    Client for interacting with Feast feature store.

    Usage:
        client = FeatureStoreClient(repo_path="./feature_repo")

        # Get features for training (offline)
        df = client.get_historical_features(
            entity_df=entity_df,
            features=["price_features:close", "technical_features:rsi_14"]
        )

        # Get features for inference (online)
        features = client.get_online_features(
            tickers=["AAPL", "MSFT"],
            features=["price_features:close", "technical_features:rsi_14"]
        )
    """

    def __init__(self, repo_path: str = "./feature_repo"):
        """
        Initialize feature store client.

        Args:
            repo_path: Path to Feast feature repository
        """
        try:
            from feast import FeatureStore
            self.store = FeatureStore(repo_path=repo_path)
        except ImportError:
            raise ImportError(
                "Feast not installed. Install with: pip install feast"
            )

    def get_historical_features(
        self,
        entity_df: pd.DataFrame,
        features: List[str]
    ) -> pd.DataFrame:
        """
        Get historical features for training.

        Args:
            entity_df: DataFrame with entity keys and timestamps
            features: List of feature references (e.g., ["price_features:close"])

        Returns:
            DataFrame with features joined
        """
        return self.store.get_historical_features(
            entity_df=entity_df,
            features=features
        ).to_df()

    def get_online_features(
        self,
        tickers: List[str],
        features: List[str],
        dates: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Get features for online inference.

        Args:
            tickers: List of ticker symbols
            features: List of feature references
            dates: Optional list of dates (default: today)

        Returns:
            DataFrame with features
        """
        if dates is None:
            dates = [datetime.now().strftime("%Y-%m-%d")] * len(tickers)

        entity_rows = [
            {"ticker": ticker, "date": date}
            for ticker, date in zip(tickers, dates)
        ]

        result = self.store.get_online_features(
            features=features,
            entity_rows=entity_rows
        )

        return result.to_df()

    def materialize_incremental(
        self,
        end_date: datetime
    ):
        """
        Materialize features up to end_date.

        Args:
            end_date: End date for materialization
        """
        self.store.materialize_incremental(end_date=end_date)

    def materialize_range(
        self,
        start_date: datetime,
        end_date: datetime
    ):
        """
        Materialize features for a date range.

        Args:
            start_date: Start date
            end_date: End date
        """
        self.store.materialize(start_date, end_date)


# =============================================================================
# Combined Feature Engineering + Storage Pipeline
# =============================================================================

class FeatureEngineeringPipeline:
    """
    Complete feature engineering pipeline with Hamilton + Feast.

    1. Hamilton computes features from raw data
    2. Feast stores and serves features

    Usage:
        pipeline = FeatureEngineeringPipeline()

        # Compute and store features
        pipeline.run(price_data=df, tickers=["AAPL", "MSFT"])

        # Retrieve features
        features = pipeline.get_features(tickers=["AAPL"], features=["rsi_14"])
    """

    def __init__(
        self,
        feast_repo_path: Optional[str] = None,
        hamilton_cache_dir: str = "./data/feature_cache"
    ):
        self.hamilton = FeaturePipeline(cache_dir=hamilton_cache_dir)
        self.feast = FeatureStoreClient(feast_repo_path) if feast_repo_path else None

    def compute_and_store(
        self,
        price_data: pd.DataFrame,
        features: Optional[List[str]] = None
    ) -> pd.DataFrame:
        """
        Compute features and optionally store in Feast.

        Args:
            price_data: Raw price data
            features: Features to compute (default: all)

        Returns:
            DataFrame with computed features
        """
        # Compute features with Hamilton
        if features is None:
            feature_df = self.hamilton.compute_all(price_data)
        else:
            feature_df = self.hamilton.compute(price_data, features)

        # Add timestamp for Feast
        feature_df['event_timestamp'] = feature_df['date']

        # Store to Parquet (Feast will read from here)
        self._save_features(feature_df)

        return feature_df

    def _save_features(self, df: pd.DataFrame):
        """Save features to Parquet files for Feast."""
        import os
        output_dir = "./data/features"
        os.makedirs(output_dir, exist_ok=True)

        # Partition by date
        for date in df['date'].unique():
            date_str = pd.to_datetime(date).strftime("%Y-%m-%d")
            date_df = df[df['date'] == date]
            date_df.to_parquet(f"{output_dir}/date={date_str}/features.parquet")

    def get_features(
        self,
        tickers: List[str],
        features: List[str]
    ) -> pd.DataFrame:
        """Get features from Feast online store."""
        if self.feast is None:
            raise ValueError("Feast not configured")
        return self.feast.get_online_features(tickers, features)
```

---

## Phase 6: Next.js Web Dashboard

**Timeline**: Weeks 16-20
**Priority**: Medium
**Goal**: Modern web UI for data management

### Libraries to Use

| Library | Purpose | Why |
|---------|---------|-----|
| **Next.js 14+** | React framework | App Router, Server Components, API routes |
| **shadcn/ui** | UI components | Beautiful, accessible, customizable |
| **TanStack Query** | Data fetching | Caching, optimistic updates |
| **DuckDB-WASM** | Client-side SQL | Query data directly in browser |
| **Recharts** | Charts | React-native charting |
| **TailwindCSS** | Styling | Utility-first CSS |

### 6.1 Next.js Project Structure

```
dashboard/
├── src/
│   ├── app/
│   │   ├── layout.tsx           # Root layout
│   │   ├── page.tsx             # Home page
│   │   ├── data/
│   │   │   ├── page.tsx         # Data browser
│   │   │   └── [source]/
│   │   │       └── page.tsx     # Source detail
│   │   ├── updates/
│   │   │   ├── page.tsx         # Update management
│   │   │   └── history/
│   │   │       └── page.tsx     # Update history
│   │   ├── features/
│   │   │   ├── page.tsx         # Feature engineering
│   │   │   └── pipeline/
│   │   │       └── page.tsx     # Pipeline config
│   │   ├── signals/
│   │   │   └── page.tsx         # Signal scanner
│   │   ├── settings/
│   │   │   └── page.tsx         # Configuration
│   │   └── api/
│   │       ├── data/
│   │       │   └── route.ts     # Data API
│   │       ├── updates/
│   │       │   └── route.ts     # Updates API
│   │       ├── features/
│   │       │   └── route.ts     # Features API
│   │       └── config/
│   │           └── route.ts     # Config API
│   ├── components/
│   │   ├── ui/                  # shadcn/ui components
│   │   ├── charts/              # Chart components
│   │   ├── data-table.tsx       # Data table
│   │   ├── update-status.tsx    # Update status widget
│   │   └── signal-card.tsx      # Signal display
│   ├── lib/
│   │   ├── api.ts               # API client
│   │   ├── duckdb.ts            # DuckDB-WASM setup
│   │   └── utils.ts             # Utilities
│   └── hooks/
│       ├── use-data.ts          # Data fetching hooks
│       └── use-updates.ts       # Update hooks
├── public/
│   └── data/                    # Static data files
├── package.json
├── tailwind.config.js
├── next.config.js
└── tsconfig.json
```

### 6.2 Core Components

```tsx
// src/app/page.tsx - Dashboard Home
"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataFreshnessChart } from "@/components/charts/freshness-chart";
import { RecentUpdatesTable } from "@/components/update-status";

export default function Dashboard() {
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => fetch("/api/stats").then((r) => r.json()),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Equity Lake Dashboard</h1>

      {/* Key Metrics */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Total Records
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {stats?.totalRecords?.toLocaleString() || "..."}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Data Sources
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats?.sources || 0}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Symbols
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {stats?.symbols?.toLocaleString() || "..."}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Last Update
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{stats?.lastUpdate || "..."}</div>
          </CardContent>
        </Card>
      </div>

      {/* Data Freshness Chart */}
      <Card>
        <CardHeader>
          <CardTitle>Data Freshness by Source</CardTitle>
        </CardHeader>
        <CardContent>
          <DataFreshnessChart />
        </CardContent>
      </Card>

      {/* Recent Updates */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Updates</CardTitle>
        </CardHeader>
        <CardContent>
          <RecentUpdatesTable />
        </CardContent>
      </Card>
    </div>
  );
}
```

```tsx
// src/app/data/page.tsx - Data Browser
"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DataTable } from "@/components/data-table";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export default function DataBrowser() {
  const [source, setSource] = useState("us_equity");
  const [symbol, setSymbol] = useState("");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");

  const { data, isLoading } = useQuery({
    queryKey: ["data", source, symbol, startDate, endDate],
    queryFn: () => {
      const params = new URLSearchParams({
        source,
        symbol,
        startDate,
        endDate,
      });
      return fetch(`/api/data?${params}`).then((r) => r.json());
    },
    enabled: symbol.length > 0,
  });

  return (
    <div className="space-y-6">
      <h1 className="text-3xl font-bold">Data Browser</h1>

      {/* Filters */}
      <div className="flex gap-4 items-end">
        <div className="space-y-2">
          <label className="text-sm font-medium">Source</label>
          <Select value={source} onValueChange={setSource}>
            <SelectTrigger className="w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="us_equity">US Equities</SelectItem>
              <SelectItem value="cn_ashare">China A-Shares</SelectItem>
              <SelectItem value="hk_sg">HK/SG</SelectItem>
              <SelectItem value="macro">Macro</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">Symbol</label>
          <Input
            placeholder="AAPL"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value.toUpperCase())}
            className="w-32"
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">Start Date</label>
          <Input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-40"
          />
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">End Date</label>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-40"
          />
        </div>

        <Button variant="outline" onClick={() => {
          setSymbol("");
          setStartDate("");
          setEndDate("");
        }}>
          Clear
        </Button>
      </div>

      {/* Data Table */}
      {isLoading ? (
        <div className="text-center py-8">Loading...</div>
      ) : data?.rows ? (
        <DataTable
          data={data.rows}
          columns={data.columns}
          onExport={() => {
            // Export to CSV
            const csv = [
              data.columns.join(","),
              ...data.rows.map((r: any) => Object.values(r).join(",")),
            ].join("\n");
            const blob = new Blob([csv], { type: "text/csv" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = `${source}_${symbol}.csv`;
            a.click();
          }}
        />
      ) : (
        <div className="text-center py-8 text-muted-foreground">
          Enter a symbol to view data
        </div>
      )}
    </div>
  );
}
```

```tsx
// src/app/api/data/route.ts - Data API
import { NextRequest, NextResponse } from "next/server";
import { DuckDBInstance } from "@duckdb/duckdb-wasm";

// This would connect to the Python backend in production
// For demo, using DuckDB-WASM for client-side queries

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const source = searchParams.get("source") || "us_equity";
  const symbol = searchParams.get("symbol") || "";
  const startDate = searchParams.get("startDate") || "";
  const endDate = searchParams.get("endDate") || "";

  // In production, this would call the Python backend
  // For now, return mock data structure
  const mockData = {
    columns: ["ticker", "date", "open", "high", "low", "close", "volume"],
    rows: [],
  };

  // Call Python backend
  const backendUrl = process.env.BACKEND_URL || "http://localhost:8000";
  const response = await fetch(
    `${backendUrl}/api/v1/data/prices?${searchParams.toString()}`
  );

  if (!response.ok) {
    return NextResponse.json(
      { error: "Failed to fetch data" },
      { status: 500 }
    );
  }

  const data = await response.json();
  return NextResponse.json(data);
}
```

### 6.3 DuckDB-WASM for Client-Side Queries

```typescript
// src/lib/duckdb.ts
import * as duckdb from "@duckdb/duckdb-wasm";

let db: duckdb.AsyncDuckDB | null = null;

export async function initDuckDB(): Promise<duckdb.AsyncDuckDB> {
  if (db) return db;

  const JSDELIVR_BUNDLES = {
    mvp: {
      mainModule: "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm/dist/duckdb-mvp.wasm",
      mainWorker: "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm/dist/duckdb-browser-mvp.worker.js",
    },
    eh: {
      mainModule: "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm/dist/duckdb-eh.wasm",
      mainWorker: "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm/dist/duckdb-browser-eh.worker.js",
    },
  };

  const bundle = await duckdb.selectBundle(JSDELIVR_BUNDLES);
  const worker = new Worker(bundle.mainWorker!);
  const logger = new duckdb.ConsoleLogger();

  db = new duckdb.AsyncDuckDB(logger, worker);
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);

  return db;
}

export async function queryParquet(
  filePath: string,
  sql: string
): Promise<any[]> {
  const db = await initDuckDB();
  const conn = await db.connect();

  // Register Parquet file
  await db.registerFileURL(filePath, filePath);

  // Execute query
  const result = await conn.query(sql);
  return result.toArray();
}

export async function loadDataFrame(
  parquetUrl: string
): Promise<{ columns: string[]; rows: any[] }> {
  const db = await initDuckDB();
  const conn = await db.connect();

  // Register and query
  await db.registerFileURL("data.parquet", parquetUrl);

  const result = await conn.query(`
    SELECT * FROM 'data.parquet'
    ORDER BY date DESC
    LIMIT 1000
  `);

  const rows = result.toArray();
  const columns = result.schema.fields.map((f) => f.name);

  return { columns, rows };
}
```

---

## Phase 7: Alternative Data Sources

**Timeline**: Weeks 21-24
**Priority**: Medium
**Goal**: Expand data coverage

### Libraries to Use

| Library | Purpose | Why |
|---------|---------|-----|
| **PRAW** | Reddit API | Official Python Reddit wrapper |
| **sec-edgar-downloader** | SEC filings | Easy 10-K, 10-Q, Form 4 downloads |
| **vaderSentiment** | Sentiment | Already in use, works well |
| **yfinance** | Options data | Already have it, add options chain |

### 7.1 Reddit Sentiment Loader

```python
# src/equity_lake/loaders/reddit_loader.py
from .base import BaseDataLoader, LoaderMetadata, LoadResult
from datetime import date
from typing import Dict, List, Any, Optional
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class RedditSentimentLoader(BaseDataLoader):
    """
    Reddit sentiment data loader using PRAW.

    Free Tier:
    - 100 requests per minute
    - OAuth required (free app registration)

    Setup:
        1. Go to https://www.reddit.com/prefs/apps
        2. Create a "script" type app
        3. Note client_id and client_secret

    Usage:
        loader = RedditSentimentLoader({
            "client_id": "xxx",
            "client_secret": "xxx",
            "user_agent": "equity_lake/1.0"
        })
        result = loader.load(["AAPL", "MSFT"], date.today() - timedelta(days=7), date.today())
    """

    metadata = LoaderMetadata(
        name="reddit_sentiment",
        version="1.0.0",
        description="Reddit sentiment analysis for stock tickers",
        author="Equity Lake Team",
        supported_markets=["US"],
        supported_intervals=["1d"],
        rate_limit={"requests_per_minute": 100},
        requires_auth=True,
        data_types=["sentiment"]
    )

    def _validate_config(self) -> None:
        required = ["client_id", "client_secret", "user_agent"]
        for key in required:
            if key not in self.config:
                raise ValueError(f"Missing required config: {key}")

        self.subreddits = self.config.get("subreddits", ["wallstreetbets", "stocks", "investing"])
        self.posts_per_symbol = self.config.get("posts_per_symbol", 100)

    def load(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        interval: str = "1d"
    ) -> LoadResult:
        """Fetch and analyze Reddit posts for sentiment."""
        import praw
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

        try:
            reddit = praw.Reddit(
                client_id=self.config["client_id"],
                client_secret=self.config["client_secret"],
                user_agent=self.config["user_agent"]
            )

            analyzer = SentimentIntensityAnalyzer()
            records = []

            for symbol in symbols:
                sentiment_data = self._fetch_symbol_sentiment(
                    reddit, analyzer, symbol
                )
                records.extend(sentiment_data)

            df = pd.DataFrame(records)

            return LoadResult(
                success=True,
                data=df,
                records_count=len(df),
                metadata={"source": "reddit", "subreddits": self.subreddits}
            )

        except Exception as e:
            logger.error(f"Reddit sentiment fetch error: {e}")
            return LoadResult(success=False, errors=[str(e)])

    def _fetch_symbol_sentiment(
        self,
        reddit,
        analyzer,
        symbol: str
    ) -> List[Dict]:
        """Fetch posts and compute sentiment for a symbol."""
        from datetime import datetime

        records = []
        search_query = f"${symbol} OR {symbol}"

        for subreddit_name in self.subreddits:
            try:
                subreddit = reddit.subreddit(subreddit_name)
                posts = subreddit.search(
                    search_query,
                    sort="relevance",
                    time_filter="week",
                    limit=self.posts_per_symbol
                )

                for post in posts:
                    # Combine title and selftext
                    text = f"{post.title} {post.selftext}"

                    # Analyze sentiment
                    scores = analyzer.polarity_scores(text)

                    records.append({
                        "ticker": symbol,
                        "date": datetime.fromtimestamp(post.created_utc).date(),
                        "subreddit": subreddit_name,
                        "title": post.title[:200],
                        "score": post.score,
                        "num_comments": post.num_comments,
                        "compound_sentiment": scores["compound"],
                        "positive_sentiment": scores["pos"],
                        "negative_sentiment": scores["neg"],
                        "neutral_sentiment": scores["neu"],
                        "url": f"https://reddit.com{post.permalink}"
                    })

            except Exception as e:
                logger.warning(f"Error fetching from r/{subreddit_name}: {e}")

        return records

    def get_available_symbols(self) -> List[str]:
        """Return popular tickers (would need configuration for full list)."""
        return ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META"]

    def validate_connection(self) -> bool:
        """Test Reddit API connection."""
        import praw
        try:
            reddit = praw.Reddit(
                client_id=self.config["client_id"],
                client_secret=self.config["client_secret"],
                user_agent=self.config["user_agent"]
            )
            reddit.user.me()
            return True
        except:
            return False
```

### 7.2 SEC Filings Loader

```python
# src/equity_lake/loaders/sec_loader.py
from .base import BaseDataLoader, LoaderMetadata, LoadResult
from datetime import date
from typing import Dict, List, Any
import pandas as pd
import logging
import os

logger = logging.getLogger(__name__)

class SECFilingsLoader(BaseDataLoader):
    """
    SEC EDGAR filings loader.

    Free access, rate limited to 10 requests/second.
    Provides 10-K, 10-Q, 8-K, Form 4 (insider trades), etc.

    Setup:
        pip install sec-edgar-downloader

    Usage:
        loader = SECFilingsLoader({
            "user_agent": "Your Company your.email@example.com"
        })
        result = loader.load_filing_types(["AAPL"], ["10-K", "10-Q", "4"])
    """

    metadata = LoaderMetadata(
        name="sec_filings",
        version="1.0.0",
        description="SEC EDGAR filings (10-K, 10-Q, 8-K, Form 4)",
        author="Equity Lake Team",
        supported_markets=["US"],
        supported_intervals=["1d"],
        rate_limit={"requests_per_second": 10},
        requires_auth=False,
        data_types=["filings", "insider_trades"]
    )

    FILING_TYPES = {
        "10-K": "Annual Report",
        "10-Q": "Quarterly Report",
        "8-K": "Current Report",
        "4": "Insider Trading",
        "13F": "Institutional Holdings",
        "DEF 14A": "Proxy Statement"
    }

    def _validate_config(self) -> None:
        self.user_agent = self.config.get(
            "user_agent",
            "Equity Lake contact@equitylake.io"
        )
        self.download_path = self.config.get("download_path", "./data/sec_filings")
        os.makedirs(self.download_path, exist_ok=True)

    def load(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        interval: str = "1d"
    ) -> LoadResult:
        """Load SEC filings for symbols."""
        from sec_edgar_downloader import Downloader

        try:
            dl = Downloader(
                company_name="Equity Lake",
                email_address="contact@equitylake.io",
                download_folder=self.download_path
            )

            records = []

            for symbol in symbols:
                for filing_type in ["10-K", "10-Q", "4"]:
                    try:
                        # Download filings
                        dl.get(
                            filing_type,
                            symbol,
                            after=start_date.strftime("%Y-%m-%d"),
                            before=end_date.strftime("%Y-%m-%d")
                        )

                        # Parse downloaded files
                        filing_records = self._parse_filings(symbol, filing_type)
                        records.extend(filing_records)

                    except Exception as e:
                        logger.warning(f"Error fetching {filing_type} for {symbol}: {e}")

            df = pd.DataFrame(records)

            return LoadResult(
                success=True,
                data=df,
                records_count=len(df),
                metadata={"source": "sec_edgar", "download_path": self.download_path}
            )

        except Exception as e:
            logger.error(f"SEC filings fetch error: {e}")
            return LoadResult(success=False, errors=[str(e)])

    def _parse_filings(self, symbol: str, filing_type: str) -> List[Dict]:
        """Parse downloaded SEC filings."""
        import glob
from bs4 import BeautifulSoup

        records = []
        pattern = f"{self.download_path}/sec_edgar_filings/{symbol}/{filing_type}/**/*.txt"
        files = glob.glob(pattern, recursive=True)

        for filepath in files[-10:]:  # Limit to recent 10 files
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                # Extract metadata
                filing_date = self._extract_filing_date(filepath)
                accession = self._extract_accession(filepath)

                # Parse Form 4 (insider trades) specifically
                if filing_type == "4":
                    trades = self._parse_form4(content, symbol, filing_date)
                    records.extend(trades)
                else:
                    records.append({
                        "ticker": symbol,
                        "date": filing_date,
                        "filing_type": filing_type,
                        "accession_number": accession,
                        "file_path": filepath,
                        "content_length": len(content)
                    })

            except Exception as e:
                logger.warning(f"Error parsing {filepath}: {e}")

        return records

    def _extract_filing_date(self, filepath: str) -> date:
        """Extract filing date from filepath or content."""
        import re
        match = re.search(r'/(\d{4}-\d{2}-\d{2})/', filepath)
        if match:
            return date.fromisoformat(match.group(1))
        return date.today()

    def _extract_accession(self, filepath: str) -> str:
        """Extract accession number from filepath."""
        import re
        match = re.search(r'/(\d{10}-\d{2}-\d{6})/', filepath)
        return match.group(1) if match else ""

    def _parse_form4(self, content: str, symbol: str, filing_date: date) -> List[Dict]:
        """Parse Form 4 insider trading data."""
        from bs4 import BeautifulSoup

        trades = []
        try:
            soup = BeautifulSoup(content, 'xml')

            # Find all nonDerivativeTransaction elements
            transactions = soup.find_all('nonDerivativeTransaction')

            for trans in transactions:
                try:
                    trade = {
                        "ticker": symbol,
                        "date": filing_date,
                        "filing_type": "4",
                        "insider_name": soup.find('rptOwnerName').text if soup.find('rptOwnerName') else "",
                        "insider_title": soup.find('rptOwnerTitle').text if soup.find('rptOwnerTitle') else "",
                        "transaction_date": trans.find('transactionDate').find('value').text if trans.find('transactionDate') else "",
                        "transaction_code": trans.find('transactionCode').text if trans.find('transactionCode') else "",
                        "shares": float(trans.find('transactionShares').find('value').text) if trans.find('transactionShares') else 0,
                        "price": float(trans.find('transactionPricePerShare').find('value').text) if trans.find('transactionPricePerShare') else 0,
                    }
                    trade["total_value"] = trade["shares"] * trade["price"]
                    trades.append(trade)
                except:
                    continue

        except Exception as e:
            logger.warning(f"Form 4 parsing error: {e}")

        return trades

    def get_available_symbols(self) -> List[str]:
        """All US public companies."""
        # Would need to fetch from SEC company index
        return []

    def validate_connection(self) -> bool:
        """Test SEC EDGAR access."""
        import requests
        try:
            resp = requests.get(
                "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany",
                headers={"User-Agent": self.user_agent},
                timeout=10
            )
            return resp.status_code == 200
        except:
            return False
```

### 7.3 Options Flow (DIY from yfinance)

```python
# src/equity_lake/loaders/options_flow_loader.py
from .base import BaseDataLoader, LoaderMetadata, LoadResult
from datetime import date
from typing import Dict, List, Any
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

class OptionsFlowLoader(BaseDataLoader):
    """
    Options flow data loader using yfinance.

    Detects unusual options activity by comparing volume to open interest.

    Free (via yfinance), no authentication required.

    Usage:
        loader = OptionsFlowLoader({})
        result = loader.load(["AAPL"], date.today(), date.today())
        # Returns options with volume > 2x open interest
    """

    metadata = LoaderMetadata(
        name="options_flow",
        version="1.0.0",
        description="Options flow and unusual activity detector (via yfinance)",
        author="Equity Lake Team",
        supported_markets=["US"],
        supported_intervals=["1d"],
        rate_limit={"requests_per_minute": 60},
        requires_auth=False,
        data_types=["options"]
    )

    def _validate_config(self) -> None:
        self.volume_threshold = self.config.get("volume_threshold", 2.0)
        self.min_open_interest = self.config.get("min_open_interest", 100)

    def load(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        interval: str = "1d"
    ) -> LoadResult:
        """Fetch options data and detect unusual activity."""
        import yfinance as yf

        records = []

        for symbol in symbols:
            try:
                ticker = yf.Ticker(symbol)

                # Get all available expiration dates
                expirations = ticker.options

                for exp in expirations[:3]:  # Limit to next 3 expirations
                    try:
                        chain = ticker.option_chain(exp)

                        # Process calls
                        calls = self._process_options(chain.calls, symbol, exp, "CALL")
                        records.extend(calls)

                        # Process puts
                        puts = self._process_options(chain.puts, symbol, exp, "PUT")
                        records.extend(puts)

                    except Exception as e:
                        logger.warning(f"Error fetching options for {symbol} exp {exp}: {e}")

            except Exception as e:
                logger.error(f"Error fetching options for {symbol}: {e}")

        df = pd.DataFrame(records)

        # Filter for unusual activity
        if not df.empty:
            df = df[df['is_unusual'] == True]

        return LoadResult(
            success=True,
            data=df,
            records_count=len(df),
            metadata={"source": "yfinance_options"}
        )

    def _process_options(
        self,
        options_df: pd.DataFrame,
        symbol: str,
        expiration: str,
        option_type: str
    ) -> List[Dict]:
        """Process options chain and detect unusual activity."""
        records = []

        for _, row in options_df.iterrows():
            volume = row.get('volume', 0) or 0
            open_interest = row.get('openInterest', 0) or 0

            if open_interest < self.min_open_interest:
                continue

            volume_oi_ratio = volume / open_interest if open_interest > 0 else 0
            is_unusual = volume_oi_ratio >= self.volume_threshold

            records.append({
                "ticker": symbol,
                "date": date.today(),
                "expiration": expiration,
                "option_type": option_type,
                "strike": row.get('strike', 0),
                "last_price": row.get('lastPrice', 0),
                "bid": row.get('bid', 0),
                "ask": row.get('ask', 0),
                "volume": volume,
                "open_interest": open_interest,
                "volume_oi_ratio": round(volume_oi_ratio, 2),
                "implied_volatility": row.get('impliedVolatility', 0),
                "is_unusual": is_unusual,
                "in_the_money": row.get('inTheMoney', False),
                "contract_symbol": row.get('contractSymbol', "")
            })

        return records

    def get_available_symbols(self) -> List[str]:
        """US optionable stocks."""
        return []

    def validate_connection(self) -> bool:
        """Test yfinance options access."""
        import yfinance as yf
        try:
            ticker = yf.Ticker("AAPL")
            exps = ticker.options
            return len(exps) > 0
        except:
            return False
```

---

## Summary: Prioritized Roadmap

### Phase 1-3: Core Enhancements (Weeks 1-9)

| Phase | Feature | Libraries |
|-------|---------|-----------|
| **1** | Configuration System | Pydantic v2, Pydantic-settings, watchdog, croniter |
| **2** | Plugin Architecture | importlib.metadata, pluggy (optional) |
| **3** | Smart Updates | APScheduler, tenacity, networkx |

### Phase 4-5: Data Quality & Features (Weeks 10-15)

| Phase | Feature | Libraries |
|-------|---------|-----------|
| **4** | Data Quality Framework | **Pandera** (schema), **whylogs** (profiling) |
| **5** | Feature Store | **Hamilton** (DAG), **Feast** (serving) |

### Phase 6: Web Dashboard (Weeks 16-20)

| Feature | Libraries |
|---------|-----------|
| Next.js Dashboard | Next.js 14+, shadcn/ui, TanStack Query |
| Client-side SQL | DuckDB-WASM |
| Charts | Recharts |

### Phase 7: Alternative Data (Weeks 21-24)

| Data Source | Library |
|-------------|---------|
| Reddit Sentiment | PRAW, vaderSentiment |
| SEC Filings | sec-edgar-downloader |
| Options Flow | yfinance (DIY) |

---

*Document Version: 2.1*
*Last Updated: 2026-04-06*
*Based on: equity_lake v0.4.0*
