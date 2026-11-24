# Decision Records

Architecture decision records (ADRs) for Equity Lake. Each ADR captures one
boundary- or architecture-level decision: the context, the decision, and its
consequences. Per `AGENTS.md`, accepted records here rank second only to
enforced contracts when guidance conflicts.

## Format

- One file per decision, named `NNNN-slug.md` with a monotonic sequence number.
  ADR names are an intentional exception to the `YYYYMMDD-filename.md` rule;
  the recorded date lives in the header.
- Sections: **Status** (proposed / accepted / superseded by `NNNN-slug.md`),
  **Recorded** (date, backfilled if the decision predates this directory),
  **Context**, **Decision**, **Consequences**.
- ADRs are immutable once accepted. To reverse or amend a decision, write a
  new ADR and mark the old one superseded, linking both ways.

## Index

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-medallion-layout-and-generated-catalog.md) | Numbered medallion layout and a generated catalog | Accepted |
| [0002](0002-top-level-modules-import-boundaries.md) | Top-level modules, no `domain/` tree, import boundary tests | Accepted |
| [0003](0003-polars-first-dataframe-engine.md) | Polars-first dataframe engine | Accepted |
| [0004](0004-single-settings-env-contract.md) | Single `Settings` with `extra="forbid"`, raw API keys at client seams | Accepted |
| [0005](0005-native-typer-cli-contract.md) | Native Typer CLI wiring contract | Accepted |
| [0006](0006-auxiliary-data-paths.md) | Auxiliary data paths with Pydantic write boundaries | Accepted |
| [0007](0007-pointblank-at-ingestion-boundaries.md) | pointblank validation at ingestion write boundaries | Accepted |
| [0008](0008-vector-backtest-engine-default.md) | `VectorBacktestEngine` as default backtesting engine | Accepted |
| [0009](0009-decisions-and-archive-directories.md) | `docs/decisions/` and `docs/archive/` directory model | Accepted |
| [0010](0010-market-vocabulary-and-directory-registry.md) | Canonical long market keys, single registry in `core/paths.py` | Accepted |
| [0011](0011-corporate-actions-dataset.md) | Corporate actions as an explicit dataset, point-in-time adjustment at read | Accepted |
| [0012](0012-source-tree-hygiene.md) | Config unification, `eod_pipeline` rename, `contracts` rename, boundary coverage extension | Proposed |
| [0013](0013-ml-signals-dependency-direction.md) | One-way dependency: `signals` may import `ml`, never the reverse | Accepted |
| [0014](0014-label-lifecycle-and-availability.md) | Label finalization on a later session, `label_available_date` metadata, strict as-of cutoff | Accepted |
| [0015](0015-point-in-time-adjustment-and-predictor-availability.md) | Split-only adjustment and point-in-time predictor availability | Accepted |
| [0016](0016-canonical-instrument-identity-and-migration.md) | Composite instrument identity and provenance-backed legacy compatibility | Accepted |
| [0017](0017-bounded-additive-evolution-and-replay.md) | Bounded additive Delta evolution and replay | Accepted |
| [0018](0018-private-watchlist-events-and-delivery.md) | Private watchlists, opportunity events, and delivery | Accepted |
| [0019](0019-cli-delta-maintenance-lake-containment.md) | Lake-relative containment for Delta maintenance CLI targets | Accepted |
| [0020](0020-synthetic-demo-lake-as-backtestable-surface.md) | Synthetic demo lake as a backtestable surface (identity + coverage + `--lake`) | Accepted |
| [0021](0021-row-bound-forecast-provenance.md) | Row-bound forecast provenance and prediction usability | Accepted |
| [0022](0022-future-forecast-provenance-envelope.md) | Future forecast-provenance envelope versions: reader tolerance, writer refusal, v2 procedure | Accepted |
| [0023](0023-writer-side-string-view-drift.md) | Writer-side Arrow `string_view` drift: keep the read seam, contain direct pushdowns, revisit on trigger | Accepted |
| [0024](0024-private-portfolio-ledger-and-accounting.md) | Private portfolio ledger (`us_equity`/USD): append-only events, average-cost book, unadjusted as-of marks | Accepted |
| [0025](0025-immutable-research-evidence-and-run-manifests.md) | Immutable per-run research evidence: run IDs, manifests, dated curves, last-success latest view | Accepted |
| [0026](0026-portfolio-projection-precision-marks-and-reconciliation.md) | P02 amendment to ADR-0024: Decimal replay, stored raw marks/session age, unknown basis and statement tolerances | Accepted |
| [0027](0027-row-bound-price-source-provenance.md) | E01-6 extension to ADR-0025: row-bound price origin, snapshot-bound aggregation and no retrospective relabeling | Accepted |
| [0028](0028-d01-real-source-slice.md) | D01 real-source slice: `us_equity`/yfinance, free tier, local-only / no redistribution, vendor split-adjusted basis; landing shape (a) chosen | Accepted |
| [0029](0029-split-treatment-ledger-and-raw-price-basis.md) | P00-2 amendment to ADR-0024 decision 9: ledger `SPLIT` quantity adjustment + replacement semantics (no migration), and D01 raw-price reconstruction at ingest | Accepted |
| [0030](0030-second-real-source-lake-root.md) | A second, real-source lake root `data/lake_real/` with the same medallion layout; never cataloged, never mixed with the demo root, explicit `--lake-dir` only | Accepted |
| [0031](0031-p00-4-ledger-backup-restore-migration-compaction.md) | P00-4: manual, verified backup/restore, v1→v2 schema migration (adds `SPLIT` to the ledger `CHECK`) and compaction policy for the private SQLite ledger | Accepted |
| [0032](0032-adjusted-read-basis-for-vendor-sourced-slices.md) | Adds one explicitly weaker, opt-in coverage basis (`vendor_events_no_clock` — vendor-event completeness, no source clock) so vendor-sourced slices can produce split-only adjusted research series; the strict source-clock basis stays the default and point-in-time research stays refused | Accepted 2026-11-18; implemented 2026-11-19 |
| [0033](0033-proven-empty-corporate-action-scopes.md) | Proven-empty corporate-action scopes for action-free instruments: a successful empty vendor list, recorded as a weak `vendor_events_no_clock` proof over the price window, satisfies ADR-0029 Part I; a failed fetch never does | Accepted 2026-11-19 |
| [0034](0034-vendor-stamped-ml-research-with-disclosure.md) | Vendor-stamped ML research rows with mandatory disclosure: research-only, explicit weak-basis opt-in, no PIT claim, no Platinum writes, nothing promoted | Accepted 2026-11-21 |
| [0035](0035-h00-sole-public-root.md) | The public surface is exactly the three-file sanitized H00 artifact: one manual, SHA-pinned publisher, both legacy deployers retired from publishing, a private deployment receipt and retained rollback bundles, and a route change requiring its own ADR | Accepted 2026-11-24 |
| [0036](0036-ledger-event-retention-and-pruning.md) | Private-ledger event retention and pruning: operator-invoked checkpoint collapse (one OPENING row-set), verified-backup precondition, the book preserved exactly, the pre-cut breakdown disclosed as lost, and an append-only manifest | Accepted 2026-11-24; implementation follows |
| [0037](0037-derived-total-return-from-stored-distributions.md) | Derived total return from the dividends and splits already stored: reinvest at the ex-date close, research-grade only because every stored action row lacks a source clock, and vendor-adjusted series are never stored, mixed or double-counted | Accepted 2026-11-24; implemented |
