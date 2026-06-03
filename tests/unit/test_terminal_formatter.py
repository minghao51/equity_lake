"""Test TerminalFormatter."""

from datetime import date

from equity_lake.signals.formatters.terminal import TerminalFormatter
from equity_lake.signals.models import Signal


def test_terminal_formatter_empty():
    """Test formatting empty signal list."""
    formatter = TerminalFormatter()
    output = formatter.format([])
    assert "No signals generated" in output


def test_terminal_formatter_summary():
    """Test summary section."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "R1", {}),
        Signal("GOOGL", date(2024, 12, 1), "sentiment", "SELL", 60, "R2", {}),
    ]

    formatter = TerminalFormatter()
    output = formatter.format(signals)

    assert "SIGNAL REPORT" in output
    assert "BUY: 1" in output
    assert "SELL: 1" in output


def test_terminal_formatter_signal_sections():
    """Test signal type sections."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "Momentum entry", {}),
    ]

    formatter = TerminalFormatter()
    output = formatter.format(signals)

    assert "BACKTEST SIGNALS" in output
    assert "AAPL" in output
    assert "BUY" in output
