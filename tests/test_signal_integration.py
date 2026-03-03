"""Integration tests for signal scanning."""

from datetime import date, timedelta

import pytest

from equity_lake.signals.config import load_signal_config, load_watchlist
from equity_lake.signals.scanner import SignalScanner


@pytest.mark.integration
def test_full_scan_workflow():
    """Test complete scan workflow with real configs."""
    # Load actual configs
    watchlist = load_watchlist()
    config = load_signal_config()

    # Initialize scanner
    scanner = SignalScanner(config, watchlist)

    # Scan for recent date
    target_date = date.today() - timedelta(days=1)
    signals = scanner.scan(target_date)

    # Verify output
    assert isinstance(signals, list)

    # Format output
    json_output = scanner.format_signals(signals, "json")
    md_output = scanner.format_signals(signals, "md")
    table_output = scanner.format_signals(signals, "table")

    assert isinstance(json_output, str)
    assert isinstance(md_output, str)
    assert isinstance(table_output, str)


@pytest.mark.integration
def test_signal_history_roundtrip():
    """Test saving and loading signal history."""
    watchlist = load_watchlist()
    config = load_signal_config()

    scanner = SignalScanner(config, watchlist)

    # Scan
    target_date = date.today() - timedelta(days=1)
    signals = scanner.scan(target_date)

    if signals:
        # Save history
        scanner.save_history(signals)

        # Verify history exists
        from equity_lake.signals.history import load_signals_from_parquet

        loaded = load_signals_from_parquet(target_date)

        assert len(loaded) > 0
        assert loaded[0].ticker == signals[0].ticker
