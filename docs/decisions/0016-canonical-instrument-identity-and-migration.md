# ADR-0016: Canonical Instrument Identity and Migration

**Status:** Accepted decision; implementation and offline-contract review accepted 2026-10-18. Operational readiness is not implied.
**Recorded:** 2026-10-16; owner acceptance recorded 2026-10-17.
**Deciders:** Repository owner
**Related:** ADR-0001 (medallion layout), ADR-0010 (market registry), ADR-0014
(label keys/availability), ADR-0015 (adjustment/predictor contract), C04/C10,
R11 and R14 in the [repository review](../finding/20261016-repository-review-and-equity-alerts.md)

## Context

Ticker text is not globally unique. Shared features, models, signals, and
watchlist records therefore require a canonical key that distinguishes market
and exchange. Existing ticker-only rows and model artifacts cannot be relabeled
safely without historical provenance. This ADR records the selected identity
and compatibility policy; it does not authorize production migration, repair,
or retraining.

## Earlier proposal alternatives (retained for traceability)

The pre-approval draft considered unique-legacy-only keys and a stable internal
instrument ID as alternatives to a canonical composite key. The accepted
selection is `(market, MIC, symbol)`, with the narrower provenance-proven
unique legacy rule for compatibility. The alternatives are not open options
after owner acceptance.

## Decision

The owner approved this decision with: **“Approve 1-9 and C10 implementation”.**

1. **Canonical composite identity.** Use `(market, MIC, symbol)` throughout
   shared tables, readers, writers, cache/model artifacts, signals, watchlists,
   events, and delivery records. MIC must come from supported source/config
   mapping; it is never guessed from ticker spelling or the market label.
2. **Legacy compatibility.** A legacy ticker-only record or artifact is usable
   only when available provenance proves one unique historical instrument
   identity for the requested scope. Unknown, missing, or ambiguous provenance
   is unavailable. Never infer a default US identity, select a first match, or
   relabel an artifact based only on its current filename/configuration.
3. **Symbol changes.** A symbol change requires an explicit mapping. Do not
   silently collapse symbols or infer continuity from ticker text.
4. **All-market implementation scope.** Initial product configuration may be
   US, but that is not a scope deferral. Through C10, implement and test
   identity, instrument-session behavior, and supported deterministic signal
   paths for all five markets: `us_equity`, `cn_ashare`, `hk_sg_equity`,
   `jpx_equity`, and `krx_equity`. HK/SG identity includes the distinct MIC;
   the union market calendar must not be mistaken for each instrument's
   exchange session. Unsupported source/enrichment coverage stays explicit.
5. **Production boundary.** Acceptance authorizes implementation and synthetic
   compatibility work only. Production storage/model migration, repair,
   backfill, and retraining require separate explicit authorization.

## Implementation status and compatibility notes

Canonical `(market, MIC, symbol)` identity is now carried through the shared
feature/model/signal/tracking paths, with deterministic fixtures scoped across
all five price markets. New model filenames use a hashed canonical identity
token; the trusted sidecar retains the full identity and binds the model bytes
by SHA-256. The digest guards against changed artifact bytes while the sidecar
is trusted; it is not a malicious-tampering signature. Ambiguous or unknown
legacy identities remain unavailable.

At the 2026-10-17 status snapshot, implementation was under independent
review and not yet implementation-accepted. Compatibility remains report-first
and scoped to synthetic fixtures and temporary copies. Do not automatically
inventory, relabel, repair, or migrate production rows/artifacts; do not
collapse distinct instruments into ticker-only keys. No production migration,
repair, backfill, model retraining, source fetch, or deployment is authorized.

## Compatibility and migration

Compatibility work is report-first, scoped to deterministic fixtures and
temporary copies of affected readers, model artifacts, signals, watchlists,
caches, and joins. Resolve legacy records only through provenance-backed
unique identity; preserve unknown/ambiguous status instead of fabricating a
market or MIC. Every shared-key read/write boundary uses the composite
identity. Before any separately authorized migration, report the proposed
scope and compare old and canonical readers on isolated copies; preserve
snapshots, prevent duplicate inclusion, and verify rollback without collapsing
distinct instruments into one symbol key. Do not automatically inventory or
repair production data. Ambiguous legacy records may remain unavailable.

## Acceptance evidence required

Implementation and independent review must include synthetic collision and
session cases for:

- The same symbol on the same dates across CN and KRX, and the same symbol on
  XHKG and XSES within `hk_sg_equity`; feature, label, prediction, model,
  signal, and watchlist paths must remain distinct.
- Proven unique, unknown, missing-MIC, duplicate, and ambiguous legacy
  mappings; only provenance-proven unique historical identities are accepted.
- Explicit symbol-change mappings and rejection of text-based inferred
  continuity.
- Instrument-specific exchange sessions, including an HK/SG union day when
  only one venue is open, and deterministic supported signal paths for all
  five markets with unsupported enrichment reported rather than fabricated.
- Legacy and canonical readers/artifacts without duplicate output or key
  collapse, using temporary copied data only.

No production migration, repair, backfill, model training, or live-provider
access is part of these tests. At the 2026-10-17 status snapshot, full C10
synthetic integration and offline gates remained under review; see the dated
acceptance supplement below.

## Consequences

Ticker-only shared keys are not the canonical contract. Safe legacy reads are
narrowly limited to provenance-proven unique identities; other legacy records
must be visibly unavailable. A US-configured initial preview does not narrow
the accepted five-market implementation/test scope. No production data or model
artifact has been changed by accepting this decision.

## Implementation/offline-contract acceptance supplement — 2026-10-18

Composite `(market, MIC, symbol)` identity, provenance-only unique legacy
compatibility, instrument-session behavior, and supported deterministic paths
across all five markets were independently reviewed in synthetic fixtures and
the C10 journey, then accepted with the offline gates. The final reviewer
returned no C10 must-fix. Compatibility and rollback evidence is limited to
synthetic readers/proofs/labels/migration fixtures and temporary copies. This
acceptance does not certify or authorize a production migration, repair,
backfill, retraining, source fetch, or production rollback.
