# ADR-0024: Private Portfolio Ledger and Book Accounting

**Status:** Proposed (2026-11-02). Policy content is owner-approved for
drafting; the record itself is **not accepted**. Acceptance is a prerequisite
for any P01 implementation, and acceptance alone does not authorize importing a
real private account — that needs separate explicit owner authorization.
**Recorded:** 2026-11-02
**Deciders:** Repository owner
**Related:** ADR-0006 (auxiliary data paths), ADR-0016 (canonical instrument
identity), ADR-0018 (private watchlist events and delivery), ADR-0020
(synthetic demo lake), ADR-0021 (row-bound forecast provenance), ADR-0023
(writer-side `string_view` drift),
[P00–P03 specification](../plans/20261102-01-portfolio-and-research-implementation-spec.md)
§5.1–5.3, [worker/reviewer pickup](../handoffs/20261102-01-portfolio-and-research-pickup.md).

## Context

The lake answers "what do the data say about this instrument"; it says nothing
about what one private person actually owns. The P00 gate of the
[specification](../plans/20261102-01-portfolio-and-research-implementation-spec.md)
requires an accepted accounting/privacy/storage record before P01 code, because
the available conveniences all mislead: `tracking.sqlite3` holds watchlist
state rather than ownership, the cataloged lake is shared/derived data rather
than a private book, and spreadsheet exports have no identity, idempotency or
correction contract.

Book accounting is also the only part of this system where a wrong number can
look authoritative. Average cost, unknown opening basis, stale marks, splits and
unmatched reconciliation all have tempting defaults (assume zero, assume
present, assume adjusted, force agreement). Those defaults are rejected here.

This record fixes the private book's boundary and semantics. It does not
implement it, does not select a broker, and does not certify any tax treatment.

## Decision

### 1. Scope: one private account, one market, one base currency

The ledger describes exactly one private, local-first account with one selected
market and one base currency. It is not a tenant, a service, or a shared store.
Instruments outside the selected market, or amounts in another currency, are
rejected rather than approximated (see Decision 6 and 8).

**OPEN (P00-1):** `us_equity` / USD is a **proposed** selection carried forward
for explicit owner confirmation. It is not decided by this record, and P01
implementation is blocked until the owner confirms market and base currency.

### 2. Non-goals

Out of scope for this record and for P01–P03: order execution or any broker
API; short and margin positions; derivatives; tax-lot or tax-return
correctness; multi-currency FX; multi-user, proxied, or hosted access;
automatic ingestion/backfill; model training or refitting. Each is a separate
decision, not an implied later step.

### 3. Storage: a private auxiliary SQLite ledger

- The ledger is a private auxiliary artifact at `data/portfolio/portfolio.sqlite3`
  (`DATA_DIR / "portfolio"`), created and owned per ADR-0006. It is never placed
  under `data/lake/`, never cataloged, and never pointblank-validated.
- Pydantic v2 models are the schema contract at the write boundary (ADR-0006).
  There is no catalog row and no lake reader compatibility promise.
- SQLite connection mechanics are a **non-normative implementation detail**: P01
  follows the existing `signals/tracking.py` pattern (explicit timeout,
  `isolation_level=None`, row factory, busy timeout, foreign keys, WAL and full
  sync on writable connections) and may adjust the settings without a new record,
  provided read commands use read-only connections that cannot create the file.
- The directory and the database, journal/WAL, export and backup paths are
  private (directory `0700`, files `0600`).
- Migration, compaction and deletion are **not authorized** by this record; any
  such step needs an approved policy first (P00-4). Nothing is migrated,
  compacted or deleted automatically today.
- `.gitignore` ignores `data/*` except `data/.gitkeep` and `data/catalog.jsonl`,
  so a private ledger cannot be committed by default. That is intentional and
  must not be special-cased open.

### 4. Event model: append-only, versioned, deterministic

- The ledger is an append-only log of versioned events; balances, quantities,
  basis, and P&L are derived by deterministic replay, never stored as the source
  of truth.
- Event kinds: `OPENING` (cash and/or positions, with a known **or explicitly
  unknown** basis), `BUY`, `SELL`, `DEPOSIT`, `WITHDRAWAL`, `FEE`,
  `CASH_DIVIDEND`.
- Quantities, money, fees and basis use `Decimal`. Binary floating point is not
  a ledger type.
- Timestamps are timezone-aware UTC, with effective time and received time kept
  distinct. Ordering within an import is a deterministic source sequence, not
  file order or wall-clock arrival.
- Every event carries: account, external source and event ID, canonical
  instrument when applicable, currency, signed cash effect, quantity, price,
  fees, source digest, and parser version.
- Non-finite values, malformed identity or currency, ambiguous ordering,
  oversells, and unsupported margin/short events are rejected at the boundary.

### 5. Idempotency, corrections, atomicity

- The same `(account, external_id)` with identical content is idempotent: a
  re-import is a no-op, not a duplicate row.
- The same `(account, external_id)` with changed content fails visibly. It is
  never silently overwritten.
- Corrections are explicit, traceable reversal/repair events. Imported history is
  never edited in place, and no "fix-up" path rewrites prior events.
- A batch import is atomic: one transaction, and a duplicate-ID conflict rolls
  the whole batch back.

### 6. Book accounting: average cost, explicit opening basis

- Holding cost is averaged per instrument (average-cost book accounting). An
  `OPENING` position must state its basis, or state that the basis is unknown.
- Fees reduce cash. A purchase fee is included in basis and cost; a sale fee
  reduces realised proceeds. Dividend withholding is netted in the cash effect
  and shown separately from gross.
- Realised gain on a sale is proceeds net of fees minus average cost of the
  quantity sold. Dividends are income, not basis.
- Unknown opening basis makes basis-dependent P&L **unavailable**. It is never
  reported as zero, and it is never back-filled from current prices.
- `DEPOSIT` and `WITHDRAWAL` are external flows, not investment profit. Return
  and time-weighted measures are not claimed until cash-flow timing and an
  independent return oracle are separately specified.

**Acceptance hand oracle** (the P01 fixture): opening 10 shares at basis 100
each plus cash 1000; buy 5 at 120 with fee 5 gives cash 395, quantity 15, basis
1605; sell 4 at 130 with fee 2 gives cash 913, remaining basis 1177, realised
gain 90; receiving a net dividend of 11 gives cash 924; marking the remaining 11
at 125 gives market value 1375, NAV 2299, unrealised gain 198. Total gain 299 is
90 realised + 198 unrealised + 11 income, with no external flows.

### 7. Identity: exact canonical instrument on every security event

Every security event carries the exact canonical `(market, MIC, symbol)`
identity from ADR-0016. Unknown or ambiguous identity is rejected, never guessed
from ticker spelling or the selected market. Ticker relabeling/continuity is not
inferred, and no historical instrument is re-mapped automatically.

### 8. Valuation: stored unadjusted as-of marks with visible provenance

- Book marks are stored, unadjusted as-of quotes with source, as-of time,
  staleness and an explicit synthetic/unknown provenance flag. Research-adjusted
  prices are not valid book marks; a quantity adjustment and its quote basis must
  agree.
- A configurable staleness bound applies, with an owner-approved provisional
  default of one venue session; P01 may change the default through configuration
  without a new record. Past that bound, complete NAV, allocation and P&L are
  withheld and the affected instruments are named, while cash and quantities
  remain visible.
- There is no network fallback and no future mark relative to the requested
  valuation time. A missing or wrong-currency mark never removes a holding from
  the denominator and is never carried silently.
- A present-day quote display does not certify historical point-in-time feature
  availability (ADR-0015/ADR-0021 remain binding).

### 9. Splits are not approved in this record

Split accounting is **not** approved here. Splits and other quantity-adjusting
events must fail visibly on import, and a position whose quantity basis is
therefore uncertain blocks valuation rather than misstating quantity or basis.
Split handling requires its own approved amendment to this record.

### 10. Reconciliation

Reconciliation compares explicitly supplied statement quantities and cash
against the ledger and reports each line as matched, mismatched, or unavailable,
with documented tolerances. It never mutates ledger state, never inserts a
balancing event, and never adjusts a mark to force agreement. Reconciliation is
not tax certification.

### 11. Privacy

- Private balances, holdings, journal notes and full account identifiers never
  appear in logs, telemetry, error messages, commits, static exports, Pages,
  W&B, or public/API projections.
- Display surfaces must be loopback-only. ADR-0018's forwarded/proxy rejection
  stays in force; multi-user or remote access is not authorized by this record.
- Broker credentials and full account identifiers stay out of `Settings` and
  away from any `EQUITY_*` config key, per the raw-seam rule in ADR-0004.

### 12. Data and spend constraint (D00-2)

Paid market-data purchase is **not** authorized; only free/no-cost sources may
feed ledger marks. GenAI/LLM spend is authorized for enrichment only — never to
obtain, substitute for, or fabricate market data or prices. Dry-run stays
non-generating and non-persisting. Actual provider feasibility, coverage and
licensing are the D00 packet's business
([specification §6](../plans/20261102-01-portfolio-and-research-implementation-spec.md));
this record asserts no provider claim.

### 13. Proposed CLI surface (to be frozen at P00)

A native `equity portfolio import|positions|summary|reconcile` sub-app, wired
with `add_typer` before the command modules that decorate it, using `Annotated`
options with help text, exiting `1` on required failure, with unified CLI help
coverage and a user guide. Preview/dry-run performs full validation and reports
proposed effects with **no** filesystem, database, or WAL writes. No write action
is hidden inside a read command.

## Open decisions

| ID | Open item | Blocks |
| --- | --- | --- |
| P00-1 | Account market and base currency. `us_equity` / USD is recorded as a **proposed** selection only and awaits explicit owner confirmation. | All P01 implementation (ledger schema, currency validation, mark selection) and any import. |
| P00-2 | Replacement split and quantity-adjustment semantics. Decision 9 already decides the *current* policy (reject split events, block valuation of affected positions); what remains open is any future supported split treatment, which needs an approved amendment. | Import of split-affected history and valuation of affected positions — the block stands until an amendment is accepted. |
| P00-3 | CLI surface freeze listed in Decision 13. | P01 CLI implementation, help-scan coverage, and the user guide. |
| P00-4 | Migration, compaction and backup/restore policy (mechanism, timing, verification). Decision 3 guarantees only that nothing happens automatically. | Any backup automation, restore tooling, or migration step; P01 ships without them. |

## Consequences

- P01–P03 gain a private book with deterministic replay, an idempotent import
  contract, explicit correction events, and a stated-basis average-cost model
  whose numbers can be checked by hand (Decision 6).
- The failure modes stay honest and visible: unknown basis, stale or missing
  marks, non-canonical identity, splits, oversells and reconciliation
  mismatches are reported as unavailable or errors rather than filled in with
  plausible defaults.
- Deliberately unavailable: tax-lot/tax-return correctness, FX and multi-currency
  total return, short/margin accounting, time-weighted/money-weighted returns,
  capacity or execution modelling, and any multi-user or hosted access.
- The private book remains outside the lake and catalog, so it inherits neither
  lake validation nor lake backup; backup/restore remains an owner decision
  (P00-4), and until it is decided there is no supported automated backup.
- Accepted boundaries that must not be bent to make this convenient: ADR-0006
  (auxiliary paths and Pydantic boundaries), ADR-0016 (no identity guessing),
  ADR-0018 (no proxied private exposure), ADR-0021/ADR-0023 read semantics.

### Rejected alternatives

- **Store holdings in `tracking.sqlite3` or the watchlist `held` flag.** Rejected:
  ownership state is not watchlist state, and mixing them would make a private
  book depend on a disposable operational store (spec §5.1).
- **A generic broker/plugin mapping framework.** Rejected: one approved broker
  mapping plus a canonical CSV schema is enough for one account; a plugin surface
  is speculative abstraction with a privacy cost.
- **Cataloged run tables for ledger events in `data/lake/`.** Rejected: shared,
  derivable, validated lake semantics do not fit single-owner private history, and
  cataloging invites accidental publication.
- **Excel/CSV as the source of truth.** Rejected: no identity, no idempotency, no
  atomic correction, no replay.
- **In-place correction of imported rows.** Rejected in favour of traceable
  reversal/repair events (Decision 5).
- **Silent basis defaults (zero, current price, first observed price).** Rejected:
  unknown basis must yield *unavailable* P&L, never a fabricated zero.
- **Force reconciliation to agree.** Rejected: agreement is reported, not created.

## References

- [P00–P03 specification](../plans/20261102-01-portfolio-and-research-implementation-spec.md)
  §5.1–5.3 — the approved contract, the hand oracle, and the D00 spend/scope
  constraints this record encodes.
- `src/equity_lake/signals/tracking.py` — `_open_database` / `tracking_db_path`,
  the existing private-SQLite precedent (timeout, `isolation_level=None`,
  `row_factory`, read-only versus writable modes, `0700` directory).
- `src/equity_lake/core/paths.py` (`DATA_DIR`-based auxiliary locations) and
  `src/equity_lake/core/instruments.py` (`IDENTITY_COLUMNS`, `InstrumentKey`).
