"""
Ticker Configuration Management

This module provides centralized configuration management for tickers
across all markets, supporting filtering, validation, and metadata queries.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models for Ticker Configuration
# =============================================================================

class TickerMetadata(BaseModel):
    """Metadata for a single ticker."""

    symbol: str
    name: str
    exchange: str
    sector: str
    tags: List[str] = Field(default_factory=list)
    active: bool = True
    priority: int = 5

    @field_validator('symbol')
    @classmethod
    def symbol_must_not_be_empty(cls, v: str) -> str:
        """Ensure symbol is not empty."""
        if not v or not v.strip():
            raise ValueError("Ticker symbol cannot be empty")
        return v.strip()

    @field_validator('exchange')
    @classmethod
    def exchange_must_not_be_empty(cls, v: str) -> str:
        """Ensure exchange is not empty."""
        if not v or not v.strip():
            raise ValueError("Exchange cannot be empty")
        return v.strip()

    @field_validator('priority')
    @classmethod
    def priority_must_be_valid(cls, v: int) -> int:
        """Ensure priority is between 1 and 10."""
        if not 1 <= v <= 10:
            raise ValueError("Priority must be between 1 and 10")
        return v

    @field_validator('tags')
    @classmethod
    def tags_must_be_unique(cls, v: List[str]) -> List[str]:
        """Ensure tags are unique and lowercase."""
        unique_tags = list(set(tag.lower().strip() for tag in v if tag.strip()))
        return unique_tags


class MarketConfig(BaseModel):
    """Configuration for a single market."""

    currency: str
    description: str = ""
    tickers: List[TickerMetadata] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_tickers(self) -> 'MarketConfig':
        """Validate ticker symbols are unique within market."""
        symbols = [t.symbol for t in self.tickers]
        if len(symbols) != len(set(symbols)):
            duplicates = [s for s in symbols if symbols.count(s) > 1]
            raise ValueError(f"Duplicate ticker symbols found: {set(duplicates)}")
        return self


class GroupConfig(BaseModel):
    """Configuration for a ticker group."""

    description: str
    markets: List[str]
    tickers: Union[List[str], Dict[str, List[str]]] = Field(default_factory=list)


class ValidationConfig(BaseModel):
    """Configuration for ticker validation rules."""

    market_formats: Dict[str, str] = Field(default_factory=dict)
    required_fields: List[str] = Field(default_factory=list)
    valid_exchanges: Dict[str, List[str]] = Field(default_factory=dict)
    valid_sectors: List[str] = Field(default_factory=list)
    valid_tags: List[str] = Field(default_factory=dict)


class TickerConfigRoot(BaseModel):
    """Root configuration model."""

    version: str = "1.0"
    metadata: Dict[str, str] = Field(default_factory=dict)
    markets: Dict[str, MarketConfig] = Field(default_factory=dict)
    groups: Dict[str, GroupConfig] = Field(default_factory=dict)
    validation: Optional[ValidationConfig] = None


# =============================================================================
# Ticker Configuration Manager
# =============================================================================

class TickerConfig:
    """
    Centralized ticker configuration manager.

    This class loads, validates, and provides access to ticker configuration
    from a YAML file. It supports filtering by market, tags, sectors, and
    custom groups.

    Usage:
        # Load default config
        config = TickerConfig()

        # Load custom config
        config = TickerConfig.from_path("config/custom_tickers.yaml")

        # Get all active US tickers
        us_tickers = config.get_tickers_for_market("us")

        # Filter by tag
        blue_chips = config.get_tickers_by_tag("blue-chip", market="us")

        # Filter by sector
        tech_stocks = config.get_tickers_by_sector("Technology", market="us")

        # Use predefined group
        faang = config.get_tickers_by_group("faang")
    """

    DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "tickers.yaml"

    def __init__(self, config_path: Optional[Path] = None):
        """
        Initialize TickerConfig.

        Args:
            config_path: Path to YAML config file. If None, uses default path.
        """
        self.config_path = config_path or self.DEFAULT_CONFIG_PATH
        self._config: Optional[TickerConfigRoot] = None

        # Load configuration
        self._load_config()

    def _load_config(self) -> None:
        """Load and parse YAML configuration file."""
        if not self.config_path.exists():
            logger.warning(
                f"Config file not found: {self.config_path}. "
                f"Using empty configuration."
            )
            self._config = TickerConfigRoot()
            return

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)

            if not data:
                logger.warning(f"Empty config file: {self.config_path}")
                self._config = TickerConfigRoot()
                return

            # Validate and parse with Pydantic
            self._config = TickerConfigRoot(**data)

            logger.info(
                f"✅ Loaded ticker config from {self.config_path}: "
                f"{self._get_total_ticker_count()} tickers across "
                f"{len(self._config.markets)} markets"
            )

        except Exception as e:
            logger.error(f"Failed to load ticker config from {self.config_path}: {e}")
            raise

    def _get_total_ticker_count(self) -> int:
        """Get total number of tickers across all markets."""
        if not self._config:
            return 0
        return sum(
            len([t for t in market.tickers if t.active])
            for market in self._config.markets.values()
        )

    # =========================================================================
    # Market Queries
    # =========================================================================

    def get_markets(self) -> List[str]:
        """
        Get list of available markets.

        Returns:
            List of market identifiers (e.g., ['us', 'cn', 'hk', 'sg'])
        """
        if not self._config:
            return []
        return list(self._config.markets.keys())

    def get_market_info(self, market: str) -> Optional[MarketConfig]:
        """
        Get market configuration.

        Args:
            market: Market identifier (e.g., 'us', 'cn')

        Returns:
            MarketConfig or None if market not found
        """
        if not self._config:
            return None
        return self._config.markets.get(market)

    def get_market_currency(self, market: str) -> str:
        """
        Get currency for a market.

        Args:
            market: Market identifier

        Returns:
            Currency code (e.g., 'USD', 'HKD') or 'USD' as default
        """
        market_info = self.get_market_info(market)
        return market_info.currency if market_info else 'USD'

    # =========================================================================
    # Ticker Queries
    # =========================================================================

    def get_tickers_for_market(
        self,
        market: str,
        active_only: bool = True,
        min_priority: Optional[int] = None
    ) -> List[str]:
        """
        Get tickers for a specific market.

        Args:
            market: Market identifier (e.g., 'us', 'cn')
            active_only: If True, only return active tickers
            min_priority: Minimum priority level (1-10)

        Returns:
            List of ticker symbols sorted by priority (descending)
        """
        market_info = self.get_market_info(market)

        if not market_info:
            logger.warning(f"Market not found: {market}")
            return []

        tickers = market_info.tickers

        # Filter by active status
        if active_only:
            tickers = [t for t in tickers if t.active]

        # Filter by priority
        if min_priority is not None:
            tickers = [t for t in tickers if t.priority >= min_priority]

        # Sort by priority (descending)
        tickers = sorted(tickers, key=lambda t: t.priority, reverse=True)

        return [t.symbol for t in tickers]

    def get_ticker_metadata(
        self,
        symbol: str,
        market: Optional[str] = None
    ) -> Optional[TickerMetadata]:
        """
        Get metadata for a specific ticker.

        Args:
            symbol: Ticker symbol
            market: Market identifier (optional, searches all markets if None)

        Returns:
            TickerMetadata or None if not found
        """
        if market:
            market_info = self.get_market_info(market)
            if not market_info:
                return None
            for ticker in market_info.tickers:
                if ticker.symbol == symbol:
                    return ticker
        else:
            # Search all markets
            for market_config in self._config.markets.values():
                for ticker in market_config.tickers:
                    if ticker.symbol == symbol:
                        return ticker
        return None

    def get_all_tickers(
        self,
        active_only: bool = True
    ) -> Dict[str, List[str]]:
        """
        Get all tickers grouped by market.

        Args:
            active_only: If True, only return active tickers

        Returns:
            Dictionary mapping market to list of tickers
        """
        result = {}
        for market in self.get_markets():
            result[market] = self.get_tickers_for_market(market, active_only=active_only)
        return result

    # =========================================================================
    # Filtering Methods
    # =========================================================================

    def get_tickers_by_tag(
        self,
        tag: str,
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[str]:
        """
        Get tickers filtered by tag.

        Args:
            tag: Tag to filter by (e.g., 'blue-chip', 'FAANG')
            market: Market identifier (optional, searches all markets if None)
            active_only: If True, only return active tickers

        Returns:
            List of ticker symbols
        """
        tag = tag.lower().strip()
        result = []

        markets_to_search = [market] if market else self.get_markets()

        for mkt in markets_to_search:
            market_info = self.get_market_info(mkt)
            if not market_info:
                continue

            for ticker in market_info.tickers:
                if active_only and not ticker.active:
                    continue
                if tag in [t.lower() for t in ticker.tags]:
                    result.append(ticker.symbol)

        return result

    def get_tickers_by_sector(
        self,
        sector: str,
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[str]:
        """
        Get tickers filtered by sector.

        Args:
            sector: Sector name (e.g., 'Technology', 'Finance')
            market: Market identifier (optional)
            active_only: If True, only return active tickers

        Returns:
            List of ticker symbols
        """
        result = []
        sector_normalized = sector.lower().strip()

        markets_to_search = [market] if market else self.get_markets()

        for mkt in markets_to_search:
            market_info = self.get_market_info(mkt)
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
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[str]:
        """
        Get tickers filtered by exchange.

        Args:
            exchange: Exchange code (e.g., 'NASDAQ', 'NYSE')
            market: Market identifier (optional)
            active_only: If True, only return active tickers

        Returns:
            List of ticker symbols
        """
        result = []
        exchange_normalized = exchange.upper().strip()

        markets_to_search = [market] if market else self.get_markets()

        for mkt in markets_to_search:
            market_info = self.get_market_info(mkt)
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
        tags: List[str],
        match_all: bool = False,
        market: Optional[str] = None,
        active_only: bool = True
    ) -> List[str]:
        """
        Get tickers filtered by multiple tags.

        Args:
            tags: List of tags to filter by
            match_all: If True, requires all tags. If False, requires any tag
            market: Market identifier (optional)
            active_only: If True, only return active tickers

        Returns:
            List of ticker symbols
        """
        tags_normalized = [t.lower().strip() for t in tags]
        result = []

        markets_to_search = [market] if market else self.get_markets()

        for mkt in markets_to_search:
            market_info = self.get_market_info(mkt)
            if not market_info:
                continue

            for ticker in market_info.tickers:
                if active_only and not ticker.active:
                    continue

                ticker_tags = [t.lower() for t in ticker.tags]

                if match_all:
                    # Must have all tags
                    if all(tag in ticker_tags for tag in tags_normalized):
                        result.append(ticker.symbol)
                else:
                    # Must have at least one tag
                    if any(tag in ticker_tags for tag in tags_normalized):
                        result.append(ticker.symbol)

        return result

    # =========================================================================
    # Group Methods
    # =========================================================================

    def get_groups(self) -> List[str]:
        """Get list of available group names."""
        if not self._config or not self._config.groups:
            return []
        return list(self._config.groups.keys())

    def get_group_info(self, group_name: str) -> Optional[GroupConfig]:
        """
        Get group configuration.

        Args:
            group_name: Name of the group

        Returns:
            GroupConfig or None if not found
        """
        if not self._config or not self._config.groups:
            return None
        return self._config.groups.get(group_name)

    def get_tickers_by_group(
        self,
        group_name: str,
        active_only: bool = True
    ) -> List[str]:
        """
        Get tickers from a predefined group.

        Args:
            group_name: Name of the group
            active_only: If True, only return active tickers

        Returns:
            List of ticker symbols
        """
        group_info = self.get_group_info(group_name)

        if not group_info:
            logger.warning(f"Group not found: {group_name}")
            return []

        result = []

        # Handle different group formats
        if isinstance(group_info.tickers, list):
            # Simple list format (single market)
            for ticker in group_info.tickers:
                metadata = self.get_ticker_metadata(ticker, market=None)
                if metadata:
                    if active_only and not metadata.active:
                        continue
                    result.append(metadata.symbol)
        elif isinstance(group_info.tickers, dict):
            # Market-specific format
            for market, tickers in group_info.tickers.items():
                for ticker in tickers:
                    metadata = self.get_ticker_metadata(ticker, market=market)
                    if metadata:
                        if active_only and not metadata.active:
                            continue
                        result.append(metadata.symbol)

        return result

    # =========================================================================
    # Validation Methods
    # =========================================================================

    def validate_ticker_format(self, symbol: str, market: str) -> bool:
        """
        Validate ticker symbol format for a specific market.

        Args:
            symbol: Ticker symbol to validate
            market: Market identifier

        Returns:
            True if format is valid, False otherwise
        """
        if not self._config or not self._config.validation:
            return True  # Skip validation if no rules defined

        validation = self._config.validation
        pattern = validation.market_formats.get(market)

        if not pattern:
            return True  # No pattern defined for this market

        try:
            return bool(re.match(pattern, symbol))
        except re.error:
            logger.error(f"Invalid regex pattern for market {market}: {pattern}")
            return False

    def validate_config(self) -> Dict[str, List[str]]:
        """
        Validate entire configuration.

        Returns:
            Dictionary with validation results:
            {
                'errors': [...],  # Critical errors
                'warnings': [...]  # Non-critical issues
            }
        """
        errors = []
        warnings = []

        if not self._config:
            errors.append("No configuration loaded")
            return {'errors': errors, 'warnings': warnings}

        # Validate ticker formats
        for market_name, market_config in self._config.markets.items():
            for ticker in market_config.tickers:
                if not self.validate_ticker_format(ticker.symbol, market_name):
                    errors.append(
                        f"Invalid ticker format for {market_name}: {ticker.symbol}"
                    )

        # Check for missing metadata
        for market_name, market_config in self._config.markets.items():
            for ticker in market_config.tickers:
                if not ticker.name:
                    warnings.append(f"Ticker {ticker.symbol} ({market_name}) missing name")

                if not ticker.sector:
                    warnings.append(f"Ticker {ticker.symbol} ({market_name}) missing sector")

                if not ticker.tags:
                    warnings.append(f"Ticker {ticker.symbol} ({market_name}) has no tags")

        # Validate groups
        if self._config.groups:
            for group_name, group_config in self._config.groups.items():
                if isinstance(group_config.tickers, list):
                    for ticker in group_config.tickers:
                        metadata = self.get_ticker_metadata(ticker)
                        if not metadata:
                            warnings.append(
                                f"Group '{group_name}' references unknown ticker: {ticker}"
                            )

        return {'errors': errors, 'warnings': warnings}

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def list_tickers(
        self,
        market: Optional[str] = None,
        active_only: bool = True,
        include_metadata: bool = False
    ) -> Union[List[str], Dict[str, Dict]]:
        """
        List tickers with optional metadata.

        Args:
            market: Market identifier (optional)
            active_only: If True, only return active tickers
            include_metadata: If True, return full metadata

        Returns:
            List of symbols or dict of metadata
        """
        if include_metadata:
            result = {}
            markets_to_search = [market] if market else self.get_markets()

            for mkt in markets_to_search:
                market_info = self.get_market_info(mkt)
                if not market_info:
                    continue

                for ticker in market_info.tickers:
                    if active_only and not ticker.active:
                        continue

                    result[ticker.symbol] = {
                        'symbol': ticker.symbol,
                        'name': ticker.name,
                        'market': mkt,
                        'exchange': ticker.exchange,
                        'sector': ticker.sector,
                        'tags': ticker.tags,
                        'active': ticker.active,
                        'priority': ticker.priority
                    }

            return result
        else:
            if market:
                return self.get_tickers_for_market(market, active_only=active_only)
            else:
                all_tickers = {}
                for mkt in self.get_markets():
                    all_tickers[mkt] = self.get_tickers_for_market(mkt, active_only=active_only)
                return all_tickers

    def get_stats(self) -> Dict[str, any]:
        """
        Get configuration statistics.

        Returns:
            Dictionary with stats about the configuration
        """
        if not self._config:
            return {}

        stats = {
            'version': self._config.version,
            'total_markets': len(self._config.markets),
            'total_groups': len(self._config.groups) if self._config.groups else 0,
            'markets': {}
        }

        for market_name, market_config in self._config.markets.items():
            active_tickers = [t for t in market_config.tickers if t.active]
            inactive_tickers = [t for t in market_config.tickers if not t.active]

            stats['markets'][market_name] = {
                'currency': market_config.currency,
                'total_tickers': len(market_config.tickers),
                'active_tickers': len(active_tickers),
                'inactive_tickers': len(inactive_tickers),
                'exchanges': list(set(t.exchange for t in market_config.tickers)),
                'sectors': list(set(t.sector for t in market_config.tickers))
            }

        return stats

    # =========================================================================
    # Class Methods
    # =========================================================================

    @classmethod
    def from_path(cls, config_path: Union[str, Path]) -> 'TickerConfig':
        """
        Create TickerConfig from custom path.

        Args:
            config_path: Path to YAML config file

        Returns:
            TickerConfig instance
        """
        return cls(config_path=Path(config_path))


# =============================================================================
# Convenience Functions
# =============================================================================

def get_default_config() -> TickerConfig:
    """
    Get default ticker configuration.

    Returns:
        TickerConfig instance with default config file
    """
    return TickerConfig()


def load_tickers_for_market(
    market: str,
    config_path: Optional[Path] = None,
    active_only: bool = True
) -> List[str]:
    """
    Convenience function to load tickers for a market.

    Args:
        market: Market identifier
        config_path: Optional custom config path
        active_only: Only return active tickers

    Returns:
        List of ticker symbols
    """
    config = TickerConfig(config_path=config_path)
    return config.get_tickers_for_market(market, active_only=active_only)
