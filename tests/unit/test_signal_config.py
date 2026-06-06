"""Test signal configuration loading."""

from pathlib import Path

import pytest

from equity_lake.signals.config import load_signal_config, load_watchlist


def test_load_watchlist():
    """Test loading watchlist from YAML."""
    watchlist = load_watchlist()
    assert watchlist.name == "My Portfolio"
    assert len(watchlist.tickers) == 5
    assert "AAPL" in watchlist.tickers
    assert "tech" in watchlist.groups


def test_load_signal_config():
    """Test loading signal config from YAML."""
    config = load_signal_config()
    assert config.backtest["enabled"] is True
    assert config.sentiment["enabled"] is True
    assert config.ml["enabled"] is True
    assert config.ml["mode"] == "v1_direction"
    assert config.backtest["min_win_rate"] == 0.55


def test_load_watchlist_missing_file():
    """Test error when watchlist file missing."""
    with pytest.raises(FileNotFoundError):
        load_watchlist(Path("nonexistent.yaml"))


def test_load_signal_config_missing_file():
    """Test error when signal config file missing."""
    with pytest.raises(FileNotFoundError):
        load_signal_config(Path("nonexistent.yaml"))
