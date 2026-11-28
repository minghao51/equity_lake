# ADR-0043 — Admit non-US real data one market at a time

**Status:** Proposed (admission policy only; no market admitted).

**Recorded:** 2026-11-27

## Context

[ADR-0030](0030-second-real-source-lake-root.md) decision 5 constrains the real root to
`us_equity`. Offline feasibility packs do not qualify a real source, confer license
rights, or authorize a call or storage write.

## Options

- **Keep all four markets offline:** no license or operational exposure, no real coverage.
- **Qualify and authorize one market at a time:** smallest change with verifiable coverage,
  calendar and rights; slower breadth.
- **Enable all four from the fixture packs:** fastest apparent expansion, but confuses
  fixture validation with actual source and market guarantees.

## Decision (recommended)

Keep the current scope until one complete packet is available. Rank `cn_ashare`,
`hk_sg_equity`, `jpx_equity` and `krx_equity` by packet quality and relevance, not by
provider availability alone. Ask for source and tier, approved use/local retention and
redistribution terms, cost, exact venue/MIC, tickers and time window, currency and volume
units, trading and holiday calendar, price/split/dividend basis, and coverage, timestamp
and vintage evidence with its limitations. HK and SG must be qualified separately by
venue even though they share a market key. A clockless source cannot acquire strict
point-in-time status through this process.

A later market-specific amendment to ADR-0030 decision 5 must name the source and scope,
review router/schema/validation and health-monitor changes, and prove demo-root/catalog
invariance plus calendar, corporate-action and containment guards. Keep explicit
`--lake-dir`, the numbered real-root layout, and no catalog registration of real
tables. Require separate owner consent for a scoped probe and any actual landing/spend;
reviewer acceptance of the mechanism is not run approval.

## Consequences

No changes to the US path or guard until a concrete amendment is accepted. No FX,
multi-market ledger support or public distribution follows from a qualified slice.
