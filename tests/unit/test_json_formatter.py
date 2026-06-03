"""Test JSONFormatter."""

import json
from datetime import date

from equity_lake.signals.formatters.json import JSONFormatter
from equity_lake.signals.models import Signal


def test_json_formatter_empty():
    """Test formatting empty signal list."""
    formatter = JSONFormatter()
    output = formatter.format([])
    assert output == "[]"


def test_json_formatter_single_signal():
    """Test formatting single signal."""
    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 1),
        signal_type="backtest",
        action="BUY",
        confidence=75.0,
        reasoning="Test signal",
        metadata={"key": "value"},
    )

    formatter = JSONFormatter()
    output = formatter.format([signal])

    data = json.loads(output)
    assert len(data) == 1
    assert data[0]["ticker"] == "AAPL"
    assert data[0]["action"] == "BUY"
    assert data[0]["confidence"] == 75.0
    assert data[0]["metadata"]["key"] == "value"


def test_json_formatter_multiple_signals():
    """Test formatting multiple signals."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "R1", {}),
        Signal("GOOGL", date(2024, 12, 1), "sentiment", "SELL", 60, "R2", {}),
    ]

    formatter = JSONFormatter()
    output = formatter.format(signals)

    data = json.loads(output)
    assert len(data) == 2
    assert data[0]["ticker"] == "AAPL"
    assert data[1]["ticker"] == "GOOGL"
