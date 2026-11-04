"""``equity portfolio import`` — canonical-CSV import into the private ledger.

Wires one sub-app: :data:`equity_lake.cli._app.portfolio_app` is declared there and
registered in :mod:`equity_lake.cli.__main__` *before* this module is imported, so
the decorator below lands on the shared instance (ADR-0024 decision 13).

Only ``import`` exists. ``positions``, ``summary`` and ``reconcile`` read the P02
projections and are deliberately neither registered nor stubbed here. The accepted
input is the canonical CSV only — there is no broker-export mapping and no plugin
surface (ADR-0024 decision 13).

Validation, idempotency, atomicity and the dry-run preview all live in
:mod:`equity_lake.portfolio`; this module parses flags, renders counts and maps
failures to exit 1. Failure text repeats the ledger's value-free issue messages, so
no amount, quantity, price, basis or instrument symbol is formatted here either
(ADR-0024 decision 11).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Final, NoReturn

import typer

from equity_lake.cli._app import portfolio_app

# A neutral local label, never a broker account identifier (ADR-0024 decision 11).
DEFAULT_ACCOUNT_LABEL: Final[str] = "main"


def _reject(summary: str, details: Iterable[str]) -> NoReturn:
    """Print a value-free failure on stderr and exit 1; nothing was written."""
    typer.secho(f"portfolio import: {summary}", fg=typer.colors.RED, err=True)
    for detail in details:
        typer.secho(f"  {detail}", fg=typer.colors.RED, err=True)
    typer.secho("portfolio import: nothing was written.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _row_prefix(row: int | None) -> str:
    return "" if row is None else f"row {row}: "


@portfolio_app.command("import")
def portfolio_import(
    file: Annotated[
        Path,
        typer.Option("--file", help="Canonical portfolio CSV to import (the only accepted format: no broker mapping, no plugin)"),
    ],
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Validate fully and print the proposed effects; writes nothing (no directory or database)"),
    ] = False,
    account: Annotated[
        str,
        typer.Option(
            "--account",
            help="Neutral local account label every CSV row must carry, e.g. main; an identifier, never a real account number",
        ),
    ] = DEFAULT_ACCOUNT_LABEL,
) -> None:
    """Import one canonical portfolio CSV into the private ledger.

    Only the canonical CSV is accepted, with the frozen header of 18 columns:
    account, external_source, external_id, event_type, effective_at,
    received_at, sequence, market, mic, symbol, currency, cash_effect,
    quantity, price, fees, basis, basis_status, reverses_external_id.

    Every row is validated before anything is written, so a rejected row
    refuses the whole file instead of applying part of it. A committed batch
    prints inserted and unchanged counts: re-importing identical content under
    the same external ID is a no-op, while changed content under one external
    ID is a conflict (ADR-0024 decision 5).

    ``--dry-run`` performs the same full validation and prints the proposed
    effects (event counts by type, quantity delta per instrument, cash delta);
    it writes nothing at all: no directory, no database, no rollback journal.

    ``--account`` is a neutral local label (default ``main``) that every row
    must carry; it is an identifier, never a broker account number.

    Exit codes: 0 when the file validates (and, on a commit, the batch is
    stored or was already stored identically); 1 when the file is missing or
    unreadable, the header is not the frozen schema, any row is rejected, a
    stored external ID conflicts, or the ledger is corrupt. Nothing is written
    in any failure case. Rejected rows are reported by 1-based file line number
    and reason category, never with an amount, quantity, price, basis or
    instrument symbol (ADR-0024 decision 11).
    """
    from equity_lake.portfolio import (
        LedgerValidationError,
        PortfolioLedgerError,
        commit_events,
        parse_csv_file,
        preview_events,
    )

    source = Path(file)
    if not source.exists():
        _reject(f"no such file: {source}", ["pass an existing canonical CSV with --file; a missing file is never created"])
    if not source.is_file():
        _reject(f"not a regular file: {source}", ["--file must name a canonical CSV file, not a directory or a device"])

    label = account.strip()
    if not label:
        _reject("--account must be a non-empty neutral label", ["use a short local identifier such as main, never a broker account number"])

    try:
        events = parse_csv_file(source)
    except LedgerValidationError as exc:
        _reject(f"{source.name} rejected: {len(exc.issues)} issue(s)", (issue.render() for issue in exc.issues))
    except OSError as exc:
        _reject(f"could not read {source} ({type(exc).__name__})", ["the file must be a readable UTF-8 canonical CSV"])

    mismatched = [event for event in events if event.account != label]
    if mismatched:
        _reject(
            f"{source.name} rejected: {len(mismatched)} row(s) do not carry the --account label",
            (
                f"{_row_prefix(event.source_row)}account column differs from --account; one private ledger holds one account (ADR-0024 decision 1)"
                for event in mismatched
            ),
        )

    try:
        if dry_run:
            preview = preview_events(events)
            by_type = ", ".join(f"{kind} {count}" for kind, count in preview.counts_by_type.items()) or "none"
            by_instrument = ", ".join(f"{name} {format(delta, 'f')}" for name, delta in preview.quantity_deltas.items()) or "none"
            typer.echo(f"Dry run: {source.name} validated - {preview.event_count} event(s), nothing was written.")
            typer.echo(f"  proposed effects: {preview.new_event_count} new, {preview.unchanged_event_count} already stored identically")
            typer.echo(f"  events by type: {by_type}")
            typer.echo(f"  quantity delta by instrument: {by_instrument}")
            typer.echo(f"  cash delta: {format(preview.cash_delta, 'f')}")
            typer.echo("Dry run wrote nothing: no directory, database, rollback journal or backup was created or modified.")
            return
        outcome = commit_events(events)
    except LedgerValidationError as exc:
        _reject(f"{source.name} rejected: {len(exc.issues)} issue(s)", (issue.render() for issue in exc.issues))
    except PortfolioLedgerError as exc:
        _reject("the private ledger refused this batch", [str(exc)])
    except sqlite3.Error as exc:
        _reject(f"the private ledger could not be read ({type(exc).__name__})", ["another process may hold the ledger; retry once it is free"])

    typer.echo(f"Imported {source.name}: inserted {outcome.inserted}, unchanged {outcome.unchanged}, rejected 0.")
    typer.echo("The private ledger is written under data/portfolio/ (not cataloged, never committed).")
