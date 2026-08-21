"""Static dashboard export helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_dashboard(output_dir: Path | None = None) -> Any:
    """Build the static dashboard lazily to keep module execution clean."""
    from equity_lake.dashboard.exporter import build_dashboard as _build_dashboard

    return _build_dashboard(output_dir=output_dir)


__all__ = ["build_dashboard"]
