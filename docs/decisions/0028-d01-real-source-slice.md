# ADR-0028 — D01 real-source slice: `us_equity`/yfinance, local-only, vendor split-adjusted basis

**Status:** Accepted (2026-11-14)

**Recorded:** 2026-11-14

**Accepted:** 2026-11-14 by the owner, who also selected landing shape **(a) re-inflate at
ingest** from Decision 5. That shape is specified in
[ADR-0029](0029-split-treatment-ledger-and-raw-price-basis.md), **accepted 2026-11-14**;
accepting this record authorizes the landing *design*, not ingestion. Decision 6 was settled
by [ADR-0030](0030-second-real-source-lake-root.md) (**accepted 2026-11-15**): a second lake
root `data/lake_real/` with the same medallion layout, rather than mixing real rows into the
demo lake.

## Context

The [D00 offline source-feasibility packet](../plans/20261103-01-d00-offline-source-feasibility.md)
left D01 blocked on three owner facts it could not establish offline: the authorized
source, a spend ceiling, and the target root. The owner recorded all three on 2026-11-14:

| Question | Owner answer (2026-11-14) |
| --- | --- |
| Source | `us_equity` via yfinance |
| Licensing / retention | local-only, no redistribution |
| Spend | free tier only (no paid feed) |
| Target root | `data/lake/` (reuse, for the eventual landing) |

A read-only probe ran the same day through the shipped adapter and this packet's
"no-write feasibility probe" requirement. It is recorded in
[the D01 source probe](../plans/20261114-01-d01-source-probe.md). It established the
provider is reachable and its coverage coherent (1,260 rows, 252/252 sessions for each of
five configured tickers against the repository's own calendar), at USD 0.00. It also
**falsified one D00 assumption**:

- yfinance's `Close`/OHLC **and** `Volume` are *split-back-adjusted* at source. NVDA's
  10-for-1 split (ex 2024-06-10) shows a +0.746 % close change and a 0.76× volume ratio
  across the boundary, not a 10× discontinuity. Only `adj_close` carries *dividend*
  adjustment on top (AAPL ratio drifts 0.987 → 0.992 across 2024).
- The slice is therefore **not** "raw, explicitly unadjusted" as D00 phrased it. D00's
  own framing was conditional ("the repository cannot prove what it claims"), and this
  probe is the proof.

That matters because the adjusted-read contract (`with_price_adjustment`, ADR-0015;
nine call sites in features, labels, tracking and backtesting) treats stored OHLC as
**unadjusted** and multiplies it by the cumulative split ratio, dividing volume by the
same factor. Applying it to vendor-adjusted prices would adjust any pre-split window a
second time — roughly 10× too low for the NVDA example — and the only existing guard
refuses frames already stamped `price_adjustment_version`, which Bronze price rows are
not.

The probe also confirmed D00's other two predictions on real rows: the provider supplied
no MIC/exchange (identity resolved from current ticker configuration for all 1,260 rows),
and all 58 action rows carry a null `source_available_at`.

## Decision

1. **Authorized source and cost.** D01 uses `us_equity` via yfinance only: free tier,
   USD 0.00, no paid feed and no second provider. Requests carry the existing shared
   `yahoo` throttle key; a probe or ingest run sets `sources.default_rpm`.
2. **Licensing position: local-only, no redistribution.** Lake rows and any
   row-derived price series stay local. No Pages/static export, public API projection,
   W&B projection or artifact publication may carry vendor rows or vendor-derived price
   values. H00 remains gated on a privacy/licensing review, not merely a privacy one.
3. **Target root.** `data/lake/` is the authorized root for the eventual landing. The root
   choice does not by itself authorize a reset, a mixed table, or any write.
4. **Price basis is named, not assumed.** The slice is **"vendor split-adjusted,
   dividend-unadjusted"** (`split_adjusted_vendor`). It must never be recorded or
   described as `unadjusted`. `adj_close` remains a cross-check column only (ADR-0011)
   and is never a research input. D00's "explicitly unadjusted" wording is superseded for
   this seam.
5. **Landing is blocked until the basis/split shape is accepted.** Two admissible shapes,
   both implemented under P00-2's ADR (which already owns replacement-split and
   quantity-adjustment semantics):
   - **(a) Re-inflate at ingest.** Multiply by the vendor's own split events to recover
     raw prices, leaving the existing `with_price_adjustment` contract correct. The
     reconstruction is exact only up to action completeness and is *reconstructed*, not
     vendor-quoted. **Owner selection, 2026-11-14**; specified in ADR-0029.
   - **(b) Store the vendor basis and stamp it.** Add a basis/stamp to price rows so every
     adjusted consumer fails closed through the existing
     `price_adjustment_version` guard until the reader understands the basis. This is a
     Bronze schema change (catalog, validators, reader compatibility, migration note).
     Not selected; kept on record as the rejected alternative.

   Interim rule, binding until one shape is accepted: **no real price row may enter
   `data/lake/`**, because today it would be silently double-adjusted by nine call sites.
6. **Demo rows never certify real rows.** The existing `corporate_action_coverage`
   proof (`source=synthetic_demo_seed`, `coverage_status=complete`,
   `source_valid_until=2026-10-28`) must not be consumed by real prices, and Bronze demo
   rows must not share a table with real rows unlabelled. Reusing `data/lake/` therefore
   requires one of: reset-then-ingest (destructive, separately authorized), or a distinct
   real dataset path under the same root (layout change, needs its own ADR).

## Consequences

- D01's **probe** is complete; D01 **ingestion** is not authorized by this record.
- P00-2 is now a blocker for real price ingestion, not only for private-ledger
  accounting. Its ADR must decide (a) or (b) before any landing.
- E01-6/ADR-0027 keeps its meaning: real rows report `unknown` origin. No origin claim
  stronger than `unknown` exists for this slice.
- Adjusted and point-in-time research stay blocked (A06): no source-issued
  `source_available_at`, no complete action proof, no provider-vintage detector. Free
  data does not repair an absent proof.
- No schema, catalog, Settings, dependency or test change is introduced by this ADR. The
  eventual landing triggers the source/schema/storage rows of the `AGENTS.md` change
  matrix.
- Accepting this ADR is authorization to *proceed on the landing design*, not to ingest.
  The landing shape must be accepted first, and a mixed-lake decision must be recorded
  before any real row lands in `data/lake/`.
