"""Unified configuration — re-export shim for backwards compatibility.

All models and loaders now live in:
- :mod:`equity_lake.core.config_models` — ticker config Pydantic models
- :mod:`equity_lake.core.settings` — application settings and loaders

This module re-exports every public name so that existing
``from equity_lake.core.config import TickerConfig, Settings`` imports
continue to work unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import Field

from equity_lake.core.config_models import (  # noqa: F401 — re-export
    DEFAULT_TICKERS_PATH,
    GroupConfig,
    MarketConfig,
    TickerConfigRoot,
    TickerMetadata,
    ValidationConfig,
)
from equity_lake.core.settings import (  # noqa: F401 — re-export
    AlertingSettings,
    DashboardSettings,
    IngestionSettings,
    MonitoringSettings,
    ProjectSettings,
    ScheduleSettings,
    Settings,
    get_settings,
    load_settings,
)


def clear_settings_cache() -> None:
    get_settings.cache_clear()


class TickerConfig(TickerConfigRoot):
    """Backwards-compatible YAML loader.

    ``TickerConfig(config_path=...)`` reads a YAML file and behaves as a
    ``TickerConfigRoot`` — all selectors are inherited, eliminating the former
    pass-through wrapper. New code should prefer ``TickerConfigRoot.from_yaml()``.
    """

    DEFAULT_CONFIG_PATH: ClassVar[Path] = DEFAULT_TICKERS_PATH
    config_path: Path | None = Field(default=None)

    def __init__(self, config_path: str | Path | None = None, **kwargs: Any) -> None:
        if not kwargs:
            path = Path(config_path) if config_path is not None else DEFAULT_TICKERS_PATH
            kwargs = TickerConfigRoot._load_yaml_data(path)
        super().__init__(**kwargs)
        self.config_path = Path(config_path) if config_path is not None else DEFAULT_TICKERS_PATH

    @classmethod
    def from_path(cls, config_path: str | Path) -> TickerConfig:
        return cls(config_path=Path(config_path))


__all__ = [
    "AlertingSettings",
    "DashboardSettings",
    "GroupConfig",
    "IngestionSettings",
    "MarketConfig",
    "MonitoringSettings",
    "ProjectSettings",
    "ScheduleSettings",
    "Settings",
    "TickerConfig",
    "TickerConfigRoot",
    "TickerMetadata",
    "ValidationConfig",
    "clear_settings_cache",
    "get_settings",
    "load_settings",
]
