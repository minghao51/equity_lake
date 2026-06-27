"""Build a catalog from Hamilton DAG topology + static dataset definitions.

Leverages the existing :class:`~equity_lake.features.pipeline.FeaturePipeline`
driver to extract node-level metadata (names, types, tags, dependencies,
validators) from the medallion-layered Hamilton DAG.

Static dataset definitions (paths, schemas, descriptions) come from
:mod:`equity_lake.catalog.datasets` and serve as the "anchor" entries for
each medallion layer.
"""

from __future__ import annotations

import structlog
from hamilton import driver

from equity_lake.catalog.datasets import ALL_DATASETS, LAYER_ORDER
from equity_lake.catalog.models import Catalog, DatasetEntry, EdgeEntry, NodeEntry
from equity_lake.features.dag import clean_02, enrichments_04, features_03, raw_01

logger = structlog.get_logger()

_HAMILTON_INTERNAL_SUFFIXES = ("_raw", "_data_type_validator", "_range_validator")

_TAGS_TO_EXCLUDE_PREFIXES = ("hamilton.", "module")


def _is_hamilton_internal(name: str) -> bool:
    """Filter out Hamilton-generated wrapper nodes (e.g. ``close_raw``, ``volume_data_type_validator``)."""
    return name.endswith(_HAMILTON_INTERNAL_SUFFIXES)


def _split_tag(value: str) -> list[str]:
    """Split a pipe-separated tag value into trimmed, non-empty parts."""
    return [c.strip() for c in value.split("|") if c.strip()]


def _build_driver() -> driver.Driver:
    """Build a Hamilton driver from the four layered DAG modules."""
    from hamilton import base
    from hamilton.plugins import h_polars

    adapter = base.SimplePythonGraphAdapter(h_polars.PolarsDataFrameResult())
    return driver.Builder().with_modules(raw_01, clean_02, features_03, enrichments_04).with_adapter(adapter).build()


def _compute_dataset_lineage(datasets: list[DatasetEntry]) -> list[DatasetEntry]:
    """Populate ``upstream``/``downstream`` via medallion layer adjacency.

    Bronze datasets have no upstream (they are the source). Each layer's
    upstream is all datasets in the preceding layer; downstream is all
    datasets in the next layer. Returns copies so the static definitions
    in :mod:`equity_lake.catalog.datasets` are not mutated.
    """
    by_layer: dict[str, list[DatasetEntry]] = {}
    for ds in datasets:
        by_layer.setdefault(ds.layer, []).append(ds)

    result: list[DatasetEntry] = []
    for ds in datasets:
        layer_idx = LAYER_ORDER.index(ds.layer) if ds.layer in LAYER_ORDER else -1
        prev_layer = LAYER_ORDER[layer_idx - 1] if layer_idx > 0 else None
        next_layer = LAYER_ORDER[layer_idx + 1] if 0 <= layer_idx < len(LAYER_ORDER) - 1 else None
        upstream = [d.name for d in by_layer.get(prev_layer, [])] if prev_layer else []
        downstream = [d.name for d in by_layer.get(next_layer, [])] if next_layer else []
        result.append(ds.model_copy(update={"upstream": upstream, "downstream": downstream}))
    return result


def build_catalog() -> Catalog:
    """Generate the full data catalog.

    Extracts node-level topology from the Hamilton DAG and merges it with
    static dataset definitions from :mod:`equity_lake.catalog.datasets`.

    Internal Hamilton wrapper nodes (``*_raw``, ``*_data_type_validator``,
    ``*_range_validator``) and self-referencing edges are filtered out.
    """
    dr = _build_driver()
    variables = dr.list_available_variables()

    nodes: list[NodeEntry] = []
    node_names: set[str] = set()

    for var in variables:
        if _is_hamilton_internal(var.name):
            continue
        if "layer" not in var.tags:
            continue
        node = NodeEntry(
            name=var.name,
            layer=var.tags.get("layer", ""),
            category=var.tags.get("category", ""),
            description=var.tags.get("description", ""),
            produces=_split_tag(var.tags.get("produces", "")),
            depends_on=_split_tag(var.tags.get("depends_on", "")),
            validators=_split_tag(var.tags.get("validators", "")),
            tags={k: v for k, v in var.tags.items() if not k.startswith(_TAGS_TO_EXCLUDE_PREFIXES)},
        )
        nodes.append(node)
        node_names.add(node.name)

    edges: list[EdgeEntry] = []
    seen_edges: set[tuple[str, str]] = set()

    for node in nodes:
        try:
            upstream = dr.what_is_upstream_of(node.name)
        except (KeyError, ValueError):
            logger.debug("upstream_lookup_failed", node=node.name)
            continue

        for up_var in upstream:
            if _is_hamilton_internal(up_var.name) or up_var.name not in node_names:
                continue
            edge_key = (up_var.name, node.name)
            if edge_key not in seen_edges and up_var.name != node.name:
                seen_edges.add(edge_key)
                edges.append(
                    EdgeEntry(
                        source=up_var.name,
                        target=node.name,
                        relationship=up_var.tags.get("relationship", "computed_from"),
                    )
                )

    nodes.sort(key=lambda n: n.name)
    edges.sort(key=lambda e: (e.source, e.target, e.relationship))

    catalog = Catalog(
        datasets=_compute_dataset_lineage(ALL_DATASETS),
        nodes=nodes,
        edges=edges,
    )

    logger.info(
        "catalog_built",
        datasets=len(catalog.datasets),
        nodes=len(catalog.nodes),
        edges=len(catalog.edges),
    )
    return catalog
