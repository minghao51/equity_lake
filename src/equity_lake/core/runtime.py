"""Backward-compatible re-exports from :mod:`core.paths`, :mod:`core.schemas`, and :mod:`core.logging`.

.. deprecated::
    Import directly from the focused modules instead:

    * Paths → :mod:`equity_lake.core.paths`
    * Schemas → :mod:`equity_lake.core.schemas`
    * Logging → :mod:`equity_lake.core.logging`
"""

__version__ = "0.1.0"
__author__ = "Equity Data Pipeline Team"

from equity_lake.core.logging import setup_logging  # noqa: F401
from equity_lake.core.paths import (  # noqa: F401
    BASE_DIR,
    CN_ASHARE_DIR,
    CONFIG_DIR,
    DATA_DIR,
    HK_SG_EQUITY_DIR,
    JPX_EQUITY_DIR,
    KRX_EQUITY_DIR,
    LAKE_DIR,
    LOGS_DIR,
    MACRO_INDICATORS_DIR,
    MODELS_DIR,
    PROJECT_ROOT,
    US_EQUITY_DIR,
    US_NEWS_DIR,
    US_SOCIAL_SENTIMENT_DIR,
    ensure_dirs,
)
from equity_lake.core.schemas import (  # noqa: F401
    MACRO_COLUMNS,
    MACRO_INDICATOR_CONFIG,
    NEWS_COLUMNS,
    SOCIAL_COLUMNS,
    STANDARD_COLUMNS,
)


def get_project_config() -> dict[str, str | int | float | bool]:
    """Get the merged project configuration."""
    from equity_lake.core.config import get_settings

    settings = get_settings()
    return {
        "db_path": settings.storage.db_path,
        "log_level": "INFO",
        "log_dir": str(LOGS_DIR),
        "data_dir": str(DATA_DIR),
        "markets": ",".join(settings.ingestion.default_markets),
        "dev_mode": settings.project.environment == "development",
        "use_test_data": settings.project.environment == "testing",
        "retry_attempts": 3,
        "retry_delay": 1.0,
    }


def validate_data_directories() -> bool:
    """Validate and create required data directories."""
    ensure_dirs()
    return True


__all__ = [
    "BASE_DIR",
    "CN_ASHARE_DIR",
    "CONFIG_DIR",
    "DATA_DIR",
    "HK_SG_EQUITY_DIR",
    "JPX_EQUITY_DIR",
    "KRX_EQUITY_DIR",
    "LAKE_DIR",
    "LOGS_DIR",
    "MACRO_COLUMNS",
    "MACRO_INDICATOR_CONFIG",
    "MACRO_INDICATORS_DIR",
    "MODELS_DIR",
    "NEWS_COLUMNS",
    "PROJECT_ROOT",
    "SOCIAL_COLUMNS",
    "STANDARD_COLUMNS",
    "US_EQUITY_DIR",
    "US_NEWS_DIR",
    "US_SOCIAL_SENTIMENT_DIR",
    "ensure_dirs",
    "get_project_config",
    "setup_logging",
    "validate_data_directories",
]
