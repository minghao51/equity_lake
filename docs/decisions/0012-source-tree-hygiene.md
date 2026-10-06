# ADR-0012: Source-Tree Hygiene — Config Unification, Module Renames, Boundary Coverage

**Status:** Proposed
**Recorded:** 2026-09-10
**Deciders:** Repository owner
**Related:** ADR-0002 (top-level modules, no `domain/` tree, import boundary tests),
ADR-0004 (single Settings contract), ADR-0010 (market vocabulary registry)

## Context

A four-scout + two-reviewer audit of `src/` (2026-09-10, verified against code)
found the architecture sound but mid-flight on its own migrations. Three
onboarding hazards are boundary- or vocabulary-level and cannot be fixed as
drive-by patches:

1. **Two `config` packages.** `src/equity_lake/config/` holds only
   `validators.py`, has **no `__init__.py`** (implicit namespace package), and
   name-collides with `equity_lake.core.config` — an 84-line re-export shim over
   `core/config_models.py` + `core/settings.py` whose `__all__` omits
   `S3SyncSettings` and `SourcesSettings`. The collision has already
   materialized: `ml/registry.py:12`'s docstring references the nonexistent
   `equity_lake.config.Settings`. 29 external files import through the shim.
2. **Four modules named `pipeline.py`.** Root `equity_lake.pipeline` (the EOD
   orchestrator) collides conceptually with `cli/commands/pipeline.py`,
   `features/pipeline.py`, and `validation/pipeline.py`. Its docstring still
   claims "Hamilton driver builder" although no Hamilton code lives there.
   `"pipeline"` is also a token in the enforced `LAYER_BOUNDARIES`.
3. **Three `schemas.py` modules with different jobs.** `core/schemas.py` (column
   lists + dtype contracts), `validation/schemas.py` (pointblank enforcement,
   ADR-0007), `features/dag/schemas.py` (Pydantic DAG row models). The names
   don't convey the mechanism, so "where do I change the column contract?"
   requires tribal knowledge.
4. **Boundary enforcement covers 5 of 15 packages.** `LAYER_BOUNDARIES`
   (`tests/unit/test_import_boundaries.py:120-126`) constrains only
   api/core/storage/features/ingestion. `ml`, `signals`, `backtesting`,
   `sources`, `monitoring`, `catalog`, `validation`, `findings`, `sentiment`,
   `devtools` are unchecked — verified today to import neither `cli` nor
   `dashboard`, but nothing pins that.

## Decision

1. **Delete the top-level `config/` package.** Move `config/validators.py` to
   `core/config_validators.py` (update its two importers). The package
   namespace `equity_lake.config` disappears.
2. **Complete, then remove the `core/config.py` shim.** First add the missing
   re-exports (`S3SyncSettings`, `SourcesSettings`) so the surface is complete;
   then migrate all importers to `core.settings` / `core.config_models`
   directly; the shim's `TickerConfig` legacy wrapper moves to
   `core/config_models.py`. Add `core.config` to the legacy-shims-absent test
   so it cannot return.
3. **Rename `equity_lake/pipeline.py` → `equity_lake/eod_pipeline.py`.**
   Update its three importers, the `LAYER_BOUNDARIES["api"]` token, and
   documentation references. The sub-package `pipeline.py` files keep their
   names (each reads naturally in context).
4. **Rename by mechanism:** `core/schemas.py` → `core/contracts.py` (column
   lists + dtype contracts; ~29 importers, mechanical);
   `features/dag/schemas.py` → `features/dag/row_models.py`.
   `validation/schemas.py` keeps its name — it *is* schemas.
5. **Extend `LAYER_BOUNDARIES`** with the ten unchecked packages, each
   forbidding `{cli, dashboard}`. The `signals → ml` import edge is **not**
   forbidden here; it is addressed by ADR-0013.

## Consequences

- Import churn only; no behavior changes, no data-path changes, no catalog
  regeneration (no schema/data changes). `equity_lake.config` disappears as an
  importable namespace — acceptable because only `cli/commands/admin.py` and
  one test import `config.validators`, both updated in the same change.
- The `equity_lake.pipeline` rename is user-invisible (CLI command stays
  `equity pipeline`); Python-level importers are 3 files.
- Future boundary drift in ten packages now fails CI instead of accreting.
- Tier-0 hygiene items (typed query errors, loud writer fallthrough, docstring
  truth-pass, lazy `akshare`, registry derivation in `features/engineering.py`,
  interface dedup) require no ADR and proceed independently under
  handoff `20260910-01`.

## Status

Proposed — implementation begins only after the owner accepts this ADR
(handoff `docs/handoffs/20260910-02-config-naming-tier1.md`).
