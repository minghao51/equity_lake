"""Load watchlist and signal configuration from YAML files."""

from pathlib import Path
from typing import Any

import yaml

from equity_lake.core.config_models import DEFAULT_TICKERS_PATH, TickerConfigRoot
from equity_lake.core.paths import CONFIG_DIR
from equity_lake.signals.models import SignalConfig, Watchlist

# Anchored to the project root (not the current working directory) so scans work
# from any CWD, mirroring core.paths.
DEFAULT_WATCHLIST_PATH = CONFIG_DIR / "watchlist.yaml"
DEFAULT_SIGNALS_PATH = CONFIG_DIR / "signals.yaml"


def _load_mapping(config_path: Path, label: str) -> dict[str, Any]:
    if not config_path.exists():
        raise FileNotFoundError(f"{label} config not found: {config_path}")
    with config_path.open(encoding="utf-8") as file_obj:
        data = yaml.safe_load(file_obj)
    if not isinstance(data, dict):
        raise ValueError(f"{label} YAML root must be a mapping")
    if any(not isinstance(key, str) for key in data):
        raise ValueError(f"{label} YAML mapping keys must be strings")
    return data


def _reject_unknown_keys(data: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"Unknown {label} field(s): {', '.join(unknown)}")


def validate_us_watchlist(watchlist: Watchlist, ticker_config_path: Path | None = None) -> None:
    """Require active, unambiguous US membership without guessing a market identity."""
    ticker_config = TickerConfigRoot.from_yaml(ticker_config_path or DEFAULT_TICKERS_PATH)
    us_market = ticker_config.markets.get("us")
    if us_market is None:
        raise ValueError("Signal scans require an active `markets.us` ticker configuration")
    active_by_market = {
        market: {ticker.symbol for ticker in market_config.tickers if ticker.active} for market, market_config in ticker_config.markets.items()
    }
    active_us = active_by_market["us"]
    for symbol in watchlist.tickers:
        active_markets = [market for market, symbols in active_by_market.items() if symbol in symbols]
        if "us" not in active_markets:
            if active_markets:
                raise ValueError(f"Ticker {symbol!r} is active only in unsupported market(s): {', '.join(active_markets)}")
            raise ValueError(f"Ticker {symbol!r} is not an active US ticker")
        other_markets = [market for market in active_markets if market != "us"]
        if other_markets:
            raise ValueError(
                f"Ticker {symbol!r} is ambiguous across markets us and {', '.join(other_markets)}; signal scans require an unambiguous US identity"
            )
        if symbol not in active_us:
            raise ValueError(f"Ticker {symbol!r} is not an active US ticker")


def load_legacy_watchlist(path: Path) -> Watchlist:
    """Load explicitly selected ticker-only YAML without inferring identity.

    Callers must resolve every symbol against an explicitly selected market
    and exact active source configuration before scanning or writing.
    """
    data = _load_mapping(path, "Legacy watchlist")
    _reject_unknown_keys(data, {"name", "description", "tickers", "groups", "metadata"}, "legacy watchlist")
    if "name" not in data:
        raise ValueError("Legacy watchlist config requires a name")
    watchlist = Watchlist(**data)
    if not watchlist.tickers:
        raise ValueError("Legacy watchlist must contain at least one ticker")
    return watchlist


def load_watchlist(path: Path | None = None, ticker_config_path: Path | None = None) -> Watchlist:
    """Load watchlist from YAML file.

    Args:
        path: Path to watchlist YAML. Defaults to config/watchlist.yaml

    Returns:
        Watchlist object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    config_path = path or DEFAULT_WATCHLIST_PATH
    data = _load_mapping(config_path, "Watchlist")
    _reject_unknown_keys(data, {"name", "description", "tickers", "groups", "metadata"}, "watchlist")
    if "name" not in data:
        raise ValueError("Watchlist config requires a name")
    watchlist = Watchlist(**data)
    if not watchlist.tickers:
        raise ValueError("Watchlist must contain at least one ticker")
    validate_us_watchlist(watchlist, ticker_config_path)
    return watchlist


def load_signal_config(path: Path | None = None) -> SignalConfig:
    """Load signal configuration from YAML file.

    Args:
        path: Path to signals YAML. Defaults to config/signals.yaml

    Returns:
        SignalConfig object

    Raises:
        FileNotFoundError: If config file doesn't exist
        ValueError: If config is invalid
    """
    config_path = path or DEFAULT_SIGNALS_PATH
    data = _load_mapping(config_path, "Signal")
    _reject_unknown_keys(data, {"backtest", "sentiment", "ml"}, "signal config")
    missing = {"backtest", "sentiment", "ml"} - set(data)
    if missing:
        raise ValueError(f"Signal config is missing section(s): {', '.join(sorted(missing))}")
    config = SignalConfig(**data)
    if config.is_generator_enabled("ml") and config.ml.get("mode") == "v2_meta_label":
        candidate_strategies = config.ml.get("candidate_strategies")
        if candidate_strategies is None:
            candidate_strategies = config.backtest.get("strategies")
        if not candidate_strategies:
            raise ValueError("v2_meta_label requires non-empty ml.candidate_strategies or backtest.strategies in the selected config")
    return config
