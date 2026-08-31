"""Load watchlist and signal configuration from YAML files."""

from pathlib import Path

import yaml

from equity_lake.core.paths import CONFIG_DIR
from equity_lake.signals.models import SignalConfig, Watchlist

# Anchored to the project root (not the current working directory) so scans work
# from any CWD, mirroring core.paths.
DEFAULT_WATCHLIST_PATH = CONFIG_DIR / "watchlist.yaml"
DEFAULT_SIGNALS_PATH = CONFIG_DIR / "signals.yaml"


def load_watchlist(path: Path | None = None) -> Watchlist:
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

    if not config_path.exists():
        raise FileNotFoundError(f"Watchlist config not found: {config_path}")

    with open(config_path) as f:
        data = yaml.safe_load(f)

    return Watchlist(**data)


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

    if not config_path.exists():
        raise FileNotFoundError(f"Signal config not found: {config_path}")

    with open(config_path) as f:
        data = yaml.safe_load(f)

    return SignalConfig(**data)
