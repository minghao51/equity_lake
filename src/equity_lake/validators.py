"""
Ticker Validation Utilities

This module provides validation functions for ticker symbols, formats,
and market-specific rules. Used by configuration module and fetchers.
"""

import logging
import re
from typing import cast

logger = logging.getLogger(__name__)


# =============================================================================
# Market-Specific Ticker Format Patterns
# =============================================================================

MARKET_FORMATS = {
    "us": r"^[A-Z]{1,5}(-[A-Z]{1,2})?$",  # e.g., AAPL, BRK-A
    "cn": r"^\d{6}$",  # 6-digit code, e.g., 000001, 600000
    "hk": r"^\d{4}\.HK$",  # e.g., 0700.HK, 9988.HK
    "sg": r"^[A-Z]\d{2}\.SI$",  # e.g., D05.SI, O39.SI
}

MARKET_EXAMPLES = {
    "us": ["AAPL", "GOOGL", "MSFT", "BRK-A", "MMM"],
    "cn": ["000001", "600000", "000002", "600036"],
    "hk": ["0700.HK", "9988.HK", "0005.HK", "0941.HK"],
    "sg": ["D05.SI", "O39.SI", "U11.SI", "Z74.SI"],
}


# =============================================================================
# Valid Exchange Codes
# =============================================================================

VALID_EXCHANGES = {
    "us": ["NYSE", "NASDAQ", "AMEX"],
    "cn": ["SSE", "SZSE"],  # Shanghai Stock Exchange, Shenzhen Stock Exchange
    "hk": ["HKEX"],  # Hong Kong Exchanges and Clearing
    "sg": ["SGX"],  # Singapore Exchange
}


# =============================================================================
# Valid Sector Classifications
# =============================================================================

VALID_SECTORS = [
    "Technology",
    "Financial Services",
    "Healthcare",
    "Consumer Cyclical",
    "Consumer Defensive",
    "Energy",
    "Industrials",
    "Communication Services",
    "Real Estate",
    "Utilities",
    "Basic Materials",
]


# =============================================================================
# Common Tags for Grouping
# =============================================================================

COMMON_TAGS = {
    # Investment style
    "blue-chip",
    "growth",
    "value",
    "dividend",
    # Market indices
    "S&P 500",
    "DOW",
    "NASDAQ",
    "HSI",
    # Sector groups
    "FAANG",
    "technology",
    "semiconductor",
    "banking",
    "insurance",
    "ecommerce",
    "internet",
    "EV",
    "healthcare",
    "pharmaceutical",
    # Special status
    "major",
    "SOE",
    "conglomerate",
    "REIT",
    # Other
    "penny-stock",
    "small-cap",
    "mid-cap",
    "large-cap",
}


# =============================================================================
# Validation Functions
# =============================================================================


def validate_ticker_format(symbol: str, market: str) -> tuple[bool, str | None]:
    """
    Validate ticker symbol format for a specific market.

    Args:
        symbol: Ticker symbol to validate
        market: Market identifier ('us', 'cn', 'hk', 'sg')

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not symbol or not isinstance(symbol, str):
        return False, "Symbol must be a non-empty string"

    symbol = symbol.strip()

    if not symbol:
        return False, "Symbol cannot be empty"

    pattern = MARKET_FORMATS.get(market.lower())

    if not pattern:
        return False, f"Unknown market: {market}"

    try:
        if re.match(pattern, symbol):
            return True, None
        else:
            examples = ", ".join(MARKET_EXAMPLES.get(market, []))
            return (
                False,
                f"Invalid {market.upper()} ticker format. Examples: {examples}",
            )
    except re.error as e:
        return False, f"Invalid regex pattern: {e}"


def validate_exchange(exchange: str, market: str) -> tuple[bool, str | None]:
    """
    Validate exchange code for a specific market.

    Args:
        exchange: Exchange code (e.g., 'NASDAQ', 'NYSE')
        market: Market identifier

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not exchange or not isinstance(exchange, str):
        return False, "Exchange must be a non-empty string"

    exchange = exchange.strip().upper()
    valid_exchanges = VALID_EXCHANGES.get(market.lower(), [])

    if not valid_exchanges:
        return False, f"Unknown market: {market}"

    if exchange in valid_exchanges:
        return True, None
    else:
        return (
            False,
            f"Invalid exchange for {market.upper()}: {exchange}. Valid: {', '.join(valid_exchanges)}",
        )


def validate_sector(sector: str) -> tuple[bool, str | None]:
    """
    Validate sector name.

    Args:
        sector: Sector name

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not sector or not isinstance(sector, str):
        return False, "Sector must be a non-empty string"

    sector = sector.strip()

    if sector in VALID_SECTORS:
        return True, None
    else:
        return (
            False,
            f"Invalid sector: {sector}. Valid sectors: {', '.join(VALID_SECTORS)}",
        )


def validate_tags(tags: list[str]) -> tuple[bool, list[str]]:
    """
    Validate and normalize tags.

    Args:
        tags: List of tags

    Returns:
        Tuple of (is_valid, list of warnings)
    """
    warnings: list[str] = []

    if not tags:
        return True, warnings

    normalized_tags = []

    for tag in tags:
        if not isinstance(tag, str):
            warnings.append(f"Tag must be string, got {type(tag)}: {tag}")  # type: ignore[unreachable]
            continue

        tag_normalized = tag.strip().lower()

        if not tag_normalized:
            warnings.append("Empty tag found and skipped")
            continue

        # Check for known tags (warning only)
        if tag_normalized not in COMMON_TAGS:
            warnings.append(f"Uncommon tag: '{tag}' (not in common tags list)")

        normalized_tags.append(tag_normalized)

    return True, warnings


def validate_priority(priority: int) -> tuple[bool, str | None]:
    """
    Validate priority value.

    Args:
        priority: Priority value (1-10)

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not isinstance(priority, int):
        return False, f"Priority must be integer, got {type(priority)}"  # type: ignore[unreachable]

    if 1 <= priority <= 10:
        return True, None
    else:
        return False, f"Priority must be between 1 and 10, got {priority}"


# =============================================================================
# Duplicate Detection
# =============================================================================


def find_duplicate_symbols(tickers: list[dict], market: str) -> list[str]:
    """
    Find duplicate ticker symbols within a market.

    Args:
        tickers: List of ticker dictionaries with 'symbol' key
        market: Market identifier (for logging)

    Returns:
        List of duplicate symbols
    """
    symbols = [t.get("symbol") for t in tickers if "symbol" in t]
    seen = set()
    duplicates = set()

    for symbol in symbols:
        if symbol in seen:
            duplicates.add(symbol)
        seen.add(symbol)

    if duplicates:
        logger.warning(f"Found duplicate symbols in {market} market: {duplicates}")

    return cast(list[str], list(duplicates))


def find_cross_market_duplicates(config: dict) -> dict[str, list[str]]:
    """
    Find ticker symbols that appear in multiple markets.

    Args:
        config: Full configuration dict with 'markets' key

    Returns:
        Dictionary mapping symbol to list of markets it appears in
    """
    symbol_markets: dict[str, list[str]] = {}

    for market_name, market_config in config.get("markets", {}).items():
        for ticker in market_config.get("tickers", []):
            symbol = ticker.get("symbol")

            if symbol:
                if symbol not in symbol_markets:
                    symbol_markets[symbol] = []
                symbol_markets[symbol].append(market_name)

    # Find symbols in multiple markets
    duplicates = {symbol: markets for symbol, markets in symbol_markets.items() if len(markets) > 1}

    if duplicates:
        logger.warning(f"Found tickers in multiple markets: {duplicates}")

    return duplicates


# =============================================================================
# Market Detection
# =============================================================================


def detect_market_from_symbol(symbol: str) -> str | None:
    """
    Attempt to detect market from ticker symbol format.

    Args:
        symbol: Ticker symbol

    Returns:
        Market identifier or None if undetectable
    """
    symbol = symbol.strip()

    # Check each market's pattern
    for market, pattern in MARKET_FORMATS.items():
        try:
            if re.match(pattern, symbol):
                return market
        except re.error:
            continue

    return None


def detect_market_from_exchange(exchange: str) -> str | None:
    """
    Attempt to detect market from exchange code.

    Args:
        exchange: Exchange code

    Returns:
        Market identifier or None if undetectable
    """
    exchange = exchange.strip().upper()

    for market, exchanges in VALID_EXCHANGES.items():
        if exchange in exchanges:
            return market

    return None


# =============================================================================
# Schema Validation
# =============================================================================


def validate_ticker_entry(ticker: dict, market: str) -> tuple[bool, list[str]]:
    """
    Validate a complete ticker entry.

    Args:
        ticker: Ticker dictionary
        market: Market identifier

    Returns:
        Tuple of (is_valid, list of errors)
    """
    errors: list[str] = []

    # Required fields
    required_fields = ["symbol", "name", "exchange", "sector", "active"]

    for field in required_fields:
        if field not in ticker:
            errors.append(f"Missing required field: {field}")

    # Validate symbol
    if "symbol" in ticker:
        is_valid, error = validate_ticker_format(ticker["symbol"], market)
        if not is_valid:
            errors.append(f"Symbol validation failed: {error}")

    # Validate exchange
    if "exchange" in ticker:
        is_valid, error = validate_exchange(ticker["exchange"], market)
        if not is_valid:
            errors.append(f"Exchange validation failed: {error}")

    # Validate sector
    if "sector" in ticker:
        is_valid, error = validate_sector(ticker["sector"])
        if not is_valid:
            errors.append(f"Sector validation failed: {error}")

    # Validate tags (if present)
    if "tags" in ticker:
        is_valid, warnings = validate_tags(ticker["tags"])
        if not is_valid:
            errors.extend(warnings)

    # Validate priority (if present)
    if "priority" in ticker:
        is_valid, error = validate_priority(ticker["priority"])
        if not is_valid:
            errors.append(f"Priority validation failed: {error}")

    # Validate active field type
    if "active" in ticker and not isinstance(ticker["active"], bool):
        errors.append(f"Active field must be boolean, got {type(ticker['active'])}")

    return len(errors) == 0, errors


def validate_market_config(market_config: dict, market_name: str) -> tuple[bool, list[str]]:
    """
    Validate entire market configuration.

    Args:
        market_config: Market configuration dictionary
        market_name: Market identifier

    Returns:
        Tuple of (is_valid, list of errors)
    """
    errors: list[str] = []

    # Check required fields
    if "currency" not in market_config:
        errors.append(f"Market {market_name}: Missing 'currency' field")

    if "tickers" not in market_config:
        errors.append(f"Market {market_name}: Missing 'tickers' field")
        return False, errors

    # Validate each ticker
    tickers = market_config.get("tickers", [])

    if not isinstance(tickers, list):
        errors.append(f"Market {market_name}: 'tickers' must be a list")
        return False, errors

    for i, ticker in enumerate(tickers):
        is_valid, ticker_errors = validate_ticker_entry(ticker, market_name)

        if not is_valid:
            errors.append(f"Market {market_name}, ticker #{i + 1}: {', '.join(ticker_errors)}")

    # Check for duplicates
    duplicates = find_duplicate_symbols(tickers, market_name)
    if duplicates:
        errors.append(f"Market {market_name}: Duplicate symbols found: {duplicates}")

    return len(errors) == 0, errors


def validate_full_config(config: dict) -> tuple[bool, dict[str, list[str]]]:
    """
    Validate entire configuration file.

    Args:
        config: Full configuration dictionary

    Returns:
        Tuple of (is_valid, {'errors': [...], 'warnings': [...]})
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Check top-level structure
    if "markets" not in config:
        errors.append("Missing 'markets' section in configuration")
        return False, {"errors": errors, "warnings": warnings}

    # Validate each market
    for market_name, market_config in config["markets"].items():
        is_valid, market_errors = validate_market_config(market_config, market_name)
        errors.extend(market_errors)

    # Check for cross-market duplicates
    duplicates = find_cross_market_duplicates(config)
    if duplicates:
        warnings.append(f"Tickers in multiple markets: {duplicates}")

    # Validate groups (if present)
    if "groups" in config:
        group_errors = validate_groups(config["groups"], config["markets"])
        errors.extend(group_errors["errors"])
        warnings.extend(group_errors["warnings"])

    return len(errors) == 0, {"errors": errors, "warnings": warnings}


def validate_groups(groups: dict, markets: dict) -> dict[str, list[str]]:
    """
    Validate ticker groups.

    Args:
        groups: Groups configuration
        markets: Markets configuration (for ticker validation)

    Returns:
        Dictionary with 'errors' and 'warnings' lists
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not groups:
        return {"errors": errors, "warnings": warnings}

    # Build symbol registry
    symbol_registry = set()
    for _market_name, market_config in markets.items():
        for ticker in market_config.get("tickers", []):
            symbol_registry.add(ticker.get("symbol"))

    # Validate each group
    for group_name, group_config in groups.items():
        if not isinstance(group_config, dict):
            errors.append(f"Group '{group_name}' must be a dictionary")
            continue

        # Check required fields
        if "markets" not in group_config:
            errors.append(f"Group '{group_name}': Missing 'markets' field")

        if "tickers" not in group_config:
            errors.append(f"Group '{group_name}': Missing 'tickers' field")
            continue

        # Validate ticker references
        tickers = group_config["tickers"]

        if isinstance(tickers, list):
            # Simple format
            for ticker in tickers:
                if ticker not in symbol_registry:
                    warnings.append(f"Group '{group_name}': Ticker '{ticker}' not found in any market")
        elif isinstance(tickers, dict):
            # Market-specific format
            for market_name, market_tickers in tickers.items():
                if market_name not in markets:
                    warnings.append(f"Group '{group_name}': References unknown market '{market_name}'")

                for ticker in market_tickers:
                    if ticker not in symbol_registry:
                        warnings.append(f"Group '{group_name}': Ticker '{ticker}' not found in market '{market_name}'")

    return {"errors": errors, "warnings": warnings}


# =============================================================================
# Utility Functions
# =============================================================================


def normalize_symbol(symbol: str, market: str) -> str:
    """
    Normalize ticker symbol for a specific market.

    Args:
        symbol: Raw ticker symbol
        market: Market identifier

    Returns:
        Normalized symbol
    """
    symbol = symbol.strip().upper()

    if market == "cn":
        # Ensure 6-digit format with leading zeros
        return symbol.zfill(6)
    elif market == "hk":
        # Ensure format: 0123.HK
        if not symbol.endswith(".HK"):
            parts = symbol.split(".")
            if len(parts) == 1:
                # Add .HK suffix
                num_part = parts[0].zfill(4)
                return f"{num_part}.HK"
        return symbol
    elif market == "sg":
        # Ensure format: X00.SI
        if not symbol.endswith(".SI"):
            parts = symbol.split(".")
            if len(parts) == 1:
                num_part = parts[0][1:].zfill(2)
                letter = parts[0][0].upper()
                return f"{letter}{num_part}.SI"
        return symbol
    else:
        return symbol


def get_market_examples(market: str, count: int = 5) -> list[str]:
    """
    Get example ticker symbols for a market.

    Args:
        market: Market identifier
        count: Number of examples to return

    Returns:
        List of example symbols
    """
    examples = MARKET_EXAMPLES.get(market.lower(), [])
    return examples[:count]


def print_validation_results(is_valid: bool, errors: list[str], warnings: list[str]) -> None:
    """
    Print validation results in a human-readable format.

    Args:
        is_valid: Whether validation passed
        errors: List of error messages
        warnings: List of warning messages
    """
    if is_valid and not warnings:
        print("✅ Configuration is valid!")
        return

    if errors:
        print(f"❌ Validation failed with {len(errors)} error(s):")
        for error in errors:
            print(f"  - {error}")

    if warnings:
        print(f"⚠️  {len(warnings)} warning(s):")
        for warning in warnings:
            print(f"  - {warning}")

    if is_valid:
        print("✅ Configuration is valid (with warnings)")
