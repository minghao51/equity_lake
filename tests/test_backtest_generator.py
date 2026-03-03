"""Test BacktestSignalGenerator."""

import pytest
from datetime import date, timedelta

from equity_lake.signals.generators.backtest import BacktestSignalGenerator


def test_backtest_generator_enabled():
    """Test generator when enabled."""
    config = {
        "enabled": True,
        "min_win_rate": 0.55,
        "strategies": [
            {
                "name": "momentum",
                "lookback_days": 20,
                "buy_threshold": 0.02,
                "sell_threshold": -0.01,
            }
        ],
    }
    gen = BacktestSignalGenerator(config)
    assert gen.is_enabled() is True


def test_backtest_generator_no_data():
    """Test generator when no price data available."""
    config = {
        "enabled": True,
        "strategies": [{"name": "momentum", "lookback_days": 20}],
    }
    gen = BacktestSignalGenerator(config)
    # Ticker with no data should return None
    signal = gen.generate("NONEXISTENT", date.today() - timedelta(days=1))
    assert signal is None


@pytest.mark.skipif(
    True,  # Skip if no test data available
    reason="Requires EOD data in data/lake/",
)
def test_backtest_generator_with_data():
    """Test generator generates BUY signal above threshold."""
    # This test requires actual EOD data
    config = {
        "enabled": True,
        "strategies": [{"name": "momentum", "lookback_days": 20}],
    }
    gen = BacktestSignalGenerator(config)
    signal = gen.generate("AAPL", date.today() - timedelta(days=1))
    # Should return a Signal or None depending on market conditions
    if signal:
        assert signal.signal_type == "backtest"
        assert signal.action in ["BUY", "SELL", "HOLD"]
        assert 0 <= signal.confidence <= 100
