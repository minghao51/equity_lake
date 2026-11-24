# Vendor total-return options — where a total-return series could come from

**Status: options survey, planning input only.** The owner decision of 2026-11-24
([residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md) §11) approves a
vendor total-return adapter **for planning only**. No adapter exists or is started, no vendor has
been contacted, no data has been fetched, and no spend is authorized. This note answers one
question — where a vendor total-return series could come from, and what each option would cost
this repository — so a later decision has something concrete to accept or reject.

**Verification caveat, read first.** Only the repository-side facts in §1 and §3 are verified
here. Every vendor/licence/clock statement in §2 is **general product knowledge, not verified in
this repository and not checked against any provider's current terms** — terms change, tiers
change, and none of this may support an implementation decision until the licence text and the
clock evidence are obtained and recorded (§4).

## 1. What "total return" would mean here

Today `total_return` is a refused literal end to end (RS-B01): one early, actionable error at
loader, engine and CLI (`core/availability.py:25-28` — "only point-in-time split-only adjustment
is implemented and no vendor total-return series exists in this lake"; the read path raises it at
`storage/lake_reader.py:205-207`). ADR-0015 decision 1 is a **split-only** read-time contract and
excludes cash dividends on purpose; the `total_return` mode stays in the public Literals as a
named refusal (RS-B01 residual).

A vendor total-return series is a different object: a vendor-adjusted close whose price path
already folds **dividends and splits** (and sometimes other distributions) into the historical
prices. It is not the split-only transform, not the ledger's `CASH_DIVIDEND` book income, and not
a substitute for either.

## 2. The options

| Option | What the series actually is | Source-issued publication/vintage clock (ADR-0032 requires one for strict reads) | Licence / redistribution | What ingestion would take |
| --- | --- | --- | --- | --- |
| Yahoo / yfinance `auto_adjust` | Adjusted close folding dividends and splits into daily OHLC; raw close also returned (`auto_adjust=False`) | **No.** Verified in-repo: the yfinance action fetcher emits `source_available_at = null` (`sources/base.py:561`; the D00 packet cited `:545` at its own baseline); no vintage field | Restricted by Yahoo's terms; redistribution not permitted (terms not verified here). D00 records source licensing/retention as unverified | Cheapest: yfinance is already the wired `us_equity` source; a disclosed research feed is an extension of an existing seam, not a new source |
| Alpha Vantage `TIME_SERIES_DAILY_ADJUSTED` | Adjusted close plus separate split and dividend event endpoints | **No** per-row publication clock or vintage is known to be published (not verified) | Free tier with call limits and stated non-redistribution limits (terms not verified) | New source row of the change matrix (router, type/map, schema, config, tests, docs, catalog); API-key seam (`os.getenv`, ADR-0004 raw-seam rule) |
| Tiingo | EOD OHLC with adjusted close; dividend/split history endpoints | Not verified; no source-issued clock claim should be assumed | Subscription tiers; redistribution restricted (not verified) | New source row; paid — blocked on spend authorization (§3) before licence even matters |
| EODHD | EOD/adjusted prices, dividends, splits, fundamentals | Not verified | Per-tier licence, redistribution restricted (not verified) | New source row; paid; breadth is its selling point, not point-in-time discipline |
| Polygon.io | Aggregates with adjusted close/volume; dividends and splits endpoints; flat files | Not verified | Paid tiers; redistribution and derived-data limits (not verified) | New source row; paid; good tooling, no demonstrated source clock |
| Nasdaq Data Link / Sharadar | Sharadar SEP-style EOD: OHLC, dividends, splits, factors, with vendor revision stamps (`lastupdated`-style) on rows | **Closest of the commercial EOD feeds**: a vendor revision stamp exists in these datasets (not verified here); whether it satisfies ADR-0032's `source_vintage` evidence — plus expiry and rights — is exactly what the RS-D01 qualification procedure must decide on evidence | Paid subscription; redistribution restricted (not verified) | New source row; paid; and the only candidate where clock qualification is a plausible evidence question rather than a known gap |
| CRSP / WRDS | Academic gold-standard security prices with distributions; reference research files | No per-row source publication clock is known in the standard files; vintage questions are research topics in themselves (not verified) | Licence-restricted to named academic subscribers; no redistribution; unavailable to a private individual outside an institution | Not realistically an ingest feed: manual licensed access, custom extract code, and it would serve best as an offline oracle for validating a cheaper feed |
| Bloomberg / LSEG (Refinitiv) / FactSet | Enterprise security/reference data with vendor adjustments; enterprise APIs and feeds | Not verified; do not assume an extract carries per-row source-availability stamps | Enterprise licences with derived-data and redistribution walls (not verified) | Enterprise integration and cost far beyond one private account; see the spend constraint in §3 |

## 3. Local decision consequences

1. **ADR-0015's split-only contract stands unchanged.** Nothing here amends it; the refusal at
   `storage/lake_reader.py:205-207` is the current, correct behaviour until an accepted ADR says
   otherwise.
2. **A vendor total-return series is a NEW source**, not a flag: router, type/map,
   schema/validation, config, tests, source docs and catalog per the change matrix (`AGENTS.md`),
   plus its own ADR recorded before implementation. §1 of the residual plan is explicit that no
   working total-return path and no vendor TR series exist today.
3. **Strict reads need a source-issued publication/vintage clock** (ADR-0015 decision 4;
   ADR-0032 decision 4 and the `source_publication|source_vintage`-only evidence in
   `core/source_clock.py`). Without qualified clock evidence the series can never back a strict
   read or a promoted claim. Note that a bare adjusted close proves **less** than ADR-0032's weak
   `vendor_events_no_clock` basis, which at least reconstructs raw prices against the vendor's own
   published event list; an adjusted series with no events and no clock is disclosure-grade
   research input only.
4. **Never double-count vendor-adjusted returns with the ledger's `CASH_DIVIDEND` events.** The
   adjusted price already contains the distribution; the ledger books it again as income
   (ADR-0024 decisions 4 and 6). Residual plan §2.10: "Never count both vendor-adjusted returns
   and cash distributions" — 100 → 90 plus a 10 cash distribution is zero gross total return, not
   −10% or +10%. Any future adapter needs the hand-math oracles RS-B01 named, and any future
   cash-distribution backtest path needs its own decision (fee/tax/reinvestment/currency and
   ex-date/payment semantics) first.
5. **Spend is a separate gate.** ADR-0024 decision 12 (D00-2): paid market-data purchase is
   **not authorized**; only free/no-cost sources may feed ledger marks. Every paid option in §2
   needs an owner decision authorizing spend on top of its licence and clock questions.
6. **Production use of anything derived would need its own ADR amending ADR-0034** plus strict
   evidence — the same rule the D2 plan records
   ([D2 preregistration plan](../research/20261124-01-r02-d2-preregistration-plan.md) §7).

## 3a. Chosen cheap path (2026-11-24): derive total return locally — ADR-0037

The owner asked "can we just use yfinance for total return?". The answer accepted in
[ADR-0037](../decisions/0037-derived-total-return-from-stored-distributions.md) is yes — and the
right way is the one this note's §3 point 3 nearly rules out: **do not take a vendor-adjusted
series at all**. yfinance keeps supplying raw prices (`auto_adjust=False`) and the vendor's own
dividend/split rows, which the lake already stores; `storage/total_return.py` then derives the
total-return series locally by reinvesting each stored cash dividend at its ex-date close and
applying the stored split ratios to earlier sessions.

Why this beats every option in §2 for a research-grade number:

- **Zero data cost and no new source row.** No router, schema, config, licence or spend gate; the
  inputs are rows the lake already holds (381 dividends and 17 splits on the landed slice).
- **Reproducible from row-backed provenance.** The series is a pure function of `(prices,
  actions)`, so it can be re-derived and audited row by row; a vendor-adjusted close cannot (its
  adjustment vintage is not stored anywhere — §3 point 3, ADR-0029 decision 3).
- **No double counting.** The distributions are the stored events, not folded into a price, so
  residual plan §2.10's rule holds by construction: 100 → 90 plus a 10 cash distribution is 0%
  gross total return, which is pinned as an oracle.
- **No contract damage.** Nothing is switched to `auto_adjust=True`, no vendor-adjusted value is
  stored, `adj_close` stays a cross-check column only, and RS-B01's uniform `total_return`
  refusal in the reader/loader/CLI is untouched.

What it cannot be: the derived series inherits the **`vendor_events_no_clock`** weakness — every
stored action row has a null `source_available_at` — so it is research-grade only, never a strict
or point-in-time read, never a live signal, and its entry point refuses any basis other than
`vendor_events_no_clock`. Tax/withholding, non-USD instruments, payment-date reinvestment and
index-level distribution treatment beyond the same per-share rule are out of scope and need their
own decision (ADR-0037 decision 3).

### Manual cross-check against a vendor adjusted series (research-only)

Optional, at a human's discretion, **not** part of any module, test, stage or CI path — there is
no network call in `src/` or `tests/` and none may be added for this. Nothing fetched here is
stored, ingested, committed or asserted:

1. Fetch the raw inputs with `auto_adjust=False` plus `Ticker.dividends` / `Ticker.splits`, and
   the vendor's adjusted series separately (`yf.download(..., auto_adjust=True)`).
2. Derive the local series with `derive_total_return(prices, actions, basis="vendor_events_no_clock")`
   using exactly those raw closes and events.
3. Compare **session-over-session returns**, not price levels (the vendor's base is arbitrary):
   `level.pct_change()` on the derived series against the adjusted close's returns.
4. Expect agreement within the vendor's own rounding except across sessions where the vendor
   applies a treatment ADR-0037 excludes (payment-date timing, withholding). Any disagreement is
   a question to investigate — never a passing assertion and never a licence to store the
   vendor's adjusted values.

This cross-check validates arithmetic only. It proves no clock, no licence right and no
point-in-time property; the strict path stays closed on its own evidence rules (ADR-0015,
ADR-0032).

## 4. Recommendation ordering

Recommendation, not a decision — each line is the reason it ranks where it does:

1. **Do not build an adapter before a licence and source-clock evidence exist.** An adapter built
   ahead of both bakes in an unverified redistribution position and a clockless series that can
   never earn a strict read; the D00 packet's `us_equity`/yfinance row is exactly this state
   (`BLOCKED-PENDING-EVIDENCE` for adjusted/PIT use).
2. **If only research-grade evidence is needed, the cheap option is an adjusted-close feed with
   explicit disclosure — never a strict-read claim.** yfinance `auto_adjust` ranks first on cost
   alone: already wired, free, and honestly labelled as clockless.
3. Alpha Vantage free tier — second because its split/dividend event endpoints let a research
   feed be cross-checked, still at zero cost and still clockless.
4. Nasdaq Data Link/Sharadar — first of the paid tier because vendor revision stamps make clock
   qualification an evidence question rather than a known gap; blocked on spend + licence.
5. Tiingo, then EODHD, then Polygon.io — competent paid EOD feeds with no demonstrated
   source-issued clock; they cannot beat Sharadar on the one property that matters here.
6. CRSP/WRDS — not an ingest candidate; rank it as a licensed offline oracle for checking a
   cheaper feed, if institutional access ever exists.
7. Bloomberg / LSEG / FactSet — last: enterprise cost and redistribution walls buy nothing this
   one-account lake needs.

## 5. What is not verified

- Every licence, redistribution and pricing statement in §2 — confirm against the provider's
  current terms before any decision.
- Every "no clock" statement except yfinance's (which is measured in-repo via the D00 packet) —
  a vendor may offer publication metadata in a product this note has not seen; that is a question
  for the source packet, not an assumption in either direction.
- Whether Sharadar-style revision stamps satisfy ADR-0032's `source_vintage` evidence — decided
  by the RS-D01 qualification procedure on submitted evidence, never by this note.
- Actual coverage, quality and survivorship behaviour of any vendor series over this lake's
  universe and windows.

## References

- [Residual plan](../plans/20261122-01-residual-spec-and-implementation-plan.md) §1.3, §2.10,
  RS-B01 and §8 item 8 — the current refusal, the no-double-count rule, and the optional vendor
  TR series decision this note feeds.
- [ADR-0015](../decisions/0015-point-in-time-adjustment-and-predictor-availability.md) decision 1
  (split-only) and decision 4 (source clocks) —
  [ADR-0032](../decisions/0032-adjusted-read-basis-for-vendor-sourced-slices.md) decision 4 — the
  strict-read clock requirement.
- [ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md) decisions 4, 6 and 12
  — `CASH_DIVIDEND` book income and the D00-2 spend constraint.
- [ADR-0034](../decisions/0034-vendor-stamped-ml-research-with-disclosure.md) — research-only
  admission and disclosure; production needs its own ADR.
- [D00 offline source feasibility](../plans/20261103-01-d00-offline-source-feasibility.md) — the
  measured yfinance seam (null action clock; licensing unverified) and the
  CN `adj_close` synthesis caveat (`sources/cn.py:143-145`).
