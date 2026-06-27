"""Data catalog: Hamilton-powered medallion metadata generator.

Produces ``data/catalog.jsonl`` — one JSON line per dataset, node, or edge.
CLI entry-point: ``equity catalog-generate``.
"""

from __future__ import annotations

from equity_lake.catalog.builder import build_catalog
from equity_lake.catalog.writer import write_catalog_jsonl

__all__ = ["build_catalog", "write_catalog_jsonl"]
