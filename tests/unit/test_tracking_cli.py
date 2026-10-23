"""CLI argument and safety contracts for tracking observations."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
import typer
from typer.testing import CliRunner

from equity_lake.cli.commands import tracking  # type: ignore[import-untyped]
from equity_lake.signals.readiness import HistoryState, SignalHistoryObservation  # type: ignore[import-untyped]

runner = CliRunner()


def _cli() -> typer.Typer:
    app = typer.Typer(add_completion=False)
    app.command("doctor")(tracking.doctor)
    signal_app = typer.Typer(add_completion=False)
    signal_app.command("history")(tracking.signal_history)
    app.add_typer(signal_app, name="signal")
    return app


def _history(*, state: HistoryState = "available") -> SignalHistoryObservation:
    return SignalHistoryObservation(state, [], 0, 0, {}, {}, 0, False)


def test_doctor_help_exposes_read_only_scope_options() -> None:
    result = runner.invoke(_cli(), ["doctor", "--help"])

    assert result.exit_code == 0
    assert "--watchlist" in result.stdout
    assert "--ticker-config" in result.stdout
    assert "--as-of" in result.stdout


def test_doctor_passes_explicit_config_and_cutoff_to_observer_without_writes(tmp_path: Path, monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def build_readiness(**kwargs: Any) -> object:
        calls.append(kwargs)
        return object()

    fake = SimpleNamespace(build_tracking_readiness=build_readiness, render_json=lambda _report: '{"inference_status":"not evaluated"}')
    monkeypatch.setattr(tracking, "import_module", lambda name: fake if name == "equity_lake.signals.readiness" else pytest.fail(name))

    result = runner.invoke(
        _cli(),
        [
            "doctor",
            "--watchlist",
            str(tmp_path / "watchlist.yaml"),
            "--ticker-config",
            str(tmp_path / "tickers.yaml"),
            "--as-of",
            "2024-12-02",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["inference_status"] == "not evaluated"
    assert calls == [
        {
            "watchlist_path": tmp_path / "watchlist.yaml",
            "ticker_config_path": tmp_path / "tickers.yaml",
            "as_of": date(2024, 12, 2),
            "lake_dir": None,
        }
    ]
    assert list(tmp_path.iterdir()) == []


def test_doctor_configuration_error_exits_nonzero_without_scanning(monkeypatch: Any) -> None:
    def fail_readiness(**_kwargs: Any) -> None:
        raise ValueError("synthetic invalid config")

    fake = SimpleNamespace(build_tracking_readiness=fail_readiness, render_json=lambda _report: "")
    monkeypatch.setattr(tracking, "import_module", lambda _name: fake)

    result = runner.invoke(_cli(), ["doctor", "--as-of", "2024-12-02"])

    assert result.exit_code == 1
    assert "synthetic invalid config" in result.stderr


def test_signal_history_requires_explicit_date_and_limit() -> None:
    result = runner.invoke(_cli(), ["signal", "history"])

    assert result.exit_code != 0
    help_result = runner.invoke(_cli(), ["signal", "history", "--help"])
    assert help_result.exit_code == 0
    assert "--date" in help_result.stdout
    assert "--limit" in help_result.stdout


def test_signal_history_reads_only_requested_date_and_limit(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []
    observation = _history()

    def read_history(**kwargs: Any) -> SignalHistoryObservation:
        calls.append(kwargs)
        return observation

    fake = SimpleNamespace(
        read_signal_history_readonly=read_history, render_json=lambda value: json.dumps({"status": value.state, "rows": value.total_rows})
    )
    monkeypatch.setattr(tracking, "import_module", lambda name: fake if name == "equity_lake.signals.readiness" else pytest.fail(name))

    result = runner.invoke(_cli(), ["signal", "history", "--date", "2024-12-02", "--limit", "5"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"status": "available", "rows": 0}
    assert calls == [{"target_date": date(2024, 12, 2), "as_of": date(2024, 12, 2), "limit": 5}]
    assert not (tmp_path / "signals").exists()


def test_signal_history_rejects_bad_limit_and_reports_corrupt_history(monkeypatch: Any) -> None:
    observation = _history(state="corrupt")
    fake = SimpleNamespace(
        read_signal_history_readonly=MagicMock(return_value=observation),
        render_json=lambda value: json.dumps({"status": value.state}),
    )
    monkeypatch.setattr(tracking, "import_module", lambda _name: fake)

    bad_limit = runner.invoke(_cli(), ["signal", "history", "--date", "2024-12-02", "--limit", "0"])
    corrupt = runner.invoke(_cli(), ["signal", "history", "--date", "2024-12-02", "--limit", "5"])

    assert bad_limit.exit_code != 0
    assert "at least 1" in bad_limit.output
    assert corrupt.exit_code == 1
    assert json.loads(corrupt.stdout)["status"] == "corrupt"
