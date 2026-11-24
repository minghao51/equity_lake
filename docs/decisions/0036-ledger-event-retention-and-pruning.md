# ADR-0036 — Ledger event retention and pruning: checkpoint collapse

**Status:** Accepted (2026-11-24) by the owner. **Not implemented** — this record precedes the
code on purpose. The intended command shape `equity portfolio prune --before <instant>
[--apply]` is the plan for a future implementation, **not an existing command**; nothing in the
repository prunes, deletes or checkpoints a ledger event today. Acceptance authorizes nothing to
be run against a real ledger.

**Recorded:** 2026-11-24

**Accepted:** 2026-11-24 by the owner as a decision record only. What it settles: retention
stays operator-invoked, pruning means checkpoint collapse rather than row deletion, the
preconditions under which a prune is lawful, exactly what a prune preserves and what it
discards, the manifest every prune must write, and idempotence. What it does not settle: the
implementation, its tests, and any instruction to actually prune a ledger — each of those is a
later act.

**Scope:** closes the retention question ADR-0024 decision 3 and ADR-0031 decision 6 left
unwritten ("deletion and retention remain unauthorized"; "a future retention decision needs its
own ADR"). This is that record. It defines the *manual, explicit* mechanism in the same spirit
as ADR-0031's migration/restore/compaction: a printed plan first, verified preconditions,
refusals instead of best effort.

**Amends:** [ADR-0024](0024-private-portfolio-ledger-and-accounting.md) decision 3 ("migration,
compaction and deletion are not authorized by this record") and
[ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md) decision 6 ("Deletion stays
unauthorized"), **for operator-invoked checkpoint-collapse pruning only**. Everything else in
those records stands unchanged: the append-only event model and the correction-by-reversal rule
(ADR-0024 decisions 4–5) govern normal operation, and ADR-0031 decision 3 (manual local verified
backup, no remote/cloud backup) still governs backups.

## Context

The private ledger at `data/portfolio/portfolio.sqlite3` is the book of record: an append-only
event log whose balances, quantities, basis and P&L are derived by deterministic replay
(ADR-0024 decision 4), kept verifiable by `verify_ledger_file` and the
`(account, external_id, content_digest)` record digest (ADR-0031). Until now every event was
kept forever by design ("There is also no retention policy", ADR-0031 context) and any deletion
was unauthorized (ADR-0031 decision 6). That is correct for an audit record but gives an
unbounded history an unbounded on-disk and replay cost.

Naive deletion is not an option. The ledger's accounting contract (ADR-0024 decision 6) derives
average-cost basis and realized gain from the whole event stream: delete the events before some
date and the positions' basis, and every realized gain already taken, are destroyed with them —
a replay would report a book that never existed. So any lawful reduction of history must
preserve the accounting state exactly, and must prove it, or refuse.

## Decision

1. **Retention stays operator-invoked.** There is no automatic, scheduled or background pruning
   and none is introduced here. ADR-0031 decision 3 still governs backups: manual, local,
   verified exact-file copies; no remote or cloud backup. Every prune requires the verified
   backup of decision 3 as a precondition precisely because pruning is irreversible.

2. **Pruning is checkpoint collapse, not row deletion.** A prune run performs, in order:
   (a) the precondition checks of decision 3, refusing when any fails;
   (b) a verified backup whose sha256 is recorded (decision 3 preconditions and decision 5);
   (c) a replay of the ledger to the cut instant, producing the accounting state — cash and,
   for every open position, quantity and basis exactly as the replay derives them (an
   explicitly unknown basis stays unknown and is never back-filled: ADR-0024 decision 6);
   (d) **one** `OPENING` checkpoint event written at the cut instant carrying that state
   (the `OPENING` kind of ADR-0024 decision 4, which already carries cash and/or positions
   with basis);
   (e) deletion of the events **strictly before** the cut — never the checkpoint, never an
   event at or after the cut.

   Deleting events without writing the checkpoint is rejected by construction: it would destroy
   basis and realized gain (see Context). A prune whose replay cannot prove that the checkpoint
   equals the state the deleted events produced must refuse (decision 7).

3. **Preconditions — all refused when unmet.** A prune refuses unless all four hold:
   (a) the ledger verifies (`verify_ledger_file`: version, row count, record digest,
   `PRAGMA integrity_check`);
   (b) a verified backup of the current file exists and its sha256 is recorded — pruning is
   irreversible and the backup is the only reversal;
   (c) the cut is a timezone-aware UTC instant strictly in the past (ADR-0024 decision 4's
   timestamp contract);
   (d) the target is the operator's own ledger — the one private account of ADR-0024 decision 1,
   not a sample, demo, fixture or foreign file. As in ADR-0031, every unmet precondition is a
   named refusal, never a best effort.

4. **What a prune preserves, and what it must disclose it lost.** Preserved **exactly** at and
   after the cut: cash, per-position quantity, basis, marked value and NAV. **Not** preserved:
   the historical breakdown of deposits, withdrawals, standalone fees, net dividend income and
   realized gain before the cut — those sums are collapsed into the checkpoint and cannot be
   re-derived event by event. Any surface that presents that breakdown (notably `equity
   portfolio summary`) must disclose that it is presenting a **pruned history** from the
   checkpoint; it must never show zeros for the collapsed categories as if the events had never
   happened.

5. **Every prune writes a manifest, and the ledger records the pruned boundary.** The manifest
   is an auxiliary artifact under `DATA_DIR` (ADR-0006: not cataloged, not pointblank-validated)
   with a Pydantic model at its write boundary. It records: the cut instant, the sorted pruned
   event ids, the record digest before and after, the backup sha256, the tool version, and a UTC
   timestamp. The ledger itself also records the pruned boundary, so a later reader cannot
   mistake the truncated history for a complete one; the manifest is the audit trail, not the
   only marker. The exact representation of the boundary record is fixed at implementation time
   within the v2 schema; this record fixes the requirement, not the column.

6. **Idempotence.** Re-running a prune against a cut that has already been pruned is a **no-op**:
   it reports the existing checkpoint and writes nothing. It never produces a second checkpoint
   and never deletes anything at or after the cut (same rule as ADR-0024 decision 5's
   identical-content no-op).

7. **The accounting invariant is what makes this safe.** A prune is accepted only when replay of
   the deleted events equals the checkpoint it leaves behind — the same replay-equals-record
   discipline as ADR-0031's migration and compaction equality checks. A prune that cannot prove
   the equality refuses; it never "fixes up" the checkpoint to agree.

8. **Every action is a printed plan first.** Following ADR-0031 decision 7, a prune supports a
   report-only mode that prints what it would do (cut, event count and ids to be pruned,
   checkpoint contents, preconditions and their expected values, manifest fields) and writes
   nothing; the `--dry-run` rule of ADR-0024 decision 13 applies (no file, no backup, no
   directory, no journal). The intended CLI shape is `equity portfolio prune --before <instant>
   [--apply]` — report-only by default, `--apply` performing the run. **This command does not
   exist today and is not created by this record.**

9. **Change matrix and stop condition when this is implemented** (storage row + a boundary the
   ledger owns): `portfolio/store.py` (checkpoint write, boundary record, prune entry point and
   its verification helpers), the manifest Pydantic model at its write boundary (ADR-0006), the
   `prune` command with its report-only mode and CLI help-scan case, tests for every refusal in
   decision 3, checkpoint-equality (decision 7), idempotence (decision 6) and the summary
   disclosure (decision 4), and the private-ledger user guide plus CLI reference. No lake schema
   change, no catalog row, no `Settings` key, no new dependency. Acceptance of this record
   authorizes building the mechanism only; an actual prune run against a real ledger needs its
   own owner instruction, exactly like ADR-0031's mechanisms.

## Consequences

- An unbounded event history gains a bounded on-disk and replay cost: after a prune the ledger
  holds one checkpoint plus the events from the cut forward, however long the pruned history
  was.
- Old external statements and old marks can no longer be reconciled against a pruned window:
  `equity portfolio reconcile` (ADR-0024 decision 10) compares supplied statements against
  ledger events, and those events are gone. This is the real, permanent cost of a prune and is
  why the verified backup precondition exists — the bytes survive in the backup, but the ledger
  no longer serves them.
- The collapsed categories (deposits, withdrawals, standalone fees, net dividend income,
  realized gain before the cut) become unavailable rather than zero; surfaces that fail to
  disclose this are defective (decision 4).
- Safety rests entirely on the accounting invariant: replay equals the checkpoint. A prune that
  cannot prove it must refuse (decision 7), which is also what keeps unknown basis honest — an
  unprovable checkpoint is a refusal, never a fabricated basis.
- The append-only contract keeps its meaning: normal operation never edits or deletes events
  (ADR-0024 decision 5), and a prune is one operator-invoked, verified, recorded boundary act —
  never a maintenance job.

## Alternatives considered

- **Delete-only (drop events before the cut, no checkpoint).** Rejected: it destroys average-cost
  basis and every realized gain already taken; a replay after deletion reports a book that never
  existed. This is the reason checkpoint collapse is the only accepted form.
- **Archive raw events to files, then delete them.** Rejected: a file archive sits outside the
  ledger's verification and replay contract — nothing replays it, nothing proves it against the
  record digest, and it invites treating unverified files as ledger history. The verified backup
  of decision 3 already preserves the bytes; what pruning owes the reader is a correct book at
  the cut (the checkpoint) and a recorded boundary (the manifest), not a second, weaker archive.
- **`VACUUM INTO` alone (compaction without pruning).** Rejected as a retention mechanism: it is
  already supported (ADR-0031 decision 5) and rebuilds the file without deleting anything —
  it reduces bytes from fragmentation, not the length of the history. ADR-0031 is explicit that
  compaction "is never a retention step, and never deletes events".
- **Automatic age-based retention.** Rejected: an owner decision, not a background job. Every
  artifact in this repository's private book — backup, restore, migration, compaction — is
  operator-invoked on purpose (ADR-0024 decision 3, ADR-0031 decision 7), and a scheduled deleter
  would be the one mechanism that acts without an instruction.

## References

- [ADR-0024](0024-private-portfolio-ledger-and-accounting.md) — the accounting contract this
  record preserves: append-only events and deterministic replay (decision 4), average-cost basis
  and realized gain (decision 6), reconciliation (decision 10), and the storage/authorization
  boundary (decision 3) this record amends.
- [ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md) — verified backup
  (decision 3), the report-only default and dry-run rule (decision 7), the named-refusal style
  (decision 8), the compaction-not-retention rule (decision 5), and decision 6's "a future
  retention decision needs its own ADR" — which is this record.
- [ADR-0006](0006-auxiliary-data-paths.md) — the prune manifest as an auxiliary artifact with a
  Pydantic model at its write boundary.
- [Residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md) §2.5 and the
  owner decisions of 2026-11-24 (§11) — where event pruning was previously `deferred-invariant`
  and is now approved against this record.
- `src/equity_lake/portfolio/store.py` — `verify_ledger_file`, the record digest and the
  verified-backup helpers the future prune entry point must build on (as implemented today;
  nothing there prunes).
