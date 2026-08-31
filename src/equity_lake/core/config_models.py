"""Pydantic models for ticker configuration and market definitions.

Defines the schema for ``config/tickers.yaml``: markets, tickers, groups,
and validation rules. The root model :class:`TickerConfigRoot` provides
selector methods to query tickers by tag, sector, exchange, or group.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import structlog
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from equity_lake.core.paths import CONFIG_DIR

logger = structlog.get_logger(__name__)

DEFAULT_TICKERS_PATH = CONFIG_DIR / "tickers.yaml"


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

    def get_tickers_by_tag(self, tag: str, active_only: bool = True) -> list[str]:
        tag_normalized = tag.lower().strip()
        result: list[str] = []
        for ticker in self.tickers:
            if active_only and not ticker.active:
                continue
            if tag_normalized in [item.lower() for item in ticker.tags]:
                result.append(ticker.symbol)
        return result

    def get_tickers_by_sector(self, sector: str, active_only: bool = True) -> list[str]:
        result: list[str] = []
        sector_normalized = sector.lower().strip()
        for ticker in self.tickers:
            if active_only and not ticker.active:
                continue
            if ticker.sector.lower() == sector_normalized:
                result.append(ticker.symbol)
        return result

    def get_tickers_by_tags(self, tags: list[str], match_all: bool = False, active_only: bool = True) -> list[str]:
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

    def get_tickers_for_market(self, market: str, active_only: bool = True, min_priority: int | None = None) -> list[str]:
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

    def get_ticker_metadata(self, symbol: str, market: str | None = None) -> TickerMetadata | None:
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

    def get_tickers_by_tag(self, tag: str, market: str | None = None, active_only: bool = True) -> list[str]:
        markets_to_search = [market] if market else self.get_markets()
        result: list[str] = []
        for market_name in markets_to_search:
            market_info = self.get_market_info(market_name)
            if market_info:
                result.extend(market_info.get_tickers_by_tag(tag, active_only=active_only))
        return result

    def get_tickers_by_sector(self, sector: str, market: str | None = None, active_only: bool = True) -> list[str]:
        markets_to_search = [market] if market else self.get_markets()
        result: list[str] = []
        for market_name in markets_to_search:
            market_info = self.get_market_info(market_name)
            if market_info:
                result.extend(market_info.get_tickers_by_sector(sector, active_only=active_only))
        return result

    def get_tickers_by_tags(self, tags: list[str], match_all: bool = False, market: str | None = None, active_only: bool = True) -> list[str]:
        markets_to_search = [market] if market else self.get_markets()
        result: list[str] = []
        for market_name in markets_to_search:
            market_info = self.get_market_info(market_name)
            if market_info:
                result.extend(market_info.get_tickers_by_tags(tags, match_all=match_all, active_only=active_only))
        return result

    def get_tickers_by_group(self, group_name: str, active_only: bool = True) -> list[str]:
        group_info = self.groups.get(group_name) if self.groups else None
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

    @staticmethod
    def _load_yaml_data(config_path: Path) -> dict[str, Any]:
        """Read a tickers YAML file; return ``{}`` for a missing/empty path."""
        if not config_path.exists():
            logger.warning("Config file not found: %s. Using empty configuration.", config_path)
            return {}
        with config_path.open("r", encoding="utf-8") as file_obj:
            data = cast("dict[str, Any] | None", yaml.safe_load(file_obj))
        if not data:
            logger.warning("Empty config file: %s", config_path)
            return {}
        return data

    @classmethod
    def from_yaml(cls, config_path: str | Path | None = None) -> TickerConfigRoot:
        """Load a ``TickerConfigRoot`` from a YAML file (defaults to the repo config)."""
        path = Path(config_path) if config_path is not None else DEFAULT_TICKERS_PATH
        data = cls._load_yaml_data(path)
        instance = cls(**data)
        active = sum(len([t for t in m.tickers if t.active]) for m in instance.markets.values())
        logger.info("Loaded ticker config from %s: %s tickers across %s markets", path, active, len(instance.markets))
        return instance
