"""Test SignalScanner."""

from datetime import date
from pathlib import Path

import pytest

import equity_lake.signals.generators.meta_label as meta_label_module  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.generators.backtest import BacktestSignalGenerator  # type: ignore[import-untyped]
from equity_lake.signals.generators.meta_label import MetaLabelSignalGenerator
from equity_lake.signals.models import Signal, SignalConfig, Watchlist  # type: ignore[import-untyped]
from equity_lake.signals.scanner import SignalScanner  # type: ignore[import-untyped]


def test_scanner_initialization(monkeypatch: pytest.MonkeyPatch, temp_partitioned_parquet: Path) -> None:
    """Test scanner initializes a canonical instrument scope without inference."""
    del monkeypatch, temp_partitioned_parquet
    config = SignalConfig(
        backtest={"enabled": True, "strategies": []},
        sentiment={"enabled": False},
        ml={"enabled": False},
    )
    watchlist = Watchlist(name="Test", tickers=["AAPL", "GOOGL"])
    instruments = [InstrumentKey(market="us_equity", mic="XNAS", symbol=symbol) for symbol in watchlist.tickers]

    scanner = SignalScanner(config, watchlist, instruments=instruments)
    assert isinstance(scanner.generators[0], BacktestSignalGenerator)
    assert len(scanner.generators) == 1  # Only backtest enabled
    assert "json" in scanner.formatters
    assert "md" in scanner.formatters
    assert "table" in scanner.formatters


def test_scanner_scan_empty_watchlist() -> None:
    """Test scanning empty watchlist."""
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": False},
    )
    watchlist = Watchlist(name="Empty", tickers=[])

    with pytest.raises(ValueError, match="require at least one canonical instrument"):
        SignalScanner(config, watchlist, instruments=[])


def test_scanner_format_signals() -> None:
    """Test formatting signals."""
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": False},
    )
    watchlist = Watchlist(name="Test", tickers=[])

    scanner = SignalScanner(
        config,
        watchlist,
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
    )

    signals = [
        Signal("AAPL", date(2024, 1, 5), "backtest", "BUY", 75, "R1", {}, market="us_equity", mic="XNAS"),
        Signal("GOOGL", date(2024, 1, 5), "sentiment", "SELL", 60, "R2", {}, market="us_equity", mic="XNAS"),
    ]

    json_output = scanner.format_signals(signals, "json")
    assert "AAPL" in json_output
    assert "BUY" in json_output

    md_output = scanner.format_signals(signals, "md")
    assert "# Signal Report" in md_output

    table_output = scanner.format_signals(signals, "table")
    assert "SIGNAL REPORT" in table_output


def test_scanner_uses_meta_label_generator_when_v2_mode_enabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test v2 selection without constructing a real model or model directory."""
    model_dir = tmp_path / "models"

    def fake_forecaster(**kwargs: object) -> object:
        assert kwargs["model_dir"] == str(model_dir)
        return object()

    monkeypatch.setattr(meta_label_module, "PriceForecaster", fake_forecaster)
    config = SignalConfig(
        backtest={"enabled": False},
        sentiment={"enabled": False},
        ml={"enabled": True, "mode": "v2_meta_label", "model_dir": str(model_dir)},
    )
    watchlist = Watchlist(name="Test", tickers=["AAPL"])

    scanner = SignalScanner(
        config,
        watchlist,
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
    )

    assert len(scanner.generators) == 1
    assert isinstance(scanner.generators[0], MetaLabelSignalGenerator)
    assert not model_dir.exists()
