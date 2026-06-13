from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, app


@app.command("backtest")
def backtest_cmd(
    strategy: Annotated[str, typer.Option("--strategy", "-s", help="Strategy name")] = "sma_crossover",
    tickers: Annotated[str, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = "AAPL,MSFT",
    start_date: Annotated[str, typer.Option("--start-date", help="Start date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    end_date: Annotated[str, typer.Option("--end-date", help="End date YYYY-MM-DD")] = ...,  # type: ignore[assignment]
    initial_cash: Annotated[float, typer.Option("--initial-cash", help="Initial capital")] = 100_000,
    walk_forward: Annotated[bool, typer.Option("--walk-forward", help="Walk-forward validation")] = False,
    output: Annotated[str | None, typer.Option("--output", "-o", help="Output JSON")] = None,
) -> None:
    """Backtest trading strategies."""
    from equity_lake.backtesting import VectorBacktestEngine
    from equity_lake.backtesting.strategy import (
        BBMeanReversionStrategy,
        CrossSectionalMomentumStrategy,
        SMACrossoverStrategy,
    )

    strategy_map = {
        "sma_crossover": SMACrossoverStrategy,
        "momentum": CrossSectionalMomentumStrategy,
        "mean_reversion": BBMeanReversionStrategy,
    }
    if strategy not in strategy_map:
        typer.secho(f"Unknown strategy: {strategy}. Available: {', '.join(strategy_map.keys())}", fg=typer.colors.RED)
        raise typer.Exit(1)

    strategy_inst = strategy_map[strategy](params={})  # type: ignore[abstract]
    eng = VectorBacktestEngine(
        strategy=strategy_inst,
        tickers=tickers.split(","),
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        initial_cash=initial_cash,
    )
    result = eng.run()
    typer.echo(result.summary())
    if output:
        Path(output).write_text(json.dumps(result.to_dict(), indent=2, default=str))


@app.command("query")
def query(
    query_name: Annotated[str | None, typer.Option("--query", "-q", help="Named query")] = None,
    db_path: Annotated[str, typer.Option("--db", help="DuckDB path")] = "equity_data.duckdb",
    date_str: Annotated[str | None, typer.Option("--date", help="Date filter")] = None,
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
    max_age_days: Annotated[int | None, typer.Option("--max-age-days", help="Max data age")] = None,
    null_threshold: Annotated[float | None, typer.Option("--null-threshold", help="Null % threshold")] = None,
    output_json: Annotated[str | None, typer.Option("--output-json", help="Save report")] = None,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Monitor pipeline health and data quality."""
    from equity_lake.monitoring.health import PipelineMonitor

    _init_logging(verbose)
    monitor_inst = PipelineMonitor(
        max_age_days=max_age_days or 2,
        null_threshold_pct=null_threshold or 5.0,
        verbose=verbose,
    )
    report = {"healthy": monitor_inst.run_health_check()}
    if output_json:
        Path(output_json).write_text(json.dumps(report, indent=2, default=str))
