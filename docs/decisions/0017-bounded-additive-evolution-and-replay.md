# ADR-0017: Bounded Additive Evolution and Replay

**Status:** Accepted decision; implementation and offline-contract review accepted 2026-10-18. Operational readiness is not implied.
**Recorded:** 2026-10-17
**Deciders:** Repository owner
**Related:** ADR-0001 (medallion layout), ADR-0006 (auxiliary data paths),
C05/C10 and R16/R17 in the [repository review](../finding/20261016-repository-review-and-equity-alerts.md)

## Context

Delta schema changes and replay must not use whole-table rewrites or let an
unreadable state look empty. Replay can repeat work, advance a checkpoint before
a durable write, send historical notifications, or incur paid-processing costs
unless its scope and per-invocation bounds are explicit. This ADR records the
owner-approved implementation contract; it does not authorize production
migration, repair, replay, paid processing, or delivery.

## Decision

1. **Additive Delta evolution.** Use native nullable additive schema evolution.
   Apply optimistic concurrency control (OCC), retry at most three times, and
   refresh/re-read table state before each retry. Do not implement schema
   evolution by rewriting the whole table. Unsupported evolution or exhausted
   conflicts fail visibly; they must not be suppressed or converted to a
   successful write.
2. **Bounded, explicit replay.** Every replay invocation requires explicit
   source, date, market, and instrument scope. Each invocation is limited to
   100 items total, in batches of at most 25 items.
   Writes are idempotent and durable
   before the corresponding checkpoint advances. Historical replay never
   sends notifications.
3. **Paid-work authorization.** The default paid-processing authorization is
   zero. Paid work requires a separately explicit actual-spend cap and an
   approved estimate; the estimate is not represented as actual spend. These
   controls do not grant authorization for any particular production run.

## Implementation status and compatibility notes

The keyed Delta writer now uses native nullable additive schema evolution,
refreshes the table snapshot after optimistic-concurrency conflicts, and retries
at most three times; it does not recover by rewriting the whole table. The
bounded replay helper requires explicit source/date/market/instrument scope,
selects at most 100 items per invocation in batches of at most 25, and keeps its
auxiliary checkpoint and spend ledger separate from authoritative Silver state.
Silver is re-read before checkpoint advancement; checkpoint state alone never
proves an item durable. Historical replay has no notification callback.

Paid work remains disabled by default and requires explicit authorization, a
finite cumulative approved estimate, a separate actual-spend cap, a cost
estimate per batch, and provider-reported actual cost. The unbounded LLM helper
paths intentionally fail closed. `equity sec --process` exits nonzero before
fetching until a bounded provider actual-cost adapter exists; this workflow is
not paid-runtime-ready. Do not infer runtime authorization from the presence of
the helper or its tests. At the 2026-10-17 status snapshot, implementation
review, C10, and the full offline gates were still pending; see the 2026-10-18
acceptance supplement below.

## Consequences and acceptance evidence

- Readers and writers must preserve unrelated rows and columns without
  whole-table rewrite behavior. OCC conflicts, unsupported schema changes,
  write failures, and checkpoint failures remain visible and recoverable.
- Synthetic tests must cover additive nullable columns, concurrent changes
  and state refresh on retry, retry exhaustion, unsupported evolution,
  idempotent bounded replay, checkpoint ordering, explicit-scope validation,
  historical-send suppression, and the zero-paid default/explicit budget gate.
- C10 acceptance evidence exercised these contracts in the integrated
  synthetic journey and received independent implementation review plus the
  required offline gates; the dated acceptance supplement records that result.
- Acceptance of this ADR is a policy decision only. No production table,
  checkpoint, historical data, paid processor, or delivery channel was touched
  or authorized by recording it. Any real paid processing, source fetch,
  production migration/repair, live delivery, or deployment requires separate
  owner authorization.

## Implementation/offline-contract acceptance supplement — 2026-10-18

Native nullable additive Delta evolution with OCC (up to three retries),
explicit bounded replay (at most 25 items per batch and 100 per invocation),
durable checkpoint ordering, and fail-closed error behavior were independently
reviewed and accepted with the C10 offline contracts. The final reviewer
returned no C10 must-fix. The actual provider-cost adapter remains unsupported;
paid authorization is zero, `SEC --process` fails nonzero before fetch, and
default paid work is a planned skip. No paid-processing, production replay,
migration, or operational-readiness claim is made.
