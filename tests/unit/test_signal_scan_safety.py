"""Offline safety regression tests for signal planning and shared connections."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from threading import get_ident
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.models import Signal, SignalConfig, Watchlist  # type: ignore[import-untyped]
from equity_lake.signals.scanner import SignalScanner  # type: ignore[import-untyped]


def _configs(tmp_path: Path) -> tuple[Path, Path]:
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text("name: Synthetic\ntickers: [AAPL, GOOGL, MSFT]\n", encoding="utf-8")
    config = tmp_path / "signals.yaml"
    config.write_text(
        "backtest: {enabled: true, strategies: [{name: synthetic, lookback_days: 5}]}\n"
        "sentiment: {enabled: true}\nml: {enabled: true, mode: v2_meta_label}\n",
        encoding="utf-8",
    )
    return watchlist, config


def test_dry_run_never_constructs_scanner_or_creates_runtime_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist, config = _configs(tmp_path)
    runtime = tmp_path / "runtime"
    for key in ("LAKE_DIR", "LOGS_DIR", "MODELS_DIR"):
        monkeypatch.setattr(f"equity_lake.core.paths.{key}", runtime / key)
    scanner = MagicMock(side_effect=AssertionError("dry-run must not construct generators"))
    monkeypatch.setattr("equity_lake.signals.scanner.SignalScanner", scanner)
    output = runtime / "report.json"
    result = CliRunner().invoke(
        app,
        [
            "signal",
            "scan",
            "--dry-run",
            "--watchlist",
            str(watchlist),
            "--market",
            "us_equity",
            "--config",
            str(config),
            "--date",
            "2024-12-02",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["reason"] == "dry_run"
    assert plan["status"] == "planned"
    assert plan["date"] == "2024-12-02"
    assert plan["tickers"] == ["AAPL", "GOOGL", "MSFT"]
    assert plan["generators"] == ["backtest", "sentiment", "ml"]
    assert plan["output"] == str(output)
    scanner.assert_not_called()
    assert not runtime.exists()


@pytest.mark.parametrize("invalid", ["format", "date", "missing", "yaml"])
def test_invalid_dry_run_exits_before_scanner_construction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str) -> None:
    watchlist, config = _configs(tmp_path)
    scanner = MagicMock()
    monkeypatch.setattr("equity_lake.signals.scanner.SignalScanner", scanner)
    args = ["signal", "scan", "--dry-run", "--watchlist", str(watchlist), "--market", "us_equity", "--config", str(config)]
    if invalid == "format":
        args += ["--format", "unknown"]
    elif invalid == "date":
        args += ["--date", "not-a-date"]
    elif invalid == "missing":
        config.unlink()
    else:
        config.write_text("backtest: [invalid", encoding="utf-8")
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 1, result.output
    assert "Invalid scan configuration" in result.stdout
    scanner.assert_not_called()


def test_dry_run_preserves_existing_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist, config = _configs(tmp_path)
    output = tmp_path / "report.json"
    output.write_text("previous report", encoding="utf-8")
    monkeypatch.setattr("equity_lake.signals.scanner.SignalScanner", MagicMock())
    result = CliRunner().invoke(
        app,
        ["signal", "scan", "--dry-run", "--watchlist", str(watchlist), "--market", "us_equity", "--config", str(config), "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    assert output.read_text(encoding="utf-8") == "previous report"


def test_signal_scan_defaults_to_canonical_managed_watchlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from equity_lake.signals.tracking import PrivateWatchlist, TrackedInstrument  # type: ignore[import-untyped]

    _legacy_watchlist, config = _configs(tmp_path)
    managed = PrivateWatchlist(
        name="Managed",
        instruments=[TrackedInstrument(instrument=InstrumentKey(market="us_equity", mic="XNAS", symbol=symbol)) for symbol in ("AAPL", "MSFT")],
    )
    monkeypatch.setattr("equity_lake.signals.tracking.load_private_watchlist", lambda **_kwargs: managed)
    scanner = MagicMock(side_effect=AssertionError("dry-run cannot construct a scanner"))
    monkeypatch.setattr("equity_lake.signals.scanner.SignalScanner", scanner)

    result = CliRunner().invoke(
        app,
        ["signal", "scan", "--dry-run", "--config", str(config), "--date", "2024-12-02"],
    )

    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)
    assert plan["watchlist"] == "Managed"
    assert plan["tickers"] == ["AAPL", "MSFT"]
    assert [(item["market"], item["mic"], item["symbol"]) for item in plan["instruments"]] == [
        ("us_equity", "XNAS", "AAPL"),
        ("us_equity", "XNAS", "MSFT"),
    ]
    scanner.assert_not_called()


def test_explicit_legacy_signal_watchlist_requires_market(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist, config = _configs(tmp_path)
    scanner = MagicMock()
    monkeypatch.setattr("equity_lake.signals.scanner.SignalScanner", scanner)

    result = CliRunner().invoke(app, ["signal", "scan", "--dry-run", "--watchlist", str(watchlist), "--config", str(config)])

    assert result.exit_code == 1
    assert "--market is required" in result.stdout
    scanner.assert_not_called()


def test_signal_scan_help_explains_safe_dry_run() -> None:
    result = CliRunner().invoke(app, ["signal", "scan", "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.stdout
    assert "without scanning" in result.stdout


def test_live_scan_initializes_paths_and_saves_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A real-run CLI path is exercised with a fake scanner, never inference."""
    watchlist, config = _configs(tmp_path)
    runtime = tmp_path / "runtime"
    paths = [runtime / name for name in ("LAKE_DIR", "LOGS_DIR", "MODELS_DIR")]
    for name, path in zip(("LAKE_DIR", "LOGS_DIR", "MODELS_DIR"), paths, strict=True):
        monkeypatch.setattr(f"equity_lake.core.paths.{name}", path)
    signal = Signal("AAPL", date(2024, 12, 2), "backtest", "BUY", 70, "synthetic", {}, market="us_equity", mic="XNAS")
    scanner = MagicMock()
    scanner.scan.return_value = [signal]
    scanner.format_signals.return_value = "synthetic report"

    def construct(*args: object, **kwargs: object) -> MagicMock:
        assert all(path.is_dir() for path in paths)
        assert kwargs["instruments"] == [InstrumentKey(market="us_equity", mic="XNAS", symbol=symbol) for symbol in ("AAPL", "GOOGL", "MSFT")]
        return scanner

    monkeypatch.setattr("equity_lake.signals.scanner.SignalScanner", construct)
    output = tmp_path / "signals.md"
    result = CliRunner().invoke(
        app,
        [
            "signal",
            "scan",
            "--no-dry-run",
            "--watchlist",
            str(watchlist),
            "--market",
            "us_equity",
            "--config",
            str(config),
            "--date",
            "2024-12-02",
            "--format",
            "md",
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    scanner.scan.assert_called_once_with(date(2024, 12, 2))
    scanner.save_history.assert_called_once_with([signal])
    assert output.read_text(encoding="utf-8") == "synthetic report"


def test_non_ml_scan_uses_calling_thread_in_watchlist_order(monkeypatch: pytest.MonkeyPatch) -> None:
    scanner = SignalScanner(
        SignalConfig(backtest={"enabled": False}, sentiment={"enabled": False}, ml={"enabled": False}),
        Watchlist(name="Synthetic", tickers=["AAA", "BBB", "CCC"]),
        max_workers=8,
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker) for ticker in ("AAA", "BBB", "CCC")],
    )
    calls = []

    def scan_ticker(instrument: InstrumentKey, target: date, **_kwargs: object) -> list[Signal]:
        calls.append((instrument.symbol, target, get_ident()))
        return []

    monkeypatch.setattr(scanner, "_scan_ticker", scan_ticker)
    target = date(2024, 12, 2)
    assert scanner.scan(target) == []
    assert calls == [(ticker, target, get_ident()) for ticker in ["AAA", "BBB", "CCC"]]


def test_real_non_ml_multi_ticker_scan_in_subprocess() -> None:
    """Keep a possible native DuckDB regression isolated from the pytest process."""
    script = """
from datetime import date, timedelta
import polars as pl
from equity_lake.core.instruments import InstrumentKey
from equity_lake.signals.generators.backtest import BacktestSignalGenerator
from equity_lake.signals.models import SignalConfig, Watchlist
from equity_lake.signals.scanner import SignalScanner

def load_prices(self, instrument, target):
    dates = [target - timedelta(days=29 - i) for i in range(30)]
    return pl.DataFrame({
        "market": [instrument.market] * 30,
        "mic": [instrument.mic] * 30,
        "symbol": [instrument.symbol] * 30,
        "ticker": [instrument.symbol] * 30,
        "date": dates,
        "close": [100.0 + i * 2 for i in range(30)],
    })

BacktestSignalGenerator._load_prices = load_prices
scanner = SignalScanner(
    SignalConfig(backtest={"enabled": True, "strategies": [
        {"name": "synthetic", "lookback_days": 5, "buy_threshold": 0.01, "sell_threshold": -0.01}
    ]}, sentiment={"enabled": False}, ml={"enabled": False}),
    Watchlist(name="Synthetic", tickers=["AAA", "BBB", "CCC"]),
    instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol=ticker) for ticker in ("AAA", "BBB", "CCC")],
)
for _ in range(5):
    signals = scanner.scan(date(2024, 1, 30))
    assert [(signal.ticker, signal.action) for signal in signals] == [(ticker, "BUY") for ticker in ("AAA", "BBB", "CCC")]
print("synthetic multi-ticker scan passed")
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=45, check=False)
    assert result.returncode == 0, f"exit {result.returncode}\n{result.stdout}\n{result.stderr}"
    assert "synthetic multi-ticker scan passed" in result.stdout
