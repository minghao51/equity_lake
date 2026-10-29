"""Tests for the static dashboard exporter."""

import json
from pathlib import Path

import pytest

from equity_lake.dashboard.exporter import DashboardExporter  # type: ignore[import-untyped]


def test_dashboard_exporter_writes_static_files(tmp_path: Path) -> None:
    """Exporter should always emit the HTML shell and JSON payload."""
    exporter = DashboardExporter(output_dir=tmp_path)

    output_path = exporter.write()

    assert output_path == tmp_path / "index.html"
    assert output_path.exists()
    assert (tmp_path / "dashboard-data.json").exists()
    assert (tmp_path / "datasets.html").exists()
    assert (tmp_path / "health.html").exists()
    assert (tmp_path / "updates.html").exists()
    assert (tmp_path / "config.html").exists()


def test_dashboard_exporter_round_trips_w02_result_fields(tmp_path: Path) -> None:
    """W02: existing JSON/HTML readers accept the added truthful-outcome fields."""
    import json
    from unittest.mock import patch

    logs = tmp_path / "logs"
    logs.mkdir()
    results = {
        "scope": {"tickers": ["AAPL", "MSFT"], "enrichment_only": False, "explicit_tickers": False},
        "ingestion": {
            "success": True,
            "partial": True,
            "optional_failures": ["us_news"],
            "errors": {"us_news": "news timeout"},
            "markets": {"us_equity": "written", "us_news": "failed"},
        },
        "features": {
            "success": True,
            "coverage": {"requested": ["AAPL", "MSFT"], "produced": ["AAPL"], "missing": {"MSFT": "missing_output_bar"}},
        },
        "ml": {"success": True, "results": {"AAPL": {"success": True, "persisted": True}}},
    }
    (logs / "pipeline_results_2024-01-02.json").write_text(json.dumps(results), encoding="utf-8")

    site = tmp_path / "site"
    with patch("equity_lake.dashboard.exporter.LOGS_DIR", logs):
        DashboardExporter(output_dir=site).write()

    payload = json.loads((site / "dashboard-data.json").read_text(encoding="utf-8"))
    saved = payload["updates"]["pipeline_results"][0]["content"]
    assert saved["scope"]["tickers"] == ["AAPL", "MSFT"]
    assert saved["ingestion"]["optional_failures"] == ["us_news"]
    assert saved["features"]["coverage"]["missing"] == {"MSFT": "missing_output_bar"}
    assert saved["ml"]["results"]["AAPL"]["persisted"] is True

    updates_html = (site / "updates.html").read_text(encoding="utf-8")
    assert "missing_output_bar" in updates_html
    assert "us_news" in updates_html


def test_dashboard_exporter_escapes_health_alerts(tmp_path: Path) -> None:
    """F5: alert text from the health report is published escaped, not as markup."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "health-report.json").write_text(
        json.dumps({"alerts": ["<script>alert('xss')</script>"], "metrics": {"note": "<img src=x onerror=alert(1)>"}}),
        encoding="utf-8",
    )

    DashboardExporter(output_dir=site).write()

    for page in ("index.html", "health.html"):
        rendered = (site / page).read_text(encoding="utf-8")
        assert "&lt;script&gt;" in rendered
        assert "<script>alert" not in rendered
    assert "&lt;img src=x onerror=alert(1)&gt;" in (site / "health.html").read_text(encoding="utf-8")


def test_dashboard_exporter_escapes_dataset_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """F5: an injected dataset path is escaped in the published inventory page."""
    injected = tmp_path / "lake" / 'broken"><script>alert("path")</script>'
    monkeypatch.setattr("equity_lake.dashboard.exporter.MARKET_DATASETS", {"injected": injected})

    site = tmp_path / "site"
    DashboardExporter(output_dir=site).write()

    rendered = (site / "datasets.html").read_text(encoding="utf-8")
    assert "&lt;script&gt;" in rendered
    assert "<script>alert" not in rendered
