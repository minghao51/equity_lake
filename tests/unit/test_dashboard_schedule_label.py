"""The static dashboard labels hosted GitHub Actions cron as UTC."""

from types import SimpleNamespace
from typing import Any, cast

from equity_lake.dashboard.exporter import DashboardExporter  # type: ignore[import-untyped]


def test_overview_labels_github_actions_schedule_as_utc() -> None:
    exporter = object.__new__(DashboardExporter)
    exporter.settings = cast(Any, SimpleNamespace(dashboard=SimpleNamespace(title="Test", subtitle="Test site")))
    payload: dict[str, Any] = {
        "summary": {"latest_update": "2024-01-02", "datasets_available": 0, "tracked_datasets": 0},
        "generated_at": "2024-01-03T00:00:00+00:00",
        "datasets": [],
        "health": None,
        "schedule": {"cron": "0 1 * * 1-5", "timezone": "America/New_York"},
    }

    html = exporter._render_overview(payload)

    assert "GitHub Actions cron (UTC): 0 1 * * 1-5" in html
    assert "America/New_York" not in html
