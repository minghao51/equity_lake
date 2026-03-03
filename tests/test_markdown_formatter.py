"""Test MarkdownFormatter."""

from datetime import date

from equity_lake.signals.formatters.markdown import MarkdownFormatter
from equity_lake.signals.models import Signal


def test_markdown_formatter_empty():
    """Test formatting empty signal list."""
    formatter = MarkdownFormatter()
    output = formatter.format([])
    assert "# Signal Report" in output
    assert "No signals generated" in output


def test_markdown_formatter_summary_table():
    """Test summary table generation."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "R1", {}),
        Signal("GOOGL", date(2024, 12, 1), "sentiment", "SELL", 60, "R2", {}),
        Signal("MSFT", date(2024, 12, 1), "ml", "HOLD", 50, "R3", {}),
    ]

    formatter = MarkdownFormatter()
    output = formatter.format(signals)

    assert "# Signal Report" in output
    assert "| BUY | 1 |" in output
    assert "| SELL | 1 |" in output
    assert "| HOLD | 1 |" in output


def test_markdown_formatter_signal_sections():
    """Test signal type sections."""
    signals = [
        Signal("AAPL", date(2024, 12, 1), "backtest", "BUY", 75, "Momentum", {}),
        Signal("TSLA", date(2024, 12, 1), "sentiment", "SELL", 60, "Negative news", {}),
    ]

    formatter = MarkdownFormatter()
    output = formatter.format(signals)

    assert "## Backtest Signals" in output
    assert "## Sentiment Signals" in output
    assert "| AAPL | BUY | 75 |" in output
    assert "| TSLA | SELL | 60 |" in output
