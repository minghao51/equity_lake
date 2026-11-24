# ADR-0037 — Derived total return from stored distributions: research-grade, clockless, never strict

**Status:** **Accepted (2026-11-24).** Implementation follows this record: the derivation module
and its hand-math oracles, refusals and research-only entry point land under this decision, and
nothing here authorizes any other use of the series. It is **research-grade only** — see
decision 2. Accepted does not mean "point-in-time", "operationally ready", or "vendor
total-return series exists".

**Recorded:** 2026-11-24

**Accepted:** 2026-11-24 by the owner, in response to the question "can we just use yfinance for
total return?" — yes, and this record is the shape of that answer: yfinance stays the raw-price
and event source we already store, and total return is **derived locally** from the stored
dividends and splits rather than taken from a vendor-adjusted close.

**Extends:** [ADR-0015](0015-point-in-time-adjustment-and-predictor-availability.md) decision 1
(split-only read-time adjustment) with a second, clearly separated series; it does not relax it.
Reads the event rows created under [ADR-0011](0011-corporate-actions-dataset.md), the raw-price
write boundary of [ADR-0029](0029-split-treatment-ledger-and-raw-price-basis.md) Part I, the
clockless basis vocabulary of
[ADR-0032](0032-adjusted-read-basis-for-vendor-sourced-slices.md), and the research-only
admission of [ADR-0034](0034-vendor-stamped-ml-research-with-disclosure.md). Feeds no strict
read, no live signal, no model and no ledger.

**Related:** [vendor total-return options note](../finding/20261124-01-vendor-total-return-options.md)
(the survey this decision answers), residual plan §2.10 (cash-distribution backtest
requirements, the no-double-count rule and its hand-math oracle) and §11 owner decisions
(2026-11-24), [ADR-0027](0027-row-bound-price-source-provenance.md) (rows stay `unknown`).

## Context

The lake already holds everything a dividend-and-split total-return series needs, and holds it
in the shape this repository's contracts require:

- **Prices are raw.** `sources/base.py` fetches with `auto_adjust=False` and, in a real-source
  root, `ingestion/price_reconstruction.py` re-inflates vendor-back-adjusted OHLC and volume to
  raw basis at the write boundary (ADR-0029 Part I). Stored prices are therefore *unadjusted*,
  which is what ADR-0027's raw-at-the-write-boundary rule and ADR-0015's read-time adjustment
  contract both assume.
- **Events are stored.** `02_silver/corporate_actions` carries the vendor's own dividend and
  split rows (`ticker, action, value, source, ingested_at, source_available_at`). On the landed
  real slice that is 381 dividend rows and 17 splits across 10 tickers.
- **The clock is absent.** Every one of those rows has `source_available_at = null`
  (`sources/base.py` writes `pl.lit(None)`), which is exactly the
  `vendor_events_no_clock` weakness ADR-0032 named. No stored action row can support a
  point-in-time claim, ever, and ex-date is not availability (ADR-0015 decision 4).

So the cheap path is not "believe a vendor's adjusted close". It is: keep the raw prices and
events we can audit row by row, and apply the textbook total-return arithmetic locally, with
the weakness stated instead of hidden.

**Why not simply ingest `auto_adjust=True` (or Alpha Vantage adjusted close)?** Because it would
break three accepted contracts at once. (a) It stores vendor-adjusted prices, violating ADR-0027's
raw-at-the-write-boundary rule and, in a real root, double-adjusting against
`price_reconstruction.py`. (b) The series becomes non-reproducible from the lake's own rows: the
vendor can silently restate its adjustment factors and nothing in the store records which vintage
produced which value (the D00 below-the-watermark limit; ADR-0029 decision 3). (c) It
double-counts: the lake already stores the dividends, so folding a dividend-adjusted close on top
of the stored events would count every distribution twice (residual plan §2.10). The adjusted
close column that yfinance already returns is stored as fetched and remains a cross-check
only — no research path may consume it (ADR-0029 decision 4).

## Decision

### 1. Derive total return locally from the stored events

`storage/total_return.py` exposes one pure function of `(raw prices, stored actions)` that
returns a total-return level series (`total_return_close`), a per-instrument index
(`total_return_index`, base 100 at the instrument's first session in the frame) and the research
basis stamp. The arithmetic is fixed and contains no vendor adjustment, no interpolation and no
guessed data:

- **Cash dividend.** For a dividend `d` with ex-date `t`, reinvest at that session's own close:
  the step is `1 + d / close_t`. The product of all steps with `t <= u` multiplies the price at
  session `u`. Reinvesting at the ex-date close is the CRSP-style convention this repository's
  existing dividend factor already leans on; it is *not* payment-date reinvestment (decision 3).
- **Split.** For a split whose stored lake ratio is `r` (the ADR-0011/ADR-0029 `old/new`
  convention, e.g. `0.1` for a 10-for-1), every session **strictly before** the split session is
  multiplied by `r` — equivalently by `1 / (new share per old share)`. The split's own session is
  already post-split at the vendor and is never scaled.
- **Ordering within one session, explicit.** A split and a dividend on the same session resolve
  as: **split first, then dividend**, and the two act on disjoint session sets, so the product is
  order-independent by construction. The split scales only sessions strictly before `t`, while
  the dividend step applies at and after `t` and uses the session's *post-split* close (`d` is
  quoted per post-split share). Applying the dividend to a pre-split close, or scaling the
  split's own session, is a defect and is pinned by oracle (iv).
- **Money.** Float64 throughout, matching `CORPORATE_ACTION_SCHEMA`'s `value: Float64` and
  `lake_reader`'s arithmetic; factors multiply in ascending session order and **nothing is
  rounded**. Tests compare against hand math at a `1e-12` relative tolerance.
- **Determinism.** Same `(prices, actions)` in, byte-identical frame out; no clock, no wall time,
  no ordering dependence on input row order (actions are processed in `(ticker, ex_date, action)`
  order).

### 2. Research-grade only: the `vendor_events_no_clock` weakness, stated plainly

Every stored action row has a **null `source_available_at`**, so this series inherits ADR-0032's
weak basis by construction: it asserts that the stored rows are the vendor's own published event
list, and nothing about *when* any event became visible. Therefore:

- it may **never** feed a strict or point-in-time read, a live or paper-shadow signal, a
  feature/label/model path, a ledger posting, a promotion, or a published claim;
- it is never claimed to be a vendor total-return series, is never called "adjusted EOD", and
  does not change ADR-0015's split-only contract;
- the entry point takes a required, keyword-only `basis` argument whose only admitted value is
  `vendor_events_no_clock`. Asking for `source_clock` (or anything else) **raises** — a strict
  consumer cannot obtain the series without failing (decision 6). The output frame stamps
  `total_return_basis = "vendor_events_no_clock"` on every row so a derived frame is
  distinguishable by data alone, and the returned metadata declares the clock basis as absent
  (`none_declared`) rather than null-by-accident.
- Consumers report the basis and the ADR-0034 disclosure beside any number taken from it.

### 3. Out of scope — each needs its own decision

Not modelled here, and no attempt may approximate them inside the derivation:

- **Tax and withholding.** Only the net cash stored by the vendor is reinvested. Gross-vs-net
  dividend treatment, withholding rates, and tax lots on reinvestment are absent.
- **Currency.** USD-single-instrument arithmetic: no FX, no conversion, no non-USD instrument.
- **Payment-date (rather than ex-date) reinvestment.** The step uses the ex-date close; a
  payment-date or lagged reinvestment model changes the answer and is a different rule.
- **Index-level distributions.** SPY/QQQ and other funds are treated by the *same* per-share
  rule; fund-level distribution composition, return-of-capital, and index rebalancing are not
  modelled.
- **Everything else already named by ADR-0029/0032:** spin-offs, mergers, cash-in-lieu,
  fractional shares, split discovery beyond the vendor's own events, revenue/earnings total
  return, and any repaired/re-derived history.
- **A cash-distribution backtest path** (residual plan §2.10) remains its own decision: fee,
  tax, reinvestment timing, currency, ex-date/payment/publication semantics. This record supplies
  the research series that path may later consume; it does not build the path.

### 4. Hand-math oracles define correctness

The implementation is verified against hand-computed values, not against a vendor:

1. **100 → 90 with a 10 cash distribution at that session is 0% gross total return** — not −10%
   (raw close change) and not +10% (counting the distribution as extra): `90 * (1 + 10/90) = 100`.
2. **A 2:1 split changes no wealth.** 100 → 50 with the split on the second session derives
   50 → 50: the series has no seam, while the same prices without the split event carry the
   phantom −50%.
3. **A three-dividend sequence matches an explicitly hand-computed series** from the closed-form
   factors: closes 100/120/130 with dividends 10/12/13 at those sessions give steps
   `1.1, 1.1, 1.1`, so the level is 110 / 145.2 / 173.03 and the index 100 / 132 / 157.3.
4. **A 4:1 split plus a dividend on the same session** resolves as decision 1 states: 400 → 100
   with the split (stored `0.25`) and a 2 cash dividend on the ex-session derives 100 → 102
   (`100 * 1.02`), and 2% is the gross total return.
5. **The 0%-oracle mutation.** Removing the reinvestment factor (or applying it before instead of
   at the ex-date) makes oracle 1 fail; the proof is one rule mutated at a time, the named test
   failing, then restored byte-identically from a `/tmp` copy.

### 5. Refusals, each named and tested — never a silent skip

`TotalReturnError.reason` is a stable token so a caller, a test and an operator message can
distinguish the causes without parsing prose. The derivation refuses on:

| Reason | Meaning |
| --- | --- |
| `ex_date_without_price` | a dividend's ex-date has no price row for that instrument; the reinvestment close does not exist |
| `non_positive_close` | a close feeding the derivation is null/non-finite/≤ 0 (a dividend ex-date close, or an instrument's first session = index base) |
| `invalid_split_ratio` | a split ratio is zero, negative, exactly 1, or non-finite — it cannot be a ratio |
| `invalid_dividend_value` | a dividend amount is negative or non-finite |
| `unknown_action` | an `action` value that is neither `dividend` nor `split` |
| `duplicate_dividend` | two dividend rows for the same `(ticker, session)` — one session, one reinvestment |
| `unknown_instrument` | actions for an instrument absent from the price frame: its factor would be unpriced |
| `ambiguous_identity` | one `ticker` spans more than one market in the price frame; partition by canonical identity first |
| `strict_basis_required` | the caller asked for any basis other than `vendor_events_no_clock`, including the strict `source_clock` |

First refusal in `(ticker, ex_date, action)` order wins, so the reported reason is deterministic.
An empty action frame is **not** refused: a frame with no events derives the raw close (factor 1)
and reports zero reinvestments and zero split adjustments — the caller decides whether the empty
event list is proven (ADR-0033), and this module asserts nothing about coverage.

### 6. The strict path is not weakened

`storage/lake_reader.py`'s `with_price_adjustment(..., method="total_return")` keeps raising
`TOTAL_RETURN_UNAVAILABLE` exactly as RS-B01 made it uniform; the `total_return` literal stays a
named refusal in the loader, engine and CLI. The derived series is **not** wired into that
function, into `backtesting/data_loader.py`, or into any adjust-mode branch, and no strict
consumer is offered a fallback to it. Wiring it anywhere is a later, separate decision.

### 7. Cross-check recipe — manual, network only at a human's discretion

Documented in the vendor note (and repeated here so it is not lost): a human may, offline from
this repository's automation, compare the derived series against yfinance's `auto_adjust=True`
for the same tickers and window — fetch raw closes and events with `auto_adjust=False`, fetch the
adjusted series separately, and compare session-over-session returns of the derived series with
the vendor's. Expected agreement is within the vendor's own rounding except where the vendor's
adjustment uses a treatment this record excludes (payment-date timing, withholding). The recipe
is:

- **research-only, manual, and never automated** — no network call exists in `src/` or `tests/`,
  and none may be added for this;
- **never stored** — no fetched adjusted series enters the lake, `data/`, a fixture or a report
  artifact;
- **never ingested and never asserted** — no test may depend on it; a discrepancy is a question
  to investigate, not a passing assertion.

## Consequences

- A research-grade total-return series exists at zero data cost, reproducible from rows the lake
  already stores, without storing a vendor-adjusted price or double-counting a dividend.
- The weakness is permanent and visible: the series is clockless, cannot back a strict read, and
  is stamped research-only on every row and in its metadata.
- Nothing upstream moves: no schema, catalog, ingestion, `sources/` or reader change; the
  `total_return` refusal everywhere is untouched; no migration and no backfill.
- New standing obligations: the four hand-math oracles and the named refusals stay green, the
  mutation proof in decision 4.5 stays reproducible, and any future consumer must opt in with
  `basis="vendor_events_no_clock"` in code that is obviously research-only.
- Still unsupported (decision 3): tax/withholding, non-USD, payment-date reinvestment,
  index-level distribution treatment beyond the same per-share rule, and any backtest path.

## Alternatives considered

- **Ingest `auto_adjust=True` (or Alpha Vantage adjusted) and store it.** Rejected: breaks the
  raw-at-write rule (ADR-0027), double-adjusts against real-root reconstruction (ADR-0029), makes
  the series non-reproducible from the lake's own rows, and double-counts the dividends already
  stored (residual plan §2.10).
- **Compute total return from `adj_close` against `close`.** Rejected: `adj_close` is a vendor
  adjustment with no recorded vintage and no visible event decomposition; it is a cross-check
  column only (ADR-0029 decision 4).
- **Buy a paid vendor series with a revision stamp.** Deferred, not rejected: the options note
  ranks it first among *paid* options and it remains blocked on licence and spend authorization
  (ADR-0024 decision 12) — and a paid series with a qualified clock would be a new source with
  its own ADR, not a replacement for this derivation.
- **Add `total_return` to `AdjustmentMode` and let a flag select it.** Rejected: RS-B01 made the
  refusal uniform on purpose; a flag would put a clockless series one argument away from every
  strict consumer. The research entry point is a separate function with a required basis
  argument, and the strict refusal stays.
- **Apply the dividend factor to pre-split prices, or use prev-close instead of the ex-date
  close.** Rejected in the design: it changes the answer, cannot be reproduced from hand math,
  and oracle (iv) exists to catch it.

## References

- `src/equity_lake/storage/total_return.py` — the derivation, its refusals and the research-only
  entry point (this record's implementation).
- `tests/unit/test_total_return.py` — the hand-math oracles, the named refusals and the
  strict-consumer guard.
- `src/equity_lake/sources/base.py` — `auto_adjust=False` raw fetch and the null
  `source_available_at` on the stored action rows.
- `src/equity_lake/ingestion/price_reconstruction.py` — raw-basis reconstruction at the real-root
  write boundary (ADR-0029 Part I), whose ratio convention the split step mirrors.
- `src/equity_lake/storage/lake_reader.py` — the untouched split-only read and the untouched
  `total_return` refusal.
- [Vendor total-return options](../finding/20261124-01-vendor-total-return-options.md) — the
  survey, now recording that local derivation is the chosen cheap path.
- [Residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md) §2.10, §11.
