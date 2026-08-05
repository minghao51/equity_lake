"""``equity demo`` commands — Strategy Lab showcase lake seeding."""

from __future__ import annotations

from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, demo_app


@demo_app.command("seed")
def demo_seed(
    years: Annotated[float, typer.Option("--years", help="Years of history to seed")] = 5.0,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers (default: demo group)")] = None,
    real: Annotated[bool, typer.Option("--real", help="Attempt live yfinance fetch (falls back to synthetic)")] = False,
    seed: Annotated[int, typer.Option("--seed", help="Synthetic RNG seed")] = 42,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Seed the lake with a demo US universe (synthetic by default, offline-safe)."""
    from equity_lake.devtools.seed_demo import seed_demo

    _init_logging(verbose)
    try:
        summary = seed_demo(
            years=years,
            tickers=_parse_comma_list(tickers),
            real=real,
            seed=seed,
            verbose=verbose,
        )
    except ValueError as exc:
        typer.secho(f"seed failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.echo(f"\nSeeded {summary['tickers']} tickers / {summary['rows']:,} rows / {summary['days']} days ({summary['source']}).")
    typer.echo(f"Lake: {summary['path']}")
    typer.echo("\nNext: equity arena run --tickers <...> --start-date YYYY-MM-DD --end-date YYYY-MM-DD")
