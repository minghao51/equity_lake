# ADR-0042 — Defer in-place SQLite compaction

**Status:** Proposed (owner review; no compaction run authorized).

**Recorded:** 2026-11-27

## Context

[ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md) decision 5 prefers
`VACUUM INTO` and admits in-place `VACUUM` under specific conditions. The supported
copy-based compaction is implemented; the residual plan deliberately excludes a new
in-place path for lack of measured need. This is SQLite ledger maintenance, not Delta
VACUUM and not event or backup-file deletion.

## Options

- **Do nothing:** right when space and replay performance are acceptable.
- **Use the existing verified `VACUUM INTO` path:** copies out, verifies, then replaces
  under ADR-0031; needs staging space and separate run approval.
- **Implement an in-place path:** changes the live file under stronger operational
  controls and still needs at least twice the database size in free space under ADR-0031.

## Decision (recommended)

Do not implement or schedule in-place compaction. Measure file size, reclaimable pages,
available space and a representative replay/compaction time before reconsidering. First
try the copy-based path on an isolated copy if there is a concrete need. Insufficient
disk for a fresh copy is not a reason to bypass ADR-0031's stronger guard.

Any later in-place proposal needs positive writer-exclusion evidence through the
maintenance window, a verified backup and successful isolated restore rehearsal, at
least twice the database size in free space, and matching integrity, schema version,
row count and record digests afterward. State what happens on interruption or failed
verification; never retry blindly or overwrite the only recovery copy.

## Consequences

No new command or operational burden. A measured case still has a review path, but
accepting a mechanism would not authorize an actual run.
