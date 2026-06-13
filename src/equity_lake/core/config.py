"""Unified configuration: Pydantic models, settings, selectors, validation, and loader."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import structlog
import yaml
from croniter import croniter
from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
LOGS_DIR = PROJECT_ROOT / "logs"


class TickerMetadata(BaseModel):
    symbol: str
    name: str
    exchange: str
    sector: str
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    priority: int = 5

    @field_validator("symbol")
    @classmethod
    def symbol_must_not_be_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Ticker symbol cannot be empty")
        return value.strip()

    @field_validator("exchange")
    @classmethod
    def exchange_must_not_be_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("Exchange cannot be empty")
        return value.strip()

    @field_validator("priority")
    @classmethod
    def priority_must_be_valid(cls, value: int) -> int:
        if not 1 <= value <= 10:
            raise ValueError("Priority must be between 1 and 10")
        return value

    @field_validator("tags")
    @classmethod
    def tags_must_be_unique(cls, value: list[str]) -> list[str]:
        return list({tag.lower().strip() for tag in value if tag.strip()})


class MarketConfig(BaseModel):
    currency: str
    description: str = ""
    tickers: list[TickerMetadata] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tickers(self) -> MarketConfig:
        symbols = [ticker.symbol for ticker in self.tickers]
        if len(symbols) != len(set(symbols)):
            duplicates = {symbol for symbol in symbols if symbols.count(symbol) > 1}
            raise ValueError(f"Duplicate ticker symbols found: {duplicates}")
        return self

    def get_tickers_by_tag(
        self,
        tag: str,
        active_only: bool = True,
    ) -> list[str]:
        tag_normalized = tag.lower().strip()
        result: list[str] = []
        for ticker in self.tickers:
            if active_only and not ticker.active:
                continue
            if tag_normalized in [item.lower() for item in ticker.tags]:
                result.append(ticker.symbol)
        return result

    def get_tickers_by_sector(
        self,
        sector: str,
        active_only: bool = True,
    ) -> list[str]:
        result: list[str] = []
        sector_normalized = sector.lower().strip()
        for ticker in self.tickers:
            if active_only and not ticker.active:
                continue
            if ticker.sector.lower() == sector_normalized:
                result.append(ticker.symbol)
        return result

    def get_tickers_by_exchange(
        self,
        exchange: str,
        active_only: bool = True,
    ) -> list[str]:
        result: list[str] = []
        exchange_normalized = exchange.upper().strip()
        for ticker in self.tickers:
            if active_only and not ticker.active:
                continue
            if ticker.exchange.upper() == exchange_normalized:
                result.append(ticker.symbol)
        return result

    def get_tickers_by_tags(
        self,
        tags: list[str],
        match_all: bool = False,
        active_only: bool = True,
    ) -> list[str]:
        tags_normalized = [tag.lower().strip() for tag in tags]
        result: list[str] = []
        for ticker in self.tickers:
            if active_only and not ticker.active:
                continue
            ticker_tags = [tag.lower() for tag in ticker.tags]
            if match_all:
                if all(tag in ticker_tags for tag in tags_normalized):
                    result.append(ticker.symbol)
            elif any(tag in ticker_tags for tag in tags_normalized):
                result.append(ticker.symbol)
        return result


class GroupConfig(BaseModel):
    description: str
    markets: list[str]
    tickers: list[str] | dict[str, list[str]] = Field(default_factory=lambda: [])


class ValidationConfig(BaseModel):
    market_formats: dict[str, str] = Field(default_factory=dict)
    required_fields: list[str] = Field(default_factory=list)
    valid_exchanges: dict[str, list[str]] = Field(default_factory=dict)
    valid_sectors: list[str] = Field(default_factory=list)
    valid_tags: list[str] = Field(default_factory=list)


class TickerConfigRoot(BaseModel):
    version: str = "1.0"
    metadata: dict[str, str] = Field(default_factory=dict)
    markets: dict[str, MarketConfig] = Field(default_factory=dict)
    groups: dict[str, GroupConfig] = Field(default_factory=dict)
    validation: ValidationConfig | None = None

    def get_markets(self) -> list[str]:
        return list(self.markets.keys())

    def get_market_info(self, market: str) -> MarketConfig | None:
        return self.markets.get(market)

    def get_market_currency(self, market: str) -> str:
        market_info = self.get_market_info(market)
        return market_info.currency if market_info else "USD"

    def get_tickers_for_market(
        self,
        market: str,
        active_only: bool = True,
        min_priority: int | None = None,
    ) -> list[str]:
        market_info = self.get_market_info(market)
        if not market_info:
            return []

        tickers = market_info.tickers
        if active_only:
            tickers = [ticker for ticker in tickers if ticker.active]
        if min_priority is not None:
            tickers = [ticker for ticker in tickers if ticker.priority >= min_priority]

        tickers = sorted(tickers, key=lambda t: t.priority, reverse=True)
        return [ticker.symbol for ticker in tickers]

    def get_ticker_metadata(
        self,
        symbol: str,
        market: str | None = None,
    ) -> TickerMetadata | None:
        if market:
            market_info = self.get_market_info(market)
            if not market_info:
                return None
            for ticker in market_info.tickers:
                if ticker.symbol == symbol:
                    return ticker
            return None

        for market_config in self.markets.values():
            for ticker in market_config.tickers:
                if ticker.symbol == symbol:
                    return ticker
        return None

    def get_all_tickers(
        self,
        active_only: bool = True,
    ) -> dict[str, list[str]]:
        return {market: self.get_tickers_for_market(market, active_only=active_only) for market in self.get_markets()}

    def get_tickers_by_tag(
        self,
        tag: str,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        tag_normalized = tag.lower().strip()
        result: list[str] = []
        markets_to_search = [market] if market else self.get_markets()

        for market_name in markets_to_search:
            market_info = self.get_market_info(market_name)
            if not market_info:
                continue
            for ticker in market_info.tickers:
                if active_only and not ticker.active:
                    continue
                if tag_normalized in [item.lower() for item in ticker.tags]:
                    result.append(ticker.symbol)

        return result

    def get_tickers_by_sector(
        self,
        sector: str,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        result: list[str] = []
        sector_normalized = sector.lower().strip()
        markets_to_search = [market] if market else self.get_markets()

        for market_name in markets_to_search:
            market_info = self.get_market_info(market_name)
            if not market_info:
                continue
            for ticker in market_info.tickers:
                if active_only and not ticker.active:
                    continue
                if ticker.sector.lower() == sector_normalized:
                    result.append(ticker.symbol)

        return result

    def get_tickers_by_exchange(
        self,
        exchange: str,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        result: list[str] = []
        exchange_normalized = exchange.upper().strip()
        markets_to_search = [market] if market else self.get_markets()

        for market_name in markets_to_search:
            market_info = self.get_market_info(market_name)
            if not market_info:
                continue
            for ticker in market_info.tickers:
                if active_only and not ticker.active:
                    continue
                if ticker.exchange.upper() == exchange_normalized:
                    result.append(ticker.symbol)

        return result

    def get_tickers_by_tags(
        self,
        tags: list[str],
        match_all: bool = False,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        tags_normalized = [tag.lower().strip() for tag in tags]
        result: list[str] = []
        markets_to_search = [market] if market else self.get_markets()

        for market_name in markets_to_search:
            market_info = self.get_market_info(market_name)
            if not market_info:
                continue
            for ticker in market_info.tickers:
                if active_only and not ticker.active:
                    continue
                ticker_tags = [tag.lower() for tag in ticker.tags]
                if match_all:
                    if all(tag in ticker_tags for tag in tags_normalized):
                        result.append(ticker.symbol)
                elif any(tag in ticker_tags for tag in tags_normalized):
                    result.append(ticker.symbol)

        return result

    def get_groups(self) -> list[str]:
        if not self.groups:
            return []
        return list(self.groups.keys())

    def get_group_info(self, group_name: str) -> GroupConfig | None:
        if not self.groups:
            return None
        return self.groups.get(group_name)

    def get_tickers_by_group(
        self,
        group_name: str,
        active_only: bool = True,
    ) -> list[str]:
        group_info = self.get_group_info(group_name)
        if not group_info:
            return []

        result: list[str] = []
        if isinstance(group_info.tickers, list):
            for symbol in group_info.tickers:
                metadata = self.get_ticker_metadata(symbol)
                if metadata and (not active_only or metadata.active):
                    result.append(metadata.symbol)
            return result

        for market_key, tickers in group_info.tickers.items():
            for symbol in tickers:
                metadata = self.get_ticker_metadata(symbol, market=market_key)
                if metadata and (not active_only or metadata.active):
                    result.append(metadata.symbol)

        return result

    def list_tickers(
        self,
        market: str | None = None,
        active_only: bool = True,
        include_metadata: bool = False,
    ) -> list[str] | dict[str, list[str]] | dict[str, dict[str, Any]]:
        if include_metadata:
            result: dict[str, dict[str, Any]] = {}
            markets_to_search = [market] if market else self.get_markets()
            for market_name in markets_to_search:
                market_info = self.get_market_info(market_name)
                if not market_info:
                    continue
                for ticker in market_info.tickers:
                    if active_only and not ticker.active:
                        continue
                    result[ticker.symbol] = {
                        "symbol": ticker.symbol,
                        "name": ticker.name,
                        "market": market_name,
                        "exchange": ticker.exchange,
                        "sector": ticker.sector,
                        "tags": ticker.tags,
                        "active": ticker.active,
                        "priority": ticker.priority,
                    }
            return result

        if market:
            return self.get_tickers_for_market(market, active_only=active_only)

        return {
            market_name: self.get_tickers_for_market(
                market_name,
                active_only=active_only,
            )
            for market_name in self.get_markets()
        }

    def get_stats(self) -> dict[str, Any]:
        stats: dict[str, Any] = {
            "version": self.version,
            "total_markets": len(self.markets),
            "total_groups": len(self.groups) if self.groups else 0,
            "markets": {},
        }

        for market_name, market_config in self.markets.items():
            active_tickers = [ticker for ticker in market_config.tickers if ticker.active]
            inactive_tickers = [ticker for ticker in market_config.tickers if not ticker.active]
            stats["markets"][market_name] = {
                "currency": market_config.currency,
                "total_tickers": len(market_config.tickers),
                "active_tickers": len(active_tickers),
                "inactive_tickers": len(inactive_tickers),
                "exchanges": list({ticker.exchange for ticker in market_config.tickers}),
                "sectors": list({ticker.sector for ticker in market_config.tickers}),
            }

        return stats

    def validate_ticker_format(self, symbol: str, market: str) -> bool:
        if not self.validation:
            return True

        pattern = self.validation.market_formats.get(market)
        if not pattern:
            return True

        try:
            return bool(re.match(pattern, symbol))
        except re.error:
            return False

    def validate_config(self) -> dict[str, list[str]]:
        errors: list[str] = []
        warnings: list[str] = []

        for market_name, market_config in self.markets.items():
            for ticker in market_config.tickers:
                if not self.validate_ticker_format(ticker.symbol, market_name):
                    errors.append(f"Invalid ticker format for {market_name}: {ticker.symbol}")

                if not ticker.name:
                    warnings.append(f"Ticker {ticker.symbol} ({market_name}) missing name")
                if not ticker.sector:
                    warnings.append(f"Ticker {ticker.symbol} ({market_name}) missing sector")
                if not ticker.tags:
                    warnings.append(f"Ticker {ticker.symbol} ({market_name}) has no tags")

        for group_name, group_config in self.groups.items():
            if not isinstance(group_config.tickers, list):
                continue
            for symbol in group_config.tickers:
                if self.get_ticker_metadata(symbol) is None:
                    warnings.append(f"Group '{group_name}' references unknown ticker: {symbol}")

        return {"errors": errors, "warnings": warnings}


class ProjectSettings(BaseModel):
    name: str = "equity-lake"
    version: str = "0.1.0"
    environment: Literal["development", "production", "testing"] = "development"


class StorageSettings(BaseModel):
    data_dir: str = "data"
    lake_dir: str = "data/lake"
    logs_dir: str = "logs"
    models_dir: str = "data/models"
    db_path: str = "equity_data.duckdb"


class IngestionSettings(BaseModel):
    default_markets: list[str] = Field(default_factory=lambda: ["us", "cn", "hk_sg"])
    parallel: bool = True
    max_workers: int = 3
    ticker_config_path: str = "config/tickers.yaml"
    cn_fallback_threshold: float = 0.3


class ScheduleSettings(BaseModel):
    enabled: bool = True
    cron: str = "0 1 * * 1-5"
    timezone: str = "UTC"

    @field_validator("cron")
    @classmethod
    def validate_cron(cls, value: str) -> str:
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


class Settings(BaseSettings):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)

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


AppSettings = Settings


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
            extra="ignore",
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


def clear_settings_cache() -> None:
    get_settings.cache_clear()


def get_project_config() -> dict[str, str | int | float | bool]:
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


logger = structlog.get_logger()


class TickerConfig:
    DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "tickers.yaml"

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path is not None else self.DEFAULT_CONFIG_PATH
        self._config: TickerConfigRoot | None = None
        self._load_config()

    def _load_config(self) -> None:
        if not self.config_path.exists():
            logger.warning(
                "Config file not found: %s. Using empty configuration.",
                self.config_path,
            )
            self._config = TickerConfigRoot()
            return

        try:
            with self.config_path.open("r", encoding="utf-8") as file_obj:
                data = yaml.safe_load(file_obj)

            if not data:
                logger.warning("Empty config file: %s", self.config_path)
                self._config = TickerConfigRoot()
                return

            self._config = TickerConfigRoot(**data)
            logger.info(
                "Loaded ticker config from %s: %s tickers across %s markets",
                self.config_path,
                self._get_total_ticker_count(),
                len(self._config.markets),
            )
        except Exception:
            logger.exception("Failed to load ticker config from %s", self.config_path)
            raise

    def _get_total_ticker_count(self) -> int:
        if not self._config:
            return 0
        return sum(len([ticker for ticker in market.tickers if ticker.active]) for market in self._config.markets.values())

    def get_markets(self) -> list[str]:
        if not self._config:
            return []
        return self._config.get_markets()

    def get_market_info(self, market: str) -> MarketConfig | None:
        if not self._config:
            return None
        return self._config.get_market_info(market)

    def get_market_currency(self, market: str) -> str:
        if not self._config:
            return "USD"
        return self._config.get_market_currency(market)

    def get_tickers_for_market(
        self,
        market: str,
        active_only: bool = True,
        min_priority: int | None = None,
    ) -> list[str]:
        market_info = self.get_market_info(market)
        if not market_info:
            logger.warning("Market not found: %s", market)
        if not self._config:
            return []
        return self._config.get_tickers_for_market(
            market,
            active_only=active_only,
            min_priority=min_priority,
        )

    def get_ticker_metadata(
        self,
        symbol: str,
        market: str | None = None,
    ) -> TickerMetadata | None:
        if not self._config:
            return None
        return self._config.get_ticker_metadata(symbol, market=market)

    def get_all_tickers(self, active_only: bool = True) -> dict[str, list[str]]:
        if not self._config:
            return {}
        return self._config.get_all_tickers(active_only=active_only)

    def get_tickers_by_tag(
        self,
        tag: str,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        if not self._config:
            return []
        return self._config.get_tickers_by_tag(
            tag,
            market=market,
            active_only=active_only,
        )

    def get_tickers_by_sector(
        self,
        sector: str,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        if not self._config:
            return []
        return self._config.get_tickers_by_sector(
            sector,
            market=market,
            active_only=active_only,
        )

    def get_tickers_by_exchange(
        self,
        exchange: str,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        if not self._config:
            return []
        return self._config.get_tickers_by_exchange(
            exchange,
            market=market,
            active_only=active_only,
        )

    def get_tickers_by_tags(
        self,
        tags: list[str],
        match_all: bool = False,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        if not self._config:
            return []
        return self._config.get_tickers_by_tags(
            tags,
            match_all=match_all,
            market=market,
            active_only=active_only,
        )

    def get_groups(self) -> list[str]:
        if not self._config:
            return []
        return self._config.get_groups()

    def get_group_info(self, group_name: str) -> GroupConfig | None:
        if not self._config:
            return None
        return self._config.get_group_info(group_name)

    def get_tickers_by_group(
        self,
        group_name: str,
        active_only: bool = True,
    ) -> list[str]:
        group_info = self.get_group_info(group_name)
        if not group_info:
            logger.warning("Group not found: %s", group_name)
        if not self._config:
            return []
        return self._config.get_tickers_by_group(group_name, active_only=active_only)

    def validate_ticker_format(self, symbol: str, market: str) -> bool:
        if not self._config:
            return True
        return self._config.validate_ticker_format(symbol, market)

    def validate_config(self) -> dict[str, list[str]]:
        if not self._config:
            return {"errors": ["No configuration loaded"], "warnings": []}
        return self._config.validate_config()

    def list_tickers(
        self,
        market: str | None = None,
        active_only: bool = True,
        include_metadata: bool = False,
    ) -> list[str] | dict[str, list[str]] | dict[str, dict[str, Any]]:
        if not self._config:
            return {}
        return self._config.list_tickers(
            market=market,
            active_only=active_only,
            include_metadata=include_metadata,
        )

    def get_stats(self) -> dict[str, Any]:
        if not self._config:
            return {}
        return self._config.get_stats()

    @classmethod
    def from_path(cls, config_path: str | Path) -> TickerConfig:
        return cls(config_path=Path(config_path))


def get_default_config() -> TickerConfig:
    return TickerConfig()


def load_tickers_for_market(
    market: str,
    config_path: Path | None = None,
    active_only: bool = True,
) -> list[str]:
    config = TickerConfig(config_path=config_path)
    return config.get_tickers_for_market(market, active_only=active_only)


__all__ = [
    "AppSettings",
    "DashboardSettings",
    "GroupConfig",
    "IngestionSettings",
    "MarketConfig",
    "MonitoringSettings",
    "ProjectSettings",
    "ScheduleSettings",
    "Settings",
    "StorageSettings",
    "TickerConfig",
    "TickerConfigRoot",
    "TickerMetadata",
    "ValidationConfig",
    "clear_settings_cache",
    "get_default_config",
    "get_project_config",
    "get_settings",
    "load_settings",
    "load_tickers_for_market",
]
