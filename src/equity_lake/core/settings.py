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

from equity_lake.core.paths import CONFIG_DIR, OPTIONAL_ENRICHMENT_MARKETS, PRICE_MARKETS, SHORT_TO_LONG


class ProjectSettings(BaseModel):
    name: str = "equity-lake"
    version: str = "0.1.0"
    environment: Literal["development", "production", "testing"] = "development"


class IngestionSettings(BaseModel):
    default_markets: list[str] = Field(default_factory=lambda: ["us_equity", "cn_ashare", "hk_sg_equity"])
    ticker_config_path: str = "config/tickers.yaml"
    retry_attempts: int = 3
    retry_delay: float = 1.0

    @field_validator("default_markets")
    @classmethod
    def _normalize_default_markets(cls, value: list[str]) -> list[str]:
        """Canonicalize deprecated short price-market aliases to long keys (ADR-0010).

        Only the five known aliases are rewritten; canonical long price keys and
        the ten single-form dataset identifiers pass through unchanged. Any
        other key raises so a config typo fails loudly at load time instead of
        becoming a silent pipeline no-op.
        """
        normalized: list[str] = []
        for market in value:
            if market in SHORT_TO_LONG:
                normalized.append(SHORT_TO_LONG[market])
            elif market in PRICE_MARKETS or market in OPTIONAL_ENRICHMENT_MARKETS:
                normalized.append(market)
            else:
                raise ValueError(
                    f"Unknown market in ingestion.default_markets: {market!r}. "
                    f"Valid price markets: {', '.join(PRICE_MARKETS)} (aliases: {', '.join(SHORT_TO_LONG)}); "
                    f"dataset identifiers: {', '.join(sorted(OPTIONAL_ENRICHMENT_MARKETS))}"
                )
        return normalized


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
    """Freshness and quality expectations for ``equity monitor``.

    Overridable via ``EQUITY_MONITORING__*`` env vars or ``config/settings.yaml``.
    """

    max_age_days: int = 2
    null_threshold_pct: float = 5.0
    market_max_age_days: dict[str, int] = Field(
        default_factory=dict,
        description=('Per price-market freshness overrides in days (e.g. {"krx_equity": 3}); markets absent here fall back to max_age_days.'),
    )
    table_max_age_days: dict[str, int] = Field(
        default_factory=lambda: {
            "bronze/raw_articles": 2,  # news/articles — daily
            "silver/processed_articles": 2,  # processed news/transcripts — daily
            "silver/sec_extractions": 95,  # SEC filings — quarterly
        },
        description=(
            "Per-table freshness expectations for the unstructured check, in days "
            "(price/news daily, SEC quarterly, transcripts monthly if split out); "
            "unlisted tables fall back to max_age_days."
        ),
    )


class AlertingSettings(BaseModel):
    """Alert delivery for ``equity monitor``.

    Overridable via ``EQUITY_ALERTING__*`` env vars or ``config/settings.yaml``.
    When ``webhook_url`` is unset, alerts go to the console only.
    """

    webhook_url: str | None = Field(default=None, description="Optional webhook URL; alerts are POSTed as JSON in addition to the console.")


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
    alerting: AlertingSettings = Field(default_factory=AlertingSettings)
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
