"""Validation helpers for ticker configuration."""

import logging
import re

from equity_lake.config.models import TickerConfigRoot
from equity_lake.config.selectors import get_ticker_metadata

logger = logging.getLogger(__name__)


def validate_ticker_format(
    config: TickerConfigRoot | None,
    symbol: str,
    market: str,
) -> bool:
    """Validate a ticker symbol against market-specific regex rules."""
    if not config or not config.validation:
        return True

    pattern = config.validation.market_formats.get(market)
    if not pattern:
        return True

    try:
        return bool(re.match(pattern, symbol))
    except re.error:
        logger.error("Invalid regex pattern for market %s: %s", market, pattern)
        return False


def validate_config(config: TickerConfigRoot | None) -> dict[str, list[str]]:
    """Validate the loaded ticker configuration."""
    errors: list[str] = []
    warnings: list[str] = []

    if not config:
        errors.append("No configuration loaded")
        return {"errors": errors, "warnings": warnings}

    for market_name, market_config in config.markets.items():
        for ticker in market_config.tickers:
            if not validate_ticker_format(config, ticker.symbol, market_name):
                errors.append(f"Invalid ticker format for {market_name}: {ticker.symbol}")

            if not ticker.name:
                warnings.append(f"Ticker {ticker.symbol} ({market_name}) missing name")
            if not ticker.sector:
                warnings.append(f"Ticker {ticker.symbol} ({market_name}) missing sector")
            if not ticker.tags:
                warnings.append(f"Ticker {ticker.symbol} ({market_name}) has no tags")

    for group_name, group_config in config.groups.items():
        if not isinstance(group_config.tickers, list):
            continue
        for symbol in group_config.tickers:
            if get_ticker_metadata(config, symbol) is None:
                warnings.append(f"Group '{group_name}' references unknown ticker: {symbol}")

    return {"errors": errors, "warnings": warnings}


__all__ = ["validate_config", "validate_ticker_format"]
