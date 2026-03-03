"""Test signal data models."""

from datetime import date

import pytest

from equity_lake.signals.models import Signal, SignalConfig, Watchlist


def test_signal_creation_valid():
    """Test creating a valid signal."""
    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 1),
        signal_type="backtest",
        action="BUY",
        confidence=75.0,
        reasoning="Momentum strategy entered long",
        metadata={"strategy": "momentum", "win_rate": 0.65},
    )
    assert signal.ticker == "AAPL"
    assert signal.action == "BUY"
    assert signal.confidence == 75.0


def test_signal_confidence_validation():
    """Test that confidence out of range raises error."""
    with pytest.raises(ValueError, match="Confidence must be 0-100"):
        Signal(
            ticker="AAPL",
            date=date(2024, 12, 1),
            signal_type="backtest",
            action="BUY",
            confidence=150.0,  # Invalid
            reasoning="Test",
            metadata={},
        )


def test_watchlist_simple_list():
    """Test watchlist with simple ticker list."""
    watchlist = Watchlist(name="My Portfolio", tickers=["AAPL", "GOOGL", "MSFT"])
    assert len(watchlist.tickers) == 3
    assert "AAPL" in watchlist.tickers


def test_watchlist_with_groups():
    """Test watchlist with grouped tickers."""
    watchlist = Watchlist(
        name="Tech Portfolio",
        tickers=["AAPL", "TSLA"],
        groups={"mega_tech": ["GOOGL", "MSFT"], "ev": ["RIVN"]},
    )
    # Groups should be merged into main tickers list
    assert len(watchlist.tickers) == 5
    assert "GOOGL" in watchlist.tickers
    assert "RIVN" in watchlist.tickers


def test_signal_config_generator_enabled():
    """Test checking if generator is enabled."""
    config = SignalConfig(
        backtest={"enabled": True, "min_win_rate": 0.55},
        sentiment={"enabled": False, "buy_threshold": 0.5},
        ml={"enabled": True, "model_dir": "models"},
    )
    assert config.is_generator_enabled("backtest")
    assert not config.is_generator_enabled("sentiment")
    assert config.is_generator_enabled("ml")
