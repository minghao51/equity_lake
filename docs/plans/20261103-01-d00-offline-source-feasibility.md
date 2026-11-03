# D00 — Offline Source-Feasibility Packet (us_equity / cn_ashare)

**Recorded:** 2026-11-03 (corrected after independent citation review)
**Status:** Evidence packet for an owner decision. No source call, ingestion,
schema, storage or provider change is authorized or performed here; D01 remains
gated on explicit scope/spend authorization.
**Owner inputs already recorded:** two candidate slices (`us_equity` via
yfinance, `cn_ashare` via the existing akshare/efinance integration); paid
market-data purchase **not** authorized; GenAI/LLM spend authorized for
enrichment only (never to obtain or fabricate market data); target-root and
isolation changes deferred to D01.
**Method:** one read-only pass over source, config, schemas, ingestion and the
tests that claim to cover them. No vendor was contacted, no lake was read or
written, no fixture was created. Every entry cites the file and line the claim
rests on; an independent reviewer then checked those citations and eleven claims
were tightened (see §10).
**Continuation:** [implementation specification](20261102-01-portfolio-and-research-implementation-spec.md) §6,
[A01–A11 audit](../finding/20261102-product-readiness-audit.md) (A06, A07),
[ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md) (the
private ledger must not treat unproven marks as certified).

## 1. Why this packet exists

The audit's A06/A07 say the real-research blocker is not "no provider wired" but
"no proof that what was read is complete, correct and available at the time we
claim". A provider integration that returns numbers is therefore not evidence of
feasibility. This packet separates what the repository can **prove** from what it
merely **assumes**.

## 2. Slice A — `us_equity` via yfinance

| Capability | Evidence | Proves | Does not prove |
| --- | --- | --- | --- |
| Canonical identity | `sources/base.py:240-276`; precedence `provider_mic > provider exchange > configured mic` at `:271`, label `current_ticker_config` at `:274-276`; venues in `config/tickers.yaml:30-444` | Rows carry `(market, mic, symbol)` + schema version, or the fetch raises (`:262-266`) | Vendor identity: when the provider supplies no MIC, the venue comes from **current configuration** and is not vendor verification |
| Raw vs adjusted price | `sources/base.py:417` (`auto_adjust=False`), `:443-444`; `core/schemas.py:47-62` | Raw `close` **and** vendor `adj_close` are both ingested | Point-in-time correctness: ADR-0011:65-69 *records the rationale* that vendor adjustments are recomputed against today's actions and are a cross-check column at most. That is an ADR assertion; current vendor behaviour was not verified here |
| Corporate actions | `sources/base.py:483-598` (`Ticker.actions`, split inversion `:585-588`); US-only gate `ingestion/corporate_actions.py:149-151` | Dividends and splits are fetchable for US, one row per `(ticker, ex_date, action)` | Completeness, and that the vendor neither omits nor back-fills events |
| Publication clock | `sources/base.py:545` emits `source_available_at = null`; schema/contract comment `core/schemas.py:131-140`, `core/availability.py:54-56`; consumer `ingestion/corporate_actions.py:186` | The yfinance action fetcher produces **no source-issued availability clock**; `ingested_at` is not a substitute | — (the absence *is* the finding). Note the separate synthetic proof generator at `devtools/seed_demo.py:147-172`: it populates availability/validity for **demo data only** and is not source-issued proof for real prices |
| History range | `sources/base.py:450-470` (single range request, exclusive end), `:291-292` | US backfill is one batched request | Any provider-side depth cap: none found in code |
| Rate limit / expiry | `sources/base.py:378` shared `yahoo` quota key, `:317` throttle, `:308-360` tenacity; throttling off by default (`config/settings.yaml:35-37`) | Shared-quota keying and a retry contract exist | Actual quota. There is **no provider-vintage or source-response revision detector**: corrections at or below the exclusive watermark are not re-fetched. Strict coverage *does* validate expiry when a proof exists (`core/availability.py:114-129`), but real action rows carry no proof to validate |
| Universe / completeness | `config/tickers.yaml:30-444` (50 configured names), `sources/us.py:11-57` (47 fallback names) | The configured universe is a hand-maintained **current** list | Historical membership and survivorship: the configured lists cannot establish delisting history or coverage breadth |
| Missing-proof behaviour | `ingestion/writers.py:299-315`; `core/availability.py:139-175` | Gold **feature** writes fail closed when the required coverage proof is missing or invalid; Bronze/Silver accept a null proof | That this blocks every Gold write regardless of input, and it does not create source-issued proof for real prices |

## 3. Slice B — `cn_ashare` via akshare/efinance

| Capability | Evidence | Proves | Does not prove |
| --- | --- | --- | --- |
| Route and provider | `ingestion/router.py:104-113` passes no `enable_efinance`; default `False` at `sources/cn_hybrid.py:62`; akshare primary `:284`, yfinance fallback only when akshare is empty `:307-320` | The default CN route is **akshare**, with a yfinance fallback; efinance is opt-in, not the route | Any comparative quality claim about the three providers |
| Field maps | akshare rename `sources/cn.py:146-149`, unadjusted request `adjust=""` at `:67`; efinance rename `sources/cn_efinance.py:88-98`, `get_quote_history` `:113-118` with no adjustment parameter | akshare requests **unadjusted** OHLCV; efinance's default adjustment is unspecified in code | Which adjustment efinance applies, and what the vendor treats `adjust=""` as |
| Adjusted-price integrity | Adapters synthesize `adj_close` from close when the column is absent: `sources/cn.py:143-145`, `sources/cn_efinance.py:100-101`; fallback requests `auto_adjust=False` and keeps vendor `Adj Close` (`sources/cn_hybrid.py:192-200`) | On the akshare/efinance paths, `adj_close` may simply **equal** raw close; on the yfinance fallback a distinct adjusted close can survive | That "CN `adj_close`" has one consistent meaning. Adjusted semantics are **not certified** on any CN route, and consuming the synthesized column as adjusted would be a silent error |
| Canonical identity | Shared `sources/base.py:240-276`; `config/tickers.yaml:444-618` (20 CN names, `SSE`/`SZSE`); `core/instruments.py:33-35,57-58` | Rows are identified to MIC (provider identity first, configured venue as fallback) | Vendor identity and correctness of the configured venue |
| Corporate actions | `ingestion/corporate_actions.py:149-151` rejects non-US markets; no corporate-action reference in `sources/cn*.py` | CN actions are **absent from the repository**: no route, no source method, no table writes | Whether a *free* CN action source is obtainable — unverified, not impossible |
| Publication clock | No clock column in `core/schemas.py:47-62`; no source-issued proof producer | No CN source-issued clock or coverage proof was found | — |
| History / backfill | No `fetch_range` override in the CN sources; base default loops per calendar day, skipping weekends only (`sources/base.py:289-303`); akshare per-stock calls `sources/cn.py:96-101`, opt-in efinance batches codes `sources/cn_efinance.py:105-118` | The default akshare route issues per-stock weekday requests; neither route skips **CN market holidays** | Provider depth caps |
| Concurrency / rate limit | `sources/cn.py:34,96-101` `ThreadPoolExecutor(max_workers=10)` with no inter-request sleep; rpm throttling off by default | Up to ten akshare stock requests run concurrently, unbounded *rate* unless an operator enables rpm limits | Vendor tolerance of that concurrency |
| Universe | 20 configured CN names; `stock_limit` default 100 (`sources/cn.py:35`, `sources/cn_efinance.py:36`), truncation at `sources/base.py:236` | The universe is a hand-picked A-share list, not the market | Market breadth or survivorship |

## 4. What cannot be established offline

Licensing, retention and redistribution for any of the three providers (no key
entry in `.env.example`, no ToS reference in the repository); the actual revision
cadence of any provider (there is no provider-vintage detector; the corporate-action
watermarks are exclusive, so corrections at or below a watermark are not
re-detected — label finalization detects divergence, warns, and then rewrites the
stored label columns, `features/labels.py:242-247,310-319,330-337`); actual
coverage breadth (no
returned-vs-expected measurement); survivorship (configured lists are current,
not historical membership); efinance's default adjustment; and whether vendor
`Adj Close` agrees with a from-actions recomputation (no cross-check test).

## 5. Verdicts

| Slice | Verdict | Most important missing proof |
| --- | --- | --- |
| A `us_equity`/yfinance | **BLOCKED-PENDING-EVIDENCE** for adjusted/PIT use; raw-price ingestion itself is wired | A source-issued `source_available_at` plus a complete `ActionCoverageProof` (with `source_valid_until`). Until then adjusted consumers are refused and Gold feature writes fail closed. Source licensing/retention also remains unverified |
| B `cn_ashare`/akshare | **BLOCKED-PENDING-EVIDENCE**; adjusted semantics are not certified on any CN route | **Any** CN corporate-action source in the repository, plus a defined adjustment path. `adj_close` synthesized from close must be disclosed or removed before CN output can be called adjusted |

Neither verdict is a statement that the vendors are wrong; both are statements
that the repository cannot currently *prove* what it claims about them.

## 6. Enrichment gaps (presence is not value)

| Column / route | State |
| --- | --- |
| CN `adj_close` | Synthesized from raw close on the akshare/efinance paths (`sources/cn.py:143-145`, `sources/cn_efinance.py:100-101`); may be a distinct vendor value on the yfinance fallback |
| efinance `成交额` → `amount` | Fetched then dropped: not in `STANDARD_COLUMNS` (`core/schemas.py:47-62`) |
| Action `source_available_at` | The yfinance action fetcher emits null (`sources/base.py:545`); writers default a **missing** clock to null and do not overwrite an explicitly supplied value (`ingestion/writers.py:178-179`) |
| `source_valid_until`, `coverage_status`, `coverage_schema_version` | Schema exists and strict coverage enforces expiry when populated (`core/availability.py:114-129`); no source-issued producer found for real rows |
| `market`/`mic`/`symbol`/`exchange` | Provider identity when available, otherwise current configuration (`sources/base.py:240-276`) — configured venue is a fallback, not verification |
| Gold `action_coverage` / `action_source_age_days` | Feature rows require a valid proof or the write fails closed (`ingestion/writers.py:299-315`) |

## 7. Test provenance

The relevant tests are fixture- or mock-based:
`tests/unit/test_corporate_actions_ingest.py` patches `yf.Ticker` and asserts the
*limitation* (all-null `source_available_at`), `tests/unit/test_fetchers.py`
patches `efinance`, and `tests/unit/test_cn_hybrid_safety.py` monkeypatches
`yf.download`. No test in these files asserts a vendor fact, and none is
network-marked. A green suite therefore says nothing about whether either
provider still serves these endpoints — which is exactly the D00 gap, not a
reason to trust it.

## 8. Consequences and the decision this packet asks for

What is already true and wired: US raw daily prices with canonical identity
(provider identity where offered, configuration otherwise), retry/throttle
plumbing, a documented refusal path when action proof is missing, and a CN price
route whose adjusted column is not certified.

What D00 recommends, given no paid-data authorization:

1. **D01 candidate = `us_equity`, raw-price vertical slice, explicitly
   unadjusted and action-proof-limited.** Present it with the unavailable states
   visible (no adjusted research features, no Gold feature claims) rather than
   inventing an availability clock. The existing integration needs no new
   paid-source purchase, but that is **not** a finding that the data is free to
   use: licensing, retention and provider suitability remain unverified and must
   be resolved before D01 is authorized.
2. **`cn_ashare` stays blocked for adjusted/action-dependent work** until either
   a free CN action source is proven (an investigation that needs source calls,
   i.e. D01 scope) or a data source is authorized. The synthesized
   `adj_close == close` path must be fixed or disclosed in the same change; it is
   a correctness defect independent of provider choice.
3. **A06 cannot be closed from repository evidence alone.** It stays blocked
   unless an authorized source can supply the required availability/coverage
   proof; the owner has not authorized market-data spend, so the honest
   resolution is to keep the adjusted/action-dependent path refused rather than
   weaken the proof.

Open question for the owner (the only one this packet cannot answer): accept
recommendation 1 as the D01 candidate (subject to the licensing/retention check),
or defer D01 entirely while P00/E00 implement? Recommendations 2–3 need no
decision now.

## 9. Scope limits of this packet

Deliberately not gathered in the same pass (named so nothing looks exhaustive):
backfill depth semantics (`ingestion/backfill.py`, `auto_backfill.py`), pointblank
schemas per source (`validation/pipeline.py`), `storage/lake_reader.py`
`with_price_adjustment` behaviour under a null proof, the specific
`features/dag/enrichments_04.py` fields that stay unavailable, and the catalog
entries for corporate-action coverage. None of these can convert a
BLOCKED-PENDING-EVIDENCE verdict into a pass; they refine the D01 work list.

## 10. Correction record (independent citation review, 2026-11-03)

Eleven claims were tightened after a reviewer checked every citation against
source. The substantive corrections: the synthetic demo proof generator exists
(`devtools/seed_demo.py:147-172`) and is not a real-source producer; expiry *is*
validated when a proof exists (`core/availability.py:114-129`) and label
finalization detects divergence, warns, and then rewrites stored label columns
(`features/labels.py:242-247,310-319,330-337`); the CN `adj_close == close`
synthesis applies to the akshare/efinance adapters, not uniformly to the yfinance
fallback; akshare concurrency is bounded at ten workers and opt-in efinance
batches stock codes; Gold refuses *feature* rows with missing proof, not every
write; a missing action clock is defaulted to null by the writer without
overwriting a supplied value; identity accepts provider MIC/exchange first with
configuration as fallback; the configured universes are current lists and cannot
establish survivorship; and "free, provable slice" and "needs a licensed feed"
were both overstatements that are now stated as unverified conditions. The two
verdicts survived the review unchanged.
