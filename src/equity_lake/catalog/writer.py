"""JSONL serialization for the data catalog.

Writes one JSON object per line — datasets, nodes, and edges — so git
diffs are clean and reviewable (adding one dataset = one green line).
"""

from __future__ import annotations

import json
from pathlib import Path

import structlog

from equity_lake.catalog.models import Catalog

logger = structlog.get_logger()


def catalog_to_jsonl(catalog: Catalog) -> str:
    """Serialize a :class:`Catalog` to a JSONL string."""
    lines: list[str] = []

    lines.append(
        json.dumps(
            {
                "type": "catalog",
                "version": catalog.version,
                "dataset_count": len(catalog.datasets),
                "node_count": len(catalog.nodes),
                "edge_count": len(catalog.edges),
            },
            ensure_ascii=False,
        )
    )

    for dataset in catalog.datasets:
        entry = {"type": "dataset", **dataset.model_dump()}
        entry["columns"] = [c.model_dump() for c in dataset.columns]
        lines.append(json.dumps(entry, ensure_ascii=False))

    for node in catalog.nodes:
        lines.append(
            json.dumps(
                {
                    "type": "node",
                    **node.model_dump(),
                },
                ensure_ascii=False,
            )
        )

    for edge in catalog.edges:
        lines.append(
            json.dumps(
                {
                    "type": "edge",
                    **edge.model_dump(),
                },
                ensure_ascii=False,
            )
        )

    return "\n".join(lines) + "\n"


def write_catalog_jsonl(catalog: Catalog, path: Path | None = None) -> Path:
    """Write the catalog to a JSONL file.

    Defaults to ``data/catalog.jsonl`` relative to the project root.
    """
    if path is None:
        from equity_lake.core.paths import PROJECT_ROOT

        path = PROJECT_ROOT / "data" / "catalog.jsonl"

    path.parent.mkdir(parents=True, exist_ok=True)
    content = catalog_to_jsonl(catalog)
    path.write_text(content, encoding="utf-8")

    logger.info("catalog_written", path=str(path), bytes=len(content.encode("utf-8")))
    return path
