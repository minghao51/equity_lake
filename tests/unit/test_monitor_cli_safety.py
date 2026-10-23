"""Safety tests for ``equity monitor`` report persistence and cleanup."""

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, call

import pytest
from click.testing import Result
from typer.testing import CliRunner

from equity_lake.cli.__main__ import app  # type: ignore[import-untyped]
from equity_lake.monitoring import health as health_module  # type: ignore[import-untyped]
from equity_lake.monitoring.health import PipelineMonitor as HealthPipelineMonitor  # type: ignore[import-untyped]

runner = CliRunner()
MonitorInvoker = Callable[..., tuple[Result, MagicMock]]


@pytest.fixture
def monitor_cli(monkeypatch: pytest.MonkeyPatch) -> MonitorInvoker:
    """Invoke the CLI with settings, alerter, and monitor construction isolated."""
    from equity_lake.cli.commands import analysis  # type: ignore[import-untyped]

    settings = SimpleNamespace(
        monitoring=SimpleNamespace(
            max_age_days=2,
            null_threshold_pct=5.0,
            market_max_age_days={},
            table_max_age_days={},
        ),
        alerting=SimpleNamespace(webhook_url=None),
    )
    monkeypatch.setattr("equity_lake.core.config.get_settings", lambda: settings)
    monkeypatch.setattr("equity_lake.monitoring.alerting.build_alerter", MagicMock())
    monkeypatch.setattr(analysis, "_init_logging", lambda _verbose: None)

    def invoke(
        output_path: Path | None,
        *,
        healthy: bool = True,
        check_results: dict[str, bool] | None = None,
        run_error: Exception | None = None,
        save_error: Exception | None = None,
    ) -> tuple[Result, MagicMock]:
        results = check_results if check_results is not None else {"Data Freshness": healthy, "Data Quality": healthy}
        monitor_inst = MagicMock()
        monitor_inst.check_results = results
        if run_error is not None:
            monitor_inst.run_health_check.side_effect = run_error
        else:
            monitor_inst.run_health_check.return_value = healthy

        report_state = cast(
            HealthPipelineMonitor,
            SimpleNamespace(
                alerts=[] if healthy else ["synthetic failure"],
                metrics={"synthetic": {"checked": True}},
                check_results=results,
            ),
        )
        monitor_inst.save_report.side_effect = lambda path: HealthPipelineMonitor.save_report(report_state, path)
        if save_error is not None:
            monitor_inst.save_report.side_effect = save_error
        monkeypatch.setattr(health_module, "PipelineMonitor", lambda **_kwargs: monitor_inst)

        args = ["monitor"] if output_path is None else ["monitor", "--output-json", str(output_path)]
        result = runner.invoke(app, args)
        return result, monitor_inst

    return invoke


def test_unhealthy_monitor_saves_report_and_closes_before_exit(monitor_cli: MonitorInvoker, tmp_path: Path) -> None:
    output_path = tmp_path / "nested" / "health-report.json"
    output_path.parent.mkdir(parents=True)
    output_path.write_text('{"stale": true}', encoding="utf-8")

    result, monitor_inst = monitor_cli(
        output_path,
        healthy=False,
        check_results={"Data Freshness": False, "Data Quality": True},
    )

    assert result.exit_code == 1
    assert "Pipeline has ISSUES" in result.stdout
    assert "Data Freshness" in result.stdout
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert "stale" not in report
    assert report["alerts"] == ["synthetic failure"]
    assert "metrics" in report and "timestamp" in report
    assert report["healthy"] is False
    assert report["check_results"] == {"Data Freshness": False, "Data Quality": True}
    monitor_inst.save_report.assert_called_once_with(output_path)
    monitor_inst.close.assert_called_once_with()
    assert monitor_inst.mock_calls.index(call.save_report(output_path)) < monitor_inst.mock_calls.index(call.close())


def test_healthy_monitor_creates_nested_report_and_exits_zero(monitor_cli: MonitorInvoker, tmp_path: Path) -> None:
    output_path = tmp_path / "new" / "nested" / "health-report.json"

    result, monitor_inst = monitor_cli(output_path, healthy=True)

    assert result.exit_code == 0
    assert "Pipeline is HEALTHY" in result.stdout
    assert output_path.is_file()
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["healthy"] is True
    assert report["check_results"] == {"Data Freshness": True, "Data Quality": True}
    assert {"alerts", "metrics", "timestamp"}.issubset(report)
    monitor_inst.close.assert_called_once_with()


def test_monitor_default_report_location(monitor_cli: MonitorInvoker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("equity_lake.cli.commands.analysis.LOGS_DIR", tmp_path)
    result, monitor_inst = monitor_cli(None, healthy=False)
    assert result.exit_code == 1
    path = tmp_path / "health-report.json"
    assert json.loads(path.read_text(encoding="utf-8"))["healthy"] is False
    monitor_inst.save_report.assert_called_once_with(path)


def test_report_with_no_checks_does_not_claim_healthy(monitor_cli: MonitorInvoker, tmp_path: Path) -> None:
    path = tmp_path / "health-report.json"
    monitor_cli(path, check_results={})
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["healthy"] is False
    assert report["check_results"] == {}


@pytest.mark.parametrize("failure", ["check", "report"])
def test_monitor_closes_when_check_or_report_raises(monitor_cli: MonitorInvoker, tmp_path: Path, failure: str) -> None:
    output_path = tmp_path / "reports" / "health-report.json"
    run_error = RuntimeError("check failed") if failure == "check" else None
    save_error = RuntimeError("report failed") if failure == "report" else None

    result, monitor_inst = monitor_cli(output_path, run_error=run_error, save_error=save_error)

    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    monitor_inst.close.assert_called_once_with()
    if failure == "check":
        monitor_inst.save_report.assert_not_called()
    else:
        monitor_inst.save_report.assert_called_once_with(output_path)
    assert monitor_inst.mock_calls[-1] == call.close()
