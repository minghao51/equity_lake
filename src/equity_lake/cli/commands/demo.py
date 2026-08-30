"""``equity demo`` commands — Strategy Lab showcase lake seeding."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, demo_app


def _targets_production_lake(lake: str) -> bool:
    """True when ``--lake`` resolves to the canonical lake (or under it)."""
    from equity_lake.devtools.seed_demo import _targets_production_lake as _seed_targets

    # Delegate so the CLI prompt gate and the seed_demo ValueError guard use
    # the exact same comparison (case-insensitive-filesystem safe).
    return _seed_targets(Path(lake).expanduser())


@demo_app.command("seed")
def demo_seed(
    years: Annotated[float, typer.Option("--years", help="Years of history to seed")] = 5.0,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers (default: demo group)")] = None,
    real: Annotated[bool, typer.Option("--real", help="Attempt live yfinance fetch (falls back to synthetic)")] = False,
    seed: Annotated[int, typer.Option("--seed", help="Synthetic RNG seed")] = 42,
    lake: Annotated[
        str | None,
        typer.Option(
            "--lake",
            help=(
                "Target lake root (default: data/sample). Pointing this at data/lake "
                "OVERWRITES canonical bronze and requires confirmation or --overwrite-production-lake."
            ),
        ),
    ] = None,
    overwrite_production_lake: Annotated[
        bool,
        typer.Option(
            "--overwrite-production-lake",
            help="Skip the confirmation prompt for --lake data/lake (script-friendly; the overwrite is still logged)",
        ),
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Preview the seed summary without writing anything")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Seed a demo US universe (synthetic by default, offline-safe) into the sample lake."""
    from equity_lake.core.paths import LAKE_DIR
    from equity_lake.devtools.seed_demo import US_EQUITY_MARKET, seed_demo

    _init_logging(verbose)

    target: Path | None = None
    production_target = False
    if lake is not None:
        target = Path(lake).expanduser()
        production_target = _targets_production_lake(lake)
        if production_target and not overwrite_production_lake:
            typer.secho(
                f"WARNING: --lake {target} overwrites the canonical bronze table {LAKE_DIR / US_EQUITY_MARKET} (mode=overwrite).",
                fg=typer.colors.YELLOW,
            )
            typer.confirm("Continue?", abort=True)
    elif overwrite_production_lake:
        typer.secho("--overwrite-production-lake requires --lake; nothing to authorize.", fg=typer.colors.RED)
        raise typer.Exit(1)

    try:
        summary = seed_demo(
            years=years,
            tickers=_parse_comma_list(tickers),
            real=real,
            seed=seed,
            verbose=verbose,
            lake_dir=target,
            overwrite_production_lake=production_target or overwrite_production_lake,
            dry_run=dry_run,
        )
    except ValueError as exc:
        typer.secho(f"seed failed: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    prefix = "Would seed" if summary.get("dry_run") else "Seeded"
    typer.echo(f"\n{prefix} {summary['tickers']} tickers / {summary['rows']:,} rows / {summary['days']} days ({summary['source']}).")
    typer.echo(f"Lake: {summary['path']}")
    typer.echo("\nNext: equity arena run --tickers <...> --start-date YYYY-MM-DD --end-date YYYY-MM-DD")
