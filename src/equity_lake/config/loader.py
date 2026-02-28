"""Configuration loader APIs."""

import logging
from pathlib import Path
from typing import Any

import yaml

from equity_lake.config.models import TickerConfigRoot, TickerMetadata
from equity_lake.config.selectors import (
    get_all_tickers,
    get_group_info,
    get_groups,
    get_market_currency,
    get_market_info,
    get_markets,
    get_stats,
    get_ticker_metadata,
    get_tickers_by_exchange,
    get_tickers_by_group,
    get_tickers_by_sector,
    get_tickers_by_tag,
    get_tickers_by_tags,
    get_tickers_for_market,
    list_tickers,
)
from equity_lake.config.validation import validate_config, validate_ticker_format

logger = logging.getLogger(__name__)


class TickerConfig:
    """Centralized ticker configuration manager."""

    DEFAULT_CONFIG_PATH = (
        Path(__file__).resolve().parents[2] / "config" / "tickers.yaml"
    )

    def __init__(self, config_path: Path | None = None):
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config: TickerConfigRoot | None = None
        self._load_config()

    def _load_config(self) -> None:
        """Load and parse the YAML configuration file."""
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
        """Return the total number of active tickers across markets."""
        if not self._config:
            return 0
        return sum(
            len([ticker for ticker in market.tickers if ticker.active])
            for market in self._config.markets.values()
        )

    def get_markets(self) -> list[str]:
        return get_markets(self._config)

    def get_market_info(self, market: str):
        return get_market_info(self._config, market)

    def get_market_currency(self, market: str) -> str:
        return get_market_currency(self._config, market)

    def get_tickers_for_market(
        self,
        market: str,
        active_only: bool = True,
        min_priority: int | None = None,
    ) -> list[str]:
        market_info = self.get_market_info(market)
        if not market_info:
            logger.warning("Market not found: %s", market)
        return get_tickers_for_market(
            self._config,
            market,
            active_only=active_only,
            min_priority=min_priority,
        )

    def get_ticker_metadata(
        self,
        symbol: str,
        market: str | None = None,
    ) -> TickerMetadata | None:
        return get_ticker_metadata(self._config, symbol, market=market)

    def get_all_tickers(self, active_only: bool = True) -> dict[str, list[str]]:
        return get_all_tickers(self._config, active_only=active_only)

    def get_tickers_by_tag(
        self,
        tag: str,
        market: str | None = None,
        active_only: bool = True,
    ) -> list[str]:
        return get_tickers_by_tag(
            self._config,
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
        return get_tickers_by_sector(
            self._config,
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
        return get_tickers_by_exchange(
            self._config,
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
        return get_tickers_by_tags(
            self._config,
            tags,
            match_all=match_all,
            market=market,
            active_only=active_only,
        )

    def get_groups(self) -> list[str]:
        return get_groups(self._config)

    def get_group_info(self, group_name: str):
        return get_group_info(self._config, group_name)

    def get_tickers_by_group(
        self,
        group_name: str,
        active_only: bool = True,
    ) -> list[str]:
        group_info = self.get_group_info(group_name)
        if not group_info:
            logger.warning("Group not found: %s", group_name)
        return get_tickers_by_group(self._config, group_name, active_only=active_only)

    def validate_ticker_format(self, symbol: str, market: str) -> bool:
        return validate_ticker_format(self._config, symbol, market)

    def validate_config(self) -> dict[str, list[str]]:
        return validate_config(self._config)

    def list_tickers(
        self,
        market: str | None = None,
        active_only: bool = True,
        include_metadata: bool = False,
    ) -> list[str] | dict[str, dict[str, Any]]:
        return list_tickers(
            self._config,
            market=market,
            active_only=active_only,
            include_metadata=include_metadata,
        )

    def get_stats(self) -> dict[str, Any]:
        return get_stats(self._config)

    @classmethod
    def from_path(cls, config_path: str | Path) -> "TickerConfig":
        """Create a config object from a custom YAML path."""
        return cls(config_path=Path(config_path))


def get_default_config() -> TickerConfig:
    """Return the default ticker configuration."""
    return TickerConfig()


def load_tickers_for_market(
    market: str,
    config_path: Path | None = None,
    active_only: bool = True,
) -> list[str]:
    """Load ticker symbols for a single market."""
    config = TickerConfig(config_path=config_path)
    return config.get_tickers_for_market(market, active_only=active_only)


__all__ = ["TickerConfig", "get_default_config", "load_tickers_for_market"]
