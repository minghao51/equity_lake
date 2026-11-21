# ADR-0031 — P00-4: backup, restore, schema migration and compaction for the private ledger

**Status:** Accepted (2026-11-16). **All nine decisions are implemented** as of 2026-11-17;
decisions 4 (restore) and 5 (compaction) were the last two and shipped with their own tests.
Implementation of a mechanism never authorizes a *run* against a real ledger.

**Recorded:** 2026-11-16

**Accepted:** 2026-11-16 by the owner, who also authorized implementing the mechanism. What
landed: schema **v2** with `SPLIT` in the `event_type` `CHECK`, a v1 ledger refused on read and
write with a message naming the upgrade, `verify_ledger_file` (integrity, version, row count,
record digest, file SHA-256), the verified exact-file backup, `migrate_ledger` with a
report-only default and a transactional rebuild that proves the record unchanged, and
`equity portfolio migrate [--apply]`. The store's constraint rejection is now reported as a
schema/contract mismatch instead of "could not be read … retry". Round-trip, refusal-matrix,
error-mapping and CLI tests shipped with it (spec §10, P00-4 record). Restore stays a
documented manual procedure (`verify_ledger_file` plus an atomic copy) and compaction stays
undefined; neither was implemented here.

**Implementation note 2026-11-17 — decisions 4 and 5 shipped.** `restore_ledger`
(`equity portfolio restore --from <verified backup> [--apply]`) re-runs the five verification
checks on the candidate, refuses a candidate that is the current file, a different account
label (ADR-0024 decision 1), a `-journal` beside either file, and insufficient free space;
`--apply` backs up the **current** file first, replaces through a temporary file in the same
directory with `os.replace`, re-applies `0600`/`0700` and re-verifies the result. The report
names what the restore rolls back (event count and newest `recorded_at`). `compact_ledger`
(`equity portfolio compact [--apply]`) rebuilds with `VACUUM INTO` into a fresh file and is
accepted only when `user_version`, the row count and the `(account, external_id,
content_digest)` digest match the pre-compaction state, then applies the restore procedure so
the pre-compaction file is kept as a verified backup. Both are report-only by default (decision
7: no file, backup, temp file, directory or journal), and neither ever runs automatically or
deletes an event. `row_id` renumbering carries no meaning, as decision 5 requires. 18 tests
(`tests/unit/test_portfolio_restore_compaction.py`) cover each refusal, the rollback report, the
atomic replacement, the identical-candidate no-op, restoring onto a missing ledger, the
record-preserving post-conditions and both CLI modes (spec §10).

**Scope:** defines the *manual, explicit* mechanisms that ADR-0024 decision 3 deliberately left
unwritten ("migration, compaction and deletion are not authorized by this record"). It does
not authorize a migration, a restore or a compaction **run** — each needs its own owner
instruction, and this record's acceptance authorized building the mechanism only.

**Amends:** [ADR-0024](0024-private-portfolio-ledger-and-accounting.md) decision 3 (storage
constraints and the "nothing happens automatically" rule), whose P00-4 row asks for
"mechanism, timing, verification". It also corrects one assumption in
[ADR-0029](0029-split-treatment-ledger-and-raw-price-basis.md) decision 9 — see "Why this is
now decision-critical".

## Context

The private ledger is a single SQLite file at `data/portfolio/portfolio.sqlite3` — never under
`data/lake/`, never cataloged, never pointblank-validated (ADR-0024 decision 3). Its enforced
properties, read from `src/equity_lake/portfolio/store.py`, are what any backup, restore,
migration or compaction must preserve:

| Property | Enforced value today |
| --- | --- |
| Location / modes | `DATA_DIR/portfolio/portfolio.sqlite3`; directory `0700`, database and any sidecar `0600` |
| Journal | rollback journal (`PRAGMA journal_mode=DELETE`), `synchronous=FULL`, `busy_timeout=10s` |
| Tables | exactly one: `ledger_events`, `row_id INTEGER PRIMARY KEY AUTOINCREMENT` |
| Logical order | `(account, sequence, external_id)`; `row_id` is not semantic |
| Identity | `UNIQUE (account, external_id)`; `UNIQUE (account, sequence)` |
| Numeric contract | money/quantity/price are exact decimal **text**, never SQLite `REAL` |
| Integrity fields | `source_digest`, `content_digest` (both 64 hex), `recorded_at`, `parser_version` |
| Schema gate | `PRAGMA user_version` must equal `PORTFOLIO_SCHEMA_VERSION` (1); a read of any other version is refused |
| Column gate | the physical column order is compared with `_LEDGER_COLUMN_ORDER` on every writable connect |
| Reads | `file:…?mode=ro` URI; a missing ledger is refused, never created; nothing is written by a read or a dry run |
| Writes | one `BEGIN IMMEDIATE` transaction per batch; identical content is a no-op, changed content rolls the batch back |

Nothing in the repository backs up, restores, migrates, compacts, vacuums or deletes this file,
and ADR-0024 decision 3 guarantees only that none of it happens automatically. There is also
no retention policy: the ledger is the private book of record and every event is kept.

## Why this is now decision-critical

ADR-0029 decision 9 added `SPLIT` to the closed event set and stated "no migration", reasoning
that the ratio travels in the existing `quantity` column so no column changes. That reasoning
covers the *column set* but not the *constraint set*: `_SCHEMA_SQL` still enumerates the seven
pre-split kinds in a `CHECK` constraint, and SQLite cannot widen a `CHECK` in place. Measured
on 2026-11-16 against a temporary ledger (no repository data touched):

```text
OPENING only (control)           -> APPENDED inserted=1; reloaded=1
OPENING then SPLIT (real flow)   -> IntegrityError: CHECK constraint failed:
                                    event_type IN ('OPENING', 'BUY', 'SELL', 'DEPOSIT',
                                                   'WITHDRAWAL', 'FEE', 'CASH_DIVIDEND')
SPLIT alone                      -> IntegrityError: (same)
```

So the accepted ledger contract (`validate_batch`, `replay_ledger`, marks and the 20 split
tests) is correct in memory, but **a split cannot be persisted.** The batch validates, then the
append's `CHECK` violation rolls the whole transaction back — atomic, nothing written, which is
right — and the CLI catches it as `sqlite3.Error` and exits 1. The diagnosis is the real
defect: measured end to end on a temporary ledger (2026-11-16), `portfolio import` reports

```text
portfolio import: the private ledger could not be read (IntegrityError)
  another process may hold the ledger; retry once it is free
portfolio import: nothing was written.
```

for a file that is perfectly readable — a `CHECK` rejection is not a read failure, and the
retry advice can never succeed. Because `CREATE TABLE IF NOT EXISTS` never alters an existing
table, updating the constant alone would fix only databases created afterwards and leave every
existing v1 file silently narrower than the code — a divergence this project has refused
elsewhere (ADR-0017).

Consequence for the queue: the persisted half of ADR-0029 Part II is **blocked on this ADR**,
and the fix is a table rebuild, i.e. the migration ADR-0029 believed it did not need.

## Decision

1. **Two versions, one upgrade path.** The schema becomes **v2**: identical columns and
   indexes, with `SPLIT` added to the `event_type` `CHECK`. `PORTFOLIO_SCHEMA_VERSION` becomes
   `2`. Code refuses any `user_version` it does not implement — already true on the read path
   and kept for writes — so a v2 file opened by v1 code fails loudly instead of being
   misinterpreted. There is **no down-migration**: the only reversal of an upgrade is
   restoring the pre-upgrade backup (decision 2).
2. **Migration is explicit, offline, verified and reversible.** v1 → v2 is one operator-invoked
   action that performs, in order:
   (a) the precondition checks of decision 4;
   (b) a **verified exact-file backup** (decision 3) named `portfolio.sqlite3.<UTC>.v1.bak`
   beside the file, mode `0600`;
   (c) one transaction: `CREATE TABLE ledger_events_v2 (…)` with the v2 `CHECK`, `INSERT INTO
   … SELECT` the 23 v1 columns in their stored order (`row_id` included, so the physical order
   is preserved — decision 5 makes it non-semantic either way), `DROP TABLE ledger_events`,
   `ALTER TABLE … RENAME`, recreate `ledger_events_sequence`, `PRAGMA user_version=2`;
   (d) inside the same transaction, the equality checks of decision 4 (row count and the
   `(account, external_id, content_digest)` set before vs after) — any difference rolls back;
   (e) after commit, `PRAGMA integrity_check` and `PRAGMA foreign_key_check` on a read-only
   open, modes re-applied, and the result reported. Re-running on a v2 file is a no-op that
   verifies and exits successfully.
3. **A backup is not a backup until it is verified.** Backup = exact-file copy taken in a
   no-writer window. Because the ledger keeps a rollback journal, there is no `-wal`/`-shm`
   sidecar to copy; a `-journal` file present means an interrupted transaction, so the backup
   refuses instead of copying a half-written state. Every backup is accepted only after all
   five checks pass: file SHA-256 recorded; read-only open succeeds; `user_version` is the
   expected version; the row count matches the source; and every `content_digest` recomputes
   from the stored fields. Retention is manual: keep the newest **verified** backup per
   version, and never delete the last verified copy of a version still in use. No automatic,
   scheduled, remote or cloud backup is introduced.
4. **Restore verifies before it replaces, and never destroys the only newer copy.** A restore
   selects an existing verified backup, re-runs the five checks, backs up the *current* file
   first (it may hold events the backup lacks), then replaces atomically with a temp file in
   the same directory plus `os.replace`, re-applies `0600`/`0700`, and re-verifies. It refuses
   when: the candidate is unverified or of an unimplemented version; the candidate's account
   label differs from the current ledger (one private book, one account — ADR-0024 decision 1)
   and no documented account change exists; a `-journal` is present; or any writer is live.
   A restore is reported with what it rolled back (event count and newest `recorded_at`), so the
   operator can see what was given up.
5. **Compaction preserves the record, not the file.** Preferred form is `VACUUM INTO` a fresh
   verified copy followed by the restore procedure of decision 4; in-place `VACUUM` is allowed
   only with a verified backup in place, a no-writer window, and free space of at least twice
   the database size. A compaction is accepted only if the row count, the
   `(account, external_id, content_digest)` set, `user_version` and `PRAGMA integrity_check`
   all match the pre-compaction state. `row_id` may be renumbered by a rebuild and carries no
   meaning (decision: logical order is `(account, sequence, external_id)`); nothing may depend
   on it. Compaction is never automatic, never a retention step, and never deletes events.
6. **Deletion stays unauthorized.** This record does not introduce retention, pruning, archival
   or deletion of events; ADR-0024 decision 3 remains authoritative there. A future retention
   decision needs its own ADR.
7. **Every action is a printed plan first.** Each mechanism supports a report-only mode that
   prints what it would do (version in/out, paths, byte counts, row count, checks and their
   expected values) and writes nothing. The `--dry-run` rule of ADR-0024 decision 13 applies:
   a dry run creates no file, no backup, no directory and no journal.
8. **Failure modes are named, not handled optimistically** — the "Failure modes" table below
   is normative: every row is a refusal, never a best effort.

9. **Change matrix when this is implemented** (storage row + a boundary the ledger owns):
   `portfolio/store.py` (v2 `CHECK`, `PORTFOLIO_SCHEMA_VERSION = 2`, the migration/backup/
   restore/compaction entry points and their verification helpers), a **v1 fixture** plus
   tests for: migration equality, every refusal in decision 8, backup verification, restore
   round-trip over a current file with newer events, compaction post-conditions, dry-run
   no-write; and the error mapping the v1 `CHECK` exposed (a constraint violation must surface
   as a named schema/contract mismatch, not as "could not be read"); the private-ledger user
   guide and the CLI reference for any command added; no
   lake schema change, no catalog row, no `Settings` key, no new dependency. If a command is
   added, `tests/unit/test_cli_unified.py` gains its help-scan case and `cli/_app.py` its
   sub-app.
10. **Stop condition.** Acceptance authorizes building the mechanism and the v2 schema, not
    running anything against a real ledger; a migration run, a restore and a compaction each
    need their own instruction. Until the v2 store lands, imports containing `SPLIT` fail at
    the storage boundary and the ledger contract must be described as
    "validated in memory, not yet persistable".

### Failure modes (decision 8)

| Failure | Detection | Required behaviour |
| --- | --- | --- |
| Unknown `user_version` (v1 file with v2 code refusing, or v3+) | version gate | Refuse with the versions it supports; never rebuild, never "upgrade" silently |
| Column order/length drift | `PRAGMA table_info` vs contract | Refuse to migrate or append; no data change |
| Stored row violates the contract (bad decimal, unknown kind) | re-validation of every row | Refuse; the ledger is repaired from a verified backup, never edited |
| `-journal` present | file stat | Refuse backup/restore/migration/compaction until the previous writer has finished |
| `busy`/locked database | SQLite `OperationalError` | Refuse; no retry loop, no forced open |
| Not a SQLite file / corrupt | `DatabaseError`, `integrity_check` ≠ ok | Refuse and preserve the bytes for inspection; never recreate empty |
| Insufficient free space (in-place `VACUUM`, migration rebuild) | pre-check against 2× size | Refuse before any write |
| Backup verification mismatch (count, digest, sha) | decision 3 checks | Treat the backup as failed: keep the source, delete or clearly mark the bad copy, refuse the operation |
| Rollback after a failed step | transaction state | The pre-step file must be byte-identical; the verified backup is the fallback |
| A contract-valid kind the schema `CHECK` rejects (v1 `SPLIT`) | `IntegrityError` raised inside the append transaction | Refuse with a **named schema/contract mismatch** (`LedgerStoreError`), never "could not be read" plus retry advice: retrying cannot succeed and masks the version drift |

## Consequences

- The `SPLIT` write path becomes functional only when the v2 store lands; until then the split
  contract is a validated, test-covered in-memory model, and the user guide must say so.
- A version bump makes the mismatch loud in both directions: v2 code refuses unknown versions
  and v1 code (an older checkout) refuses a v2 file rather than reading it half-blind.
- Backups are manual and verified, so their correctness is a property of the operation, not of
  a schedule. The cost is that nothing runs unless the owner runs it.
- The ledger stays one file with one table: no auxiliary index, provenance file or journal
  table is introduced, so a backup is still a single exact-file copy.
- Compaction is defined in terms of the logical record (digest set and order), which keeps it
  safe to renumber `row_id` — and forces anything reading the file directly to stop relying on
  `row_id`, which nothing does today.

## Alternatives considered

- **Keep v1 and never persist `SPLIT`** — rejected: the accepted ADR-0029 Part II contract would
  be unreachable from the only place it matters, and the CLI would keep producing a traceback.
- **Widen the `CHECK` without a version bump** — rejected: SQLite cannot alter a `CHECK` in
  place, so "widening" means rebuilding while claiming the same version; v1 and v2 files would
  be indistinguishable and the version gate would be a lie.
- **Recreate the ledger from the CSV instead of migrating** — rejected: the CSV is an input, not
  the record. It may be gone, edited or re-exported; `recorded_at`, `source_digest` and the
  reversal history are ledger facts, and a replay would silently change accepted content
  (ADR-0024 decision 5).
- **Move to WAL for easier hot backups** — rejected: a read-only WAL connection creates
  `-shm`/`-wal` files, which breaks ADR-0024's "read and dry-run write nothing" rule; the
  rollback journal makes a no-writer exact-file copy sufficient.
- **`VACUUM` on a schedule** — rejected: nothing about this artifact may run automatically, and
  compaction cannot be a retention mechanism by construction.
