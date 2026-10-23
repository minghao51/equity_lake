"""Private watchlist, readiness, signal-history, inbox, and delivery commands."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stdout
from datetime import date, datetime
from importlib import import_module
from pathlib import Path
from typing import Annotated, Any

import typer

from equity_lake.cli._app import app, signal_app, tracking_app
from equity_lake.core.paths import CONFIG_DIR


@contextmanager
def _json_diagnostics_to_stderr() -> Iterator[None]:
    """Keep structured diagnostics off stdout while a tracking command renders JSON."""
    changed_handlers: list[tuple[logging.StreamHandler[Any], Any]] = []
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            previous = handler.stream
            handler.setStream(sys.stderr)
            changed_handlers.append((handler, previous))
    try:
        with redirect_stdout(sys.stderr):
            yield
    finally:
        for handler, previous in reversed(changed_handlers):
            handler.setStream(previous)


@app.command("doctor")
def doctor(
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Watchlist YAML; defaults to the existing configured watchlist")] = None,
    ticker_config: Annotated[
        Path | None, typer.Option("--ticker-config", help="Ticker configuration YAML; defaults to the existing configured universe")
    ] = None,
    as_of: Annotated[str | None, typer.Option("--as-of", help="Observation cutoff date (YYYY-MM-DD); defaults to today")] = None,
    lake_dir: Annotated[Path | None, typer.Option("--lake-dir", help="Medallion lake root for read-only source observations")] = None,
) -> None:
    """Report canonical all-market source, feature, scan, and signal-history observations."""
    readiness = import_module("equity_lake.signals.readiness")
    try:
        cutoff = _parse_iso_date(as_of, "--as-of") if as_of is not None else None
        with _json_diagnostics_to_stderr():
            report = readiness.build_tracking_readiness(
                watchlist_path=watchlist,
                ticker_config_path=ticker_config,
                as_of=cutoff,
                lake_dir=lake_dir,
            )
    except Exception as exc:
        typer.secho(f"Readiness observation failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(readiness.render_json(report))


@signal_app.command("history")
def signal_history(
    target_date: Annotated[str, typer.Option("--date", help="Explicit session date (YYYY-MM-DD) to inspect")],
    limit: Annotated[int, typer.Option("--limit", help="Maximum history rows to display")],
) -> None:
    """Read existing signal history for one date without migration or generator construction."""
    target = _parse_iso_date(target_date, "--date")
    readiness = import_module("equity_lake.signals.readiness")
    if limit < 1:
        raise typer.BadParameter("--limit must be at least 1", param_hint="--limit")
    with _json_diagnostics_to_stderr():
        result = readiness.read_signal_history_readonly(target_date=target, as_of=target, limit=limit)
    typer.echo(readiness.render_json(result))
    if result.state == "corrupt":
        raise typer.Exit(1)


@tracking_app.command("list")
def tracking_list(
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private watchlist YAML path")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact active source ticker configuration YAML")] = None,
) -> None:
    """List canonical private watchlist identities and their local-only annotations."""
    tracking = import_module("equity_lake.signals.tracking")
    try:
        with _json_diagnostics_to_stderr():
            result = tracking.load_private_watchlist(watchlist, ticker_config)
    except Exception as exc:
        typer.secho(f"Watchlist read failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.model_dump_json(indent=2))


@tracking_app.command("add")
def tracking_add(
    market: Annotated[str, typer.Option("--market", help="Canonical source market, e.g. us_equity or hk_sg_equity")],
    symbol: Annotated[str, typer.Option("--symbol", help="Exact configured instrument symbol")],
    mic: Annotated[str | None, typer.Option("--mic", help="Explicit configured MIC; required when the symbol is ambiguous")] = None,
    group: Annotated[list[str] | None, typer.Option("--group", help="Group membership; repeat to add multiple groups")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Private tag; repeat to add multiple tags")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Private notes; never included in public projections or webhook payloads")] = None,
    held: Annotated[bool, typer.Option("--held/--watch-only", help="Mark as held explicitly; default is watch-only")] = False,
    zone_low: Annotated[float | None, typer.Option("--zone-low", help="Optional user-supplied low price bound")] = None,
    zone_high: Annotated[float | None, typer.Option("--zone-high", help="Optional user-supplied high price bound")] = None,
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private watchlist YAML path")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact source ticker configuration YAML")] = None,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Auxiliary data root (tests/local isolation)")] = None,
) -> None:
    """Add one exact active configured instrument without fetching or backfilling data."""
    tracking = import_module("equity_lake.signals.tracking")
    try:
        with _json_diagnostics_to_stderr():
            result = tracking.add_instrument(
                market=market,
                symbol=symbol,
                mic=mic,
                groups=group,
                tags=tag,
                notes=notes,
                held=held,
                zone_low=zone_low,
                zone_high=zone_high,
                path=watchlist,
                ticker_config_path=ticker_config,
                data_dir=data_dir,
            )
    except Exception as exc:
        typer.secho(f"Watchlist add failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.model_dump_json(indent=2))


@tracking_app.command("update")
def tracking_update(
    market: Annotated[str, typer.Option("--market", help="Canonical market")],
    mic: Annotated[str, typer.Option("--mic", help="Canonical MIC")],
    symbol: Annotated[str, typer.Option("--symbol", help="Exact symbol")],
    held: Annotated[bool, typer.Option("--held", help="Mark as explicitly held")] = False,
    watch_only: Annotated[bool, typer.Option("--watch-only", help="Clear the held flag")] = False,
    group: Annotated[list[str] | None, typer.Option("--group", help="Replace group memberships")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Replace private tags")] = None,
    notes: Annotated[str | None, typer.Option("--notes", help="Replace private notes")] = None,
    clear_notes: Annotated[bool, typer.Option("--clear-notes", help="Clear private notes")] = False,
    zone_low: Annotated[float | None, typer.Option("--zone-low", help="Set an explicit low price bound")] = None,
    zone_high: Annotated[float | None, typer.Option("--zone-high", help="Set an explicit high price bound")] = None,
    clear_zone: Annotated[bool, typer.Option("--clear-zone", help="Remove explicit user price bounds")] = False,
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private watchlist YAML path")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact active source ticker configuration YAML")] = None,
) -> None:
    """Update private group, tag, note, held, or explicitly supplied price-zone fields."""
    if held and watch_only:
        raise typer.BadParameter("choose at most one of --held and --watch-only")
    tracking = import_module("equity_lake.signals.tracking")
    try:
        instrument = tracking.InstrumentKey(market=market, mic=mic, symbol=symbol)
        with _json_diagnostics_to_stderr():
            result = tracking.update_instrument(
                instrument,
                held=True if held else False if watch_only else None,
                groups=group,
                tags=tag,
                notes=notes,
                set_notes=notes is not None or clear_notes,
                zone_low=zone_low,
                zone_high=zone_high,
                clear_zone=clear_zone,
                path=watchlist,
                ticker_config_path=ticker_config,
            )
    except Exception as exc:
        typer.secho(f"Watchlist update failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.model_dump_json(indent=2))


@tracking_app.command("remove")
def tracking_remove(
    market: Annotated[str, typer.Option("--market", help="Canonical market")],
    mic: Annotated[str, typer.Option("--mic", help="Canonical MIC")],
    symbol: Annotated[str, typer.Option("--symbol", help="Exact symbol")],
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private watchlist YAML path")] = None,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Auxiliary data root (tests/local isolation)")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact active source ticker configuration YAML")] = None,
) -> None:
    """Remove active membership, retain history, and cancel pending deliveries."""
    tracking = import_module("equity_lake.signals.tracking")
    try:
        instrument = tracking.InstrumentKey(market=market, mic=mic, symbol=symbol)
        with _json_diagnostics_to_stderr():
            result = tracking.remove_instrument(instrument, path=watchlist, data_dir=data_dir, ticker_config_path=ticker_config)
    except Exception as exc:
        typer.secho(f"Watchlist removal failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.model_dump_json(indent=2))


@tracking_app.command("migrate-legacy")
def tracking_migrate_legacy(
    source: Annotated[Path, typer.Option("--source", help="Legacy config/watchlist.yaml; migration is explicit")] = CONFIG_DIR / "watchlist.yaml",
    destination: Annotated[Path | None, typer.Option("--destination", help="New private YAML path")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact source ticker configuration YAML")] = None,
) -> None:
    """Explicitly migrate current watchlist membership; historical signal identity is not guessed."""
    tracking = import_module("equity_lake.signals.tracking")
    try:
        with _json_diagnostics_to_stderr():
            result = tracking.migrate_legacy_watchlist(source=source, destination=destination, ticker_config_path=ticker_config)
    except Exception as exc:
        typer.secho(f"Legacy watchlist migration failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.model_dump_json(indent=2))


@tracking_app.command("settings")
def tracking_settings(
    cooldown_minutes: Annotated[
        int | None,
        typer.Option("--cooldown-minutes", min=0, max=1440, help="Set actionable-event cooldown (default 0)"),
    ] = None,
    name: Annotated[str | None, typer.Option("--name", help="Set private watchlist display name")] = None,
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private YAML path")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact active source ticker configuration YAML")] = None,
) -> None:
    """Update watchlist-level settings; cooldown defaults to zero minutes."""
    if cooldown_minutes is None and name is None:
        raise typer.BadParameter("provide --cooldown-minutes or --name")
    tracking = import_module("equity_lake.signals.tracking")
    try:
        with _json_diagnostics_to_stderr():
            result = tracking.update_watchlist_settings(
                cooldown_minutes=cooldown_minutes,
                name=name,
                path=watchlist,
                ticker_config_path=ticker_config,
            )
    except Exception as exc:
        typer.secho(f"Watchlist settings update failed: {type(exc).__name__}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.model_dump_json(indent=2))


@tracking_app.command("scan")
def tracking_scan(
    as_of: Annotated[str | None, typer.Option("--as-of", help="Aware ISO-8601 clock; defaults to current UTC time")] = None,
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private YAML path")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact source ticker configuration YAML")] = None,
    signal_config: Annotated[Path | None, typer.Option("--config", help="Signal YAML (ML remains disabled by default)")] = None,
    lake_dir: Annotated[Path | None, typer.Option("--lake-dir", help="Medallion lake root; never written by this command")] = None,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Auxiliary data root for reports and outbox")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run/--execute", help="Print a no-read, no-inference, no-write plan")] = False,
) -> None:
    """Run readiness-first exact-session private tracking against stored data only."""
    if dry_run:
        plan = {"status": "planned", "reason": "dry_run", "reads": False, "inference": False, "persistence": False, "delivery": False}
        typer.echo(json.dumps(plan, indent=2))
        return
    tracking = import_module("equity_lake.signals.tracking")
    try:
        cutoff = _parse_aware_datetime(as_of) if as_of is not None else None
        with _json_diagnostics_to_stderr():
            report = tracking.scan_private_watchlist(
                now=cutoff,
                watchlist_file=watchlist,
                ticker_config_path=ticker_config,
                signal_config_path=signal_config,
                lake_dir=lake_dir,
                data_dir=data_dir,
            )
    except Exception as exc:
        typer.secho(f"Tracking scan failed: {type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(report.model_dump_json(indent=2))
    if report.status in {"failed", "not_scheduled"}:
        raise typer.Exit(1)


@tracking_app.command("readiness")
def tracking_readiness(
    as_of: Annotated[str | None, typer.Option("--as-of", help="Aware ISO-8601 observation clock")] = None,
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private YAML path")] = None,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Auxiliary data root")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact active source ticker configuration YAML")] = None,
    lake_dir: Annotated[Path | None, typer.Option("--lake-dir", help="Medallion lake root for read-only exact-session readiness")] = None,
) -> None:
    """Show exact-session readiness and independent stale-run warnings."""
    tracking = import_module("equity_lake.signals.tracking")
    try:
        with _json_diagnostics_to_stderr():
            report = tracking.private_tracking_snapshot(
                now=_parse_aware_datetime(as_of) if as_of else None,
                watchlist_file=watchlist,
                data_dir=data_dir,
                ticker_config_path=ticker_config,
                lake_dir=lake_dir,
            )
    except Exception as exc:
        typer.secho(f"Tracking readiness failed: {type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(report.model_dump_json(indent=2))


@tracking_app.command("inbox")
def tracking_inbox(
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private YAML path")] = None,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Auxiliary data root")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact active source ticker configuration YAML")] = None,
) -> None:
    """Show local signal-event history and pending/sent/failed/expired delivery state."""
    tracking = import_module("equity_lake.signals.tracking")
    try:
        with _json_diagnostics_to_stderr():
            report = tracking.private_tracking_snapshot(watchlist_file=watchlist, data_dir=data_dir, ticker_config_path=ticker_config)
    except Exception as exc:
        typer.secho(f"Tracking inbox failed: {type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps([item.model_dump(mode="json") for item in report.recent_events], indent=2))


@tracking_app.command("digest")
def tracking_digest(
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Auxiliary data root")] = None,
) -> None:
    """Show opportunity and scan-failure digest with stale-run warnings independent of quiet hours."""
    tracking = import_module("equity_lake.signals.tracking")
    with _json_diagnostics_to_stderr():
        report = tracking.build_digest(data_dir=data_dir)
    typer.echo(report.model_dump_json(indent=2))


@tracking_app.command("schedule")
def tracking_schedule() -> None:
    """Describe the DST-aware readiness-first schedule plan without activating a scheduler."""
    tracking = import_module("equity_lake.signals.tracking")
    typer.echo(json.dumps(tracking.format_schedule_plan(), indent=2))


@tracking_app.command("deliver")
def tracking_deliver(
    enable_live: Annotated[bool, typer.Option("--enable-live", help="Explicitly authorize a webhook send from this invocation")] = False,
    data_dir: Annotated[Path | None, typer.Option("--data-dir", help="Auxiliary data root")] = None,
    watchlist: Annotated[Path | None, typer.Option("--watchlist", help="Private YAML path used for fresh-condition revalidation")] = None,
    lake_dir: Annotated[Path | None, typer.Option("--lake-dir", help="Medallion lake root for local revalidation")] = None,
    ticker_config: Annotated[Path | None, typer.Option("--ticker-config", help="Exact active source ticker configuration YAML")] = None,
    signal_config: Annotated[
        Path | None,
        typer.Option("--config", help="Exact signal config used for explicit-config events; mismatch expires the event"),
    ] = None,
) -> None:
    """Deliver a bounded local outbox batch; live sending is disabled unless explicitly opted in."""
    tracking = import_module("equity_lake.signals.tracking")
    # The raw credential URL is read only at the explicitly enabled client seam and is never echoed/logged.
    webhook_url = os.getenv("EQUITY_OPPORTUNITY_WEBHOOK_URL") if enable_live else None
    try:
        with _json_diagnostics_to_stderr():
            result = tracking.deliver_pending(
                enabled=enable_live,
                webhook_url=webhook_url,
                watchlist_file=watchlist,
                ticker_config_path=ticker_config,
                lake_dir=lake_dir,
                data_dir=data_dir,
                signal_config_path=signal_config,
            )
    except Exception as exc:
        typer.secho(f"Delivery was not completed ({type(exc).__name__}).", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    typer.echo(result.model_dump_json(indent=2))


def _parse_iso_date(value: str, option_name: str) -> date:
    """Parse strict YYYY-MM-DD CLI dates because Typer does not expose date options."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{option_name} must use YYYY-MM-DD", param_hint=option_name) from exc
    if parsed.isoformat() != value:
        raise typer.BadParameter(f"{option_name} must use YYYY-MM-DD", param_hint=option_name)
    return parsed


def _parse_aware_datetime(value: str) -> datetime:
    """Parse an offset-aware ISO-8601 clock; naive local times are ambiguous and rejected."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter("--as-of must be an offset-aware ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise typer.BadParameter("--as-of must include a timezone offset")
    return parsed
