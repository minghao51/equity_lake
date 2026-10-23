from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer
from click.core import ParameterSource

from equity_lake.cli._app import bootstrap_app, config_app, validate_app


@config_app.command("show")
def config_show() -> None:
    """Show configuration with sensitive values redacted."""
    from equity_lake.core.config import load_settings
    from equity_lake.core.logging import redact_secrets

    settings = load_settings()
    typer.echo(json.dumps(redact_secrets(settings.model_dump()), indent=2))


@config_app.command("get")
def config_get(
    path: Annotated[str, typer.Argument(help="Dotted config path (e.g. storage.data_dir)")],
) -> None:
    """Get a config value, redacting sensitive keys and nested values."""
    from equity_lake.core.config import load_settings
    from equity_lake.core.logging import redact_secrets

    settings = load_settings()
    data = redact_secrets(settings.model_dump())
    current: Any = data
    for segment in path.split("."):
        if not isinstance(current, dict):
            raise typer.BadParameter(f"Invalid path: {path}")
        if segment not in current:
            raise typer.BadParameter(f"Key not found: {segment}")
        current = current[segment]
    typer.echo(json.dumps(current, indent=2))


@config_app.command("validate")
def config_validate(
    ctx: typer.Context,
    tickers: Annotated[str, typer.Option("--tickers", "-t", help="Path to tickers.yaml (always validated)")] = "config/tickers.yaml",
    watchlist: Annotated[
        str, typer.Option("--watchlist", "-w", help="Additional watchlist YAML (not selected by default)")
    ] = "config/watchlist.yaml",
    signals: Annotated[str, typer.Option("--signals", "-s", help="Additional signals YAML (not selected by default)")] = "config/signals.yaml",
    all_configs: Annotated[bool, typer.Option("--all", help="Also validate watchlist and signals; default scope is tickers only")] = False,
) -> None:
    """Validate tickers.yaml by default; explicitly select other YAML files or use --all."""
    from pathlib import Path

    from equity_lake.config.validators import (
        validate_signals,
        validate_tickers,
        validate_watchlist,
    )

    tickers_path = Path(tickers)
    watchlist_path = Path(watchlist)
    signals_path = Path(signals)
    all_errors: list[str] = []
    has_warnings = False

    typer.echo(f"Validating {tickers_path}...")
    errors = validate_tickers(tickers_path)
    all_errors.extend(errors)
    ticker_warnings = getattr(errors, "warnings", [])
    has_warnings = bool(ticker_warnings)
    status = "OK" if not errors else f"{len(errors)} error(s)"
    if ticker_warnings and not errors:
        status += f" ({len(ticker_warnings)} warning(s))"
    typer.echo(f"  {status}")
    for warning in ticker_warnings:
        typer.secho(f"  Warning: {warning}", fg=typer.colors.YELLOW)

    selected_configs: list[tuple[str, Path, Any]] = []
    if all_configs or ctx.get_parameter_source("watchlist") is ParameterSource.COMMANDLINE:
        selected_configs.append(("watchlist", watchlist_path, validate_watchlist))
    if all_configs or ctx.get_parameter_source("signals") is ParameterSource.COMMANDLINE:
        selected_configs.append(("signals", signals_path, validate_signals))

    for _name, path, validator in selected_configs:
        typer.echo(f"Validating {path}...")
        errors = validator(path)
        all_errors.extend(errors)
        typer.echo(f"  {'OK' if not errors else f'{len(errors)} error(s)'}")

    if all_errors:
        typer.secho("\nValidation FAILED:", fg=typer.colors.RED)
        for error in all_errors:
            typer.secho(f"  {error}", fg=typer.colors.RED)
        raise typer.Exit(1)

    summary = "\nAll validations passed with warnings." if has_warnings else "\nAll validations passed!"
    typer.secho(summary, fg=typer.colors.YELLOW if has_warnings else typer.colors.GREEN)


@config_app.command("export")
def config_export(
    output: Annotated[str | None, typer.Argument(help="Output file path")] = None,
) -> None:
    """Export redacted configuration to a file or stdout."""
    from equity_lake.core.config import load_settings
    from equity_lake.core.logging import redact_secrets

    settings = load_settings()
    rendered = json.dumps(redact_secrets(settings.model_dump()), indent=2)
    if output:
        Path(output).write_text(rendered, encoding="utf-8")
        typer.echo(f"exported:{output}")
    else:
        typer.echo(rendered)


def _load_parquet(path: str) -> Any:
    import pandas as pd

    target = Path(path)
    if target.is_dir():
        files = list(target.rglob("*.parquet"))
        if not files:
            typer.secho(f"No parquet files found in {path}", fg=typer.colors.RED)
            raise typer.Exit(1)
        return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    return pd.read_parquet(target)


@validate_app.command("check")
def validate_check(
    path: Annotated[str, typer.Argument(help="Path to parquet file or directory")],
    data_type: Annotated[str, typer.Option("--type", "-t", help="price, macro, news")] = "price",
    strict: Annotated[bool, typer.Option("--strict", help="Fail on warnings")] = False,
) -> None:
    """Validate data against schema and produce quality metrics."""
    from rich.console import Console
    from rich.table import Table

    from equity_lake.validation.pipeline import ValidationPipeline

    console = Console()
    df = _load_parquet(path)

    vp = ValidationPipeline(strict=strict)
    result = vp.validate(df, data_type=data_type, name="check")

    table = Table(title="Validation Result")
    table.add_column("Check", style="cyan")
    table.add_column("Status")
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
    path: Annotated[str, typer.Argument(help="Path to parquet file or directory")],
    name: Annotated[str, typer.Option("--name", "-n", help="Profile name")],
) -> None:
    """Profile a dataset and display quality metrics."""
    from rich.console import Console
    from rich.table import Table

    from equity_lake.validation.profiling import DataProfiler

    console = Console()
    df = _load_parquet(path)

    profiler = DataProfiler()
    profile = profiler.profile(df, name)
    metrics = profiler.get_quality_metrics(profile)

    table = Table(title=f"Profile: {name}")
    table.add_column("Column", style="cyan")
    table.add_column("Completeness")
    table.add_column("Null Count")
    table.add_column("Mean")

    for col_name, col_metrics in metrics.items():
        table.add_row(
            col_name,
            f"{col_metrics.get('completeness', 0):.2%}",
            str(col_metrics.get("null_count", 0)),
            f"{col_metrics.get('mean', 'N/A')}",
        )

    console.print(table)
    console.print(f"\nTotal rows: {len(df)}, Columns: {len(df.columns)}")


@validate_app.command("drift")
def validate_drift(
    current: Annotated[str, typer.Argument(help="Path to current data")],
    baseline: Annotated[str, typer.Argument(help="Path to baseline data")],
    threshold: Annotated[float, typer.Option("--threshold", "-t", help="Drift threshold")] = 0.1,
) -> None:
    """Compare two datasets for drift detection."""
    from rich.console import Console

    from equity_lake.validation.profiling import DataProfiler

    console = Console()
    profiler = DataProfiler()

    df_current = _load_parquet(current)
    df_baseline = _load_parquet(baseline)

    profile_current = profiler.profile(df_current, "current")
    profile_baseline = profiler.profile(df_baseline, "baseline")

    report = profiler.compare(profile_current, profile_baseline, threshold=threshold)

    if report.has_drift:
        console.print(f"[red]Drift detected in {len(report.columns)} column(s):[/red]")
        for col, metrics in report.columns.items():
            console.print(f"  {col}: {metrics['pct_change']:.1%} change")
        raise typer.Exit(1)
    else:
        console.print("[green]No significant drift detected[/]")


@bootstrap_app.command("sample")
def bootstrap_sample(
    days: Annotated[int, typer.Option("--days", "-d", help="Number of trading days")] = 30,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    output_dir: Annotated[str | None, typer.Option("--output-dir", "-o", help="Output directory")] = None,
    seed: Annotated[int, typer.Option("--seed", "-s", help="Random seed")] = 42,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Verbose logging")] = False,
) -> None:
    """Generate mock Parquet data for testing."""
    from equity_lake.cli.bootstrap import cmd_sample

    cmd_sample(
        days=days,
        tickers=tickers,
        output_dir=output_dir,
        seed=seed,
        verbose=verbose,
    )
