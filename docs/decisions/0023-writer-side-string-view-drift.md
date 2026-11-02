# ADR-0023: Writer-Side Arrow `string_view` Drift

**Status:** Accepted (2026-10-31). Records that the storage-level drift R1
mitigated at the read seam is contained rather than eliminated. Acceptance does
not authorize a writer rewrite, a table migration, or a dependency change; R1
remains in force. The implementation follow-up is the direct-pushdown containment
in Decision 2.

**Recorded:** 2026-10-31

**Deciders:** Repository owner

**Related:** ADR-0001 (medallion layout), ADR-0017 (bounded additive evolution and
replay), ADR-0019 (Delta maintenance containment), [20261030-01 R1](../handoffs/20261030-01-postpreload-findings-and-remaining-issues.md),
[20261031-01 §4.3](../handoffs/20261031-01-next-thread-and-r12-watch.md)

## Context

R1 established that a delta-rs merge rewrite can store Delta string columns as
Arrow `string_view` while the Delta log still declares `string`. PyArrow 21 and
22 have no comparison kernels for `string_view`, so every Arrow string predicate
on an affected file raises `ArrowNotImplementedError`. R1 fixed every
`read_delta` caller by classifying string predicates and applying them in Polars,
made the drift observable in `equity doctor`, and measured the read trade. That
read seam is accepted and is not reopened here.

The storage-level cause is unchanged: `update_delta_columns` and
`merge_delta` execute `DeltaTable.merge(...)`. When the merge rewrites a matched
target file, delta-rs writes the string columns as `string_view`. The Delta log
does not change, so a schema check cannot detect the drift; only the Parquet
footers can. The rewrite is file-scoped: untouched files in the same table can
remain `large_string`, leaving one table with mixed encodings.

### Current state (2026-10-31, canonical synthetic lake)

| Table | Active files | Active columns stored as `string_view` | Stale files on disk |
|---|---:|---|---:|
| `01_bronze/market_data/us_equity` | 783 | none | 0 |
| `03_gold/features` | 61 | 10 (identity, market/mic, action-coverage columns, `price_adjustment_version`) | 193 |
| `04_platinum/predictions` | 20 | 13 (identity, direction, model/provenance and threshold columns) | 39 |

The stale files are unreferenced Delta history, not active table state. They
matter because `storage.delta.string_view_columns()` scans Parquet files on disk
(capped at the newest 50) rather than only the active snapshot, so `equity doctor`
can keep reporting drift after a normalizing rewrite until those files are
vacuumed. That is an observability detail here, not a reason to rewrite the lake.

### What was tested (isolated probes, not changes to the repository)

| Operation | Active file string type |
|---|---|
| Initial `write_delta` / full overwrite | `large_string` |
| `merge_delta` / `update_delta_columns` (matched rewrite) | `string_view` |
| `optimize.compact()` after merge | Existing `string_view` remains |
| Predicate-scoped overwrite of the affected date partition | `large_string` |
| Full `write_delta(mode="overwrite")` after merge | `large_string` for active files; stale files remain |

Changing `streamed_exec` or casting the merge source to `pa.string()` did not
change the result: the merge output is `string_view` either way. The latest
available upstream pair at probe time, `deltalake 1.6.6` + `pyarrow 26.0.0`, still
wrote `string_view` files on merge, and PyArrow 26 still had no comparison kernel
for a `string_view` array. Newer delta-rs did serve a filtered read in that probe,
but that masks the read failure; it does not eliminate the storage drift. A
dependency upgrade is therefore not a writer-side fix on its own.

R1 measured the read cost of residual string filtering at **0.88×** the pushdown
time for a 60-session `symbol in` window and **0.98×** for full history, with date
pushdown still doing the pruning. There is no measured read regression today that
would justify a risky write-path change.

## Options considered

**A. Keep the read seam, do not change the writer (recommended).** R1 remains the
contract. Existing drifted files stay readable, visible, and un-migrated. Add
containment for any remaining direct Arrow string pushdown that bypasses
`read_delta`.

**B. Replace matched updates with partition-scoped overwrite.** Read the affected
date partition(s), apply the matched updates in Polars, and commit with
`write_deltalake(mode="overwrite", predicate="date = …")`. This is proven to leave
`large_string` active files. It rewrites the whole affected partition rather than
only the matched rows/files, so its cost scales with partition size, and it needs
its own conflict/retry story: a read-modify-overwrite can lose a concurrent update
that the current merge would retry around. Applying it to `merge_delta` as well
would change ingestion semantics and conflict with ADR-0017's preference for
native additive evolution without materializing unrelated rows.

**C. Merge, then normalize affected partitions.** Keep the merge, then overwrite
the affected partition(s) from the post-merge rows in a second commit. Proven to
normalize active files, but it doubles I/O and creates a two-commit window during
which the table is still drifted. It is a transitional migration path, not a
writer design.

**D. Upgrade delta-rs/pyarrow.** Rejected as a fix: the latest pair probed still
wrote `string_view`, and there is no released switch that controls it. A future
upgrade can be re-probed under this ADR, but it cannot be assumed to fix the
writer.

## Decision

1. **Keep writer behaviour and R1's read seam.** Do not add a normalization pass,
   do not replace merge with partition overwrite, and do not migrate or rewrite
   existing tables for this issue now. The measured read cost does not justify the
   write/concurrency risk, and the current lake state is fully readable.

2. **Contain direct string pushdowns.** Any consumer that needs a string predicate
   on a table that a merge can rewrite must use `read_delta` (or the same
   classify-and-post-filter split) rather than calling
   `DeltaTable.to_pyarrow_table(filters=…)` directly. The known latent case is
   `features/engineering.py`'s `symbol in` pushdown on Bronze, which works today
   only because Bronze has never been merge-rewritten. Auditing and routing that
   caller is the implementation follow-up, not part of this decision record.

3. **Do not remove the read seam as a defence.** Even if a future writer stops
   producing `string_view`, existing files, foreign tables, and older pinned
   snapshots can still contain it. `_split_filters`, its tests, and the doctor
   observation stay.

4. **Revisit only on a recorded trigger.** Open a new implementation decision when
   at least one of the following is true:
   - a non-`read_delta` consumer fails on drifted data (for example after Bronze
     is merge-rewritten);
   - a measured read or pruning regression is attributable to residual string
     filters;
   - mixed row-group encodings produce a measured storage/maintenance cost;
   - a dependency upgrade adopted for another reason (for example R12) is probed
     and shown to change merge output; or
   - the owner explicitly prioritizes writer purity over the rewrite risk.

5. **If a future decision chooses B or C, the migration is explicit.** Normalize
   only the affected active partitions, keep the old files until the normal
   vacuum retention has elapsed, then vacuum; make the drift observation report
   active files separately from stale files; re-run the writer concurrency tests
   and R1's D4 benchmark; and keep the residual read seam for legacy snapshots.
   Acceptance of this ADR does not authorize that migration.

## Implementation status (2026-10-31)

The accepted containment follow-up is implemented:

- `features/engineering.py::_load_price_rows` now reads Delta price sources
  through `read_delta(path.name, lake_dir=path.parent, …)`. The date range still
  pushes down; the `symbol in` scope is applied in Polars, so a drifted Bronze
  file cannot raise.
- `tests/unit/test_feature_loader_string_view.py` now proves the engineering
  reader handles a merge-rewritten Bronze table, and the mutation drill (restore
  the direct Arrow pushdown) fails with the expected
  `ArrowNotImplementedError`.
- `tests/unit/test_feature_jobs.py` pins the new pushdown contract: Delta reads
  never send the symbol predicate to Arrow, while the Parquet scan still does.

No writer, migration, dependency, or lake-content change was made.

## Consequences

- The project keeps a small, tested read seam instead of a second write path. No
  ingestion or label-finalization semantics change, and ADR-0017's additive
  evolution and optimistic-concurrency behaviour remain intact.
- Drift remains a storage-level fact: string predicates stay residual, mixed
  encodings remain possible, and `equity doctor` may keep reporting `drifted`
  until stale files are vacuumed. That is visible debt, not hidden corruption.
- The latent direct-pushdown risk stays on the register until the containment
  audit lands. It is not used by the current canonical pipeline, but it is the one
  place where a future merge rewrite could turn a working read into an exception.
- If the writer is ever fixed, the read seam remains necessary for old files, and
  the performance question is reopened with the D4 benchmark rather than assumed.

## References

- [20261030-01 R1](../handoffs/20261030-01-postpreload-findings-and-remaining-issues.md)
  — the defect, the accepted read seam, the D4 measurement, and the open writer
  item.
- [20261031-01 §4.3](../handoffs/20261031-01-next-thread-and-r12-watch.md) — the
  directive to decide this at ADR level before changing storage.
- `src/equity_lake/storage/delta.py` — `merge_delta`, `update_delta_columns`,
  `read_delta`, `_split_filters`, `string_view_columns`.
- `tests/unit/test_delta_read_filters.py`,
  `tests/unit/test_feature_loader_string_view.py`,
  `tests/unit/test_delta_concurrency.py` — the read policy, the drifted-feature
  path, and the write-conflict contract any replacement must preserve.
