"""Thin dependency getters for the read API.

Each getter wraps an existing storage/findings reader so routers stay thin and
reuse the canonical lake accessors (no duplicated I/O). Kept FastAPI-free;
routers call these directly (or via ``Depends`` for lifecycle-managed resources
like the DuckDB connection added in a later slice).
"""

from __future__ import annotations

from equity_lake.findings.models import FindingCard
from equity_lake.findings.writer import load_finding_cards


def list_findings() -> list[FindingCard]:
    """Load every serialized FindingCard under ``data/findings/``."""
    return load_finding_cards()


__all__ = ["list_findings"]
