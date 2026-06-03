"""Validate configuration files for CI/CD pipeline.

This script validates tickers.yaml, watchlist.yaml, and signals.yaml
to ensure changes don't break the ingestion pipeline.

Usage:
    uv run python scripts/validate_configs.py
    uv run python scripts/validate_configs.py --tickers config/tickers.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml


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

    # Check required top-level keys
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
        # Check required market keys
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

        # Get exchange list for this market
        valid_exchanges_list = valid_exchanges.get(market_name, [])
        ticker_pattern = market_formats.get(market_name)
        compiled_pattern = re.compile(ticker_pattern) if ticker_pattern else None

        seen_symbols = set()
        for i, ticker in enumerate(tickers):
            prefix = f"Market '{market_name}', ticker[{i}]"

            # Check required fields
            for field in ["symbol", "name", "exchange", "sector", "active"]:
                if field not in ticker:
                    errors.append(f"{prefix}: missing field '{field}'")

            symbol = ticker.get("symbol", "")

            # Check duplicate symbols
            if symbol in seen_symbols:
                errors.append(f"{prefix}: duplicate symbol '{symbol}'")
            seen_symbols.add(symbol)

            # Check symbol format
            if compiled_pattern and symbol and not compiled_pattern.match(symbol):
                errors.append(f"{prefix}: symbol '{symbol}' does not match format '{ticker_pattern}'")

            # Check exchange is valid
            exchange = ticker.get("exchange", "")
            if valid_exchanges_list and exchange and exchange not in valid_exchanges_list:
                errors.append(f"{prefix}: invalid exchange '{exchange}' (valid: {', '.join(valid_exchanges_list)})")

            # Check active is boolean
            active = ticker.get("active")
            if active is not None and not isinstance(active, bool):
                errors.append(f"{prefix}: 'active' must be boolean, got {type(active).__name__}")

    # Check groups
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

    if "name" not in config:
        errors.append("Watchlist missing 'name' field")
    if "tickers" not in config:
        errors.append("Watchlist missing 'tickers' field")
    elif not isinstance(config["tickers"], list):
        errors.append("Watchlist 'tickers' must be a list")

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

    if "signals" not in config:
        errors.append("Signals config missing 'signals' key")
    elif not isinstance(config["signals"], dict):
        errors.append("'signals' must be a mapping")

    return errors


def main() -> int:
    """Main validation entry point."""
    parser = argparse.ArgumentParser(description="Validate Equity Lake configuration files")
    parser.add_argument(
        "--tickers",
        type=Path,
        default=Path("config/tickers.yaml"),
        help="Path to tickers.yaml",
    )
    parser.add_argument(
        "--watchlist",
        type=Path,
        default=Path("config/watchlist.yaml"),
        help="Path to watchlist.yaml",
    )
    parser.add_argument(
        "--signals",
        type=Path,
        default=Path("config/signals.yaml"),
        help="Path to signals.yaml",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Validate all config files",
    )
    args = parser.parse_args()

    all_errors: list[str] = []

    # Always validate tickers
    print(f"Validating {args.tickers}...")
    errors = validate_tickers(args.tickers)
    all_errors.extend(errors)
    print(f"  {'✅ OK' if not errors else f'❌ {len(errors)} error(s)'}")

    if args.all:
        for name, path in [("watchlist", args.watchlist), ("signals", args.signals)]:
            if path.exists():
                print(f"Validating {path}...")
                validator = {"watchlist": validate_watchlist, "signals": validate_signals}[name]
                errors = validator(path)
                all_errors.extend(errors)
                print(f"  {'✅ OK' if not errors else f'❌ {len(errors)} error(s)'}")
            else:
                print(f"Skipping {path} (not found)")

    if all_errors:
        print(f"\n{'=' * 60}")
        print("Validation FAILED:")
        for error in all_errors:
            print(f"  ❌ {error}")
        return 1

    print(f"\n{'=' * 60}")
    print("✅ All validations passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
