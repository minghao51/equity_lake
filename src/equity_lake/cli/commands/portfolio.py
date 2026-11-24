"""Private portfolio ledger commands: canonical CSV import and three read reports.

Wires one sub-app: :data:`equity_lake.cli._app.portfolio_app` is declared there and
registered in :mod:`equity_lake.cli.__main__` *before* this module is imported, so
the decorators below land on the shared instance (ADR-0024 decision 13).

``import`` is the only write path and accepts the canonical CSV only — there is
no broker-export mapping and no plugin surface. ``positions``, ``summary`` and
``reconcile`` are read-only reports over the P02 projections (ADR-0026 decision 7):
they require an explicit ``--as-of`` and either ``--marks`` or ``--statement``,
never create a database, directory, journal, mark file, checkpoint or cache, and
name affected instruments only inside the explicitly requested local report — not
in logs, telemetry or error text (ADR-0024 decision 11).

Exit codes are part of the contract: ``0`` only for a complete requested result
(all reconciliation lines matched), ``1`` for required unavailability, a
mismatch, invalid input or a refused ledger. A partial report stays visible and a
nonzero exit never means it was complete.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final, NoReturn

import typer

from equity_lake.cli._app import portfolio_app

if TYPE_CHECKING:
    from equity_lake.portfolio.marks import PortfolioValuation, PositionValuation
    from equity_lake.portfolio.models import LedgerEvent
    from equity_lake.portfolio.projections import LedgerProjection

# A neutral local label, never a broker account identifier (ADR-0024 decision 11).
DEFAULT_ACCOUNT_LABEL: Final[str] = "main"
DEFAULT_MAX_STALE_SESSIONS: Final[int] = 1


def _reject(summary: str, details: Iterable[str]) -> NoReturn:
    """Print a value-free import failure on stderr and exit 1; nothing was written."""
    typer.secho(f"portfolio import: {summary}", fg=typer.colors.RED, err=True)
    for detail in details:
        typer.secho(f"  {detail}", fg=typer.colors.RED, err=True)
    typer.secho("portfolio import: nothing was written.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _row_prefix(row: int | None) -> str:
    return "" if row is None else f"row {row}: "


def _reject_read(command: str, summary: str, details: Iterable[str]) -> NoReturn:
    """Print a value-free read failure on stderr and exit 1; nothing was written."""
    typer.secho(f"portfolio {command}: {summary}", fg=typer.colors.RED, err=True)
    for detail in details:
        typer.secho(f"  {detail}", fg=typer.colors.RED, err=True)
    typer.secho(f"portfolio {command}: no files were written.", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


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
            typer.echo(f"  quantity delta by instrument (share-count events only): {by_instrument}")
            if preview.split_event_count:
                typer.echo(f"  split events: {preview.split_event_count} (the ratio adjusts quantity at replay, not a share-count delta)")
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
    from equity_lake.portfolio import LedgerEventType, instrument_label

    split_events = [event for event in events if event.event_type is LedgerEventType.SPLIT]
    if split_events:
        labels = sorted({instrument_label(event.instrument) for event in split_events if event.instrument is not None})
        # ADR-0029: name the affected instruments, and state the consequence that the
        # operator has to act on. The ledger never edits or clears a valuation block.
        typer.echo(
            f"Split events in this batch: {len(split_events)} for {', '.join(labels)}. Quantities adjust at replay; "
            "a stored mark from before a split is refused until it is replaced, and a valuation block stays until you remove it."
        )
    typer.echo("The private ledger is written under data/portfolio/ (not cataloged, never committed).")


@portfolio_app.command("migrate")
def portfolio_migrate(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--report-only",
            help="Perform the verified v1->v2 ledger upgrade; the default reports the plan and writes nothing",
        ),
    ] = False,
) -> None:
    """Upgrade the private ledger schema (v1 -> v2, which accepts the SPLIT kind).

    Report-only by default: it verifies the file, reports its schema version, row count and
    record digest, and writes nothing at all. ``--apply`` first writes a verified exact-file
    backup beside the ledger, then rebuilds the table in one transaction, proves the record is
    unchanged (row count and record digest) and re-verifies the result (ADR-0031).

    No command ever upgrades implicitly: a v1 ledger refuses reads and writes with a message
    naming this one. There is no down-migration — the verified backup is the reversal.

    Exit codes: 0 when the ledger is already current, when a report is produced, or when the
    upgrade completes and re-verifies; 1 when the ledger is missing, unreadable, of an
    unsupported version, has a rollback journal beside it, or fails any check.
    """
    from equity_lake.portfolio.store import LedgerStoreError, migrate_ledger, portfolio_db_path

    try:
        report = migrate_ledger(portfolio_db_path(), apply=apply)
    except LedgerStoreError as exc:
        # No blanket "nothing was written" here: a failed --apply may have left a verified
        # backup beside the ledger, and the message says so where that happened.
        typer.secho(f"portfolio migrate: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if report.already_current:
        typer.echo(f"Ledger {report.filename} is already at schema v{report.target_version}; nothing to do.")
        return
    if not report.applied:
        typer.echo(f"Report only: {report.filename} is schema v{report.found_version}; v{report.target_version} is available.")
        typer.echo(f"  rows: {report.row_count}")
        typer.echo(f"  record digest: {report.record_digest}")
        typer.echo(f"  --apply writes a verified {report.filename}.<utc>.v{report.found_version}.bak copy first, then rebuilds")
        typer.echo("Report only: nothing was written (no backup, no directory, no journal).")
        return
    typer.secho(
        f"Upgraded {report.filename}: schema v{report.found_version} -> v{report.target_version}, {report.row_count} row(s) unchanged.",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  verified backup: {report.backup_filename} (sha256 {report.backup_sha256})")
    typer.echo("  record digest unchanged; integrity_check and foreign_key_check passed")


# --- restore and compaction (P00-4 remainder, ADR-0031 decisions 4-5) ---------------


def _echo_ledger_backup(filename: str | None, sha256: str | None) -> None:
    """Report the verified backup a mechanism kept, without naming a path it did not write."""
    if filename is None:  # pragma: no cover - --apply always writes one for an existing ledger
        return
    suffix = f" (sha256 {sha256})" if sha256 else ""
    typer.echo(f"  verified backup of the current file: {filename}{suffix}")


@portfolio_app.command("restore")
def portfolio_restore(
    from_path: Annotated[
        Path,
        typer.Option("--from", help="Verified backup file to restore from (the file itself is re-verified before it is used)"),
    ],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--report-only",
            help="Back up the current ledger, replace it atomically and re-verify; the default reports the plan and writes nothing",
        ),
    ] = False,
) -> None:
    """Restore the private ledger from a verified backup (ADR-0031 decision 4).

    Report-only by default: both files are verified and the plan is printed, including what the
    current ledger holds that the candidate does not (event count and newest booking instant),
    and nothing at all is written.

    ``--apply`` takes a verified backup of the *current* file first — it may hold events the
    backup lacks — then replaces the ledger atomically through a temporary file in the same
    directory and re-verifies the result. A restore is refused when the candidate is unverified
    or of an unimplemented version, is the current file itself, names a different account than
    the current ledger, when a rollback journal is present, or when there is no room for the
    copy plus its backup.

    Exit codes: 0 when a report is produced or the restore completes and re-verifies; 1 on any
    refusal or failed check.
    """
    from equity_lake.portfolio.store import LedgerStoreError, portfolio_db_path, restore_ledger

    try:
        report = restore_ledger(Path(from_path), portfolio_db_path(), apply=apply)
    except LedgerStoreError as exc:
        # No blanket "nothing was written": a failed --apply may have left a verified backup
        # beside the ledger, and the message names it where that happened.
        typer.secho(f"portfolio restore: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if report.already_identical:
        typer.echo(f"Ledger {report.filename} is already byte-identical to {report.candidate_filename}; nothing to do.")
        return
    typer.echo(f"Ledger {report.filename} <- {report.candidate_filename} (schema v{report.candidate_version})")
    typer.echo(f"  rows: {report.row_count}")
    typer.echo(f"  record digest: {report.record_digest}")
    typer.echo(f"  candidate sha256: {report.candidate_sha256}")
    if report.target_existed:
        typer.echo(f"  replaces the current file: {report.rolled_back_rows} event(s) it holds would be left out")
        if report.rolled_back_newest_recorded_at is not None:
            typer.echo(f"  current ledger's newest booking instant: {report.rolled_back_newest_recorded_at}")
    else:
        typer.echo("  no current ledger exists; the candidate becomes the ledger")
    if not report.applied:
        typer.echo("Report only: nothing was written (no backup, no temporary file, no directory, no journal).")
        return
    _echo_ledger_backup(report.backup_filename, report.backup_sha256)
    typer.secho(f"Restored {report.filename}: {report.row_count} row(s), re-verified against the candidate.", fg=typer.colors.GREEN)


@portfolio_app.command("compact")
def portfolio_compact(
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--report-only",
            help="Rebuild the file with VACUUM INTO, verify it and swap it in; the default reports the plan and writes nothing",
        ),
    ] = False,
) -> None:
    """Rebuild the private ledger with no free pages, preserving the record (ADR-0031 decision 5).

    Report-only by default and never automatic: the version, row count, record digest and file
    size are printed and nothing is written.

    ``--apply`` runs ``VACUUM INTO`` a fresh file beside the ledger, verifies that the version,
    row count and ``(account, external_id, content_digest)`` digest all match the pre-compaction
    state, backs up the current file, replaces it atomically and re-verifies. No event is ever
    deleted and ``row_id`` renumbering carries no meaning.

    Exit codes: 0 when a report is produced or the compaction completes and re-verifies; 1 on a
    missing or v1 ledger, a rollback journal, or any failed post-condition.
    """
    from equity_lake.portfolio.store import LedgerStoreError, compact_ledger, portfolio_db_path

    try:
        report = compact_ledger(portfolio_db_path(), apply=apply)
    except LedgerStoreError as exc:
        typer.secho(f"portfolio compact: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Ledger {report.filename} (schema v{report.schema_version}): {report.before_bytes} bytes, {report.row_count} row(s)")
    typer.echo(f"  record digest: {report.record_digest}")
    if not report.applied:
        typer.echo("Report only: VACUUM INTO a verified copy, then the verified-restore swap. Nothing was written.")
        return
    _echo_ledger_backup(report.backup_filename, report.backup_sha256)
    typer.secho(
        f"Compacted {report.filename}: {report.before_bytes} -> {report.after_bytes} bytes, {report.row_count} row(s) preserved.",
        fg=typer.colors.GREEN,
    )


# --- verified backup export and backup inventory (RS-P02, ADR-0031 decision 3) -------


@portfolio_app.command("backup")
def portfolio_backup(
    output_dir: Annotated[
        Path,
        typer.Option(
            "--output-dir",
            help="Private local directory to write the verified exact-file copy into (created 0700 with --apply only; never a shared or public path)",
        ),
    ],
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--report-only",
            help="Write and verify the copy; the default reports the plan and writes nothing at all",
        ),
    ] = False,
) -> None:
    """Export a verified exact-file backup of the private ledger (ADR-0031 decision 3).

    Report-only by default: the ledger is verified, the destination name is computed and
    printed, and nothing at all is written — no directory, no file, no temporary file, no
    journal.

    ``--apply`` writes a copy named ``<ledger>.<utc>.v<version>.bak`` into ``--output-dir``
    (created with ``0700`` when missing), mode ``0600``, under a temporary name that is
    verified against its source and only then renamed into place. The row count and record
    digest must match the source, an existing backup name is never overwritten, and the
    ledger itself is not modified, moved or compacted.

    This is a local copy only. There is no scheduled, remote, encrypted, off-host or cloud
    export and no backup-file pruning: both need a new decision (and their own ADR) before
    they may be implemented. Nothing here deletes an event or a backup.

    Exit codes: 0 when a report is produced or the verified copy is written and re-verified;
    1 when the ledger is missing, locked, corrupt, of an unsupported version, has a rollback
    journal beside it, when the destination is a symlink or not a directory, or when the copy
    does not match its source. A failed export leaves no backup file behind.
    """
    from equity_lake.portfolio.store import LedgerStoreError, export_verified_backup, portfolio_db_path

    try:
        report = export_verified_backup(Path(output_dir), portfolio_db_path(), apply=apply)
    except LedgerStoreError as exc:
        typer.secho(f"portfolio backup: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    typer.echo(f"Ledger {report.filename} (schema v{report.schema_version}): {report.size_bytes} bytes, {report.row_count} row(s)")
    typer.echo(f"  record digest: {report.record_digest}")
    typer.echo(f"  source sha256: {report.sha256}")
    typer.echo(f"  destination: {report.directory}{'' if report.directory_exists else ' (created 0700 by --apply)'}")
    if not report.applied:
        typer.echo(f"  --apply writes {report.filename}.<utc>.v{report.schema_version}.bak there, mode 0600, verified then renamed into place")
        typer.echo("Report only: nothing was written (no directory, no file, no journal).")
        return
    typer.secho(f"Exported a verified backup: {report.backup_filename}", fg=typer.colors.GREEN)
    typer.echo(f"  backup sha256: {report.backup_sha256}")
    typer.echo("  the copy's row count and record digest match the source; the ledger itself was not modified.")
    typer.echo("  local copy only: no off-host, encrypted or cloud export exists (a new ADR is required) and no backup is ever pruned.")


@portfolio_app.command("backups")
def portfolio_backups(
    directory: Annotated[
        Path,
        typer.Option("--directory", help="Private directory to list read-only; symlinks and foreign files are reported and never read"),
    ],
) -> None:
    """List the backup files in a private directory, read-only (ADR-0031 decision 3).

    Each ``.bak`` entry is verified with the same five checks a backup must pass and reported
    with its name, size, modification time, schema version, row count, digest and verification
    result. A candidate that fails is reported as refused: it is not a recovery point until it
    is replaced by a fresh verified export.

    Nothing is created, read through a symlink, modified or deleted. A symlink, a directory
    entry and any file whose name is not a ledger backup name are listed as skipped and never
    opened, so an unrelated directory can be inspected safely. A missing directory is reported
    as absent and is never created.

    Exit codes: 0 when the directory exists and every ledger backup in it verifies (skipped
    entries do not change the exit code); 1 when the directory is missing, is a symlink or not a
    directory, or any backup fails verification.
    """
    from equity_lake.portfolio.store import LedgerStoreError, inventory_backups

    try:
        inventory = inventory_backups(Path(directory))
    except LedgerStoreError as exc:
        typer.secho(f"portfolio backups: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if not inventory.exists:
        typer.secho(f"portfolio backups: no directory at {inventory.directory}", fg=typer.colors.RED, err=True)
        typer.secho(
            "  this listing never creates a directory; export a backup with `equity portfolio backup --apply` first",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    refused = [candidate for candidate in inventory.candidates if not candidate.verified]
    verified = len(inventory.candidates) - len(refused)
    typer.echo(
        f"Backups in {inventory.directory}: {len(inventory.candidates)} candidate(s), "
        f"{verified} verified, {len(refused)} refused, {len(inventory.skipped)} skipped"
    )
    for candidate in inventory.candidates:
        version = "-" if candidate.schema_version is None else f"v{candidate.schema_version}"
        rows = "-" if candidate.row_count is None else str(candidate.row_count)
        state = "verified" if candidate.verified else "refused"
        typer.echo(f"  {candidate.filename}  {candidate.size_bytes} bytes  {candidate.modified_at}  {version}  {rows} row(s)  {state}")
        if candidate.detail is not None:
            typer.echo(f"    {candidate.detail}")
    for skip in inventory.skipped:
        typer.echo(f"  skipped {skip.filename}: {skip.reason}")
    typer.echo("Read only: nothing was created, read through a symlink, modified or deleted.")
    if refused:
        typer.secho(
            f"portfolio backups: {len(refused)} backup file(s) failed verification and are not recovery points",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)


# --- checkpoint-collapse pruning (ADR-0036) -----------------------------------------


@portfolio_app.command("prune")
def portfolio_prune(
    before: Annotated[
        str | None,
        typer.Option(
            "--before",
            help="Required timezone-aware UTC cut instant; events strictly before it are collapsed into one checkpoint",
        ),
    ] = None,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply/--report-only",
            help="Take the verified backup, write the checkpoint and manifest; the default reports the plan and writes nothing",
        ),
    ] = False,
    backup: Annotated[
        Path | None,
        typer.Option(
            "--backup",
            help="Existing verified backup of this exact ledger to re-verify and record instead of writing a new one",
        ),
    ] = None,
) -> None:
    """Collapse ledger history before an instant into a verified checkpoint (ADR-0036).

    Report-only by default: it verifies the ledger, replays it to the cut, prints the events
    that would be deleted, the checkpoint row-set it would write, the planned record digest
    and manifest fields, and writes nothing at all — no backup, no copy, no manifest, no
    directory, no journal.

    Pruning is **checkpoint collapse**, never bare deletion: the accounting state at the cut
    is written as an `OPENING` row-set (one cash row plus one row per open position, because
    `OPENING` carries at most one instrument per row), then every event strictly before the
    cut is deleted in the same transaction. Cash, per-position quantity, basis and NAV at and
    after the cut are preserved exactly; deposits, withdrawals, standalone fees, net dividend
    income and realized gain before the cut are collapsed and can no longer be reconstructed
    from the ledger. `summary` and `positions` therefore disclose that they present a pruned
    history.

    `--apply` requires a verified backup whose sha256 is recorded. With `--backup` the named
    file is re-verified and must be this exact ledger; without it a verified exact-file copy
    is written beside the ledger first. The prune is refused when the ledger is missing,
    unverifiable or not schema v2; the target is not the configured private ledger; the cut
    is naive or not strictly in the past; no event is strictly before the cut; the ledger
    holds more than one account label; the checkpoint would carry negative cash; there is no
    source-sequence room before the earliest retained event; the supplied backup does not
    match; or replay of the pruned ledger does not reproduce the pre-prune book at the cut
    and at a later instant. Running it again for the same cut is a no-op that writes nothing.

    The manifest is written under `data/portfolio/prune/` and records the cut, the sorted
    pruned event ids, the record digest before and after, the backup sha256, the tool version,
    a UTC timestamp and the checkpoint external ids.

    Exit codes: 0 when a report is produced, the cut was already pruned, or the prune
    completes and re-verifies; 1 on any refusal or failed check. Refusals go to stderr
    without naming a stored amount, quantity, price or instrument.
    """
    from equity_lake.portfolio.store import LedgerStoreError, prune_ledger

    instant = _parse_as_of("prune", before)
    try:
        report = prune_ledger(before=instant, apply=apply, backup=Path(backup) if backup is not None else None)
    except LedgerStoreError as exc:
        typer.secho(f"portfolio prune: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc

    if report.already_pruned:
        typer.echo(f"Ledger {report.filename} has already been pruned at {report.cut.isoformat()}; nothing to do.")
        typer.echo(f"  checkpoint cash {report.checkpoint_cash}, {report.checkpoint_position_count} position row(s)")
        typer.echo(f"  checkpoint external ids: {', '.join(report.checkpoint_external_ids)}")
        typer.echo("Nothing was written: a prune for this cut is a no-op and never produces a second checkpoint.")
        return

    typer.echo(f"Ledger {report.filename}: prune cut {report.cut.isoformat()}")
    typer.echo(f"  events strictly before the cut (to delete): {report.pruned_event_count}")
    typer.echo(f"    ids: {', '.join(report.pruned_event_external_ids)}")
    typer.echo(f"  retained events at or after the cut: {report.retained_event_count}")
    typer.echo(f"  checkpoint row-set: one cash row + {report.checkpoint_position_count} position row(s); cash {report.checkpoint_cash}")
    typer.echo(f"  checkpoint effective instant: {report.checkpoint_effective_at.isoformat()} (one microsecond before the cut, so it sorts first)")
    typer.echo(f"  checkpoint external ids: {', '.join(report.checkpoint_external_ids)}")
    typer.echo(f"  rows {report.row_count_before} -> {report.row_count_after}")
    typer.echo(f"  record digest {report.record_digest_before} -> {report.record_digest_after}")
    if report.backup_source == "supplied":
        typer.echo(f"  backup: supplied and re-verified: {report.backup_filename} (sha256 {report.backup_sha256})")
    elif report.backup_source == "created":
        typer.echo(f"  backup: created and verified: {report.backup_filename} (sha256 {report.backup_sha256})")
    else:
        typer.echo("  backup: --apply writes a verified exact-file copy beside the ledger first (no --backup supplied)")
    if not report.applied:
        typer.echo(f"  manifest: {report.manifest_directory}/<utc>.json (written by --apply)")
        typer.echo("Report only: nothing was written (no backup, no copy, no manifest, no directory, no journal).")
        return
    typer.echo(f"  manifest: {report.manifest_filename} under {report.manifest_directory}")
    typer.secho(
        f"Pruned {report.filename}: {report.pruned_event_count} event(s) collapsed into the checkpoint; the book at and after the cut is unchanged.",
        fg=typer.colors.GREEN,
    )
    typer.echo("  irreversible: the pre-cut events no longer exist in the ledger; the verified backup is the reversal.")
    typer.echo("  summary and positions now disclose that they present a pruned history.")


# --- read commands (P02) -------------------------------------------------------------

_MONEY_NOTE: Final[str] = "money is displayed in cents, half-even; quantities are shown exactly"


def _parse_as_of(command: str, text: str | None) -> datetime:
    """Require an explicit, timezone-aware ``--as-of``; the current clock is never a default."""
    if text is None or not text.strip():
        _reject_read(
            command,
            "--as-of is required",
            ["pass an explicit timezone-aware UTC instant such as 2024-01-05T21:00:00Z; the current clock is never used as a valuation time"],
        )
    try:
        parsed = datetime.fromisoformat(text.strip())
    except ValueError:
        _reject_read(command, "--as-of is not an ISO-8601 timestamp", ["expected an aware UTC instant such as 2024-01-05T21:00:00Z"])
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _reject_read(
            command, "--as-of must be timezone-aware", ["a naive timestamp has no defined valuation instant; use Z or an explicit UTC offset"]
        )
    return parsed.astimezone(UTC)


def _load_stored_events(command: str) -> tuple[LedgerEvent, ...]:
    from equity_lake.portfolio import LedgerStore, PortfolioLedgerError

    store = LedgerStore()
    if not store.exists():
        _reject_read(
            command,
            "the private ledger does not exist",
            ["read paths never create a ledger; import an authorized canonical CSV first (neither a missing ledger nor an empty account is assumed)"],
        )
    try:
        return store.load_events()
    except PortfolioLedgerError as exc:
        # Store messages can carry ledger metadata (for example a ``user_version``
        # number) that came from the file itself, so a read surface reports a fixed
        # refusal and the exception class only, never the forwarded text.
        _reject_read(
            command,
            f"the private ledger refused this read ({type(exc).__name__})",
            [
                "the stored ledger is unusable, has an unsupported version, or holds an event that violates the event contract; "
                "the metadata is not repeated here because it comes from the private file",
                "nothing was recreated, migrated or modified; inspect data/portfolio/portfolio.sqlite3 locally",
            ],
        )
    except sqlite3.Error as exc:
        _reject_read(
            command,
            f"the private ledger could not be read ({type(exc).__name__})",
            ["another process may hold the ledger; retry once it is free"],
        )


def _pruned_history_cut(events: tuple[LedgerEvent, ...]) -> datetime | None:
    """The checkpoint cut a read report must disclose, or ``None`` for an unpruned ledger."""
    from equity_lake.portfolio.store import prune_checkpoint_cut

    return prune_checkpoint_cut(events)


def _reject_projection(command: str) -> NoReturn:
    """Refuse an unusable stored timeline without repeating any ledger text.

    Projection messages may name a stored ``external_id``, which is user-supplied
    text, so a read surface must not forward them: stderr stays value-free while
    the reason category tells the operator where to look (ADR-0026 decision 7).
    """
    _reject_read(
        command,
        "the stored ledger cannot support this projection",
        [
            "a replayed event fails a ledger invariant (oversell, negative cash/margin or an inconsistent "
            "cash/basis relationship); the failing row is not named here because ledger text is private",
            "re-validate the source canonical CSV with `equity portfolio import --file … --dry-run` to locate the rejected row",
        ],
    )


def _projection(command: str, account: str, as_of: str | None) -> tuple[LedgerProjection, tuple[LedgerEvent, ...]]:
    from equity_lake.portfolio import ProjectionError, replay_ledger

    instant = _parse_as_of(command, as_of)
    events = _load_stored_events(command)
    try:
        return replay_ledger(events, account=account, as_of=instant), events
    except ProjectionError:
        _reject_projection(command)


def _valuation(
    command: str,
    projection: LedgerProjection,
    marks: Path | None,
    max_stale_sessions: int,
) -> PortfolioValuation:
    from equity_lake.portfolio import MarksError, MarksValidationError, load_marks_file, value_positions

    if marks is None:
        _reject_read(command, "--marks is required", ["pass a stored book-mark JSON file; marks are never fetched, guessed or created here"])
    path = Path(marks)
    if not path.exists():
        _reject_read(
            command,
            "--marks names a file that does not exist",
            ["pass an existing stored mark file; a read path never creates one (the name is not echoed: it may contain private text)"],
        )
    if not path.is_file():
        _reject_read(command, "--marks does not name a regular file", ["--marks must name a stored book-mark JSON file"])
    try:
        document = load_marks_file(path)
        return value_positions(projection, document, max_stale_sessions=max_stale_sessions)
    except MarksValidationError as exc:
        _reject_read(command, f"the marks file was rejected: {len(exc.issues)} issue(s)", (issue.render() for issue in exc.issues))
    except MarksError as exc:
        _reject_read(command, "book marks cannot be used for valuation", [str(exc)])


def _money(value: Decimal) -> str:
    from equity_lake.portfolio import money_display

    return format(money_display(value), "f")


def _quantity(value: Decimal) -> str:
    return format(value, "f")


def _optional_money(value: Decimal | None) -> str:
    return "unavailable" if value is None else _money(value)


def _echo_position_block(valuation: PositionValuation) -> None:
    from equity_lake.portfolio import instrument_label

    label = instrument_label(valuation.instrument)
    certain = "" if valuation.quantity_certain else "  (quantity uncertain: unsupported quantity adjustment on record)"
    typer.echo(f"  {label}  quantity {_quantity(valuation.quantity)}{certain}")
    average_cost = valuation.average_cost
    typer.echo(
        f"    basis {_optional_money(valuation.basis)}"
        f"  average cost {'unavailable' if average_cost is None else _money(average_cost)}"
        f"  realized gain {_optional_money(valuation.realized_gain)}"
    )
    if valuation.mark_price is not None:
        age = "unknown" if valuation.mark_age_sessions is None else str(valuation.mark_age_sessions)
        provenance = "unavailable" if valuation.mark_provenance is None else valuation.mark_provenance.value
        typer.echo(
            f"    mark {format(valuation.mark_price, 'f')} ({valuation.mark_source}, provenance {provenance})"
            f"  session {valuation.mark_session}  as of {valuation.mark_as_of}"
            f"  age {age} session(s), bound {valuation.max_stale_sessions}"
        )
    typer.echo(
        f"    value {_optional_money(valuation.value)}  unrealized gain {_optional_money(valuation.unrealized_gain)}  status {valuation.status.value}"
    )
    if valuation.blocker is not None:
        typer.secho(f"    blocker: {valuation.blocker}", fg=typer.colors.YELLOW)


def _blocked_status_counts(valuation: PortfolioValuation) -> str:
    """Value-free tally of why positions are unvalued: status categories and counts.

    Never an identity, symbol, amount or quantity: stderr must stay value-free
    (ADR-0026 decision 7). The affected identities are printed only inside the
    requested local report on stdout.
    """
    from equity_lake.portfolio import MarkStatus

    counts: dict[str, int] = {}
    for position in valuation.positions:
        if position.status is MarkStatus.VALUED:
            continue
        counts[position.status.value] = counts.get(position.status.value, 0) + 1
    return ", ".join(f"{status} {count}" for status, count in sorted(counts.items())) or "none"


def _fail_incomplete(command: str, valuation: PortfolioValuation) -> None:
    """Report an incomplete valuation without leaking any private value on stderr."""
    typer.secho(
        f"portfolio {command}: {len(valuation.blockers)} instrument(s) cannot be valued ({_blocked_status_counts(valuation)})",
        fg=typer.colors.YELLOW,
        err=True,
    )
    typer.secho(
        "  affected identities are named only in the local report above; no private value is written to this message",
        fg=typer.colors.YELLOW,
        err=True,
    )


@portfolio_app.command("positions")
def portfolio_positions(
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Required timezone-aware UTC valuation instant, e.g. 2024-01-05T21:00:00Z; the clock is never a default"),
    ] = None,
    marks: Annotated[
        Path | None,
        typer.Option("--marks", help="Required stored book-mark JSON (version 1): read and validated, never created or fetched"),
    ] = None,
    account: Annotated[
        str,
        typer.Option("--account", help="Neutral local account label, e.g. main; an identifier, never a real account number"),
    ] = DEFAULT_ACCOUNT_LABEL,
    max_stale_sessions: Annotated[
        int,
        typer.Option(
            "--max-stale-sessions",
            help="Completed exact-venue sessions a mark may be old: 1 (default) or a stricter 0; never above 1",
        ),
    ] = DEFAULT_MAX_STALE_SESSIONS,
) -> None:
    """Report exact holdings, basis and marks at one effective-date instant.

    Replays the stored ledger in source-sequence order up to and including
    ``--as-of`` and reports every open position with its exact quantity, basis
    status, mark, source, session, completed-session age, value, unrealized gain
    and any blocker.

    Printed output is an **effective-date reconstruction** from the supplied
    ledger: it is not proof of what a source had published or what the owner knew
    at that instant. Cash, quantities and the valid marked subtotal stay visible
    when a mark is missing, stale, conflicting or its quantity is uncertain;
    complete NAV and valuation-dependent totals are then withheld, and the
    affected instruments are named in this report.

    ``--max-stale-sessions`` accepts only 0 or 1: age is counted in completed
    sessions for the exact venue (early closes and holidays included), so a
    Friday close through a closed weekend is age zero.

    Exit codes: 0 when every open position is valued and no blocker applies; 1
    when ``--as-of`` or ``--marks`` is missing or invalid, the ledger or the mark
    file is refused, or any required mark is missing, stale, conflicting or
    quantity-uncertain. A partial report is still printed, and a nonzero exit
    never means the report was complete. Read paths create no database,
    directory, journal, mark file or cache, and private values appear only in
    this explicitly requested local report (never in logs or error text).
    """
    label = account.strip()
    if not label:
        _reject_read("positions", "--account must be a non-empty neutral label", ["use a short local identifier such as main"])

    projection, events = _projection("positions", label, as_of)
    valuation = _valuation("positions", projection, marks, max_stale_sessions)

    typer.echo(f"portfolio positions as of {projection.as_of.isoformat()} (account {label})")
    typer.echo(f"  {projection.replayed_event_count} replayed event(s); effective-date reconstruction, not a historical-knowledge proof")
    pruned_cut = _pruned_history_cut(events)
    if pruned_cut is not None:
        typer.echo(
            f"  PRUNED HISTORY: this ledger contains a prune checkpoint at {pruned_cut.isoformat()}; quantities and basis "
            "are carried by that checkpoint, but realized gain and every event-level detail before the cut cannot be "
            "reconstructed from this ledger (an unavailable realized gain is not zero)"
        )
    for position in valuation.positions:
        _echo_position_block(position)
    typer.echo(f"  cash {_money(valuation.cash)}")
    typer.echo(f"  marked position value {_money(valuation.marked_position_value)} (valid marks only)")
    if valuation.complete:
        typer.echo(f"  NAV {_optional_money(valuation.nav)}")
        typer.echo(f"  unrealized gain {_optional_money(valuation.unrealized_gain)}")
        typer.echo(f"  total gain {_optional_money(valuation.total_gain)}")
    else:
        typer.echo("  NAV unavailable: required valuation coverage is incomplete (see blockers); a partial subtotal is not a total")
    typer.echo(f"  staleness bound: {valuation.max_stale_sessions} completed session(s); {_MONEY_NOTE}.")
    typer.echo("  upstream provenance of these book marks is not a research price-source certification.")

    if not valuation.complete:
        _fail_incomplete("positions", valuation)
        raise typer.Exit(1)


@portfolio_app.command("summary")
def portfolio_summary(
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Required timezone-aware UTC valuation instant, e.g. 2024-01-05T21:00:00Z; the clock is never a default"),
    ] = None,
    marks: Annotated[
        Path | None,
        typer.Option("--marks", help="Required stored book-mark JSON (version 1): read and validated, never created or fetched"),
    ] = None,
    account: Annotated[
        str,
        typer.Option("--account", help="Neutral local account label, e.g. main; an identifier, never a real account number"),
    ] = DEFAULT_ACCOUNT_LABEL,
    max_stale_sessions: Annotated[
        int,
        typer.Option(
            "--max-stale-sessions",
            help="Completed exact-venue sessions a mark may be old: 1 (default) or a stricter 0; never above 1",
        ),
    ] = DEFAULT_MAX_STALE_SESSIONS,
) -> None:
    """Report cash, flows, fees, income, gains and NAV at one instant.

    Combines the deterministic replay with the stored marks: cash, external
    flows, embedded and standalone fees (each counted once), net dividend
    income, realized gain, the valid marked subtotal, and — only when every
    required mark is valid — complete NAV and allocation.

    Gross dividend income and withholding are **unavailable**, not zero: ledger
    schema v1 records net dividend cash only. Total book gain is realized plus
    unrealized gain plus net dividend income less standalone fees; purchase and
    sale fees are already inside those gains and are never deducted twice. No
    time-weighted, money-weighted, tax-lot or tax-return claim is made, and
    deposits/withdrawals are external flows, not profit.

    Exit codes: 0 when NAV is complete; 1 when ``--as-of`` or ``--marks`` is
    missing or invalid, the ledger or mark file is refused, or any required mark
    is missing, stale, conflicting or quantity-uncertain. The partial summary
    (cash, quantities, marked subtotal, blockers) is still printed, and a nonzero
    exit never means it was complete. Read paths create no database, directory,
    journal, mark file, checkpoint or cache, and private values appear only in
    this explicitly requested local report (never in logs or error text).
    """
    label = account.strip()
    if not label:
        _reject_read("summary", "--account must be a non-empty neutral label", ["use a short local identifier such as main"])

    projection, events = _projection("summary", label, as_of)
    valuation = _valuation("summary", projection, marks, max_stale_sessions)

    from equity_lake.portfolio import DIVIDEND_BREAKDOWN_UNAVAILABLE

    typer.echo(f"portfolio summary as of {projection.as_of.isoformat()} (account {label})")
    typer.echo(f"  {projection.replayed_event_count} replayed event(s); effective-date reconstruction, not a historical-knowledge proof")
    pruned_cut = _pruned_history_cut(events)
    pruned_note = ""
    if pruned_cut is not None:
        pruned_note = " [pruned history: excludes events before the checkpoint cut; not an account total]"
        typer.echo(
            f"  PRUNED HISTORY: this ledger contains a prune checkpoint at {pruned_cut.isoformat()}; deposits, withdrawals, "
            "standalone fees, net dividend income and realized gain before that cut were collapsed into the checkpoint and "
            "cannot be reconstructed from this ledger (those collapsed sums are not zero)"
        )
    typer.echo(f"  cash {_money(valuation.cash)}")
    typer.echo(
        f"  external flows: deposits {_money(projection.deposits)}, withdrawals {_money(projection.withdrawals)}, "
        f"net {_money(projection.net_external_flow)} (external flows, never profit){pruned_note}"
    )
    typer.echo(
        f"  fees counted once: embedded {_money(projection.embedded_fees)}, standalone {_money(projection.standalone_fees)}"
        f" (embedded purchase/sale fees are already inside realized and unrealized gain){pruned_note}"
    )
    typer.echo(f"  net dividend income {_money(valuation.net_dividend_income)}{pruned_note}")
    typer.echo(f"  gross dividend income and withholding {DIVIDEND_BREAKDOWN_UNAVAILABLE}")
    typer.echo(f"  realized gain {_optional_money(valuation.realized_gain)}{pruned_note}")
    typer.echo(f"  marked position value {_money(valuation.marked_position_value)} (valid marks only)")
    if valuation.complete:
        typer.echo(f"  NAV {_optional_money(valuation.nav)}")
        typer.echo(f"  unrealized gain {_optional_money(valuation.unrealized_gain)}")
        typer.echo(f"  total gain {_optional_money(valuation.total_gain)}")
        if valuation.allocation is None:
            typer.echo("  allocation unavailable: NAV is zero, so no share of the book is defined")
        else:
            rendered = ", ".join(f"{name} {format(weight, 'f')}" for name, weight in valuation.allocation.items())
            typer.echo(f"  allocation of NAV: {rendered}")
    else:
        typer.echo("  NAV unavailable: required valuation coverage is incomplete (see blockers)")
        typer.echo("  unrealized gain and total gain unavailable: a partial marked subtotal is not a total")
        # ADR-0026 decision 5: the affected instruments are named in the report.
        typer.echo("  affected instruments and blockers:")
        for blocker in valuation.blockers:
            typer.echo(f"    {blocker}")
    typer.echo(f"  staleness bound: {valuation.max_stale_sessions} completed session(s); {_MONEY_NOTE}.")

    if not valuation.complete:
        _fail_incomplete("summary", valuation)
        raise typer.Exit(1)


@portfolio_app.command("reconcile")
def portfolio_reconcile(
    as_of: Annotated[
        str | None,
        typer.Option("--as-of", help="Required timezone-aware UTC snapshot instant, e.g. 2024-01-05T21:00:00Z; the clock is never a default"),
    ] = None,
    statement: Annotated[
        Path | None,
        typer.Option("--statement", help="Required canonical statement CSV: read and validated, never created or mapped from a broker export"),
    ] = None,
    account: Annotated[
        str,
        typer.Option("--account", help="Neutral local account label, e.g. main; an identifier, never a real account number"),
    ] = DEFAULT_ACCOUNT_LABEL,
) -> None:
    """Reconcile one canonical statement snapshot against the replayed ledger.

    The statement is one canonical UTF-8 CSV with the columns
    account,as_of,line_type,market,mic,symbol,currency,quantity,cash: exactly one
    CASH row, optionally several POSITION rows, USD only, and one common aware
    snapshot instant equal to ``--as-of``. No price marks are needed and no
    broker mapping or currency conversion exists.

    Every line is reported as ``matched``, ``mismatched`` or ``unavailable`` with
    both quantities/amounts, the signed ``statement - ledger`` delta and the
    applied tolerance: 0.00000001 share and 0.01 USD, applied inclusively to
    unrounded differences. A statement-only position is compared with ledger
    quantity zero; a ledger holding the statement omits, a statement without a
    cash line, or a snapshot-time disagreement is ``unavailable`` — never an
    implicit zero. Overall state takes the worst line: unavailable beats
    mismatched beats matched.

    Reconciliation never mutates ledger state, inserts a balancing event, adjusts
    a mark or widens a tolerance to force agreement, and it is not tax
    certification.

    Exit codes: 0 only when every line matched; 1 when ``--as-of`` or
    ``--statement`` is missing or invalid, the statement is malformed or
    contradictory, the ledger is refused, or any line is mismatched or
    unavailable. All line outcomes are still printed. Read paths create no
    database, directory, journal or cache, and private values appear only in this
    explicitly requested local report.
    """
    from equity_lake.portfolio import ReconcileLineType, StatementError, parse_statement_file, reconcile

    label = account.strip()
    if not label:
        _reject_read("reconcile", "--account must be a non-empty neutral label", ["use a short local identifier such as main"])
    instant = _parse_as_of("reconcile", as_of)
    if statement is None:
        _reject_read("reconcile", "--statement is required", ["pass a canonical statement CSV; the ledger is never balanced to agree"])
    path = Path(statement)
    if not path.exists():
        _reject_read(
            "reconcile",
            "--statement names a file that does not exist",
            ["pass an existing canonical CSV; a read path never creates one (the name is not echoed: it may contain private text)"],
        )
    if not path.is_file():
        _reject_read("reconcile", "--statement does not name a regular file", ["--statement must name a canonical statement CSV"])

    events = _load_stored_events("reconcile")

    from equity_lake.portfolio import ProjectionError, replay_ledger

    try:
        projection = replay_ledger(events, account=label, as_of=instant)
    except ProjectionError:
        _reject_projection("reconcile")
    # Reconciliation compares supplied statements against ledger events; a pruned window no
    # longer holds them, so the report says so instead of implying a complete comparison.
    pruned_cut = _pruned_history_cut(events)
    if pruned_cut is not None:
        typer.echo(
            f"  PRUNED HISTORY: events before the checkpoint cut {pruned_cut.isoformat()} were collapsed and are not available to reconciliation"
        )
    try:
        parsed = parse_statement_file(path)
    except StatementError as exc:
        _reject_read("reconcile", "the statement file was rejected", [str(exc)])
    try:
        report = reconcile(projection, parsed)
    except StatementError as exc:
        _reject_read("reconcile", "the statement cannot be reconciled against this book", [str(exc)])

    typer.echo(f"portfolio reconcile at {projection.as_of.isoformat()} (account {label})")
    typer.echo(f"  statement snapshot {parsed.as_of.isoformat()}")
    for line in report.lines:
        render = _money if line.line_type is ReconcileLineType.CASH else _quantity
        prefix = "cash" if line.instrument is None else f"quantity {line.label}"
        statement_value = "unavailable" if line.statement_value is None else render(line.statement_value)
        delta = "unavailable" if line.delta is None else render(line.delta)
        typer.echo(
            f"  {prefix}: statement {statement_value}"
            f", ledger {render(line.ledger_value)}"
            f", delta {delta}"
            f", tolerance {format(line.tolerance, 'f')}, {line.state.value} ({line.reason})"
        )
    typer.echo(f"  reconciliations are comparisons, not tax certification; {_MONEY_NOTE}.")
    typer.echo(f"  overall: {report.state.value}")

    if not report.matched:
        typer.secho(
            f"portfolio reconcile: overall {report.state.value} across {len(report.lines)} line(s)",
            fg=typer.colors.YELLOW,
            err=True,
        )
        typer.secho("  the ledger was not adjusted and no balancing event was inserted", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1)
