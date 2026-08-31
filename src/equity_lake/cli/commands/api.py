"""``equity api`` — Phase 2B read API server (FastAPI over uvicorn)."""

from __future__ import annotations

from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, api_app

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}


@api_app.command("serve")
def api_serve(
    host: Annotated[
        str,
        typer.Option("--host", help="Bind host (non-loopback hosts require confirmation — the API is unauthenticated)"),
    ] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-p", help="Bind port")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes (dev)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Serve the read-only FastAPI API over the equity data lake."""
    import uvicorn

    from equity_lake.api.main import create_app

    _init_logging(verbose)
    if host not in _LOOPBACK_HOSTS:
        # Mirrors the demo-seed guard pattern: warn + confirm before exposing.
        # The API (including /docs) has no authentication; binding a wildcard or
        # LAN interface publishes findings, models, and predictions to anyone
        # who can reach the port. Non-interactive contexts (EOF on stdin) abort.
        typer.secho(
            f"WARNING: --host {host} binds a non-loopback interface and the API — including /docs — is UNAUTHENTICATED.",
            fg=typer.colors.YELLOW,
        )
        typer.confirm("Expose the unauthenticated API on this interface anyway?", abort=True)
    typer.secho(f"Serving Equity Lake API on http://{host}:{port}", fg=typer.colors.GREEN)
    typer.echo("  OpenAPI docs at /docs  ·  health at /health")
    if reload:
        uvicorn.run("equity_lake.api.main:create_app", host=host, port=port, reload=True, factory=True)
    else:
        uvicorn.run(create_app(), host=host, port=port)
