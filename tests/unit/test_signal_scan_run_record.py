"""Persistence, empty-scan observability, and CLI exit contracts."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from typer.testing import CliRunner

import equity_lake.core.paths as paths_module  # type: ignore[import-untyped]
import equity_lake.signals.config as config_module  # type: ignore[import-untyped]
import equity_lake.signals.history as history_module  # type: ignore[import-untyped]
import equity_lake.signals.scanner as scanner_module  # type: ignore[import-untyped]
from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]
from equity_lake.core.instruments import InstrumentKey  # type: ignore[import-untyped]
from equity_lake.signals.generators.base import GenerationOutcome  # type: ignore[import-untyped]
from equity_lake.signals.history import save_scan_report
from equity_lake.signals.models import Signal, SignalOutcomeStatus, SignalScanOutcome, SignalScanReport  # type: ignore[import-untyped]


def _write_configs(tmp_path: Path) -> tuple[Path, Path, Path]:
    watchlist = tmp_path / "watchlist.yaml"
    watchlist.write_text("name: Synthetic\ntickers: [AAPL]\n", encoding="utf-8")
    signal_config = tmp_path / "signals.yaml"
    signal_config.write_text(
        "backtest: {enabled: true, strategies: [{name: selected, lookback_days: 5}]}\nsentiment: {enabled: false}\nml: {enabled: false}\n",
        encoding="utf-8",
    )
    ticker_config = tmp_path / "tickers.yaml"
    ticker_config.write_text(
        "markets:\n  us:\n    currency: USD\n    tickers:\n      - {symbol: AAPL, name: Apple, exchange: NASDAQ, sector: Technology, active: true}\n",
        encoding="utf-8",
    )
    return watchlist, signal_config, ticker_config


def _report(*, signals: list[Signal] | None = None) -> SignalScanReport:
    return SignalScanReport(
        run_id=uuid4().hex,
        target_date=date(2024, 12, 2),
        watchlist="Synthetic",
        tickers=["AAPL"],
        instruments=[InstrumentKey(market="us_equity", mic="XNAS", symbol="AAPL")],
        generators=["backtest"],
        status="success",
        outcomes=[SignalScanOutcome(ticker="AAPL", market="us_equity", mic="XNAS", generator="backtest", status="no_condition")],
        signals=signals or [],
    )


def test_zero_event_report_is_pydantic_validated_and_saved_outside_lake(tmp_path: Path) -> None:
    report = _report()

    report_path = save_scan_report(report, tmp_path / "data")

    assert report_path.parent == tmp_path / "data" / "signal_scans"
    assert report_path.is_file()
    stored = SignalScanReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert stored.status == "success"
    assert stored.signals == []
    assert stored.outcome_counts == {"no_condition": 1}


def test_scan_report_serializes_signals_and_effective_threshold_metadata(tmp_path: Path) -> None:
    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 2),
        signal_type="ml",
        action="BUY",
        confidence=70.0,
        reasoning="effective threshold",
        metadata={"threshold_used": 0.5, "threshold_source": "stored_optimized", "horizon_days": 1},
        market="us_equity",
        mic="XNAS",
    )
    report = _report(signals=[signal])
    report.generators = ["ml"]
    report.outcomes[0].generator = "ml"

    report_path = save_scan_report(report, tmp_path / "data")
    data = json.loads(report_path.read_text(encoding="utf-8"))

    assert data["signals"][0]["metadata"]["threshold_used"] == 0.5
    assert data["signals"][0]["metadata"]["threshold_source"] == "stored_optimized"


def test_signal_history_round_trips_threshold_provenance_with_legacy_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setattr(history_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(history_module, "SIGNALS_DIR", data_dir / "signals")
    signal = Signal(
        ticker="AAPL",
        date=date(2024, 12, 2),
        signal_type="ml",
        action="BUY",
        confidence=70.0,
        reasoning="effective threshold (source=stored_optimized)",
        metadata={
            "meta_label_threshold": 0.5,
            "threshold_used": 0.5,
            "threshold_source": "stored_optimized",
            "horizon_days": 1,
        },
        market="us_equity",
        mic="XNAS",
    )

    history_module.save_signals([signal], signal.date)
    restored = history_module.load_signals(signal.date)

    assert len(restored) == 1
    assert restored[0].metadata["meta_label_threshold"] == 0.5
    assert restored[0].metadata["horizon_days"] == 1
    assert "threshold_used" not in restored[0].metadata
    assert "threshold_source" not in restored[0].metadata
    assert "source=stored_optimized" in restored[0].reasoning


@pytest.mark.parametrize(("outcome_status", "expected_report", "expected_exit"), [("no_condition", "success", 0), ("unavailable", "failed", 1)])
def test_cli_persists_zero_event_report_and_keeps_json_array(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome_status: SignalOutcomeStatus,
    expected_report: str,
    expected_exit: int,
) -> None:
    watchlist, signal_config, ticker_config = _write_configs(tmp_path)
    monkeypatch.setattr(config_module, "DEFAULT_TICKERS_PATH", ticker_config)
    monkeypatch.setattr(history_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths_module, "ensure_dirs", lambda: None)
    monkeypatch.setattr(scanner_module, "is_instrument_trading_day", lambda _instrument, _target_date: True)

    class FakeGenerator:
        def __init__(self, _config: dict[str, object]) -> None:
            pass

        def generate_outcome(self, _instrument: InstrumentKey, _target_date: date) -> GenerationOutcome:
            return GenerationOutcome(status=outcome_status)

    monkeypatch.setattr(scanner_module, "BacktestSignalGenerator", FakeGenerator)
    output = tmp_path / "signals.json"
    result = CliRunner().invoke(
        app,
        [
            "signal",
            "scan",
            "--watchlist",
            str(watchlist),
            "--market",
            "us_equity",
            "--config",
            str(signal_config),
            "--date",
            "2024-12-02",
            "--format",
            "json",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == expected_exit, result.output
    assert json.loads(output.read_text(encoding="utf-8")) == []
    report_paths = list((tmp_path / "data" / "signal_scans").glob("*.json"))
    assert len(report_paths) == 1
    report = SignalScanReport.model_validate_json(report_paths[0].read_text(encoding="utf-8"))
    assert report.status == expected_report
    assert report.signals == []
    assert report.outcomes[0].status == outcome_status


def test_cli_keeps_partial_results_without_imposing_nonzero_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist, signal_config, ticker_config = _write_configs(tmp_path)
    signal_config.write_text(
        "backtest: {enabled: true, strategies: [{name: selected, lookback_days: 5}]}\nsentiment: {enabled: true}\nml: {enabled: false}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "DEFAULT_TICKERS_PATH", ticker_config)
    monkeypatch.setattr(history_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths_module, "ensure_dirs", lambda: None)
    monkeypatch.setattr(scanner_module, "is_instrument_trading_day", lambda _instrument, _target_date: True)

    class NoConditionGenerator:
        def __init__(self, _config: dict[str, object]) -> None:
            pass

        def generate_outcome(self, _instrument: InstrumentKey, _target_date: date) -> GenerationOutcome:
            return GenerationOutcome(status="no_condition")

    class UnavailableGenerator:
        def __init__(self, _config: dict[str, object]) -> None:
            pass

        def generate_outcome(self, _instrument: InstrumentKey, _target_date: date) -> GenerationOutcome:
            return GenerationOutcome(status="unavailable", detail="no sentiment rows")

    monkeypatch.setattr(scanner_module, "BacktestSignalGenerator", NoConditionGenerator)
    monkeypatch.setattr(scanner_module, "SentimentSignalGenerator", UnavailableGenerator)
    output = tmp_path / "signals.json"
    result = CliRunner().invoke(
        app,
        [
            "signal",
            "scan",
            "--watchlist",
            str(watchlist),
            "--market",
            "us_equity",
            "--config",
            str(signal_config),
            "--date",
            "2024-12-02",
            "--format",
            "json",
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text(encoding="utf-8")) == []
    reports = list((tmp_path / "data" / "signal_scans").glob("*.json"))
    assert len(reports) == 1
    report = SignalScanReport.model_validate_json(reports[0].read_text(encoding="utf-8"))
    assert report.status == "partial"
    assert [outcome.status for outcome in report.outcomes] == ["no_condition", "unavailable"]


def test_dry_run_does_not_create_scan_report_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    watchlist, signal_config, ticker_config = _write_configs(tmp_path)
    data_dir = tmp_path / "data"
    monkeypatch.setattr(config_module, "DEFAULT_TICKERS_PATH", ticker_config)
    monkeypatch.setattr(history_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths_module, "ensure_dirs", lambda: pytest.fail("dry-run must not initialize paths"))
    monkeypatch.setattr(scanner_module, "SignalScanner", lambda *_args, **_kwargs: pytest.fail("dry-run must not construct scanner"))

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
            str(signal_config),
            "--date",
            "2024-12-02",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not data_dir.exists()
