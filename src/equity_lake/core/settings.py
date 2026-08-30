"""Application settings (pydantic-settings).

Reads from environment variables (``EQUITY_*``), ``.env``, and
``config/settings.yaml``.  Priority: init > env > dotenv > YAML.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

from equity_lake.core.paths import CONFIG_DIR


class ProjectSettings(BaseModel):
    name: str = "equity-lake"
    version: str = "0.1.0"
    environment: Literal["development", "production", "testing"] = "development"


class IngestionSettings(BaseModel):
    default_markets: list[str] = Field(default_factory=lambda: ["us", "cn", "hk_sg"])
    ticker_config_path: str = "config/tickers.yaml"
    retry_attempts: int = 3
    retry_delay: float = 1.0


class ScheduleSettings(BaseModel):
    enabled: bool = True
    cron: str = "0 1 * * 1-5"
    timezone: str = "UTC"

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, value: str) -> str:
        from croniter import croniter

        if not croniter.is_valid(value):
            raise ValueError(f"Invalid cron expression: {value}")
        return value


class DashboardSettings(BaseModel):
    enabled: bool = True
    output_dir: str = "site"
    data_file: str = "dashboard-data.json"
    title: str = "Equity Lake"
    subtitle: str = "Local-first market data, published as a static status page."


class MonitoringSettings(BaseModel):
    max_age_days: int = 2
    null_threshold_pct: float = 5.0


class S3SyncSettings(BaseModel):
    """Subprocess limits for the S3 historical sync (``equity data sync``)."""

    timeout_seconds: int = Field(
        default=600,
        ge=1,
        description="Max seconds to wait for a single s5cmd/AWS sync subprocess.",
    )


class Settings(BaseSettings):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    s3_sync: S3SyncSettings = Field(default_factory=S3SyncSettings)

    model_config = SettingsConfigDict(
        env_prefix="EQUITY_",
        env_nested_delimiter="__",
        yaml_file=str(CONFIG_DIR / "settings.yaml"),
        extra="forbid",
        validate_assignment=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def load_settings(config_path: str | None = None) -> Settings:
    if config_path is None:
        return Settings()

    class _CustomSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="EQUITY_",
            env_nested_delimiter="__",
            yaml_file=config_path,
            extra="forbid",
        )

        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            return (
                init_settings,
                env_settings,
                dotenv_settings,
                YamlConfigSettingsSource(settings_cls),
            )

    return _CustomSettings()
