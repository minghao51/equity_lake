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
