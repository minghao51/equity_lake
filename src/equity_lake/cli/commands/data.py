"""Data management commands (ingest, backfill, sync, delta operations)."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _parse_markets, _resolve_run_date, app
from equity_lake.core.paths import LAKE_DIR


def _resolve_dataset_paths(markets: list[str], lake_dir: Path | None = None) -> list[str]:
    """Resolve maintenance identifiers to safe, effective paths under the lake root.

    Accept short and canonical price-market names, mapped dataset/table routes,
    full lake-relative medallion paths, and other safe lake-relative paths.
    Unknown relative paths pass through for compatibility; absolute paths,
    traversal components, and symlink targets outside the configured lake are
    rejected. In-root symlinks are normalized to their effective route.
    """
    from equity_lake.core.paths import PRICE_MARKETS, SHORT_TO_LONG, canonical_market
    from equity_lake.ingestion.types import MARKET_DIR_MAP

    try:
        lake_root = (lake_dir or LAKE_DIR).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ValueError("Cannot safely resolve the configured lake root") from exc

    if not markets:
        raise ValueError("At least one Delta-maintenance target is required")

    resolved: list[str] = []
    for raw_market in markets:
        market = raw_market.strip()
        if not market:
            raise ValueError("Delta-maintenance target identifiers must not be empty")
        requested_path = Path(market)
        if requested_path.is_absolute():
            raise ValueError(f"Delta-maintenance target {market!r} must be lake-relative, not absolute")
        if ".." in requested_path.parts:
            raise ValueError(f"Delta-maintenance target {market!r} must not contain a '..' path component")

        route = MARKET_DIR_MAP[canonical_market(market)] if market in PRICE_MARKETS or market in SHORT_TO_LONG else MARKET_DIR_MAP.get(market, market)

        route_path = Path(route)
        if route_path.is_absolute() or ".." in route_path.parts:
            raise ValueError(f"Resolved Delta-maintenance route for {market!r} is not lake-relative")
        try:
            target_path = (lake_root / route_path).resolve()
        except (OSError, RuntimeError, ValueError) as exc:
            raise ValueError(f"Cannot safely resolve Delta-maintenance target {market!r}") from exc
        if not target_path.is_relative_to(lake_root):
            raise ValueError(f"Delta-maintenance target {market!r} resolves outside the configured lake root")
        resolved.append(target_path.relative_to(lake_root).as_posix())
    return resolved


def _resolve_maintenance_targets(markets: str) -> list[str]:
    """Validate and resolve the complete comma-separated CLI target list."""
    try:
        return _resolve_dataset_paths(markets.split(","))
    except ValueError as exc:
        typer.secho(f"Invalid Delta-maintenance target list: {exc}", fg=typer.colors.RED)
        raise typer.Exit(1) from exc


@app.command("ingest")
def ingest(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date YYYY-MM-DD")] = None,
    markets: Annotated[
        str | None,
        typer.Option("--markets", "-m", help="Comma-separated markets (long keys like us_equity; short aliases like us accepted)"),
    ] = None,
    tickers: Annotated[
        str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers (omit for all configured; an explicit empty value is rejected)")
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Simulate without writes (default: off)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Ingest daily equity market data."""
    from equity_lake.core.config import get_settings
    from equity_lake.ingestion.orchestrator import run_daily_ingestion
    from equity_lake.ingestion.types import REQUIRED_PRICE_MARKETS, SourceOutcome, SourceStatus

    _init_logging(verbose)
    market_list = _parse_markets(markets) or list(get_settings().ingestion.default_markets)
    trading_date = _resolve_run_date(date_str, 1, market_list)
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
        # Deduplicate deterministically for consistent scope reporting.
        ticker_list = list(dict.fromkeys(ticker_list))
    results = run_daily_ingestion(
        trading_date=trading_date,
        markets=market_list,
        dry_run=dry_run,
        parallel=True,
        explicit_tickers=ticker_list,
    )

    # Surface per-market outcomes; mirror the pipeline command's failure contract:
    # required price markets failing (or missing from the results) exits non-zero,
    # while optional-enrichment degradation stays a warning only.
    for market, outcome in results.items():
        status_line = f"  {market}: {outcome.status.value}"
        if outcome.error:
            status_line += f" ({outcome.error})"
        typer.echo(status_line)

    required_failures = sorted(
        m for m in market_list if m in REQUIRED_PRICE_MARKETS and not results.get(m, SourceOutcome(SourceStatus.FAILED)).succeeded
    )
    if required_failures:
        typer.secho(f"Ingestion failed for required markets: {', '.join(required_failures)}", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("backfill")
def backfill(
    start: Annotated[str | None, typer.Option("--start", help="Start date")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End date")] = None,
    days_back: Annotated[int | None, typer.Option("--days-back", help="Calendar days back")] = None,
    markets: Annotated[
        str,
        typer.Option("--markets", "-m", help="Comma-separated markets (long keys like us_equity; short aliases like us accepted)"),
    ] = "us_equity,cn_ashare,hk_sg_equity",
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="No writes (default: off)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Backfill historical data."""
    from datetime import timedelta

    from equity_lake.core.config import TickerConfig, get_settings
    from equity_lake.ingestion.backfill import backfill_date_range

    _init_logging(verbose)
    yesterday = date.today() - timedelta(days=1)
    end_date = date.fromisoformat(end) if end else yesterday
    if days_back:
        start_date = end_date - timedelta(days=days_back)
    elif start:
        start_date = date.fromisoformat(start)
    else:
        typer.secho("Must specify --days-back or --start", fg=typer.colors.RED)
        raise typer.Exit(1)

    settings = get_settings()
    config_path = Path(settings.ingestion.ticker_config_path)
    ticker_config = TickerConfig(config_path=config_path)

    market_list = _parse_markets(markets) or []
    result = backfill_date_range(
        start_date=start_date,
        end_date=end_date,
        markets=market_list,
        ticker_config=ticker_config,
        dry_run=dry_run,
        authorized=True,
    )

    typer.echo(
        f"Backfill complete: {result.total_dates} market-dates succeeded "
        f"({result.succeeded}/{result.market_date_attempts} attempts over {result.trading_dates} date(s), "
        f"{result.dates_recovered} date(s) fully recovered, {result.failed} failed) across {market_list}"
    )


@app.command("auto-backfill")
def auto_backfill(
    days_back: Annotated[int, typer.Option("--days-back", help="Days to scan for gaps")] = 90,
    markets: Annotated[
        str | None,
        typer.Option("--markets", "-m", help="Comma-separated markets (long keys like us_equity; short aliases like us accepted)"),
    ] = None,
    max_gap_days: Annotated[int, typer.Option("--max-gap-days", help="Skip gaps larger than this")] = 30,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Show gaps without filling")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Auto-detect and fill data gaps across markets."""
    from equity_lake.ingestion.auto_backfill import find_and_fill_gaps

    _init_logging(verbose)
    market_list = _parse_markets(markets)
    results = find_and_fill_gaps(
        days_back=days_back,
        markets=market_list,
        dry_run=dry_run,
        max_gap_days=max_gap_days,
    )
    if not results:
        typer.echo("No gaps found.")
        return
    for mkt, count in results.items():
        label = "would fill" if dry_run else "filled"
        typer.echo(f"  {mkt}: {label} {count} dates")
    typer.echo(f"Auto-backfill {'(dry run) ' if dry_run else ''}complete.")


@app.command("label-repair")
def label_repair(
    start: Annotated[str | None, typer.Option("--start", help="Start date YYYY-MM-DD (default: 30 days before --end)")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End date YYYY-MM-DD (default: yesterday)")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated ticker subset (default: every ticker)")] = None,
    repair: Annotated[
        bool, typer.Option("--repair/--report-only", help="Authorize the bounded label-only update (default: report only, no writes)")
    ] = False,
    authorize_broad: Annotated[
        bool, typer.Option("--authorize-broad", help="Owner authorization (D09) required when finalizable rows exceed --max-keys")
    ] = False,
    max_keys: Annotated[int, typer.Option("--max-keys", help="Bounded repair limit; a broader scope requires --authorize-broad")] = 1000,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Report unlabeled Gold rows; with --repair apply a bounded label-only fix.

    Report-only by default: the command inventories finalizable rows (the next
    observed session's bar exists) and pending rows (no next-session bar yet),
    then stops. ``--repair`` authorizes a matched-only update of the label
    columns for the reported scope; a scope larger than ``--max-keys`` requires
    the separate owner authorization ``--authorize-broad`` (D09). No broad or
    automatic repair is ever performed.
    """
    from datetime import timedelta

    from equity_lake.features.labels import apply_label_repair, scan_label_repair

    _init_logging(verbose)
    end_date = date.fromisoformat(end) if end else date.today() - timedelta(days=1)
    start_date = date.fromisoformat(start) if start else end_date - timedelta(days=30)
    if start_date > end_date:
        typer.secho("--start must be on or before --end.", fg=typer.colors.RED)
        raise typer.Exit(1)
    ticker_list = _parse_comma_list(tickers)

    plan = scan_label_repair(start=start_date, end=end_date, tickers=ticker_list)
    scope = ", ".join(ticker_list) if ticker_list else "all tickers"
    typer.echo(f"Label repair scope {start_date} .. {end_date} ({scope})")
    typer.echo(f"  finalizable (next-session bar present): {plan.finalizable_count}")
    typer.echo(f"  pending (no next-session bar yet): {plan.pending_count}")
    typer.echo(f"  availability-unknown (target present, availability unknown; strict-excluded from training): {plan.availability_unknown_count}")
    for row in plan.finalizable.head(20).iter_rows(named=True):
        typer.echo(f"    finalizable {row['ticker']} {row['date']} -> available {row['label_available_date']}")
    for row in plan.pending.head(20).iter_rows(named=True):
        typer.echo(f"    pending     {row['ticker']} {row['date']}")
    for row in plan.availability_unknown.head(20).iter_rows(named=True):
        typer.echo(f"    avail-unknown {row['ticker']} {row['date']}")

    if not repair:
        typer.echo("Report-only; re-run with --repair to apply the bounded label-only update.")
        return
    if plan.finalizable_count == 0:
        typer.secho("Nothing to repair.", fg=typer.colors.GREEN)
        return
    if plan.finalizable_count > max_keys and not authorize_broad:
        typer.secho(
            f"Refusing repair: {plan.finalizable_count} finalizable rows exceed --max-keys {max_keys}. "
            "Broad historical repair requires separate owner authorization (D09); re-run with --authorize-broad "
            "after inventorying affected keys and model compatibility.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(1)

    updated = apply_label_repair(plan)
    typer.secho(f"Repaired {updated} label row(s); {plan.pending_count} remain pending.", fg=typer.colors.GREEN)


@app.command("sync")
def sync(
    bucket: Annotated[
        str | None,
        typer.Option(
            "--bucket",
            "-b",
            help=(
                "S3 bucket root URL (e.g. s3://my-bucket). The remote tree must "
                "mirror the local numbered medallion layout WITHOUT a data/lake/ "
                "prefix — each market is pulled from "
                "<bucket>/01_bronze/market_data/<market_dir>."
            ),
        ),
    ] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Download workers")] = 16,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Simulate")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Sync data lake to S3.

    The remote bucket mirrors the local numbered medallion layout. Each equity
    market is synced separately from ``<bucket>/<medallion_path>`` to its
    canonical local directory, so s5cmd can parallelize per market and partial
    failures don't abort the rest.
    """
    from equity_lake.core.paths import PRICE_MARKETS
    from equity_lake.ingestion.types import MARKET_DIR_MAP
    from equity_lake.storage.s3_sync import S3Syncer

    _init_logging(verbose)
    bucket_url = bucket or os.environ.get("S3_BUCKET", "")
    if not bucket_url:
        typer.secho("No S3 bucket specified. Use --bucket or set S3_BUCKET.", fg=typer.colors.RED)
        raise typer.Exit(1)

    bucket_root = bucket_url.rstrip("/")
    failed: list[str] = []
    # Equity market-data directories (Bronze layer), straight from the registry.
    for market in PRICE_MARKETS:
        market_dir = MARKET_DIR_MAP[market]
        # Derive the per-market remote source from the canonical medallion path
        # so each market pulls its own tree, not the same bucket root repeatedly.
        source_url = f"{bucket_root}/{market_dir}"
        target_dir = LAKE_DIR / market_dir
        typer.secho(f"Syncing {market}: {source_url} -> {target_dir} ...", fg=typer.colors.CYAN)
        syncer = S3Syncer(bucket=source_url, target_dir=target_dir, workers=workers, dry_run=dry_run)
        try:
            syncer.sync()
        except Exception as exc:
            typer.secho(f"  {market} FAILED: {exc}", fg=typer.colors.RED)
            failed.append(market)

    if failed:
        typer.secho(f"\nSync complete with failures: {', '.join(failed)}", fg=typer.colors.YELLOW)
        raise typer.Exit(1)
    typer.secho("\nAll markets synced.", fg=typer.colors.GREEN)


@app.command("macro")
def macro(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    indicators: Annotated[str | None, typer.Option("--indicators", help="Comma-separated indicators")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Simulate")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch macro indicators."""
    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.ingestion.writers import upsert_dataset
    from equity_lake.sources.macro import MacroDataPipeline

    _init_logging(verbose)
    trading_date = resolve_trading_date(date_str)

    if dry_run:
        import structlog

        structlog.get_logger().info("[DRY RUN MODE] No files will be written")

    pipeline = MacroDataPipeline()

    if indicators:
        indicator_list = [i.strip() for i in indicators.split(",")]
        pipeline.indicators = [f for f in pipeline.indicators if f.indicator_name in indicator_list]

    df = pipeline.fetch_with_fallback(trading_date)

    if df.is_empty():
        typer.secho("No macro data fetched", fg=typer.colors.YELLOW)
        raise typer.Exit(1)

    success = upsert_dataset(df, "01_bronze/macro", trading_date, dry_run=dry_run)

    if success:
        typer.secho("Macro indicators fetch completed successfully", fg=typer.colors.GREEN)
    else:
        typer.secho("Macro indicators fetch failed", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("delta-vacuum")
def delta_vacuum(
    markets: Annotated[str, typer.Option("--markets", "-m", help="Comma-separated markets")] = "us_equity,cn_ashare,hk_sg_equity",
    retention_hours: Annotated[int, typer.Option("--retention-hours", help="Retention window in hours")] = 168,
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Preview only (default: on; disable with --no-dry-run)")] = True,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Remove stale files from Delta Lake tables."""
    from equity_lake.storage.delta import vacuum_delta

    _init_logging(verbose)
    dataset_paths = _resolve_maintenance_targets(markets)
    for market in dataset_paths:
        files = vacuum_delta(market, retention_hours=retention_hours, dry_run=dry_run)
        label = "would remove" if dry_run else "removed"
        typer.echo(f"  {market}: {label} {len(files)} stale files")
    if dry_run:
        typer.echo("Dry run — no files deleted. Use --no-dry-run to execute.")


@app.command("delta-compact")
def delta_compact(
    markets: Annotated[str, typer.Option("--markets", "-m", help="Comma-separated markets")] = "us_equity,cn_ashare,hk_sg_equity",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Compact small files in Delta Lake tables."""
    from equity_lake.storage.delta import compact_delta

    _init_logging(verbose)
    dataset_paths = _resolve_maintenance_targets(markets)
    for market in dataset_paths:
        metrics = compact_delta(market)
        if metrics:
            typer.echo(f"  {market}: added={metrics.get('numFilesAdded', 0)} removed={metrics.get('numFilesRemoved', 0)}")
        else:
            typer.echo(f"  {market}: skipped (not a Delta table)")
    typer.echo("Compaction complete.")


@app.command("delta-migrate")
def delta_migrate(
    markets: Annotated[str, typer.Option("--markets", "-m", help="Comma-separated markets")] = (
        "us_equity,cn_ashare,hk_sg_equity,jpx_equity,krx_equity"
    ),
    dry_run: Annotated[bool, typer.Option("--dry-run/--no-dry-run", help="Preview only")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Migrate Hive-partitioned Parquet to Delta Lake format."""
    from equity_lake.storage.delta import migrate_parquet_to_delta

    _init_logging(verbose)
    dataset_paths = _resolve_maintenance_targets(markets)
    for market in dataset_paths:
        ok = migrate_parquet_to_delta(market, dry_run=dry_run)
        status = "OK" if ok else "SKIPPED/FAILED"
        typer.echo(f"  {market}: {status}")
    typer.echo(f"Migration {'(dry run) ' if dry_run else ''}complete.")
