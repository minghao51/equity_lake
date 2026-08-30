# ADR-0006: Auxiliary data paths with Pydantic write boundaries

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled)

## Context

Not every runtime artifact belongs in the cataloged lake: signals, update
history, model outputs, findings, and backtest/risk reports are operational
outputs rather than medallion datasets. Forcing them through catalog and
pointblank machinery added cost without a contract.

## Decision

- Cataloged Delta tables live under `data/lake/0{1..4}_*/` only.
- Auxiliary non-lake artifacts live under `data/<name>/`
  (`DATA_DIR / "<name>"` in `core/paths.py`). They are not cataloged and not
  pointblank-validated.
- Each auxiliary artifact gets a Pydantic model at its write boundary as its
  schema contract instead.

## Consequences

- The lake keeps a single cataloged namespace; auxiliary outputs stay cheap
  to add but remain typed and introspectable.
- Readers of auxiliary artifacts rely on the Pydantic models, not the catalog.
- Promoting an auxiliary artifact into the lake is a storage change per the
  change matrix (writer, reader, health checks, idempotency tests, docs).
