# ADR-0041 — Keep ledger-backup deletion separate from event retention

**Status:** Proposed (owner review; no deletion authorized).

**Recorded:** 2026-11-27

## Context

[ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md) defines manual
verified backup retention and the project has a report-only inventory. Backup files are
not ledger events; [ADR-0036](0036-ledger-event-retention-and-pruning.md) has separate
authority and preconditions for event pruning.

## Options

- **Inventory only:** avoids irreversible loss; disk usage is not bounded.
- **Exact-file manual cleanup:** can relieve measured disk pressure while preserving
  recovery points; requires a separate reviewed deletion action.
- **Age/count-based automation:** cheaper to operate, but filenames, age and counts
  alone cannot prove restorability or account/version identity.

## Decision (recommended)

Keep inventory-only until disk pressure or an owner retention requirement is measured.
If it is, return with a manual exact-file policy: protect the newest verified copy per
version in use, pre/post-migration recovery points, backups referenced by prune manifests,
and any owner hold. Never remove the last verified recovery copy for an active account or
version. Off-host presence alone is not evidence of successful recovery.

A candidate report would bind identities, exact contained paths and digests, versions,
reason for removal, and the recovery copies that remain. Refuse on incomplete inventory,
unknown files, symlinks/aliasing, changed digests, unverifiable required copies, live
maintenance or missing authorization for those exact targets. Recheck against the approved
report before deletion and retain an audit record. Do not touch the active ledger, any
journal, or backups outside the approved directory. No automatic purge or bulk override.

## Consequences

The safer default uses disk; material savings require evidence and explicit action.
This draft does not change ADR-0031 retention or authorize deletion of any file or event.
