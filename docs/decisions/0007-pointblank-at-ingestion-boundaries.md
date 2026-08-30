# ADR-0007: pointblank validation at ingestion write boundaries

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled)

## Context

Schema drift from upstream fetchers previously surfaced downstream in
features or ML. An earlier whylogs-based profiling pass produced metrics but
no enforceable pass/fail contract.

## Decision

- pointblank schemas are enforced at ingestion write boundaries via
  `validation/pipeline.py` (Polars-native, replacing whylogs).
- Schema contracts live with the ingestion stage; a write that fails its
  contract does not land.

## Consequences

- Bad batches stop at the boundary instead of poisoning silver/gold.
- Schema changes must update schema constants, validators, catalog, and
  reader compatibility per the change matrix.
- Auxiliary artifacts (ADR-0006) are out of scope here by design.
