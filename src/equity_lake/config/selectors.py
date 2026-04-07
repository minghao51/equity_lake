"""Query helpers for ticker configuration."""

from typing import Any

from equity_lake.config.models import (
    GroupConfig,
    MarketConfig,
    TickerConfigRoot,
    TickerMetadata,
)


def get_markets(config: TickerConfigRoot | None) -> list[str]:
    """Return the configured market identifiers."""
    if not config:
        return []
    return list(config.markets.keys())


def get_market_info(
    config: TickerConfigRoot | None,
    market: str,
) -> MarketConfig | None:
    """Return the configuration for a single market."""
    if not config:
        return None
    return config.markets.get(market)


def get_market_currency(config: TickerConfigRoot | None, market: str) -> str:
    """Return the configured market currency, defaulting to USD."""
    market_info = get_market_info(config, market)
    return market_info.currency if market_info else "USD"


def get_tickers_for_market(
    config: TickerConfigRoot | None,
    market: str,
    active_only: bool = True,
    min_priority: int | None = None,
) -> list[str]:
    """Return ticker symbols for a market sorted by priority."""
    market_info = get_market_info(config, market)
    if not market_info:
        return []

    tickers = market_info.tickers
    if active_only:
        tickers = [ticker for ticker in tickers if ticker.active]
    if min_priority is not None:
        tickers = [ticker for ticker in tickers if ticker.priority >= min_priority]

    tickers = sorted(tickers, key=lambda ticker: ticker.priority, reverse=True)
    return [ticker.symbol for ticker in tickers]


def get_ticker_metadata(
    config: TickerConfigRoot | None,
    symbol: str,
    market: str | None = None,
) -> TickerMetadata | None:
    """Find metadata for a ticker in one market or across all markets."""
    if market:
        market_info = get_market_info(config, market)
        if not market_info:
            return None
        for ticker in market_info.tickers:
            if ticker.symbol == symbol:
                return ticker
        return None

    if not config:
        return None

    for market_config in config.markets.values():
        for ticker in market_config.tickers:
            if ticker.symbol == symbol:
                return ticker
    return None


def get_all_tickers(
    config: TickerConfigRoot | None,
    active_only: bool = True,
) -> dict[str, list[str]]:
    """Return all tickers grouped by market."""
    return {market: get_tickers_for_market(config, market, active_only=active_only) for market in get_markets(config)}


def get_tickers_by_tag(
    config: TickerConfigRoot | None,
    tag: str,
    market: str | None = None,
    active_only: bool = True,
) -> list[str]:
    """Return tickers matching a single tag."""
    tag_normalized = tag.lower().strip()
    result: list[str] = []
    markets_to_search = [market] if market else get_markets(config)

    for market_name in markets_to_search:
        market_info = get_market_info(config, market_name)
        if not market_info:
            continue
        for ticker in market_info.tickers:
            if active_only and not ticker.active:
                continue
            if tag_normalized in [item.lower() for item in ticker.tags]:
                result.append(ticker.symbol)

    return result


def get_tickers_by_sector(
    config: TickerConfigRoot | None,
    sector: str,
    market: str | None = None,
    active_only: bool = True,
) -> list[str]:
    """Return tickers matching a sector."""
    result: list[str] = []
    sector_normalized = sector.lower().strip()
    markets_to_search = [market] if market else get_markets(config)

    for market_name in markets_to_search:
        market_info = get_market_info(config, market_name)
        if not market_info:
            continue
        for ticker in market_info.tickers:
            if active_only and not ticker.active:
                continue
            if ticker.sector.lower() == sector_normalized:
                result.append(ticker.symbol)

    return result


def get_tickers_by_exchange(
    config: TickerConfigRoot | None,
    exchange: str,
    market: str | None = None,
    active_only: bool = True,
) -> list[str]:
    """Return tickers matching an exchange."""
    result: list[str] = []
    exchange_normalized = exchange.upper().strip()
    markets_to_search = [market] if market else get_markets(config)

    for market_name in markets_to_search:
        market_info = get_market_info(config, market_name)
        if not market_info:
            continue
        for ticker in market_info.tickers:
            if active_only and not ticker.active:
                continue
            if ticker.exchange.upper() == exchange_normalized:
                result.append(ticker.symbol)

    return result


def get_tickers_by_tags(
    config: TickerConfigRoot | None,
    tags: list[str],
    match_all: bool = False,
    market: str | None = None,
    active_only: bool = True,
) -> list[str]:
    """Return tickers matching one or more tags."""
    tags_normalized = [tag.lower().strip() for tag in tags]
    result: list[str] = []
    markets_to_search = [market] if market else get_markets(config)

    for market_name in markets_to_search:
        market_info = get_market_info(config, market_name)
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


def get_groups(config: TickerConfigRoot | None) -> list[str]:
    """Return configured group names."""
    if not config or not config.groups:
        return []
    return list(config.groups.keys())


def get_group_info(
    config: TickerConfigRoot | None,
    group_name: str,
) -> GroupConfig | None:
    """Return one configured ticker group."""
    if not config or not config.groups:
        return None
    return config.groups.get(group_name)


def get_tickers_by_group(
    config: TickerConfigRoot | None,
    group_name: str,
    active_only: bool = True,
) -> list[str]:
    """Return tickers defined by a named group."""
    group_info = get_group_info(config, group_name)
    if not group_info:
        return []

    result: list[str] = []
    if isinstance(group_info.tickers, list):
        for symbol in group_info.tickers:
            metadata = get_ticker_metadata(config, symbol)
            if metadata and (not active_only or metadata.active):
                result.append(metadata.symbol)
        return result

    for market, tickers in group_info.tickers.items():
        for symbol in tickers:
            metadata = get_ticker_metadata(config, symbol, market=market)
            if metadata and (not active_only or metadata.active):
                result.append(metadata.symbol)

    return result


def list_tickers(
    config: TickerConfigRoot | None,
    market: str | None = None,
    active_only: bool = True,
    include_metadata: bool = False,
) -> list[str] | dict[str, list[str]] | dict[str, dict[str, Any]]:
    """List configured tickers with optional metadata."""
    if include_metadata:
        result: dict[str, dict[str, Any]] = {}
        markets_to_search = [market] if market else get_markets(config)
        for market_name in markets_to_search:
            market_info = get_market_info(config, market_name)
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
        return get_tickers_for_market(config, market, active_only=active_only)

    return {
        market_name: get_tickers_for_market(
            config,
            market_name,
            active_only=active_only,
        )
        for market_name in get_markets(config)
    }


def get_stats(config: TickerConfigRoot | None) -> dict[str, Any]:
    """Return summary statistics for the loaded configuration."""
    if not config:
        return {}

    stats: dict[str, Any] = {
        "version": config.version,
        "total_markets": len(config.markets),
        "total_groups": len(config.groups) if config.groups else 0,
        "markets": {},
    }

    for market_name, market_config in config.markets.items():
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


__all__ = [
    "get_all_tickers",
    "get_group_info",
    "get_groups",
    "get_market_currency",
    "get_market_info",
    "get_markets",
    "get_stats",
    "get_ticker_metadata",
    "get_tickers_by_exchange",
    "get_tickers_by_group",
    "get_tickers_by_sector",
    "get_tickers_by_tag",
    "get_tickers_by_tags",
    "get_tickers_for_market",
    "list_tickers",
]
