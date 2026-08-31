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
    # min_win_rate was dead config (never read by the generator) and is removed
    assert "min_win_rate" not in config.backtest


def test_default_config_paths_are_project_root_anchored():
    """B4: defaults must not depend on the current working directory."""
    from equity_lake.core.paths import PROJECT_ROOT
    from equity_lake.signals.config import DEFAULT_SIGNALS_PATH, DEFAULT_WATCHLIST_PATH

    assert DEFAULT_WATCHLIST_PATH == PROJECT_ROOT / "config" / "watchlist.yaml"
    assert DEFAULT_SIGNALS_PATH == PROJECT_ROOT / "config" / "signals.yaml"


def test_load_watchlist_missing_file():
    """Test error when watchlist file missing."""
    with pytest.raises(FileNotFoundError):
        load_watchlist(Path("nonexistent.yaml"))


def test_load_signal_config_missing_file():
    """Test error when signal config file missing."""
    with pytest.raises(FileNotFoundError):
        load_signal_config(Path("nonexistent.yaml"))


def test_signal_scan_missing_config_exits_cleanly():
    """B4: a missing watchlist/config must exit 1 with a clean error, not a traceback."""
    from typer.testing import CliRunner

    from equity_lake.cli.__main__ import app

    runner = CliRunner()
    result = runner.invoke(app, ["signal", "scan", "--watchlist", "nonexistent.yaml"])
    assert result.exit_code == 1
    assert "not found" in result.stdout
