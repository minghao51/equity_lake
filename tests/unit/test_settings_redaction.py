"""Regression tests for sensitive settings rendered by exports and the CLI."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from equity_lake.cli.commands.admin import config_export, config_get, config_show  # type: ignore[import-untyped]
from equity_lake.core.logging import redact_secrets, setup_structured_logging  # type: ignore[import-untyped]
from equity_lake.core.settings import AlertingSettings, Settings  # type: ignore[import-untyped]
from equity_lake.dashboard.exporter import DashboardExporter  # type: ignore[import-untyped]
from equity_lake.monitoring.alerting import WebhookAlerter  # type: ignore[import-untyped]

_SYNTHETIC_WEBHOOK_URL = "https://hooks.example.test/services/R01-SYNTHETIC?signature=secret-r01"
_REDACTED = "***REDACTED***"


def test_dashboard_export_redacts_webhook_url(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = Settings(alerting=AlertingSettings(webhook_url=_SYNTHETIC_WEBHOOK_URL))
    monkeypatch.setattr("equity_lake.dashboard.exporter.get_settings", lambda: settings)
    monkeypatch.setattr("equity_lake.dashboard.exporter.MARKET_DATASETS", {})
    monkeypatch.setattr(DashboardExporter, "_load_health_report", lambda self: None)
    monkeypatch.setattr(DashboardExporter, "_load_updates", lambda self: {"history": [], "pipeline_results": []})

    DashboardExporter(tmp_path / "site").write()

    json_output = (tmp_path / "site" / settings.dashboard.data_file).read_text(encoding="utf-8")
    html_output = (tmp_path / "site" / "config.html").read_text(encoding="utf-8")
    assert _SYNTHETIC_WEBHOOK_URL not in json_output
    assert _SYNTHETIC_WEBHOOK_URL not in html_output
    assert _REDACTED in json_output
    assert _REDACTED in html_output


def test_config_show_and_export_redact_webhook_url(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    settings = Settings(alerting=AlertingSettings(webhook_url=_SYNTHETIC_WEBHOOK_URL))
    monkeypatch.setattr("equity_lake.core.config.load_settings", lambda: settings)

    config_show()
    show_output = capsys.readouterr().out
    assert _SYNTHETIC_WEBHOOK_URL not in show_output
    assert json.loads(show_output)["alerting"]["webhook_url"] == _REDACTED

    config_export()
    export_output = capsys.readouterr().out
    assert _SYNTHETIC_WEBHOOK_URL not in export_output
    assert json.loads(export_output)["alerting"]["webhook_url"] == _REDACTED

    output_path = tmp_path / "settings.json"
    config_export(str(output_path))
    saved_output = output_path.read_text(encoding="utf-8")
    assert _SYNTHETIC_WEBHOOK_URL not in saved_output
    assert json.loads(saved_output)["alerting"]["webhook_url"] == _REDACTED


@pytest.mark.parametrize("path", ["alerting", "alerting.webhook_url"])
def test_config_get_redacts_nested_and_direct_secret(path: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    settings = Settings(alerting=AlertingSettings(webhook_url=_SYNTHETIC_WEBHOOK_URL))
    monkeypatch.setattr("equity_lake.core.config.load_settings", lambda: settings)
    config_get(path)
    output = capsys.readouterr().out
    assert _SYNTHETIC_WEBHOOK_URL not in output
    assert _REDACTED in output


def test_redaction_preserves_nonsecrets_without_mutating_input() -> None:
    original: dict[str, Any] = {
        "nested": [{"webhook_url": _SYNTHETIC_WEBHOOK_URL, "token": "private"}],
        "schedule": {"cron": "0 8 * * 1-5"},
    }
    result = redact_secrets(original)
    assert result["nested"] == [{"webhook_url": _REDACTED, "token": _REDACTED}]
    assert result["schedule"] == original["schedule"]
    assert original["nested"][0]["webhook_url"] == _SYNTHETIC_WEBHOOK_URL


def test_normal_cli_logging_suppresses_http_wire_url_logs(caplog: pytest.LogCaptureFixture) -> None:
    levels = {name: logging.getLogger(name).level for name in ("httpx", "httpcore", "urllib3", "requests")}
    root_level = logging.getLogger().level
    try:
        caplog.set_level(logging.DEBUG)
        setup_structured_logging(level="DEBUG")
        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        client = httpx.Client(transport=transport)
        with patch("equity_lake.monitoring.alerting.httpx.Client", return_value=client):
            # The adapter uses a context manager; the transport never contacts a provider.
            WebhookAlerter(_SYNTHETIC_WEBHOOK_URL).send_alert(["synthetic"], severity="info")
        assert _SYNTHETIC_WEBHOOK_URL not in caplog.text
        assert "webhook_alert_sent" in caplog.text
        assert "HTTP Request" not in caplog.text
        logging.getLogger("urllib3.connectionpool").debug("GET /fred?api_key=SYNTHETIC_FRED_KEY")
        assert "SYNTHETIC_FRED_KEY" not in caplog.text
    finally:
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)
        logging.getLogger().setLevel(root_level)
