"""``equity api`` — Phase 2B read API server (FastAPI over uvicorn)."""

from __future__ import annotations

from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, api_app


@api_app.command("serve")
def api_serve(
    host: Annotated[str, typer.Option("--host", help="Bind host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Bind port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes (dev)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Serve the read-only FastAPI API over the equity data lake."""
    import uvicorn

    from equity_lake.api.main import create_app

    _init_logging(verbose)
    typer.secho(f"Serving Equity Lake API on http://{host}:{port}", fg=typer.colors.GREEN)
    typer.echo("  OpenAPI docs at /docs  ·  health at /health")
    if reload:
        uvicorn.run("equity_lake.api.main:create_app", host=host, port=port, reload=True, factory=True)
    else:
        uvicorn.run(create_app(), host=host, port=port)
