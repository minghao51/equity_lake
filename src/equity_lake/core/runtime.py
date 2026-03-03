"""Shared runtime configuration for the equity pipeline package."""

__version__ = "0.1.0"
__author__ = "Equity Data Pipeline Team"

import logging
from pathlib import Path

# Project root directory
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BASE_DIR = PROJECT_ROOT
CONFIG_DIR = PROJECT_ROOT / "config"

# Data directories
DATA_DIR = PROJECT_ROOT / "data"
LAKE_DIR = DATA_DIR / "lake"
LOGS_DIR = PROJECT_ROOT / "logs"
MODELS_DIR = DATA_DIR / "models"

# Ensure directories exist
LAKE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Market directories
US_EQUITY_DIR = LAKE_DIR / "us_equity"
CN_ASHARE_DIR = LAKE_DIR / "cn_ashare"
HK_SG_EQUITY_DIR = LAKE_DIR / "hk_sg_equity"
MACRO_INDICATORS_DIR = LAKE_DIR / "macro_indicators"
US_NEWS_DIR = LAKE_DIR / "us_news"
US_SOCIAL_SENTIMENT_DIR = LAKE_DIR / "us_social_sentiment"

# Macro indicators configuration
MACRO_COLUMNS = [
    "date",
    "indicator",
    "value",
    "source",
    "updated_at",
]

# Macro indicator definitions
MACRO_INDICATOR_CONFIG = {
    "dxy": {"source": "yfinance", "ticker": "^DXY"},
    "treasury_10y": {"source": "yfinance", "ticker": "^TNX"},
    "tips_yield": {"source": "fred", "series": "DFII10"},
    "breakeven_inflation": {"source": "fred", "series": "T10YIE"},
    "vix": {"source": "yfinance", "ticker": "^VIX"},
    "gld": {"source": "yfinance", "ticker": "GLD"},
    "iau": {"source": "yfinance", "ticker": "IAU"},
    "policy_uncertainty": {
        "source": "fred",
        "series": "USEPUINDXD",
    },  # US Economic Policy Uncertainty Index
}

# Standard OHLCV schema
STANDARD_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
]

# News schema
NEWS_COLUMNS = [
    "ticker",  # STRING: Stock symbol
    "date",  # DATE: Published date (partition key)
    "datetime",  # DATETIME: Exact publication timestamp
    "source",  # STRING: News source
    "headline",  # STRING: Article title
    "summary",  # STRING: Article summary
    "url",  # STRING: Article URL
    "category",  # STRING: News category
    "sentiment_score",  # FLOAT: VADER score (-1.0 to 1.0)
    "sentiment_label",  # STRING: 'positive', 'negative', 'neutral'
    "relevance_score",  # FLOAT: API relevance (0.0 to 1.0)
]

# Social sentiment schema
SOCIAL_COLUMNS = [
    "ticker",  # STRING: Stock symbol
    "date",  # DATE: Date of measurement (partition key)
    "datetime",  # DATETIME: Exact timestamp
    "source",  # STRING: 'reddit', 'twitter', etc.
    "mention_count",  # INT: Number of mentions
    "positive_score",  # FLOAT: Positive sentiment score
    "negative_score",  # FLOAT: Negative sentiment score
    "score",  # FLOAT: Normalized sentiment (-1.0 to 1.0)
    "social_metric",  # STRING: Metric type
]


def setup_logging(
    name: str, level: str = "INFO", log_file: str | None = None
) -> logging.Logger:
    """
    Setup logging configuration for equity_lake.

    Now uses structured logging with JSON output for better observability.

    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file name (will be placed in LOGS_DIR)

    Returns:
        Logger instance (standard library logger for backward compatibility)
    """
    # Import here to avoid circular dependency
    from equity_lake.core.logging import setup_structured_logging

    # Convert log_file string to Path if provided
    log_path = None
    if log_file:
        log_path = LOGS_DIR / log_file

    # Setup structured logging (JSON output by default)
    setup_structured_logging(
        level=level, log_file=log_path, json_output=True, console=True
    )

    # Return standard library logger for backward compatibility
    stdlib_logger = logging.getLogger(name)
    stdlib_logger.setLevel(getattr(logging, level.upper()))

    return stdlib_logger


def get_project_config() -> dict[str, str | int | float | bool]:
    """Get project configuration from environment variables."""
    import os

    from dotenv import load_dotenv

    load_dotenv()

    config: dict[str, str | int | float | bool] = {
        "db_path": os.getenv("DB_PATH", "equity_data.duckdb"),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "log_dir": os.getenv("LOG_DIR", "logs"),
        "data_dir": os.getenv("DATA_DIR", str(DATA_DIR)),
        "markets": os.getenv("MARKETS", "us,cn,hk,sg"),
        "dev_mode": os.getenv("DEV_MODE", "false").lower() == "true",
        "use_test_data": os.getenv("USE_TEST_DATA", "false").lower() == "true",
        "retry_attempts": int(os.getenv("API_RETRY_ATTEMPTS", "3")),
        "retry_delay": float(os.getenv("API_RETRY_DELAY", "1.0")),
    }

    return config


def validate_data_directories() -> bool:
    """Validate that all required data directories exist."""
    required_dirs = [US_EQUITY_DIR, CN_ASHARE_DIR, HK_SG_EQUITY_DIR, LOGS_DIR]

    for dir_path in required_dirs:
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"Created directory: {dir_path}")

    return True


__all__ = [
    "PROJECT_ROOT",
    "BASE_DIR",
    "CONFIG_DIR",
    "DATA_DIR",
    "LAKE_DIR",
    "LOGS_DIR",
    "MODELS_DIR",
    "US_EQUITY_DIR",
    "CN_ASHARE_DIR",
    "HK_SG_EQUITY_DIR",
    "MACRO_INDICATORS_DIR",
    "US_NEWS_DIR",
    "US_SOCIAL_SENTIMENT_DIR",
    "STANDARD_COLUMNS",
    "NEWS_COLUMNS",
    "SOCIAL_COLUMNS",
    "MACRO_COLUMNS",
    "MACRO_INDICATOR_CONFIG",
    "setup_logging",
    "get_project_config",
    "validate_data_directories",
]
