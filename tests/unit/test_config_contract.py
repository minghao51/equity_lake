"""Contract tests for the settings + ticker-config public API.

These characterize behavior that the planned TickerConfig deepening
(collapsing the pass-through wrapper onto TickerConfigRoot + a cached
``get_ticker_config()``) must preserve.
"""

from pathlib import Path

import pytest

from equity_lake.core.config import TickerConfig, clear_settings_cache, get_settings

_SAMPLE_TICKERS_YAML = """
version: "1.0"
markets:
  us:
    currency: USD
    description: "US Equities"
    tickers:
      - symbol: AAPL
        name: Apple Inc.
        exchange: NASDAQ
        sector: Technology
        tags: [blue-chip, technology]
        active: true
        priority: 9
      - symbol: BRK.B
        name: Berkshire Hathaway
        exchange: NYSE
        sector: Finance
        tags: [blue-chip]
        active: true
        priority: 7
      - symbol: DEAD
        name: Delisted Co
        exchange: NYSE
        sector: Finance
        tags: []
        active: false
  cn:
    currency: CNY
    description: "China A-Shares"
    tickers:
      - symbol: "600519"
        name: Kweichow Moutai
        exchange: SSE
        sector: Consumer
        tags: [blue-chip]
        active: true
"""


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    clear_settings_cache()
    yield
    clear_settings_cache()


def test_get_settings_is_cached() -> None:
    """get_settings() returns the same instance until the cache is cleared."""
    first = get_settings()
    second = get_settings()
    assert first is second

    clear_settings_cache()
    third = get_settings()
    assert third is not first


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """EQUITY__ env vars (nested via __) take precedence over defaults."""
    clear_settings_cache()
    monkeypatch.setenv("EQUITY_PROJECT__NAME", "env-injected-name")

    settings = get_settings()
    assert settings.project.name == "env-injected-name"


def test_ticker_config_loads_and_selects(tmp_path: Path) -> None:
    """TickerConfig reads YAML and exposes working selectors."""
    config_path = tmp_path / "tickers.yaml"
    config_path.write_text(_SAMPLE_TICKERS_YAML, encoding="utf-8")

    config = TickerConfig(config_path=config_path)

    assert "us" in config.get_markets()
    # active_only default filters out the inactive ticker.
    assert config.get_tickers_for_market("us") == ["AAPL", "BRK.B"]
    assert set(config.get_tickers_for_market("us", active_only=False)) == {"AAPL", "BRK.B", "DEAD"}


def test_ticker_config_selector_by_tag_and_sector(tmp_path: Path) -> None:
    """Tag and sector selectors return matching tickers across markets."""
    config_path = tmp_path / "tickers.yaml"
    config_path.write_text(_SAMPLE_TICKERS_YAML, encoding="utf-8")

    config = TickerConfig(config_path=config_path)

    blue_chips = set(config.get_tickers_by_tag("blue-chip"))
    assert "AAPL" in blue_chips
    assert "600519" in blue_chips

    tech = config.get_tickers_by_sector("Technology")
    assert tech == ["AAPL"]


def test_ticker_config_missing_file_falls_back_to_empty(tmp_path: Path) -> None:
    """A missing config path yields an empty-but-valid configuration (no selectors raise)."""
    config = TickerConfig(config_path=tmp_path / "does_not_exist.yaml")

    assert config.get_markets() == []
    assert config.get_tickers_for_market("us") == []
