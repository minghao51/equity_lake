"""YAML configuration file validators for CI/CD pipeline."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from equity_lake.core.config import TickerConfigRoot
from equity_lake.signals.models import SignalConfig
from equity_lake.signals.models import Watchlist as SignalWatchlist


def validate_tickers(filepath: Path) -> list[str]:
    """Validate tickers.yaml structure and content via the canonical models.

    Structural checks (required fields, types, duplicate symbols) are enforced
    by the pydantic ``TickerConfigRoot`` model; content-format checks (symbol
    regex, unknown group references) come from ``TickerConfigRoot.validate_config``.
    """
    errors: list[str] = []

    if not filepath.exists():
        errors.append(f"File not found: {filepath}")
        return errors

    try:
        with open(filepath, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        errors.append(f"YAML parse error: {exc}")
        return errors

    if not isinstance(raw, dict):
        errors.append("Top-level configuration must be a mapping")
        return errors

    for key in ("version", "markets"):
        if key not in raw:
            errors.append(f"Missing required key: {key}")

    markets = raw.get("markets")
    if isinstance(markets, dict):
        for market_name, market_data in markets.items():
            if not isinstance(market_data, dict) or not market_data.get("tickers"):
                errors.append(f"Market '{market_name}' has no tickers defined")

    try:
        instance = TickerConfigRoot.from_yaml(filepath)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "<root>"
            errors.append(f"{loc}: {err['msg']}")
        return errors

    result = instance.validate_config()
    errors.extend(result["errors"])
    return errors


def validate_watchlist(filepath: Path) -> list[str]:
    """Validate watchlist.yaml structure."""
    errors = []

    if not filepath.exists():
        errors.append(f"File not found: {filepath}")
        return errors

    try:
        with open(filepath, encoding="utf-8") as f:
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
        with open(filepath, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
        return errors

    try:
        SignalConfig(**config)
    except (TypeError, ValueError) as exc:
        errors.append(f"Invalid signals config: {exc}")

    return errors
