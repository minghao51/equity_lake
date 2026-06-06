"""Test SignalScanner."""

from datetime import date

from equity_lake.signals.generators.meta_label import MetaLabelSignalGenerator
from equity_lake.signals.models import Signal, SignalConfig, Watchlist
from equity_lake.signals.scanner import SignalScanner


def test_scanner_initialization():
    """Test scanner initializes with config."""
    config = SignalConfig(
        backtest={"enabled": True, "min_win_rate": 0.55, "strategies": []},
        sentiment={"enabled": False},
        ml={"enabled": False},
    )
    watchlist = Watchlist(name="Test", tickers=["AAPL", "GOOGL"])

    scanner = SignalScanner(config, watchlist)

    assert len(scanner.generators) == 1  # Only backtest enabled
    assert "json" in scanner.formatters
    assert "md" in scanner.formatters
    assert "table" in scanner.formatters


def test_scanner_scan_empty_watchlist():
    """Test scanning empty watchlist."""
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": False},
    )
    watchlist = Watchlist(name="Empty", tickers=[])

    scanner = SignalScanner(config, watchlist)
    signals = scanner.scan()

    assert len(signals) == 0


def test_scanner_format_signals():
    """Test formatting signals."""
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": False},
    )
    watchlist = Watchlist(name="Test", tickers=[])

    scanner = SignalScanner(config, watchlist)

    # Create test signals
    signals = [
        Signal("AAPL", date.today(), "backtest", "BUY", 75, "R1", {}),
        Signal("GOOGL", date.today(), "sentiment", "SELL", 60, "R2", {}),
    ]

    # Test JSON format
    json_output = scanner.format_signals(signals, "json")
    assert "AAPL" in json_output
    assert "BUY" in json_output

    # Test Markdown format
    md_output = scanner.format_signals(signals, "md")
    assert "# Signal Report" in md_output

    # Test table format
    table_output = scanner.format_signals(signals, "table")
    assert "SIGNAL REPORT" in table_output


def test_scanner_uses_meta_label_generator_when_v2_mode_enabled():
    """Test scanner swaps in the v2 ML generator when configured."""
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": True, "mode": "v2_meta_label", "model_dir": "models"},
    )
    watchlist = Watchlist(name="Test", tickers=["AAPL"])

    scanner = SignalScanner(config, watchlist)

    assert len(scanner.generators) == 1
    assert isinstance(scanner.generators[0], MetaLabelSignalGenerator)
