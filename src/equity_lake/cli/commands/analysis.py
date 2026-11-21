from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, cast

import typer

from equity_lake.cli._app import _init_logging, _resolve_lake_dir, app
from equity_lake.core.paths import DUCKDB_DEFAULT_PATH, HEALTH_REPORT_FILENAME, LOGS_DIR


@app.command("backtest")
def backtest_cmd(
    strategy: Annotated[str, typer.Option("--strategy", "-s", help="Strategy name (see equity report backtest)")] = "momentum",
    tickers: Annotated[str, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = "AAPL,MSFT",
    start_date: Annotated[str, typer.Option("--start-date", help="Start date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    end_date: Annotated[str, typer.Option("--end-date", help="End date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    initial_cash: Annotated[float, typer.Option("--initial-cash", help="Initial capital")] = 100_000,
    adjust: Annotated[
        str, typer.Option("--adjust", help="Point-in-time split-only adjustment (default); 'none' is a legacy alias, not a raw-price bypass")
    ] = "splits",
    coverage_basis: Annotated[
        str,
        typer.Option(
            "--coverage-basis",
            help="Action-coverage basis: source_clock (strict point-in-time default) or vendor_events_no_clock (opt-in weak basis, ADR-0032)",
        ),
    ] = "source_clock",
    lake_dir: Annotated[
        str | None,
        typer.Option("--lake-dir", help="Explicit lake root such as data/lake_real (ADR-0030); default is the canonical lake"),
    ] = None,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Output JSON")] = None,
) -> None:
    """Backtest trading strategies (shares the strategy registry with ``equity report backtest``).

    Reads one lake root only, at exactly one action-coverage basis: the strict
    ``source_clock`` default, or the explicit ``vendor_events_no_clock`` opt-in
    (ADR-0032). A basis is never inferred from the data and two bases are never
    mixed in one run.
    """
    from equity_lake.backtesting.factory import build_backtest_engine
    from equity_lake.core.availability import COVERAGE_BASES, CoverageBasis

    if adjust == "total_return":
        typer.secho(
            "--adjust total_return is unsupported; only point-in-time split-only adjustment is implemented (use --adjust splits)", fg=typer.colors.RED
        )
        raise typer.Exit(1)
    if adjust not in ("none", "splits"):
        typer.secho(f"Unknown --adjust value: {adjust} (expected splits; none is a legacy split-only alias)", fg=typer.colors.RED)
        raise typer.Exit(1)
    if coverage_basis not in COVERAGE_BASES:
        typer.secho(
            f"Unknown --coverage-basis {coverage_basis!r}; expected one of {', '.join(COVERAGE_BASES)}.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    basis = cast("CoverageBasis", coverage_basis)
    root = _resolve_lake_dir(lake_dir)

    try:
        eng = build_backtest_engine(
            strategy=strategy,
            tickers=tickers.split(","),
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            initial_cash=initial_cash,
            adjust=cast("Literal['none', 'splits']", adjust),
            lake_dir=root,
            coverage_basis=basis,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    try:
        result = eng.run()
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"backtest failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    typer.echo("Price adjustment: split-only (corporate actions applied at read time; ADR-0015)")
    typer.echo(f"Action-coverage basis: {basis}")
    typer.echo(result.summary())
    if output:
        Path(output).write_text(json.dumps(result.to_dict(), indent=2, default=str))


@app.command("query")
def query(
    query_name: Annotated[str | None, typer.Option("--query", "-q", help="Named query")] = None,
    db_path: Annotated[str, typer.Option("--db", help="DuckDB path (default: data/equity_data.duckdb)")] = str(DUCKDB_DEFAULT_PATH),
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Query the data lake via DuckDB."""
    from equity_lake.storage.duckdb import EquityDataDB, QueryError

    _init_logging(verbose)
    try:
        with EquityDataDB(db_path=db_path) as db:
            if query_name:
                df = db.run_named_query(query_name)
                if not df.is_empty():
                    typer.echo(df)
                else:
                    typer.secho(f"No results for query: {query_name}", fg=typer.colors.YELLOW)
            else:
                results = db.run_all_queries()
                for name, df in results.items():
                    typer.echo(f"\n{'=' * 60}")
                    typer.echo(f"Query: {name}")
                    typer.echo(f"{'=' * 60}")
                    if not df.is_empty():
                        typer.echo(df)
                    else:
                        typer.secho("No results", fg=typer.colors.YELLOW)
    except QueryError as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc


@app.command("monitor")
def monitor(
    max_age_days: Annotated[int | None, typer.Option("--max-age-days", help="Max data age (default: from settings)")] = None,
    null_threshold: Annotated[float | None, typer.Option("--null-threshold", help="Null % threshold (default: from settings)")] = None,
    output_json: Annotated[str | None, typer.Option("--output-json", help="Save full report. Defaults to logs/health-report.json")] = None,
    lake_dir: Annotated[
        str | None,
        typer.Option("--lake-dir", help="Explicit lake root inside data/ (default: canonical data/lake); reported per root, never combined"),
    ] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Monitor pipeline health and data quality. Exits 1 when unhealthy."""
    from equity_lake.core.config import get_settings
    from equity_lake.monitoring.alerting import build_alerter
    from equity_lake.monitoring.health import CHECK_FAIL, CHECK_NOT_APPLICABLE, CHECK_PASS, PipelineMonitor

    _init_logging(verbose)
    # Resolve settings-backed defaults only when the CLI flag is omitted — matches
    # the legacy argparse main()'s deferred-settings behavior.
    settings = get_settings()
    monitor_inst = PipelineMonitor(
        max_age_days=max_age_days if max_age_days is not None else settings.monitoring.max_age_days,
        null_threshold_pct=null_threshold if null_threshold is not None else settings.monitoring.null_threshold_pct,
        market_max_age_days=settings.monitoring.market_max_age_days,
        table_max_age_days=settings.monitoring.table_max_age_days,
        verbose=verbose,
        alerter=build_alerter(settings.alerting.webhook_url),
        lake_dir=_resolve_lake_dir(lake_dir),
    )
    try:
        all_healthy = monitor_inst.run_health_check()

        # Presentation lives here: the monitor computes and dispatches alerts via
        # its alerter (console alerts print exactly once through ConsoleAlerter).
        typer.echo("\n" + "=" * 70)
        typer.echo("PIPELINE HEALTH MONITOR")
        typer.echo("=" * 70 + "\n")
        labels = {
            CHECK_PASS: "\u2705 PASS",
            CHECK_FAIL: "\u274c FAIL",
            CHECK_NOT_APPLICABLE: "\u2796 N/A",
        }
        for check_name, healthy in monitor_inst.check_results.items():
            status_code = monitor_inst.check_status.get(check_name, CHECK_PASS if healthy else CHECK_FAIL)
            typer.echo(f"{labels.get(status_code, labels[CHECK_FAIL]):<12} {check_name}")
            note = monitor_inst.check_notes.get(check_name)
            if note:
                typer.echo(f"{'':<12} {note}")
        typer.echo("\n" + "=" * 70)
        if all_healthy:
            typer.echo("\u2705 Pipeline is HEALTHY")
        else:
            typer.echo("\u26a0\ufe0f  Pipeline has ISSUES")
        typer.echo("=" * 70 + "\n")

        # Always persist the canonical report, including when unhealthy. Schedulers
        # use the exit code, while dashboards need the latest result either way.
        output_path = Path(output_json) if output_json is not None else LOGS_DIR / HEALTH_REPORT_FILENAME
        monitor_inst.save_report(output_path)
    finally:
        monitor_inst.close()

    # Close the monitor before exposing an unhealthy result to cron/schedulers.
    if not all_healthy:
        raise typer.Exit(1)
