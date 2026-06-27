from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from equity_lake.cli._app import _init_logging, _parse_comma_list, _resolve_date, app


@app.command("ingest")
def ingest(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date YYYY-MM-DD")] = None,
    markets: Annotated[str | None, typer.Option("--markets", "-m", help="Comma-separated markets")] = None,
    tickers: Annotated[str | None, typer.Option("--tickers", "-t", help="Comma-separated tickers")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate without writes")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Ingest daily equity market data."""
    from equity_lake.ingestion.orchestrator import run_daily_ingestion

    _init_logging(verbose)
    trading_date = _resolve_date(date_str)
    market_list = _parse_comma_list(markets)
    ticker_list = _parse_comma_list(tickers)
    run_daily_ingestion(
        trading_date=trading_date,
        markets=market_list or [],
        dry_run=dry_run,
        parallel=True,
        explicit_tickers=ticker_list,
    )


@app.command("backfill")
def backfill(
    start: Annotated[str | None, typer.Option("--start", help="Start date")] = None,
    end: Annotated[str | None, typer.Option("--end", help="End date")] = None,
    days_back: Annotated[int | None, typer.Option("--days-back", help="Calendar days back")] = None,
    markets: Annotated[str, typer.Option("--markets", "-m", help="Comma-separated markets")] = "us,cn,hk_sg",
    dry_run: Annotated[bool, typer.Option("--dry-run", help="No writes")] = False,
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

    market_list = [m.strip() for m in markets.split(",")]
    total = backfill_date_range(
        start_date=start_date,
        end_date=end_date,
        markets=market_list,
        ticker_config=ticker_config,
        dry_run=dry_run,
    )

    typer.echo(f"Backfill complete: {total} dates processed across {market_list}")


@app.command("update")
def update(
    date_str: Annotated[str | None, typer.Option("--date", help="Trading date")] = None,
    markets: Annotated[str | None, typer.Option("--markets", "-m", help="Comma-separated markets")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Smart incremental data updates."""
    from equity_lake.updates.engine import UpdateEngine

    _init_logging(verbose)
    trading_date = _resolve_date(date_str)
    market_list = _parse_comma_list(markets)
    engine_inst = UpdateEngine()
    engine_inst.run(trading_date=trading_date, markets=market_list or [], dry_run=dry_run)  # type: ignore[attr-defined]


@app.command("auto-backfill")
def auto_backfill(
    days_back: Annotated[int, typer.Option("--days-back", help="Days to scan for gaps")] = 90,
    markets: Annotated[str | None, typer.Option("--markets", "-m", help="Comma-separated markets")] = None,
    max_gap_days: Annotated[int, typer.Option("--max-gap-days", help="Skip gaps larger than this")] = 30,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show gaps without filling")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Auto-detect and fill data gaps across markets."""
    from equity_lake.ingestion.auto_backfill import find_and_fill_gaps

    _init_logging(verbose)
    market_list = _parse_comma_list(markets)
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


@app.command("sync")
def sync(
    bucket: Annotated[str | None, typer.Option("--bucket", "-b", help="S3 bucket URL")] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Download workers")] = 16,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Sync data lake to S3."""
    from equity_lake.ingestion.types import MARKET_DIR_MAP
    from equity_lake.storage.s3_sync import S3Syncer

    _init_logging(verbose)
    bucket_url = bucket or os.environ.get("S3_BUCKET", "")
    if not bucket_url:
        typer.secho("No S3 bucket specified. Use --bucket or set S3_BUCKET.", fg=typer.colors.RED)
        raise typer.Exit(1)

    # Equity market-data directories (Bronze layer). Each is synced separately so
    # s5cmd can parallelize per market and partial failures don't abort the rest.
    equity_markets = ["us", "cn", "hk_sg", "jpx", "krx"]
    failed: list[str] = []
    for market in equity_markets:
        market_dir = MARKET_DIR_MAP[market]
        typer.secho(f"Syncing {market} -> {market_dir} ...", fg=typer.colors.CYAN)
        syncer = S3Syncer(bucket=bucket_url, target_dir=Path("data/lake") / market_dir, workers=workers, dry_run=dry_run)
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
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Simulate")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Fetch macro indicators."""
    from equity_lake.core.dates import resolve_trading_date
    from equity_lake.ingestion.writers import write_to_partitioned_parquet
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

    success = write_to_partitioned_parquet(df, "01_bronze/macro", trading_date, dry_run=dry_run)

    if success:
        typer.secho("Macro indicators fetch completed successfully", fg=typer.colors.GREEN)
    else:
        typer.secho("Macro indicators fetch failed", fg=typer.colors.RED)
        raise typer.Exit(1)


@app.command("delta-vacuum")
def delta_vacuum(
    markets: Annotated[str, typer.Option("--markets", "-m", help="Comma-separated markets")] = "us_equity,cn_ashare,hk_sg_equity",
    retention_hours: Annotated[int, typer.Option("--retention-hours", help="Retention window in hours")] = 168,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview only")] = True,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Remove stale files from Delta Lake tables."""
    from equity_lake.storage.delta import vacuum_delta

    _init_logging(verbose)
    market_list = [m.strip() for m in markets.split(",")]
    for market in market_list:
        files = vacuum_delta(market, retention_hours=retention_hours, dry_run=dry_run)
        label = "would remove" if dry_run else "removed"
        typer.echo(f"  {market}: {label} {len(files)} stale files")
    if dry_run:
        typer.echo("Dry run — no files deleted. Use --dry-run=false to execute.")


@app.command("delta-compact")
def delta_compact(
    markets: Annotated[str, typer.Option("--markets", "-m", help="Comma-separated markets")] = "us_equity,cn_ashare,hk_sg_equity",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Compact small files in Delta Lake tables."""
    from equity_lake.storage.delta import compact_delta

    _init_logging(verbose)
    market_list = [m.strip() for m in markets.split(",")]
    for market in market_list:
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
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview only")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="Debug logging")] = False,
) -> None:
    """Migrate Hive-partitioned Parquet to Delta Lake format."""
    from equity_lake.storage.delta import migrate_parquet_to_delta

    _init_logging(verbose)
    market_list = [m.strip() for m in markets.split(",")]
    for market in market_list:
        ok = migrate_parquet_to_delta(market, dry_run=dry_run)
        status = "OK" if ok else "SKIPPED/FAILED"
        typer.echo(f"  {market}: {status}")
    typer.echo(f"Migration {'(dry run) ' if dry_run else ''}complete.")
