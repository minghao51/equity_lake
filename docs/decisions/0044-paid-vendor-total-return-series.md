# ADR-0044 — Defer a paid vendor total-return series

**Status:** Proposed (owner review; no purchase or source integration authorized).

**Recorded:** 2026-11-27

## Context

[ADR-0037](0037-derived-total-return-from-stored-distributions.md) derives total return
from stored dividends and splits for disclosed research only. It does not activate the
reader/backtest `total_return` mode, create strict point-in-time data, or supply a reason
to pay for another price series. Provider rights and clock claims in the
[vendor options survey](../finding/20261124-01-vendor-total-return-options.md)
are not procurement evidence.

## Options

- **Keep local derivation:** reproducible and no incremental spend, but clockless and
  limited to its stated dividend and split treatment.
- **Use an independently licensed sample as an offline check:** can test return arithmetic
  and explain methodology differences; does not prove strict coverage or justify ingestion.
- **Buy and integrate a qualified vendor series:** may address a named research or
  coverage gap, with license, spend, vintage and schema costs.

## Decision (recommended)

Do not buy or integrate now. Reopen only for a specific unmet use case and cost cap.
Request written rights, sample coverage, currency and gross/net treatment,
ex-date/payment/reinvestment semantics, revisions/vintages and source-issued timing
evidence. A funded trial needs separate owner approval. An offline sample must not be
committed, redistributed, or substituted for raw stored prices. No source qualifies for
strict use until its evidence passes the applicable source-clock and coverage gates.

Any eventual adapter needs its own design decision, versioned provenance and an isolated
series that is never used as raw OHLC or combined with the same cash distributions.
Require hand-math cases for dividends and splits and compare matched-period returns,
not arbitrary level bases. Keep strict reads, production, ledger marks and promotion
under their current contracts.

## Consequences

There is no additional recurring cost or double-counting path. Vendor benchmarking and
procurement remain distinct choices; a qualified data packet for R02-D2 is not the
same thing as a paid total-return subscription.
