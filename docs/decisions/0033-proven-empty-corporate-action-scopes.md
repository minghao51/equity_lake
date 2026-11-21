# ADR-0033 — Proven-empty corporate-action scopes for action-free instruments

**Status:** **Accepted (2026-11-19).** Accepted authorizes the implementation in this
record; it authorizes no ingest run beyond the widening it was requested for, and it does
not relax any point-in-time rule.

**Recorded:** 2026-11-19

**Amends:** [ADR-0029](0029-split-treatment-ledger-and-raw-price-basis.md) Part I decision 2
(the write-boundary refusals `action_store_missing` and `action_scope_incomplete`).
**Extends** [ADR-0032](0032-adjusted-read-basis-for-vendor-sourced-slices.md): the evidence
this record accepts is a `vendor_events_no_clock` proof, and nothing here creates a source
clock. ADR-0027 (origin stays `unknown`) and ADR-0030 (root isolation) are untouched.

## Context

ADR-0029 Part I reconstructs vendor split-back-adjusted prices to raw basis at the write
boundary, and fails closed when a ticker has **no stored action at all**
(`action_scope_incomplete`), because "this instrument had no actions" and "this instrument's
actions were never fetched" are indistinguishable in an empty table. That is correct for
equities that split or pay dividends. It makes an action-free instrument impossible to land:
`GLD` (SPDR Gold Shares) has never split and never paid a distribution, so its vendor action
list is empty by fact, not by omission. The same refusal would hit any instrument whose
vendor history contains no actions for the requested window.

The reason the refusal exists is epistemic, and it can be answered with evidence: a vendor
fetch that **succeeded** and returned an empty list for the instrument's whole history is
different from a fetch that failed. Today the batch fetch conflates them — it logs
`corporate_actions_fetch_failed` and returns no rows either way — so no decision can rest on
its emptiness.

## Decision

**Decision 1 — A successful empty fetch is evidence, a failed one is not.** The clockless
fetch seam gains one operation that returns a vendor action list for one ticker and
**raises** on fetch failure (no swallowing, no retry-as-empty). Only a non-raising call that
returns no rows may be read as "the vendor published no actions for this instrument".

**Decision 2 — The evidence is a weak coverage proof over the written price window.** For
such a ticker the widening path writes one `VendorEventCoverageProof`
(`vendor_events_no_clock`, `vendor_events_complete`) covering the exact price window being
written, and then writes the prices. The proof asserts exactly what the weak basis asserts —
the vendor's own event list is complete for that instrument and window — and it carries no
clock at all (ADR-0032 decisions 5-6). No `source_available_at`, no expiry, no inferred
timestamp.

**Decision 3 — Reconstruction accepts a proven-empty scope, and only that.** A ticker absent
from the action store may pass the write boundary when a weak proof covers its price rows
(`coverage_start <= first price date` and `coverage_end >= last price date`). Because there
are no splits, the split factor is `1`: the vendor series needs no re-inflation and is
written unchanged. Every other refusal is unchanged: an absent store with no proof still
fails `action_store_missing`; a ticker with no proof still fails
`action_scope_incomplete`; an invalid split ratio still fails `action_invalid`; a watermark
short of the price window still fails `action_window_uncovered`.

**Decision 4 — No new adjustment semantics.** The adjusted read still applies stored split
ratios only (ADR-0015); with an empty action list it multiplies by 1. Dividends remain out
of scope, `adj_close` is untouched, origin stays `unknown`, and the basis stays the weak one.
A point-in-time consumer still refuses vendor-stamped rows (ADR-0032 decision 4).

**Decision 5 — Strength is not upgraded by an empty list.** "The vendor listed no actions"
is a statement about the vendor's list, not about reality: a vendor omission remains
undetectable below the watermark, exactly as ADR-0032 decision 3 says. Reports that include
such an instrument disclose the weak basis.

## Consequences

- Action-free instruments (`GLD` first) can be landed and read with split adjustment that is
  provably a no-op, without inventing an action row, a clock or an expiry.
- The write boundary gains one evidence path, so it also needs one more mutation-proofed
  refusal: a failed fetch must never produce a proof (mutation: treat failure as empty →
  named test fails), and a proof that does not cover the price rows must still refuse.
- The batch corporate-action ingest keeps its current behaviour for mixed universes: a failed
  ticker is skipped and logged, and it will simply have no proof, so its prices still refuse.

## Alternatives considered

- **Write a synthetic zero-value action row.** Rejected: it would fabricate an event and
  pollute the event store; the empty interval is representable as coverage (ADR-0015
  decision 2 already accepts a complete empty window as proof).
- **Treat any missing ticker as action-free.** Rejected: that is exactly the
  indistinguishable case the original refusal protects.
- **Use the fetch time as coverage_end.** Rejected: coverage_end is a date bound, not a
  clock, and the price window is the honest, checkable bound; the fetch time would smuggle
  ingestion time into the evidence (ADR-0015 decision 4).
