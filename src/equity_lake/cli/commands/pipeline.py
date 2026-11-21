"""Pipeline orchestration commands."""

from __future__ import annotations

from datetime import date
from typing import Annotated, cast

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _parse_markets, _resolve_lake_dir, _resolve_run_date, app
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


@app.command("features")
def features(
    tickers: Annotated[
        str,
        typer.Option("--tickers", "-t", help="Comma-separated tickers; an explicit scope is required (there is no configured default)"),
    ],
    start: Annotated[str, typer.Option("--start", help="First output session YYYY-MM-DD")],
    end: Annotated[str, typer.Option("--end", help="Last output session YYYY-MM-DD")],
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
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Compute and validate the scoped run without writing Gold rows"),
    ] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Run a scoped feature (Gold) build for explicit tickers and one window.

    Reads one lake root only: the price markets and the corporate-action store of
    the selected root. Action inputs are scoped per instrument, so a subset read of
    a multi-ticker action store is coherent; the run refuses instead of widening
    the resolver or clamping the window.
    """
    from equity_lake.core.availability import COVERAGE_BASES, AvailabilityContractError, CoverageBasis
    from equity_lake.core.config import TickerConfig
    from equity_lake.core.instruments import InstrumentIdentityError, LegacyIdentityUnavailableError
    from equity_lake.core.paths import price_markets_for_root
    from equity_lake.features import NoFeatureHistoryError, run_feature_job, scope_action_inputs
    from equity_lake.pipeline import resolve_explicit_instruments

    _init_logging(verbose)
    ticker_list = _parse_comma_list(tickers)
    if not ticker_list:
        typer.secho(
            "--tickers is required and must name at least one ticker; a feature run is never widened to the configured universe.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)
    ticker_list = list(dict.fromkeys(ticker_list))
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        typer.secho(f"--start/--end must be YYYY-MM-DD dates: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    if end_date < start_date:
        typer.secho(f"--end ({end_date}) must not precede --start ({start_date}).", fg=typer.colors.RED)
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
        instruments = resolve_explicit_instruments(list(price_markets_for_root(root)), ticker_list, TickerConfig())
    except (InstrumentIdentityError, LegacyIdentityUnavailableError) as exc:
        typer.secho(f"Feature scope refused: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    diagnostics: dict[str, object] = {}
    try:
        action_inputs = scope_action_inputs(
            instruments=instruments,
            start_date=start_date,
            end_date=end_date,
            lake_dir=root,
            coverage_basis=basis,
        )
        frame = run_feature_job(
            tickers=[instrument.symbol for instrument in instruments],
            instruments=instruments,
            output_start_date=start_date,
            output_end_date=end_date,
            compute_target=True,
            diagnostics=diagnostics,
            coverage_basis=basis,
            lake_dir=root,
            action_inputs=action_inputs,
            dry_run=dry_run,
        )
    except AvailabilityContractError as exc:
        typer.secho(f"Feature run refused: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc
    except NoFeatureHistoryError as exc:
        typer.secho(f"Feature run produced no rows for the requested window: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    missing = diagnostics.get("missing", {})
    produced = diagnostics.get("produced", [])
    mode = "dry run, nothing written" if dry_run else "written to 03_gold/features"
    typer.echo(
        f"{frame.height} feature rows for {len(instruments)} instrument(s), {start_date}..{end_date}, "
        f"basis {basis} ({mode}); produced={produced} missing={missing}"
    )
