"""Tests for the static dashboard exporter."""

from pathlib import Path

from equity_lake.dashboard.exporter import DashboardExporter


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
