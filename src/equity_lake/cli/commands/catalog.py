"""Catalog CLI commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, app


@app.command("catalog-generate")
def catalog_generate(
    output: Annotated[str | None, typer.Option("--output", "-o", help="Output JSONL path")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Generate data/catalog.jsonl from the Hamilton DAG topology.

    Produces one JSON line per dataset, node, and edge for clean
    git diffs.  Requires no data — extracts metadata from the existing
    medallion-layered feature DAG modules.
    """
    from equity_lake.catalog import build_catalog, write_catalog_jsonl

    _init_logging(verbose)

    typer.secho("Building catalog from Hamilton DAG...", fg=typer.colors.CYAN)
    catalog = build_catalog()

    output_path = Path(output) if output else None
    written = write_catalog_jsonl(catalog, path=output_path)

    typer.secho(f"Catalog written: {written}", fg=typer.colors.GREEN)
    typer.echo(f"  {len(catalog.datasets)} datasets, {len(catalog.nodes)} nodes, {len(catalog.edges)} edges")
