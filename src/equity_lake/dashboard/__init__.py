"""Static dashboard export helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_dashboard(output_dir: Path | None = None) -> Any:
    """Build the static dashboard lazily to keep module execution clean."""
    from equity_lake.dashboard.exporter import build_dashboard as _build_dashboard

    return _build_dashboard(output_dir=output_dir)


class DashboardExporter:  # pragma: no cover - thin compatibility wrapper
    """Lazy proxy for the concrete exporter implementation."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Any:
        from equity_lake.dashboard.exporter import (
            DashboardExporter as _DashboardExporter,
        )

        return _DashboardExporter(*args, **kwargs)


__all__ = ["DashboardExporter", "build_dashboard"]
