from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, app
from equity_lake.core.paths import DUCKDB_DEFAULT_PATH, LOGS_DIR


@app.command("backtest")
def backtest_cmd(
    strategy: Annotated[str, typer.Option("--strategy", "-s", help="Strategy name (see equity report backtest)")] = "momentum",
    tickers: Annotated[str, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = "AAPL,MSFT",
    start_date: Annotated[str, typer.Option("--start-date", help="Start date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    end_date: Annotated[str, typer.Option("--end-date", help="End date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    initial_cash: Annotated[float, typer.Option("--initial-cash", help="Initial capital")] = 100_000,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Output JSON")] = None,
) -> None:
    """Backtest trading strategies (shares the strategy registry with ``equity report backtest``)."""
    from equity_lake.backtesting import VectorBacktestEngine
    from equity_lake.backtesting.arena import STRATEGY_REGISTRY

    if strategy not in STRATEGY_REGISTRY:
        typer.secho(f"Unknown strategy: {strategy}. Available: {', '.join(STRATEGY_REGISTRY)}", fg=typer.colors.RED)
        raise typer.Exit(1)

    strategy_inst = STRATEGY_REGISTRY[strategy]()
    eng = VectorBacktestEngine(
        strategy=strategy_inst,
        tickers=tickers.split(","),
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        initial_cash=initial_cash,
    )
    try:
        result = eng.run()
    except Exception as exc:  # noqa: BLE001 — surface a clean CLI error
        typer.secho(f"backtest failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
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
    from equity_lake.storage.duckdb import EquityDataDB

    _init_logging(verbose)
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


@app.command("monitor")
def monitor(
    max_age_days: Annotated[int | None, typer.Option("--max-age-days", help="Max data age (default: from settings)")] = None,
    null_threshold: Annotated[float | None, typer.Option("--null-threshold", help="Null % threshold (default: from settings)")] = None,
    output_json: Annotated[str | None, typer.Option("--output-json", help="Save full report. Defaults to logs/health-report.json")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Monitor pipeline health and data quality."""
    from equity_lake.core.config import get_settings
    from equity_lake.monitoring.health import PipelineMonitor

    _init_logging(verbose)
    # Resolve settings-backed defaults only when the CLI flag is omitted — matches
    # the legacy argparse main()'s deferred-settings behavior.
    settings = get_settings()
    monitor_inst = PipelineMonitor(
        max_age_days=max_age_days if max_age_days is not None else settings.monitoring.max_age_days,
        null_threshold_pct=null_threshold if null_threshold is not None else settings.monitoring.null_threshold_pct,
        verbose=verbose,
    )
    monitor_inst.run_health_check()
    # Always persist to the canonical location so dashboards can render it.
    # --output-json overrides the default logs/health-report.json path.
    output_path = Path(output_json) if output_json is not None else LOGS_DIR / "health-report.json"
    monitor_inst.save_report(output_path)
