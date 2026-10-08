# ADR-0014: Label Lifecycle, Availability Metadata, and As-Of Training Cutoff

**Status:** Accepted (2026-10-08)

**Recorded:** 2026-10-08

**Related:** ADR-0001 (medallion layout + generated catalog), ADR-0003
(Polars-first), ADR-0006 (auxiliary data paths), ADR-0007 (pointblank at
ingestion boundaries), [W03 handoff](../plans/20261006-dag-pipeline-03-incremental-contracts.md),
audit findings F03/F17/F25

## Context

Gold feature rows are written only for the requested output window, and the
target (`next_day_return` / `target`) is derived from the *next* observation.
The newest row's label is therefore null at write time, and the daily writer
never revisits the preceding session — persisted labels stay null (F03).

Feature/enrichment availability (source release date, filing date, processing
time, label computation time) is not recorded, so an as-of training or backtest
cannot distinguish information that was knowable at the cutoff from information
finalized later (F17). Broad historical repair is not authorized automatically
(D09).

The canonical writer merges every column of the incoming frame on the natural
key `(ticker, date)`, so a narrower label-only source merged carelessly could
null unrelated feature columns or trigger schema evolution.

## Decision

1. **Keep the existing Gold target columns** (`next_day_return`, `target`, and
   the meta-label fields) and their names/semantics. Do not add a separate label
   table by default; a separate table would require its own accepted ADR.

2. **Finalize labels on a later session.** When session *D* is processed, the
   eligible labels for the preceding observed session become computable and may
   be finalized using only information available by *D*. Add availability
   metadata **`label_available_date: Date`** — the session on which the target
   value became computable — to the Gold contract. Legacy rows carry null
   (unknown availability); timestamps are never fabricated.

3. **"Next observation" means the next exchange session with a valid bar for
   that instrument.** Labels never span a gap silently. If the expected
   next-session bar is absent, the label stays pending and the condition is
   reported; a calendar day is never substituted for a session.

4. **Label finalization is a bounded, key-scoped, label-only update** through
   the canonical storage seam (matched-column update on the natural key). It
   must not null, shrink, evolve, or insert unrelated feature columns, keys, or
   rows. Rows that do not exist are rejected rather than created.

5. **As-of eligibility.** Training and backtests must exclude labels whose
   `label_available_date` is after the requested cutoff. Rows with unknown
   availability (legacy) are excluded from strict as-of training by default; a
   documented tolerance may only be adopted as a separate owner decision.
   Availability and label metadata columns are excluded from predictors and
   model feature matrices.

6. **`compute_target=False` performs no label finalization.** Completing labels
   over a historical range is an explicit label-only path, never a side effect
   of normal feature computation.

7. **Corrections are idempotent by natural key.** Historical labels are not
   rewritten broadly. A bounded, report-first, explicitly authorized repair
   path exists for that purpose (D09); scope and snapshots are inventoried
   before any repair. A change to label *semantics* requires model-version
   rejection/retraining, not transparent reuse.

8. **`FEATURE_SCHEMA_VERSION` is raised only when the availability-metadata
   change lands**, together with catalog regeneration, reader compatibility, and
   the model feature-list exclusion. Installed models must not treat the new
   column as a predictor.

## Consequences

- **Schema:** one additive Gold column. The catalog must describe it and must
  exclude it from model feature lists; pre-existing readers tolerate the extra
  column.
- **Training/model compatibility:** strict as-of eligibility excludes
  unknown-availability legacy rows, so affected models must be retrained or
  explicitly validated before reuse. No automatic retraining is performed.
- **Repair:** existing unlabeled/null-target history is reported first; broad
  relabeling requires separate owner authorization (D09). Rollback restores the
  prior Gold snapshot; label-only updates are key-scoped and replayable.
- **Dry-run:** never finalizes labels, performs inference, or writes.
