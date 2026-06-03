"""Unified CLI for Equity Lake.

Usage:
    equity ingest --markets us
    equity pipeline --markets us,cn
    equity signal scan
    equity bootstrap sample
    equity backtest --strategy sma_crossover --tickers AAPL,MSFT ...
    equity dashboard build
    equity dashboard serve

Legacy commands (equity-daily, equity-pipeline, etc.) are deprecated but still work.
"""

from __future__ import annotations

import sys

import typer

PASSTHROUGH_CONTEXT = {
    "allow_extra_args": True,
    "ignore_unknown_options": True,
}

app = typer.Typer(
    name="equity",
    help="Equity Lake: Local-first equity data pipeline",
    add_completion=False,
    rich_markup_mode="rich",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DEPRECATION_MSG = (
    "[yellow]⚠ This command is a legacy wrapper.[/]\n"
    "The unified [cyan]equity[/] CLI provides all functionality:\n"
    "  [dim]equity-daily[/] → [cyan]equity ingest --daily[/]\n"
    "  [dim]equity-pipeline[/] → [cyan]equity pipeline[/]\n"
    "  [dim]equity-backtest[/] → [cyan]equity backtest[/]\n"
    "  [dim]equity-signal[/] → [cyan]equity signal[/]\n"
    "Run [cyan]equity --help[/] for all commands.\n"
)


def _run_legacy(entry_point: str, extra_argv: list[str]) -> None:
    """Invoke a legacy argparse-based main() with a patched sys.argv."""
    module_path, func_name = entry_point.rsplit(":", 1)
    import importlib

    mod = importlib.import_module(module_path)
    main_func = getattr(mod, func_name)

    saved_argv = list(sys.argv)
    try:
        sys.argv = [saved_argv[0]] + extra_argv
        main_func()
    except SystemExit as exc:
        if exc.code and exc.code != 0:
            raise
    finally:
        sys.argv = saved_argv


def _passthrough(entry_point: str, ctx: typer.Context) -> None:
    """Pass all remaining arguments through to the legacy CLI."""
    typer.secho(DEPRECATION_MSG, err=True)
    extra = list(ctx.args) if hasattr(ctx, "args") else []
    _run_legacy(entry_point, extra)


def _passthrough_with_prefix(entry_point: str, ctx: typer.Context, *prefix_args: str) -> None:
    """Pass prefixed subcommands plus remaining arguments through to the legacy CLI."""
    typer.secho(DEPRECATION_MSG, err=True)
    extra = [*prefix_args, *(list(ctx.args) if hasattr(ctx, "args") else [])]
    _run_legacy(entry_point, extra)


# ---------------------------------------------------------------------------
# Command groups
# ---------------------------------------------------------------------------

signal_app = typer.Typer(help="Signal scanning for equity watchlists")
app.add_typer(signal_app, name="signal")

dashboard_app = typer.Typer(help="Dashboard build and serve")
app.add_typer(dashboard_app, name="dashboard")

bootstrap_app = typer.Typer(help="Data bootstrapping and sample generation")
app.add_typer(bootstrap_app, name="bootstrap")

loader_app = typer.Typer(help="Manage data loaders")
app.add_typer(loader_app, name="loader")

config_app = typer.Typer(help="Configuration management")
app.add_typer(config_app, name="config")

validate_app = typer.Typer(help="Data quality validation and profiling")
app.add_typer(validate_app, name="validate")


# ---------------------------------------------------------------------------
# Top-level commands (wrapping legacy CLIs)
# ---------------------------------------------------------------------------


@app.command("ingest", context_settings=PASSTHROUGH_CONTEXT)
def ingest(ctx: typer.Context) -> None:
    """Ingest daily equity market data (replaces equity-daily)."""
    _passthrough("equity_lake.ingestion.orchestrator:main", ctx)


@app.command("sync", context_settings=PASSTHROUGH_CONTEXT)
def sync(ctx: typer.Context) -> None:
    """Sync data lake to S3 (replaces equity-sync)."""
    _passthrough("equity_lake.storage.s3_sync:main", ctx)


@app.command("query", context_settings=PASSTHROUGH_CONTEXT)
def query(ctx: typer.Context) -> None:
    """Query the data lake via DuckDB (replaces equity-query)."""
    _passthrough("equity_lake.storage.duckdb:main", ctx)


@app.command("pipeline", context_settings=PASSTHROUGH_CONTEXT)
def pipeline(ctx: typer.Context) -> None:
    """Run the full EOD pipeline (replaces equity-pipeline)."""
    _passthrough("equity_lake.run_pipeline:main", ctx)


@app.command("monitor", context_settings=PASSTHROUGH_CONTEXT)
def monitor(ctx: typer.Context) -> None:
    """Monitor pipeline health and data quality (replaces equity-monitor)."""
    _passthrough("equity_lake.monitoring.health:main", ctx)


@app.command("backfill", context_settings=PASSTHROUGH_CONTEXT)
def backfill(ctx: typer.Context) -> None:
    """Backfill historical data (replaces equity-backfill)."""
    _passthrough("equity_lake.backfill_data:main", ctx)


@app.command("macro", context_settings=PASSTHROUGH_CONTEXT)
def macro(ctx: typer.Context) -> None:
    """Fetch macro indicators (replaces equity-macro)."""
    _passthrough("equity_lake.fetch_macro:main", ctx)


@app.command("forecast", context_settings=PASSTHROUGH_CONTEXT)
def forecast(ctx: typer.Context) -> None:
    """Price forecasting (replaces equity-price-forecast)."""
    _passthrough("equity_lake.price_forecaster:main", ctx)


@app.command("backtest", context_settings=PASSTHROUGH_CONTEXT)
def backtest(ctx: typer.Context) -> None:
    """Backtest trading strategies (replaces equity-backtest)."""
    _passthrough("equity_lake.cli.backtest:main", ctx)


@app.command("news", context_settings=PASSTHROUGH_CONTEXT)
def news(ctx: typer.Context) -> None:
    """Fetch market news (replaces equity-news)."""
    _passthrough("equity_lake.cli.news:main", ctx)


@app.command("sentiment", context_settings=PASSTHROUGH_CONTEXT)
def sentiment(ctx: typer.Context) -> None:
    """Analyze market sentiment (replaces equity-sentiment)."""
    _passthrough("equity_lake.cli.sentiment:main", ctx)


@app.command("update", context_settings=PASSTHROUGH_CONTEXT)
def update(ctx: typer.Context) -> None:
    """Smart updates (replaces equity-update)."""
    _passthrough("equity_lake.cli.update:main", ctx)


# ---------------------------------------------------------------------------
# signal subcommands
# ---------------------------------------------------------------------------


@signal_app.command("scan", context_settings=PASSTHROUGH_CONTEXT)
def signal_scan(ctx: typer.Context) -> None:
    """Scan watchlist and generate signals."""
    _passthrough_with_prefix("equity_lake.cli.signal:main", ctx, "scan")


# ---------------------------------------------------------------------------
# dashboard subcommands
# ---------------------------------------------------------------------------


@dashboard_app.command("build", context_settings=PASSTHROUGH_CONTEXT)
def dashboard_build(ctx: typer.Context) -> None:
    """Build static HTML dashboard."""
    _passthrough("equity_lake.cli.dashboard:main", ctx)


@dashboard_app.command("serve")
def dashboard_serve(port: int = typer.Option(8501, "--port", "-p", help="Port for Streamlit")) -> None:
    """Serve the Streamlit data quality dashboard locally."""
    import subprocess
    import sys
    from pathlib import Path

    app_path = Path(__file__).parent.parent / "dashboard" / "streamlit_app.py"
    if not app_path.exists():
        typer.echo(f"Error: Streamlit app not found at {app_path}")
        raise typer.Exit(1)

    typer.echo(f"Launching Streamlit dashboard on port {port}...")
    typer.echo(f"Visit: http://localhost:{port}")

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


# ---------------------------------------------------------------------------
# loader subcommands
# ---------------------------------------------------------------------------


@loader_app.command("list")
def loader_list() -> None:
    """List available data loaders."""
    from rich.console import Console
    from rich.table import Table

    from equity_lake.loaders import registry

    console = Console()
    table = Table(title="Available Loaders")
    table.add_column("Name", style="cyan")
    table.add_column("Markets")
    table.add_column("Data Types")
    table.add_column("Auth")

    for loader in sorted(registry.list(), key=lambda item: item.name):
        table.add_row(
            loader.name,
            ", ".join(loader.supported_markets) or "-",
            ", ".join(loader.data_types) or "-",
            "yes" if loader.requires_auth else "no",
        )

    console.print(table)


@loader_app.command("show")
def loader_show(name: str) -> None:
    """Show loader metadata."""
    import json

    from equity_lake.loaders import registry

    loader = registry.get(name)
    typer.echo(json.dumps(loader.metadata.model_dump(), indent=2))


@loader_app.command("test")
def loader_test(name: str) -> None:
    """Test loader connection."""
    from equity_lake.loaders import registry

    loader = registry.create(name, {})
    if loader.validate_connection():
        typer.echo(f"Loader '{name}' connection OK")
        return
    typer.echo(f"Loader '{name}' connection FAILED")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# config subcommands
# ---------------------------------------------------------------------------


@config_app.command("show", context_settings=PASSTHROUGH_CONTEXT)
def config_show(ctx: typer.Context) -> None:
    """Show full configuration."""
    _passthrough_with_prefix("equity_lake.cli.config:main", ctx, "show")


@config_app.command("get", context_settings=PASSTHROUGH_CONTEXT)
def config_get(ctx: typer.Context) -> None:
    """Get a specific config value."""
    _passthrough_with_prefix("equity_lake.cli.config:main", ctx, "get")


@config_app.command("validate", context_settings=PASSTHROUGH_CONTEXT)
def config_validate(ctx: typer.Context) -> None:
    """Validate configuration files."""
    _passthrough_with_prefix("equity_lake.cli.config:main", ctx, "validate")


@config_app.command("export", context_settings=PASSTHROUGH_CONTEXT)
def config_export(ctx: typer.Context) -> None:
    """Export configuration to a file."""
    _passthrough_with_prefix("equity_lake.cli.config:main", ctx, "export")


# ---------------------------------------------------------------------------
# bootstrap subcommands
# ---------------------------------------------------------------------------


@bootstrap_app.command("sample")
def bootstrap_sample(
    days: int = typer.Option(30, "--days", "-d", help="Number of trading days to generate"),
    tickers: str = typer.Option(None, "--tickers", "-t", help="Comma-separated ticker symbols (default: curated sample)"),
    output_dir: str = typer.Option(None, "--output-dir", "-o", help="Output directory (default: data/sample/)"),
    seed: int = typer.Option(42, "--seed", "-s", help="Random seed for reproducibility"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose logging"),
) -> None:
    """Generate 30 days of mock Parquet data for a few tickers.

    Uses a subset of real historical data if available, falling back to
    synthetic generation. Output goes to data/sample/ by default.
    """
    from equity_lake.cli.bootstrap import cmd_sample

    cmd_sample(
        days=days,
        tickers=tickers,
        output_dir=output_dir,
        seed=seed,
        verbose=verbose,
    )


# ---------------------------------------------------------------------------
# validate subcommands
# ---------------------------------------------------------------------------


@validate_app.command("check")
def validate_check(
    path: str = typer.Argument(help="Path to parquet file or directory"),
    data_type: str = typer.Option("price", "--type", "-t", help="Data type: price, macro, news"),
    strict: bool = typer.Option(False, "--strict", help="Fail on warnings"),
) -> None:
    """Validate data against schema and produce quality metrics."""
    from pathlib import Path as P

    import pandas as pd
    from rich.console import Console
    from rich.table import Table

    from equity_lake.validation.pipeline import ValidationPipeline

    console = Console()
    target = P(path)

    if target.is_dir():
        files = list(target.rglob("*.parquet"))
        if not files:
            console.print(f"[red]No parquet files found in {path}[/red]")
            raise typer.Exit(1)
        dfs = [pd.read_parquet(f) for f in files]
        df = pd.concat(dfs, ignore_index=True)
    else:
        df = pd.read_parquet(target)

    vp = ValidationPipeline(strict=strict)
    result = vp.validate(df, data_type=data_type, name="check")

    table = Table(title="Validation Result")
    table.add_column("Check", style="cyan")
    table.add_column("Status", style="green")
    table.add_row("Schema", "[green]PASS[/]" if result.schema_valid else "[red]FAIL[/]")
    table.add_row("Profile", "[green]OK[/]" if result.profile_valid else "[yellow]WARN[/]")
    table.add_row("Drift", "[red]DETECTED[/]" if result.drift_detected else "[green]NONE[/]")
    console.print(table)

    if result.errors:
        console.print("\n[red]Errors:[/red]")
        for err in result.errors:
            console.print(f"  - {err}")
    if result.warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for w in result.warnings:
            console.print(f"  - {w}")

    if not result.success:
        raise typer.Exit(1)


@validate_app.command("profile")
def validate_profile(
    path: str = typer.Argument(help="Path to parquet file or directory"),
    name: str = typer.Option(..., "--name", "-n", help="Profile name"),
    save: bool = typer.Option(False, "--save", help="Save profile to disk"),
) -> None:
    """Profile a dataset and display quality metrics."""
    from pathlib import Path as P

    import pandas as pd
    from rich.console import Console
    from rich.table import Table

    from equity_lake.validation.profiling import DataProfiler

    console = Console()
    target = P(path)

    if target.is_dir():
        files = list(target.rglob("*.parquet"))
        dfs = [pd.read_parquet(f) for f in files]
        df = pd.concat(dfs, ignore_index=True)
    else:
        df = pd.read_parquet(target)

    profiler = DataProfiler()
    profile = profiler.profile(df, name)
    metrics = profiler.get_quality_metrics(profile)

    table = Table(title=f"Profile: {name}")
    table.add_column("Column", style="cyan")
    table.add_column("Completeness", style="green")
    table.add_column("Null Count", style="yellow")
    table.add_column("Mean", style="blue")

    for col_name, col_metrics in metrics.items():
        table.add_row(
            col_name,
            f"{col_metrics.get('completeness', 0):.2%}",
            str(col_metrics.get("null_count", 0)),
            f"{col_metrics.get('mean', 'N/A')}",
        )

    console.print(table)
    console.print(f"\nTotal rows: {len(df)}, Columns: {len(df.columns)}")

    if save:
        console.print(f"[green]Profile saved to data/profiles/{name}.bin[/green]")


@validate_app.command("drift")
def validate_drift(
    current: str = typer.Argument(help="Path to current data"),
    baseline: str = typer.Argument(help="Path to baseline data"),
    threshold: float = typer.Option(0.1, "--threshold", "-t", help="Drift threshold (fraction)"),
) -> None:
    """Compare two datasets for drift detection."""
    from pathlib import Path as P

    import pandas as pd
    from rich.console import Console

    from equity_lake.validation.profiling import DataProfiler

    console = Console()
    profiler = DataProfiler()

    df_current = pd.read_parquet(P(current))
    df_baseline = pd.read_parquet(P(baseline))

    profile_current = profiler.profile(df_current, "current")
    profile_baseline = profiler.profile(df_baseline, "baseline")

    report = profiler.compare(profile_current, profile_baseline, threshold=threshold)

    if report.has_drift:
        console.print(f"[red]Drift detected in {len(report.columns)} column(s):[/red]")
        for col, metrics in report.columns.items():
            console.print(f"  {col}: mean {metrics['mean_baseline']:.4f} -> {metrics['mean_current']:.4f} ({metrics['pct_change']:.1%} change)")
    else:
        console.print("[green]No significant drift detected[/green]")

    if not report.has_drift:
        raise typer.Exit(0)
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
