"""YAML configuration file validators for CI/CD pipeline."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from equity_lake.signals.models import SignalConfig
from equity_lake.signals.models import Watchlist as SignalWatchlist


def validate_tickers(filepath: Path) -> list[str]:
    """Validate tickers.yaml structure and content."""
    errors = []

    if not filepath.exists():
        errors.append(f"File not found: {filepath}")
        return errors

    try:
        with open(filepath) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
        return errors

    for key in ["version", "markets"]:
        if key not in config:
            errors.append(f"Missing required key: {key}")

    if "markets" not in config:
        return errors

    markets = config["markets"]
    validation = config.get("validation", {})
    market_formats = validation.get("market_formats", {})
    valid_exchanges = validation.get("valid_exchanges", {})

    for market_name, market_data in markets.items():
        if "tickers" not in market_data:
            errors.append(f"Market '{market_name}' missing 'tickers' key")
            continue

        tickers = market_data["tickers"]
        if not isinstance(tickers, list):
            errors.append(f"Market '{market_name}' tickers must be a list")
            continue

        if not tickers:
            errors.append(f"Market '{market_name}' has no tickers defined")
            continue

        valid_exchanges_list = valid_exchanges.get(market_name, [])
        ticker_pattern = market_formats.get(market_name)
        compiled_pattern = re.compile(ticker_pattern) if ticker_pattern else None

        seen_symbols = set()
        for i, ticker in enumerate(tickers):
            prefix = f"Market '{market_name}', ticker[{i}]"

            for field in ["symbol", "name", "exchange", "sector", "active"]:
                if field not in ticker:
                    errors.append(f"{prefix}: missing field '{field}'")

            symbol = ticker.get("symbol", "")

            if symbol in seen_symbols:
                errors.append(f"{prefix}: duplicate symbol '{symbol}'")
            seen_symbols.add(symbol)

            if compiled_pattern and symbol and not compiled_pattern.match(symbol):
                errors.append(f"{prefix}: symbol '{symbol}' does not match format '{ticker_pattern}'")

            exchange = ticker.get("exchange", "")
            if valid_exchanges_list and exchange and exchange not in valid_exchanges_list:
                errors.append(f"{prefix}: invalid exchange '{exchange}' (valid: {', '.join(valid_exchanges_list)})")

            active = ticker.get("active")
            if active is not None and not isinstance(active, bool):
                errors.append(f"{prefix}: 'active' must be boolean, got {type(active).__name__}")

    if "groups" in config:
        groups = config["groups"]
        if not isinstance(groups, dict):
            errors.append("'groups' must be a mapping")
        else:
            for group_name, group_data in groups.items():
                if "description" not in group_data:
                    errors.append(f"Group '{group_name}': missing 'description'")
                if "tickers" not in group_data:
                    errors.append(f"Group '{group_name}': missing 'tickers'")

    return errors


def validate_watchlist(filepath: Path) -> list[str]:
    """Validate watchlist.yaml structure."""
    errors = []

    if not filepath.exists():
        errors.append(f"File not found: {filepath}")
        return errors

    try:
        with open(filepath) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
        return errors

    try:
        SignalWatchlist(**config)
    except (TypeError, ValueError) as exc:
        errors.append(f"Invalid watchlist config: {exc}")

    return errors


def validate_signals(filepath: Path) -> list[str]:
    """Validate signals.yaml structure."""
    errors = []

    if not filepath.exists():
        errors.append(f"File not found: {filepath}")
        return errors

    try:
        with open(filepath) as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
        return errors

    try:
        SignalConfig(**config)
    except (TypeError, ValueError) as exc:
        errors.append(f"Invalid signals config: {exc}")

    return errors
