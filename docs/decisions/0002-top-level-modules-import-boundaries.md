# ADR-0002: Top-level modules, no `domain/` tree, import boundary tests

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled)

## Context

An earlier cleanup considered a `domain/` package hierarchy. The project is a
single-package pipeline (`src/equity_lake/`) where stage modules (ingestion,
features, ml, signals, …) already express the domain; a second taxonomy would
add indirection without adding a measured benefit.

## Decision

- Top-level modules under `src/equity_lake/` are canonical; there is no
  `domain/` tree.
- Import boundaries are enforced by tests:
  `tests/unit/test_import_boundaries.py` (`LAYER_BOUNDARIES`) keeps `core/`
  independent of `cli/`, `dashboard/`, and `sources/`.

## Consequences

- New packages are cheap (single hatch glob covers them) but must extend
  `LAYER_BOUNDARIES` per the change matrix.
- Dependency direction stays testable; a violation fails the fast suite
  rather than surfacing as an architectural drift.
- Reintroducing a layered taxonomy would require superseding this record.
