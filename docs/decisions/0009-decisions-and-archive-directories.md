# ADR-0009: `docs/decisions/` and `docs/archive/` directory model

**Status:** Accepted
**Recorded:** 2026-08-29

## Context

AGENTS.md was restructured around an explicit purpose-and-decision-order
format: enforced contracts first, then accepted decision records, then
canonical architecture pages. The docs tree had no ADR home, and superseded
material lived in `docs/developer/history/` alongside spent migration
scripts, with no clear rule for where deprecated docs go.

## Decision

- `docs/decisions/` is the ADR home: `NNNN-slug.md` records with
  proposed/accepted/superseded status (see its README). ADR names are an
  intentional exception to the `YYYYMMDD-filename.md` rule; dates live in
  the record header. Boundary and architecture changes require an ADR before
  implementation (AGENTS.md change matrix).
- `docs/archive/` is the only home for superseded and deprecated material.
  The spent migration scripts moved there from
  `docs/developer/history/`, which was removed. Dated plans, audits, and
  handoffs keep the `YYYYMMDD-*` name and stay in their existing folders.
- `docs/README.md` remains the documentation map and lists both directories.

## Consequences

- Decision provenance is greppable and ranked: contracts > ADRs > architecture
  pages > guides.
- Superseded docs have exactly one location; living guides stop referencing
  `developer/history/`.
- Future boundary changes carry an ADR-writing cost, traded for an explicit,
  reviewable record.
