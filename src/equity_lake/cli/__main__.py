"""Unified CLI for Equity Lake."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import (
    _init_logging,
    api_app,
    app,
    arena_app,
    bootstrap_app,
    config_app,
    dashboard_app,
    demo_app,
    ml_app,
    portfolio_app,
    report_app,
    signal_app,
    tracking_app,
    validate_app,
)

app.add_typer(signal_app, name="signal")
app.add_typer(tracking_app, name="tracking")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(bootstrap_app, name="bootstrap")
app.add_typer(config_app, name="config")
app.add_typer(validate_app, name="validate")
app.add_typer(arena_app, name="arena")
app.add_typer(report_app, name="report")
app.add_typer(demo_app, name="demo")
app.add_typer(ml_app, name="ml")
app.add_typer(api_app, name="api")
app.add_typer(portfolio_app, name="portfolio")


@dashboard_app.command("build")
def dashboard_build(
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Output directory")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Build static HTML dashboard."""
    from equity_lake.dashboard.exporter import build_dashboard

    _init_logging(verbose)
    build_dashboard(output_dir=Path(output_dir) if output_dir else None)


@dashboard_app.command("h00-build")
def dashboard_h00_build(
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Artifact directory (default: data/research/h00)")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Build the sanitized demo-lake page (offline; it never publishes).

    The build reads the synthetic demo lake only and writes one composed artifact plus
    its sanitization report. Publication is not implemented: it needs a separate
    explicit owner approval.
    """
    from equity_lake.dashboard.h00 import H00BuildError, build_sanitized_page

    _init_logging(verbose)
    try:
        artifact = build_sanitized_page(output_dir=Path(output_dir) if output_dir else None)
    except H00BuildError as exc:
        typer.secho(f"H00 build refused: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"H00 artifact: {artifact.directory}")
    typer.echo(f"Page built at: {artifact.built_at.isoformat()}")
    if artifact.stale:
        typer.secho("stale — rebuild due", fg=typer.colors.YELLOW)
    typer.echo("Preview: equity dashboard h00-preview  (loopback only; not published)")


@dashboard_app.command("h00-preview")
def dashboard_h00_preview(
    port: Annotated[int, typer.Option("--port", "-p", help="Loopback port")] = 8765,
    host: Annotated[str, typer.Option("--host", help="Loopback bind host (127.0.0.1 or localhost)")] = "127.0.0.1",
    artifact_dir: Annotated[str | None, typer.Option("--artifact-dir", help="Artifact directory (default: data/research/h00)")] = None,
) -> None:
    """Preview the sanitized demo-lake page on a loopback address only."""
    from equity_lake.dashboard.h00 import H00PreviewError, artifact_staleness, serve_preview

    target = Path(artifact_dir) if artifact_dir else None
    try:
        stale, build_date = artifact_staleness(target)
        if stale:
            typer.secho(f"stale — rebuild due (last build {build_date or 'unknown'})", fg=typer.colors.YELLOW)
        serve_preview(host=host, port=port, artifact_dir=target)
    except H00PreviewError as exc:
        typer.secho(f"H00 preview refused: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


@dashboard_app.command("serve")
def dashboard_serve(
    port: Annotated[int, typer.Option("--port", "-p", help="Port")] = 8501,
) -> None:
    """Serve the Streamlit dashboard locally."""
    from equity_lake.core.paths import PROJECT_ROOT

    app_path = PROJECT_ROOT / "src" / "equity_lake" / "dashboard" / "streamlit_app.py"
    if not app_path.exists():
        typer.secho(f"Streamlit app not found at {app_path}", fg=typer.colors.RED)
        raise typer.Exit(1)

    typer.echo(f"Launching Streamlit on port {port}...")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.address",
        "127.0.0.1",
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    raise typer.Exit(subprocess.run(cmd).returncode)


import equity_lake.cli.commands.data  # noqa: E402, F401, I001
import equity_lake.cli.commands.pipeline  # noqa: E402, F401
import equity_lake.cli.commands.ingest  # noqa: E402, F401
import equity_lake.cli.commands.intelligence  # noqa: E402, F401
import equity_lake.cli.commands.analysis  # noqa: E402, F401
import equity_lake.cli.commands.admin  # noqa: E402, F401
import equity_lake.cli.commands.catalog  # noqa: E402, F401
import equity_lake.cli.commands.arena  # noqa: E402, F401
import equity_lake.cli.commands.demo  # noqa: E402, F401
import equity_lake.cli.commands.ml  # noqa: E402, F401
import equity_lake.cli.commands.api  # noqa: E402, F401
import equity_lake.cli.commands.tracking  # noqa: E402, F401
import equity_lake.cli.commands.portfolio  # noqa: E402, F401


if __name__ == "__main__":
    app()
