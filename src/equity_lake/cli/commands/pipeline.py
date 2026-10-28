"""Pipeline orchestration commands."""

from __future__ import annotations

from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _parse_markets, _resolve_run_date, app
from equity_lake.core.paths import LOGS_DIR, PIPELINE_RESULTS_NAME_PREFIX


def _pipeline_succeeded(results: dict[str, object]) -> bool:
    """Return False only for required stage failures.

    Optional enrichment sub-stages (``bronze_to_silver``/``sec_to_silver``) are
    nested inside ``results["ingestion"]`` and never surface as top-level keys.
    """
    for stage_name, stage_result in results.items():
        if not isinstance(stage_result, dict) or stage_result.get("success") is not False:
            continue
        if stage_name == "ml" and stage_result.get("reason") == "feature stage failed":
            continue
        return False
    return True


@app.command("pipeline")
def pipeline(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date YYYY-MM-DD")] = None,
    days_back: Annotated[int, typer.Option("--days-back", help="Days back")] = 1,
    markets: Annotated[
        str | None,
        typer.Option("--markets", "-m", help="Comma-separated markets (long keys like us_equity; short aliases like us accepted)"),
    ] = None,
    tickers: Annotated[
        str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers (omit for all configured; an explicit empty value is rejected)")
    ] = None,
    skip_ingestion: Annotated[bool, typer.Option("--skip-ingestion", help="Skip Stage 1")] = False,
    skip_features: Annotated[bool, typer.Option("--skip-features", help="Skip Stage 2")] = False,
    skip_ml: Annotated[bool, typer.Option("--skip-ml", help="Skip Stage 3")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Simulate")] = False,
    allow_history_backfill: Annotated[bool, typer.Option("--allow-history-backfill", help="Authorize a 120-day feature-history recovery")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
    save_results: Annotated[
        bool,
        typer.Option("--save-results", help="Save JSON results to logs/pipeline_results_<date>.json (skipped under --dry-run)"),
    ] = False,
) -> None:
    """Run the full EOD pipeline (ingest -> features -> ML)."""
    import json

    from equity_lake.pipeline import execute_eod_pipeline

    _init_logging(verbose)
    market_list = _parse_markets(markets)
    ticker_list = _parse_comma_list(tickers)
    # D02: an explicitly provided but empty ticker scope is rejected, never
    # silently broadened into the configured universe.
    if tickers is not None and not ticker_list:
        typer.secho(
            "--tickers was provided but resolved to an empty scope; omit --tickers to use the configured "
            "scope (an explicit empty scope is never broadened).",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    if ticker_list:
        # Deduplicate deterministically for consistent scope/backfill reporting.
        ticker_list = list(dict.fromkeys(ticker_list))
    trading_date = _resolve_run_date(date_str, days_back, market_list)

    results = execute_eod_pipeline(
        trading_date=trading_date,
        markets=market_list,
        tickers=ticker_list,
        dry_run=dry_run,
        skip_ingestion=skip_ingestion,
        skip_features=skip_features,
        skip_ml=skip_ml,
        explicit_tickers=ticker_list,
        allow_history_backfill=allow_history_backfill,
    )

    if save_results:
        if dry_run:
            typer.echo("--save-results skipped: dry-run persists nothing.")
        else:
            # The dashboard exporter reads PIPELINE_RESULTS_GLOB from LOGS_DIR —
            # writer and reader share the core/paths.py artifact-path contract;
            # trading_date is a resolved date, so the name is always YYYY-MM-DD.
            output_path = LOGS_DIR / f"{PIPELINE_RESULTS_NAME_PREFIX}{trading_date.isoformat()}.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(results, indent=2, default=str))
            typer.echo(f"Results saved to {output_path}")

    if not _pipeline_succeeded(results):
        raise typer.Exit(1)
