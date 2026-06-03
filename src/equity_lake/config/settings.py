"""Application settings for the beta configuration system."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from croniter import croniter
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def _resolve_config_path(config_path: str | Path) -> Path:
    path = Path(config_path).expanduser()
    if path.is_absolute():
        return path
    candidate = PROJECT_ROOT / path
    if candidate.exists():
        return candidate
    return path.resolve()


class ProjectSettings(BaseModel):
    """Project metadata."""

    name: str = "equity-lake"
    version: str = "0.1.0"
    environment: Literal["development", "production", "testing"] = "development"


class StorageSettings(BaseModel):
    """Filesystem locations for runtime artifacts."""

    data_dir: str = "data"
    lake_dir: str = "data/lake"
    logs_dir: str = "logs"
    models_dir: str = "data/models"
    db_path: str = "equity_data.duckdb"


class IngestionSettings(BaseModel):
    """Default ingestion behavior."""

    default_markets: list[str] = Field(default_factory=lambda: ["us", "cn", "hk_sg"])
    parallel: bool = True
    max_workers: int = 3
    ticker_config_path: str = "config/tickers.yaml"


class ScheduleSettings(BaseModel):
    """GitHub Action and cron-oriented scheduling settings."""

    enabled: bool = True
    cron: str = "0 1 * * 1-5"
    timezone: str = "UTC"

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        """Ensure cron expressions are valid before they reach automation."""
        if not croniter.is_valid(value):
            raise ValueError(f"Invalid cron expression: {value}")
        return value


class DashboardSettings(BaseModel):
    """Static dashboard export settings."""

    enabled: bool = True
    output_dir: str = "site"
    data_file: str = "dashboard-data.json"
    title: str = "Equity Lake"
    subtitle: str = "Local-first market data, published as a static status page."


class MonitoringSettings(BaseModel):
    """Pipeline monitoring defaults."""

    max_age_days: int = 2
    null_threshold_pct: float = 5.0


class AppSettings(BaseModel):
    """Single application settings document."""

    project: ProjectSettings = Field(default_factory=ProjectSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)


class SettingsOverrides(BaseSettings):
    """Environment overrides for the YAML settings file."""

    model_config = SettingsConfigDict(
        env_prefix="EQUITY_LAKE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    config_path: str = str(DEFAULT_SETTINGS_PATH)
    environment: Literal["development", "production", "testing"] | None = None
    data_dir: str | None = None
    lake_dir: str | None = None
    logs_dir: str | None = None
    models_dir: str | None = None
    db_path: str | None = None
    dashboard_output_dir: str | None = None
    dashboard_title: str | None = None
    schedule_cron: str | None = None
    schedule_timezone: str | None = None


def _deep_merge(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge nested dictionaries without adding a larger dependency."""
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _build_override_payload(overrides: SettingsOverrides) -> dict[str, Any]:
    payload: dict[str, Any] = {}

    if overrides.environment is not None:
        payload.setdefault("project", {})["environment"] = overrides.environment

    storage_overrides = {
        "data_dir": overrides.data_dir,
        "lake_dir": overrides.lake_dir,
        "logs_dir": overrides.logs_dir,
        "models_dir": overrides.models_dir,
        "db_path": overrides.db_path,
    }
    clean_storage = {key: value for key, value in storage_overrides.items() if value}
    if clean_storage:
        payload["storage"] = clean_storage

    dashboard_overrides = {
        "output_dir": overrides.dashboard_output_dir,
        "title": overrides.dashboard_title,
    }
    clean_dashboard = {key: value for key, value in dashboard_overrides.items() if value is not None}
    if clean_dashboard:
        payload["dashboard"] = clean_dashboard

    schedule_overrides = {
        "cron": overrides.schedule_cron,
        "timezone": overrides.schedule_timezone,
    }
    clean_schedule = {key: value for key, value in schedule_overrides.items() if value is not None}
    if clean_schedule:
        payload["schedule"] = clean_schedule

    return payload


def load_settings(config_path: str | Path | None = None) -> AppSettings:
    """Load application settings from YAML with optional env overrides."""
    overrides = SettingsOverrides()
    resolved_path = _resolve_config_path(config_path or overrides.config_path)

    if not resolved_path.exists():
        raise FileNotFoundError(f"Settings file not found: {resolved_path}")

    with resolved_path.open("r", encoding="utf-8") as file_obj:
        raw_data = yaml.safe_load(file_obj) or {}

    merged = _deep_merge(raw_data, _build_override_payload(overrides))
    return AppSettings.model_validate(merged)


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return the cached application settings."""
    return load_settings()


def clear_settings_cache() -> None:
    """Clear cached settings for tests or explicit reload flows."""
    get_settings.cache_clear()
    runtime_module = sys.modules.get("equity_lake.core.runtime")
    if runtime_module is not None and hasattr(runtime_module, "refresh_runtime_state"):
        runtime_module.refresh_runtime_state()


__all__ = [
    "AppSettings",
    "DashboardSettings",
    "IngestionSettings",
    "MonitoringSettings",
    "ProjectSettings",
    "ScheduleSettings",
    "SettingsOverrides",
    "StorageSettings",
    "clear_settings_cache",
    "get_settings",
    "load_settings",
]
