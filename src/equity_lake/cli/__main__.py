"""Unified CLI for Equity Lake."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import (
    _init_logging,
    app,
    bootstrap_app,
    config_app,
    dashboard_app,
    loader_app,
    signal_app,
    validate_app,
)

app.add_typer(signal_app, name="signal")
app.add_typer(dashboard_app, name="dashboard")
app.add_typer(bootstrap_app, name="bootstrap")
app.add_typer(loader_app, name="loader")
app.add_typer(config_app, name="config")
app.add_typer(validate_app, name="validate")


@dashboard_app.command("build")
def dashboard_build(
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Output directory")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Build static HTML dashboard."""
    from equity_lake.dashboard.exporter import build_dashboard

    _init_logging(verbose)
    build_dashboard(output_dir=Path(output_dir) if output_dir else None)


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
import equity_lake.cli.commands.intelligence  # noqa: E402, F401
import equity_lake.cli.commands.analysis  # noqa: E402, F401
import equity_lake.cli.commands.admin  # noqa: E402, F401


if __name__ == "__main__":
    app()
