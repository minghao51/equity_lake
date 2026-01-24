"""
Equity EOD Data Pipeline - Scripts Module

This module contains scripts for:
- Syncing historical data from S3
- Daily EOD data ingestion from various APIs
- Data validation and quality checks
- Query examples and analysis tools
"""

__version__ = "0.1.0"
__author__ = "Equity Data Pipeline Team"

# Common imports used across scripts
from pathlib import Path
import logging
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Data directories
DATA_DIR = PROJECT_ROOT / "data"
LAKE_DIR = DATA_DIR / "lake"
LOGS_DIR = PROJECT_ROOT / "logs"

# Ensure directories exist
LAKE_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Market directories
US_EQUITY_DIR = LAKE_DIR / "us_equity"
CN_ASHARE_DIR = LAKE_DIR / "cn_ashare"
HK_SG_EQUITY_DIR = LAKE_DIR / "hk_sg_equity"

# Standard OHLCV schema
STANDARD_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close"
]

def setup_logging(
    name: str,
    level: str = "INFO",
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Setup logging configuration for scripts.

    Now uses structured logging with JSON output for better observability.

    Args:
        name: Logger name
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        log_file: Optional log file name (will be placed in LOGS_DIR)

    Returns:
        Logger instance (standard library logger for backward compatibility)
    """
    # Import here to avoid circular dependency
    from scripts.logging_utils import setup_structured_logging

    # Convert log_file string to Path if provided
    log_path = None
    if log_file:
        log_path = LOGS_DIR / log_file

    # Setup structured logging (JSON output by default)
    structlog_logger = setup_structured_logging(
        level=level,
        log_file=log_path,
        json_output=True,
        console=True
    )

    # Return standard library logger for backward compatibility
    stdlib_logger = logging.getLogger(name)
    stdlib_logger.setLevel(getattr(logging, level.upper()))

    return stdlib_logger

def get_project_config() -> Dict[str, str]:
    """Get project configuration from environment variables."""
    import os
    from dotenv import load_dotenv

    # Load environment variables
    load_dotenv()

    config = {
        "aws_access_key_id": os.getenv("AWS_ACCESS_KEY_ID", ""),
        "aws_secret_access_key": os.getenv("AWS_SECRET_ACCESS_KEY", ""),
        "s3_bucket": os.getenv("S3_BUCKET", ""),
        "db_path": os.getenv("DB_PATH", "equity_data.duckdb"),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "log_dir": os.getenv("LOG_DIR", "logs"),
        "data_dir": os.getenv("DATA_DIR", "data"),
        "markets": os.getenv("MARKETS", "us,cn,hk,sg"),
        "dev_mode": os.getenv("DEV_MODE", "false").lower() == "true",
        "use_test_data": os.getenv("USE_TEST_DATA", "false").lower() == "true",
    }

    return config

def validate_data_directories() -> bool:
    """Validate that all required data directories exist."""
    required_dirs = [
        US_EQUITY_DIR,
        CN_ASHARE_DIR,
        HK_SG_EQUITY_DIR,
        LOGS_DIR
    ]

    for dir_path in required_dirs:
        if not dir_path.exists():
            dir_path.mkdir(parents=True, exist_ok=True)
            print(f"Created directory: {dir_path}")

    return True

__all__ = [
    "PROJECT_ROOT",
    "DATA_DIR",
    "LAKE_DIR",
    "LOGS_DIR",
    "US_EQUITY_DIR",
    "CN_ASHARE_DIR",
    "HK_SG_EQUITY_DIR",
    "STANDARD_COLUMNS",
    "setup_logging",
    "get_project_config",
    "validate_data_directories"
]