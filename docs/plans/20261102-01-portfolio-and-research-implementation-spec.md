# Portfolio and Research — Implementation Plan and Specification

**Recorded:** 2026-11-02
**Status (updated 2026-11-21):** Active specification and outcome ledger. Every
stage in the §2 queue is closed for its recorded scope except H00 publication,
which needs one explicit owner approval; no implementation stage is open. R02
was negative and promoted nothing. What remains is in the
[gap accounting record](../handoffs/20261121-01-gap-accounting.md).
**Purpose:** Personal portfolio monitoring and a defensible raw-data → structured-data
→ features → research → optional ML-signal workflow.
**Evidence:** [A01–A11 audit](../finding/20261102-product-readiness-audit.md).
**Execution:** [Gap accounting — 2026-11-21](../handoffs/20261121-01-gap-accounting.md)
(current), and the [completion handoff](../handoffs/20261120-01-complete-remaining-stages.md)
(closed 2026-11-21). The
[original Nov02 pickup](../handoffs/20261102-01-portfolio-and-research-pickup.md)
is historical. §10 records approvals, reviews and outcomes; §2 is the current queue.

## 1. Product direction and authorization

Deliver useful portfolio accounting independently of whether ML has skill.
First repair research correctness, then implement a narrow private portfolio
slice and prove one real-data path. Only then evaluate strategies and incremental
ML value. Do not prioritize operational refits or widen the seven-day age cap.
Controlled historical fitting for an approved experiment is distinct from
production retraining and must use isolated artifacts.

This specification contains implemented stages and approval-gated future work.
Before new code changes, the owner approves the selected bounded stage plan.
ADR-0024/0025 and the decisions recorded in §10 are accepted; do not reopen them
as if nothing had been decided. Approval does **not** authorize private-data or
provider access, source spend, delivery/scheduling/deployment, or the rest of the
roadmap. Architecture/storage/schema changes require accepted ADRs and the
change-matrix work in `AGENTS.md` first.

### Explicit non-goals

- No order execution, shorting/margin, tax filing, derivatives or multi-user service.
- No automatic ingestion/backfill, model training, repair, lake reset or vacuum.
- No synthetic proof on real data, guessed source expiry, raw-price research
  fallback, relaxed PIT checks or inference from current configuration.
- No new frontend framework, scheduler platform, distributed stack or generic
  experiment/plugin abstraction. Reuse local Streamlit, existing CLI and storage.
- No reopening accepted R1–R13 closure decisions without new evidence. R12 remains
  watched, not fixed; ADR-0023 containment and ADR-0021/0022 semantics remain binding.

## 2. Baseline and stage queue

**Current code baseline:** `603fc8d` on `main` (E01a), clean before the
2026-11-06 handoff-only refresh. ADR-0023 was preserved and committed separately
at `1b380af`; do not redo B00 or look for a deliberately dirty baseline.

**Historical audit baseline:** `00dad39` plus the then-dirty accepted ADR-0023
implementation and `.gitignore` checkpoint exclusion. Its historical full
gates were 1739 passed / 17 skipped, serial and parallel, with clean ruff/mypy,
unchanged generated catalog and passing Markdown lint. The audit's separate
78-test selection passed, but four diagnostic counterexamples still reproduced.
Neither result establishes real-market performance. Re-establish actual baseline
and dependency groups in the pickup thread; never copy historical counts as new.

| Stage | Deliverable | Depends on | Entry approval / remaining gate | Current status (updated 2026-11-21) |
| --- | --- | --- | --- | --- |
| B00 | Preserve/freeze carried ADR-0023 work | — | Completed separately | Complete: `1b380af`, `d85b561` |
| F01–F04 | Benchmark, momentum/ties, signed costs, arena completion | B00 | Approved and reviewed | Complete: `df4e423` |
| P00 | Private accounting/privacy ADR and account/import policy | F04 | ADR-0024 accepted; US/USD, canonical CSV only | Complete; split, backup, migrate, restore and compaction landed (ADR-0029 Part II, ADR-0031) |
| E00 | Research-artifact/reader compatibility ADR | F04 | ADR-0025 accepted; E01-1..5 frozen | Complete |
| D00 | US and CN offline feasibility packet | F04 | No source calls; adjusted/PIT evidence blocked | Complete: packet reviewed |
| P01a | Validated private ledger and atomic idempotent import | P00 | Fixtures only | Complete: `0f6d771` |
| P01b | Import CLI, help scans, user guide | P01a | P00-3 closed | Complete: `aeabe4b` |
| E01a | Dated evidence and immutable per-pair write side | E00 | Approved write-side scope | Complete: `603fc8d`; not full E01 |
| E01b.1 | Attempt records, last attempt/latest success, arena legacy-write removal | E01a | Owner-approved bounded plan (2026-11-06) | Complete (uncommitted): attempt dir, validated-run readers, frozen legacy view |
| E01b.2 | Migrate `equity report backtest` off the legacy layout | E01b.1 | Owner-authorized continuation (2026-11-06) | Complete (uncommitted): run + attempt evidence, no production legacy writer |
| E01c | Canonical universe identity and read-seam snapshot binding | E01b.1/E01b.2 | Owner-approved 2026-11-06: protocol v3, provenance stays `unknown` | Complete (uncommitted): protocol v3, universe basis, Delta/parquet read-seam snapshots |
| E01-6 | Persisted price-source marker (the only honest route to `real`/`synthetic`) | E01c | ADR-0027 accepted and implemented in three commits | Complete; rows without evidence still report `unknown` |
| P02 | Positions, cash, marks, P&L, reconciliation and three read CLI commands | P01a/P01b | Approved bounded fixture-only plan under ADR-0024/§5.3 | Complete: `positions`/`summary`/`reconcile`, ADR-0026 precision marks |
| D01 | Authorized US raw/unadjusted data slice | D00 | ADR-0028/0029/0030 accepted; raw reconstruction at the write boundary | Complete: 11 tickers / 58,653 rows in `data/lake_real/`; demo root and catalog byte-identical |
| P03 | Private Streamlit portfolio/readiness console | P02 | Approved private UI scope; display-only, loopback bind | Complete: four reviewed stages plus two closed follow-ups |
| R00 | Synthetic controls and computational scale benchmark | F04, completed E01 | Frozen experiment and numeric resource budget, §7 | Complete: `backtesting/protocol.py` freeze/refuse controls |
| R01 | Real-data baseline strategy evidence | R00, D01 | Frozen universe/window/holdout/costs; owner-approved scope | Complete: protocol v3 `e5387297…`, pre-holdout +22.79% / Sharpe 1.49 / 229 trades |
| R02 | Incremental ML experiment, not deployment/refitting | R01 | ADR-0034 accepted: vendor-stamped research with mandatory disclosure | Complete and negative: `promoted=false`, 1/5 criteria; nothing promoted |
| H00 | Single sanitized public Pages deployment | E01 | Owner decisions adopted 2026-11-21; publication still needs its own approval | Built, reviewed PASS, **not published**; Pages workflow is manual dispatch only |

The in-session todo list is a convenience, not the cross-thread source of
truth. Use this table, §10 and the current handoff.

**Status 2026-11-21:** every row above is closed except one gated decision —
H00 publication — which needs a single explicit approval. E01 is no longer
partial: E01c plus E01-6's persisted row-bound price-origin envelope (ADR-0027)
satisfy ADR-0025, and rows that carry no evidence still report `unknown`. What
remains is recorded in the [gap accounting record](../handoffs/20261121-01-gap-accounting.md):
that publish approval, environment deltas local to this checkout, and no open
implementation stage.

**Owner approval (2026-11-02 UTC):** B00 commit plan (b) — separate commits for the carried
ADR-0023 work and the planning docs, then one F01–F04 tranche commit after the full gates.
F01 fixed-share benchmark with the conservative complete-panel missing-mark policy; F02
instrument-local locality contract **plus the added deterministic tie policy (§3.2a)**; F03
signed cost-delta verdict with `1e-12` numerical tolerance; F04 zero/partial-run `exit 1` with
the `evaluated | no_trades | ineligible` vocabulary. The owner asked for the reproduced
rank-tie defect to be fixed now, inside F02, rather than deferred. B00 is done; F01–F04 were
approved for implementation in order. This paragraph records the Nov02 approval;
later P00/E00/D00 decisions are recorded in §10 and the current queue above.

Recommended implementation order is B00 → F01 → F02 → F03 → F04. Then P00 and
D00 can be investigated independently; E00/E01 must precede persisted research
experiments. Do not let a provider blocker prevent offline ledger tests or
portfolio accounting. Do not let a portfolio UI deadline bypass evidence fixes.
Use one writing worker at a time on shared files even where dependencies permit
parallel work. Independent **read-only** reviews may be parallel.

For each stage, append an outcome containing worker/reviewer, actual files,
commit/diff identity, test/mutation evidence, compatibility and next entry gate.
A stage is not complete until implementation, tests, docs and independent review
are accepted. Blocked stages remain blocked; no invented completion dates.

## 3. First implementation tranche: correctness fixes

These are small fixes in existing modules. They must not redesign storage,
introduce model fitting, alter default strategy parameters or regenerate the
canonical synthetic lake. Existing artifacts remain historical; fixes do not
retroactively certify or silently rewrite them.

### 3.1 F01 — Fixed-share benchmark (A01)

**Files:** `backtesting/arena.py`, `backtesting/report.py` only if error/metadata
propagation needs it; focused tests; backtesting/arena user guides.

**Proposed frozen contract:**

- Equal capital is allocated once at the first evaluation date to the fixed
  scoped cohort. For N instruments, `q_i = initial_cash / (N * price_i(start))`;
  `equity(t) = sum(q_i * price_i(t))`. There is no daily rebalancing,
  survivor renormalization, late admission, cash dividend or FX assumption.
- Use full `(market, MIC, symbol)` whenever present. Never collapse identical
  ticker spellings across venues. Standalone ticker-only helper fixtures remain
  supported when no canonical identity columns exist and ticker/date is unique;
  partial canonical identity is an error, not permission to guess it.
- The arena validates the requested/resolved cohort against loaded identities;
  an entirely absent requested instrument is not silently dropped. The standalone
  helper can only validate identities represented in its frame. Test both levels.
- Nonempty input requires unique identity/date keys, finite positive prices and
  positive finite initial capital. Empty input preserves the empty date/equity
  schema. Invalid nonempty input raises an actionable `ValueError`.
- Conservative initial missing-mark policy: every scoped identity needs a mark
  at inception and every evaluation date in the cohort's date union. Reject an
  incomplete panel; do not silently drop dates/instruments, carry a stale price,
  start late or replace missing values with zero. This deliberately does not
  promise a mixed-calendar benchmark. A different carry/delisting policy requires
  owner approval before F01 changes it.
- Use the existing verified split-neutral marks handed to the arena. Do not
  substitute raw prices, bypass action proofs or change adjustment semantics.
  Keep the documented zero-cost/zero-lag benchmark asymmetry visible.
- Output sorted actual dates and equity. The same rows in any input order must
  yield identical results. Report benchmark policy in card scope; do not present
  the correction as evidence that a strategy now works.

**Required regressions:** proposed names describe expected coverage; adapt file
placement after reading current tests.

- `test_buyhold_uses_fixed_initial_shares`: A 100→200→100 and B 100→100→100,
  initial 100, must produce 100→150→100, not 112.5 terminal.
- Single instrument, unequal paths, and initial-capital scaling match hand math.
- `test_buyhold_keeps_same_symbol_different_venues_distinct`.
- `test_buyhold_is_order_invariant`.
- Parameterized duplicate keys, partial identity, missing inception/interior mark,
  non-positive/non-finite marks and invalid capital reject visibly.
- Arena/card integration consumes the corrected curve, not an independently
  reconstructed benchmark. Keep empty-helper behaviour compatible.

**Mutation:** restore daily-return averaging; the fixed-share named test fails.

### 3.2 F02 — Momentum locality (A02)

**Files:** `backtesting/strategy/momentum.py`, regression tests and strategy docs.

- Sort by canonical identity and date before all time operations. Compute return
  and skip within the same instrument window, including forward-filled target
  weights. No ticker's lag may come from another ticker or venue.
- For lookback L and skip S, no instrument receives a nonzero target until it
  has the required L+S prior observations. Another instrument's longer history
  cannot satisfy this requirement.
- Deterministic date/ticker-major/shuffled inputs produce identical key-sorted
  outputs. Duplicate identity/date input is rejected, not resolved by row order.
- Preserve ranking direction, rebalance schedule, long-only/default parameters,
  target-weight interface and identity columns. Do not lower `min_stocks=10`
  just to make the five-ticker demo trade. The ranking *tie* policy is amended by
  §3.2a (owner-approved extension, not a silent widening).
- Truncating the history at cutoff must preserve weights on/before that cutoff;
  add an engine as-of integration regression, not just an isolated expression test.

**Required regressions:**

- `test_momentum_lag_is_instrument_local` with lookback 2, skip 1 and the audit's
  three paths; instrument starts cannot inherit preceding-group returns.
- `test_momentum_is_order_invariant`, staggered listing histories, duplicate keys,
  identical symbols on two MICs, warmup/insufficient-universe zero targets.
- `test_momentum_future_append_does_not_change_past_weights` and engine fixture
  verifying cutoff behaviour with isolated synthetic action proofs.

**Mutation:** move shift outside the identity window; locality/order tests fail.

### 3.2a F02 amendment — deterministic rank-tie selection (owner-approved)

Reproduced defect: selection uses `rank()` with its average method, so a tie that
straddles the `top_count` cut selects fewer names than `top_count` while keeping
`1/top_count` weights. Probe: four instruments, `lookback=1`, `skip=0`,
`top_pct=0.2`, two tied best performers → every weight `0.0`, i.e. an unreported
full-cash portfolio despite a valid decision. No test pinned it, so the stage
fails silently.

**Frozen contract addition:**

- Order candidates by `past_return` descending, then by canonical identity
  ascending (`market`, `mic`, `symbol`, or `ticker` for identity-less fixtures).
  Selection is by ordinal rank over that order, so it is deterministic and
  independent of input row order.
- Whenever at least `top_count` eligible (non-null `past_return`) instruments
  exist, exactly `top_count` are selected and their long targets sum to `1.0`.
  The short side mirrors this for `bottom_count` with `-1/bottom_count`; the
  existing long-side precedence where the two sets overlap is unchanged.
- No new parameter, no change to `top_pct`/`bottom_pct`/`min_stocks` semantics.

**Required regressions:** `test_momentum_tie_at_cut_keeps_full_investment` (the
probe above must be fully invested with `top_pct=0.2` selecting exactly
`top_count=1` name at `1.0`, and `top_pct=0.5` selecting both tied names at `0.5`
summing to `1.0`; both readings must fail on the old average-rank code),
`test_momentum_tie_break_is_identity_deterministic` (same returns, venues
or symbols permuted in input order → same selected set), plus a long/short
overlap case pinning long precedence.

**Mutation:** restore `rank()`'s average method → the tie test fails.

### 3.3 F03 — Cost-card truthfulness (A03)

**Files:** `backtesting/report.py`, `tests/unit/test_arena.py` or a dedicated fast
pure-report test module, arena/findings guide.

- Read actually present finite metrics. A missing metric is not the zero returned
  by a convenience property. Missing/non-finite comparison data is inconclusive;
  omit unavailable numeric fields and explain why, never persist NaN as evidence.
- Proposed rule: `delta = realistic_sharpe - zero_cost_sharpe`, numerical equality
  tolerance `1e-12` (not a significance test). Delta below `-tolerance` is a
  negative cost effect, above tolerance is a positive **observed difference**,
  and equality is inconclusive. Do not divide by Sharpe. Positive differences
  must disclose that cost/execution interaction is not evidence of causal benefit.
- Preserve available regime metric keys; add an explicit signed delta. Describe
  measured values and scope, not “statistically significant” or low turnover.
- Remove low-turnover assertions unless a measured turnover artifact exists;
  implementing turnover is not required for F03. Do not invent a percent loss
  for a zero/negative denominator.

**Required regressions:** negative→more-negative (-1→-2), positive→negative,
zero→negative, unchanged zero, positive deterioration, observed improvement,
missing metric and non-finite metric. Specifically,
`test_cost_card_negative_sharpe_deterioration_is_negative` must fail on the old
formula; `test_cost_card_does_not_infer_turnover` pins the text.

**Mutation:** restore ratio-based verdict and/or turnover sentence; named tests fail.

### 3.4 F04 — Arena completion and failure policy (A04, A10)

**Files:** `backtesting/arena.py`, CLI arena command, report/card admission checks,
focused engine/CLI tests, unified CLI help scan, user guides.

- Preserve per-run isolation: one failed strategy/regime does not prevent other
  runs from executing. Record requested, completed and failed pairs with a
  sanitized failure type/reason in the in-memory outcome; additions default
  compatibly for existing library fixtures/callers. No new durable manifest is
  required here; E01 owns that boundary.
- Zero completed requested runs: print actionable diagnostics, `typer.Exit(1)`,
  no success banner and no new success cards. Validate before calling a writer
  that would replace latest artifacts.
- Any requested pair failed: successful artifacts may be written, but label the
  invocation partial, show counts/pairs and exit **1**. Only all-completed requested
  runs exit 0. Do not add an allow-partial flag in this stage.
- Completed computation and evaluable evidence are distinct. No-trades, insufficient
  universe/history and unavailable metrics must be disclosed and must not become
  measured zero Sharpe or a positive strategy verdict. A legitimate fully executed
  cash-only/no-condition run need not fail the CLI; its evaluation status must be
  explicit. For the default momentum/five-ticker case, report insufficient universe
  rather than loosening defaults. Specify any new auxiliary evaluation fields in
  the worker's approved contract; do not change prediction API status vocabulary.
- Run-level exceptions and output-write failures stay visible/nonzero. Do not
  implement catch-all success fallbacks or remove the successful runs' evidence.
- Until E01, clearly disclose that old artifact directories can remain. Do not
  claim that a failed run refreshed previously published evidence; do not delete
  old results automatically as part of this fix.

**Required regressions:** all-failed outcome exits 1 and writer not called;
one-success/one-failure finishes both and exits 1; full matrix exits 0; failed
pair details retained; no-trades vs failure vs ineligible distinguished; actual
`run_arena` exception isolation and CLI wiring exercised, not just one mock.
Help-scan and a temporary-output artifact test must accompany public CLI changes.

**Mutation:** remove the zero/partial failure exits and failure recording; named
CLI/outcome tests fail while success-path tests still pass.

## 4. E00–E01 — Reproducible research evidence (A09)

**ADR gate:** changing auxiliary layout/readers requires a new accepted record.
Do not reserve an ADR number in advance; inspect the decision index at drafting.
Use existing `data/findings/` auxiliary ownership and Pydantic write-boundary
models, not cataloged run tables or a parallel research platform.

The ADR must settle immutable per-run layout, atomic publication, latest-view
compatibility and failed-attempt visibility before E01 implementation. Proposed
requirements:

- Each run records unique run ID, aware timestamps, protocol/version, exact
  canonical universe and dates, strategy/model parameters, actual fees/taxes,
  execution timing, seed, code commit plus dirty-diff digest, non-secret config
  digest, and `synthetic | real | mixed | unknown` data provenance. Missing source
  evidence stays unknown; Bronze identity provenance alone does not prove real data.
- Source snapshots/hashes identify **what was actually read**. Collecting current
  Delta versions after computation is not snapshot binding. Non-Delta fixtures
  need immutable input hashes. Do not hash or copy secrets into manifests.
- Every requested pair is completed/failed/ineligible explicitly, with measured
  metric availability and failure detail. Distinguish last attempt from last
  successful comparable run so failures cannot leave a misleading healthy “latest”.
- Persist actual backend equity timestamps alongside values/drawdown, and OOS
  identity/date/label/prediction/fold rows where applicable. Never reconstruct a
  calendar range from a Series length. Legacy undated curves remain undated,
  labeled historical, not silently repaired.
- Keep the existing `BacktestResult.equity_curve` Series interface for compatibility;
  add dated evidence through an explicitly tested companion field/view as needed.
  Read `_extract_equity_curve` and backend report timestamps before designing it.
- Immutable run directories are finalized only after validated artifacts and
  checksums exist. A failed/partial write cannot advance the successful latest
  projection. Retry/restart behaviour and collisions are tested without overwrites.
- `/findings`, `/backtests`, notebook, W&B and static/local readers need an explicit
  compatibility plan. Keep their existing default shapes unless a separately
  approved API change is documented/tested. Legacy paths may be read-only views;
  an old card must never point silently at a different run's newly overwritten curve.
- No automatic migration/deletion of existing findings or lake/model artifacts.
  Historical unverifiable runs must not gain a fabricated manifest or real-data label.

**Acceptance:** two identical-scope invocations coexist; another scope cannot
replace earlier evidence; actual irregular dates round-trip; corrupt/incomplete
runs do not look successful; readers retain expected legacy shapes; unknown
provenance remains visible; failed latest attempts are displayed without hiding
last-success age; private information is excluded from public/W&B projections.
Update architecture/storage reader docs. Catalog runs are unnecessary for purely
auxiliary changes; regenerate if a pipeline/feature contract is also changed.

## 5. P00–P03 — Private portfolio MVP (A05, A11)

### 5.1 Owner decisions required in P00

| Decision | Recommended bounded proposal | Not assumed approved |
| --- | --- | --- |
| Account scope | One account, one selected market and base currency | Actual broker/market/currency and a sanitized CSV format |
| Accounting | Average-cost book accounting with explicit opening basis | Tax-lot/tax-reporting correctness; unknown opening basis policy |
| Persistence | Separate private auxiliary ledger, preferably SQLite using existing local patterns | Exact layout, correction/reversal semantics, migration/backup policy |
| Identity | Exact canonical instrument on every security event | Ticker/MIC guessing, automatic historical relabeling |
| Valuation | Stored, unadjusted as-of quote plus explicit staleness/provenance | Strict-research availability certification or live refresh |
| UI | Existing loopback Streamlit private area | Public hosting or proxied/multi-user access |

P00 must produce an accepted accounting/privacy/storage ADR before P01. If a new
top-level `portfolio/` package is selected, extend import boundary tests and
architecture docs; keep it independent of `cli/`, `dashboard/`, `sources/`, ML
and signal policy. Do not put account state into tracking's held flag or
`tracking.sqlite3` by convenience. Auxiliary state is not cataloged and does not
belong in `data/lake/`.

### 5.2 P01 — Ledger/import contract

- Pydantic-validated, versioned events: opening cash/positions with known or
  explicitly unknown basis, BUY, SELL, DEPOSIT, WITHDRAWAL, fee and cash dividend.
  Decide split-event handling in P00; until supported, block affected position
  valuation rather than misstate quantity. No broker/order API or auto-fetch.
- Each record identifies account, external source/event ID, canonical instrument
  when applicable, currency, aware effective/received times, deterministic source
  sequence, signed cash effects, quantity, price, fees, source digest/parser version.
- Decimal quantities/money/basis; currency rounding and ordering policy must be
  frozen in the ADR. Reject non-finite values, malformed identity/currency,
  ambiguous ordering, oversells and unsupported margin/short events.
- Same account/external ID and same content is idempotent; changed content under
  the same ID fails visibly. Corrections are explicitly authorized traceable
  events, not in-place source-history edits. Batch import is atomic.
- Preview/dry-run performs validation and reports proposed effects without
  mkdir/DB/WAL/checkpoint writes or quote/model loading. Tests snapshot the
  filesystem to prove this. Do not rely on existing path creation as “no write”.
- No credentials/full account identifiers in logs. Apply private directory/file
  permissions to DB, journal/WAL, export and backup paths. No public telemetry.
- Use a canonical import CSV schema plus one approved broker mapping, not a
  generic mapping/plugin framework. Fixtures contain invented account values.

**CLI proposal (freeze at P00):** native `equity portfolio import`, `positions`,
`summary`, `reconcile`. No write action is hidden in a read command. Wire sub-app
before decorating imports, provide help/Annotated options and exit 1 on required
failure; add unified CLI help coverage and user guide. An actual private account
import requires separate explicit owner authorization, even after tests pass.

### 5.3 P02 — Projections and valuation contract

Replay ledger events deterministically into quantity, cost basis, cash,
realized/unrealized P&L, dividend/fee totals and as-of NAV/allocation. Watchlist
membership remains separate; do not infer fills from BUY/SELL candidates.

- Reject unsupported FX rather than adding unlike currencies. Imports may record
  an unknown opening basis, but basis-dependent P&L remains unavailable, never zero.
- Stored raw quote marks are not research-adjusted prices. Quantity adjustments
  and quote basis must agree; source/as-of/staleness and synthetic/unknown mode are
  visible. No future mark relative to the requested valuation time.
- Missing/stale required marks: show cash/quantities and any marked subtotal,
  but withhold complete NAV/P&L/portfolio allocation, naming affected instruments.
  Never omit an unpriced holding from the denominator or carry silently.
- Mark selection uses exact identity and venue session; no network fallback.
  A reliable present-day quote display does not certify historical PIT features.
- Reconciliation compares explicit statement quantities/cash and reports matched,
  mismatched or unavailable with documented precision/tolerances. It does not
  mutate ledger state to make the numbers agree. No tax-return certification.

**Proposed average-cost hand oracle (P00 must approve):** opening 10 shares at
basis 100 each and cash 1000; buy 5 at 120 with fee 5 gives cash 395, quantity 15,
basis 1605; sell 4 at 130 with fee 2 gives cash 913, remaining basis 1177 and
realized gain 90; receive net dividend 11 gives cash 924; mark remaining 11 at
125 gives market value 1375, NAV 2299 and unrealized gain 198. Total gain 299
is 90 realized + 198 unrealized + 11 income, with no external flows. Test split
quantity/basis preservation separately once split accounting is approved.

**Acceptance:** hand oracle; same-file rerun; duplicate-ID conflict rolls back;
multiple fills/order; fractional shares/fees; deposit/withdrawal not investment
profit; unknown basis; missing/stale/future/wrong-currency marks; split handling;
oversell; restart replay; read/dry-run no-write; exact reconciliation and mismatch.
Do not claim time-weighted/money-weighted returns until cash-flow timing and an
independent return oracle are separately specified.

### 5.4 P03 — Private console

Extend existing Streamlit, not a new frontend. Portfolio and Data readiness are
first: as-of NAV or explicit unavailable state, cash, positions, book P&L,
allocation/concentration, quote age/provenance and reconciliation differences.
Keep held/watch-only context distinct from ledger positions. Render immutable
Research runs after E01; show Signals as candidates with reasons/timestamps and
unavailable states, never as orders or calibrated profit probabilities.

Require effective loopback binding before private reads. No private account
contents in static exports, Pages, public API projections, error logs or W&B.
Display-only pages do not import/write ledger events, backfill or run inference.
Test privacy guards before data access, empty/error states, missing/stale totals,
and an invented-data UI smoke path. Do not weaken ADR-0018's proxy rejection to
make private data accessible remotely.

## 6. D00–D01 — Real-data feasibility and raw/structured lineage

D00 is an offline decision packet, not a new source implementation. Identify
one proposed market/provider/universe and document which contracts its actual
published format can support: identity, raw vs adjusted prices, actions,
publication/revision clocks, complete coverage, expiry and licensing/retention.
Separate observed evidence from vendor documentation and unknown claims.

- Produce a feasibility matrix plus a blocked/feasible decision. If the existing
  proof cannot be supplied, propose an ADR amendment or stop; never guess it.
- Design receipt/publication/effective timestamps and source-response digest,
  parser/schema version, validation result and read snapshot binding only for
  the selected slice. Delta time travel is not a vendor publication vintage.
- Identify current enrichment paths that remain null/unavailable; do not promise
  macro/news/analyst/SEC value merely because routes or columns exist.
- Preserve synthetic/real separation. Do not reset the current canonical lake,
  relabel Bronze as real or reuse demo coverage on real prices. Target root and
  any isolation/migration changes require explicit design and owner authorization.

D01 requires source access/spend authorization, approved scope and any required
source/schema/storage ADR, config/test/catalog/docs work. Start with temporary
fixtures and isolated authorized samples, then report actual date/identity/action
coverage and rerun consistency. It must not claim historical availability from
local ingestion time. No operational readiness if the selected proof is blocked.

## 7. R00–R02 — Evidence gates before ML operations

> **Status 2026-11-21: closed.** R00 shipped the freeze/refuse controls, R01 produced the
> frozen-protocol baseline, and R02 ran once under ADR-0034 and came back negative with
> `promoted=false`. The text below is the binding contract those stages ran under; the
> disposition of what remains is §11.

### R00: offline harness controls and computational scale

Freeze a versioned protocol before running: data generator/seeds, tradable signal
clock, entry time/price, label maturity/horizon, purge/embargo, train/test/holdout,
metrics, baselines, acceptance tolerances and resource budget. Use E01 manifests.
Synthetic results are **harness evidence only**. A known signal must be observable
before entry; a same-close oracle must not grant an impossible execution price.

- Positive controls: an implementable planted signal traverses the selected
  feature/label/predict/decision/backtest path and beats its defined baseline.
- Null controls: independent noise/permuted labels, multiple predeclared seeds
  and dependence-aware resampling. Statistical false positives are possible;
  do not require every random seed to lose or choose thresholds after seeing
  results. Owner approves a nominal false-positive protocol and power target.
- Leakage controls: append-future invariance, delayed label/source observations,
  wrong identity, pending labels, missing coverage and execution lag/horizon.
- Scale: 5/50/200 instruments over the same three-year session design, with
  sufficient strategy warmup and an explicit minimum-universe disclosure. Measure
  wall time, peak RSS, I/O where measurable, decision count and output parity.
  Record hardware/dependencies, warm/cold cache and replication strategy.
- Numeric resource ceilings must be approved before benchmarks; no arbitrary
  performance target is implied here. Cap pilot time/RSS and record a larger
  point as censored/aborted when it exceeds budget, not as zero/success. Benchmark
  correctness and numerical parity before optimizing the measured hotspot.

### R01: real-data simple-strategy baseline

Requires D01 feasibility, E01 evidence and R00 harness checks. Freeze an explicit
universe, survivorship/selection limits, dates and untouched holdout. Compare
simple deterministic strategies against the corrected benchmark with matching
scope and disclosed fee/lag asymmetry. Report net returns, drawdown, Sharpe
convention, turnover/exposure, sample size, unavailable coverage and uncertainty.
Do not select the best strategy from a sweep and evaluate it on the same holdout.

Computational scale is not economic capacity. Capital-capacity claims additionally
need spread/slippage, market impact, volume participation, execution constraints,
and an approved cost/liquidity model; multi-market use additionally needs FX and
venue-appropriate costs. Those are separate decisions, not implicit additions to
F01 or a larger `initial_cash` argument.

### R02: one incremental ML hypothesis

Only after R01, freeze one target/policy and compare against no-skill/simple-model
and technical-rule baselines on matched OOS dates/opportunities. V1 direction
precision vs V2 candidate-success precision is not an apples-to-apples trading
comparison. Use actual event intervals where available and prove per-refit
maturity/purging, not only row-count splits.

Persist fold predictions/probabilities/outcomes, Brier/calibration and other
appropriate discrimination metrics, plus the same economic metrics/costs as the
baseline. Require prespecified uncertainty and deployment/promotion criteria;
negative/inconclusive is a valid outcome, not a request to retune the holdout.

Freeze signal availability, entry and outcome alignment: a next-session-close
forecast is not automatically tradable by an engine entering at that same next
close. Do not silently alter ADR-0021 forecast semantics. No automated refit,
seven-day cap change, live signal enablement or model promotion follows from
passing a synthetic control or obtaining one positive fold.

## 8. H00 — Optional public hosting cleanup

> **Status 2026-11-21: built, reviewed `PASS`, not published.** The rules below are met by the
> allowlist builder and the retired push/schedule triggers; no publish path exists yet and
> publication is owner-gated. See §11 and the
> [page guide](../user-guide/20261121-h00-sanitized-demo-lake-page.md).

One workflow must publish one composed/sanitized Pages artifact rather than
letting catalog/dashboard deployments replace each other. Decide public routes,
producer/retention, privacy allowlist and failure/stale-data display first. Do
not publish a private portfolio snapshot or make a fresh CI checkout the durable
portfolio/lake. No deployment authorization is implied by documentation/tests.

## 9. Shared verification and completion contract

Before editing, read `AGENTS.md`, relevant accepted ADRs, source bodies, tests,
and matching skills (pipeline/schema/catalog as applicable). Record new nested
settings with `.env.example`; API keys stay raw at client seams. No dependency
or architecture addition without its required work.

Each implementation stage requires:

1. An approved minimal file/behaviour plan and verified dependency/ADR gates.
2. Named regressions that fail on the old rule, with independent hand oracles
   where numeric results matter. No tests that simply restate the implementation.
3. An adversarial reviewer on the **dirty stage diff**, with no product edits or
   commits by that reviewer. Resolve blockers and re-review changed behaviour.
4. One-rule-at-a-time mutation proof, restore from a `/tmp` copy of the current
   file and `diff -q`. Never `git checkout`/stash/reset to restore another agent's
   or the owner's dirty work. Shared-file mutations/tests must be serialized.
5. Exact commands/counts, compatibility notes, remaining unknowns, privacy/no-write
   checks, relevant user/architecture docs and stage ledger updates.

Focused selections should avoid repeating the slow arena matrix for pure-helper
iterations. At a frozen integration boundary run the project gates once, with
full output captured and JUnit summaries where appropriate:

```bash
uv sync --group dev --group backtesting
uv run pytest --junitxml=/tmp/portfolio-research-serial.xml \
  > /tmp/portfolio-research-serial.log 2>&1
uv run pytest -n auto --junitxml=/tmp/portfolio-research-parallel.xml \
  > /tmp/portfolio-research-parallel.log 2>&1
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run equity catalog-generate
make lint-md
git diff --check
```

Run serial and parallel suites **sequentially**, never overlapping with worker
pytest. Inspect current markers: default fast selection excludes network and
integration, so add only individually reviewed offline integration fixtures when
a stage requires them. Do not enable network suites broadly. Optional feature
checks must install their dependency group rather than claim skipped paths as
verified. Serial pytest remains a real gate because R13 was serial-order-only.

Use proactive `lens_diagnostics source=lsp scope=paths` for changed code; an empty
session cache is not proof of cleanliness. Full gates are last, after review and
mutation fixes. Preserve logs; do not rerun an unchanged suite to retrieve a
summary already captured. On failure select the failing tests with `-x`.

Schema/pipeline/storage changes must satisfy the repository change matrix,
including catalog regeneration where applicable; never edit JSONL directly.
Only the coordinator stages/commits explicit stage files after owner permission;
no worker commits, blanket `git add .`, runtime/private data, generated site or
ignored progress artifacts. Implementation acceptance and actual operational
run/deployment authorization remain separate.

## 10. Approval and outcome ledger

The entries below preserve dated approvals and stage outcomes. The Nov02 draft
started with no new implementation/ADR acceptance; subsequent entries record
F01–F04, accepted ADR-0024/0025, P01a/P01b and E01a. Use the current §2 queue
and Nov06 handoff for next work, not a historical entry's then-pending gate.
Record new P02/E01b/provider/experiment decisions explicitly when approved.

Use this entry shape per stage; link a dated tracked report if it becomes long:

```text
Stage / state / recorded UTC:
Owner-approved scope and policy reference:
Dependency and accepted-ADR evidence:
Stage-start snapshot and reviewed final diff/commit identity:
Worker invocation/checkpoint (optional local convenience, not sole evidence):
Independent reviewer, verdict and issue resolution:
Named regression and one-rule mutation/restore evidence:
Focused tests / final gates / exact counts / retained log locations:
Docs, schema/catalog, privacy, compatibility and migration status:
Residuals, explicit stop condition, next eligible stage:
Owner acceptance and authorized coordinator commit (if any):
```

### Recorded stage entries

```text
B00 / done / 2026-11-02T12:09Z:
Owner-approved scope and policy reference: commit plan (b); preserve carried ADR-0023.
Dependency and accepted-ADR evidence: HEAD 00dad39; ADR-0023 accepted; no new ADR needed.
Stage-start snapshot: B00 checkpoint (ignored) + /tmp/b00-stage-start-tracked.diff;
  tracked/untracked sha256 captured; nothing staged or committed at capture time.
Independent reviewer: not applicable (no product change).
Named regression / mutation: none (no product change).
Focused tests: 36 passed / 0 failed / 0 skipped / 85.075 s over test_feature_jobs.py,
  test_feature_loader_string_view.py, test_arena.py, test_backtesting_regressions.py;
  logs /tmp/b00-focused-baseline.{log,xml}. Backtesting group verified installed.
Docs, schema/catalog, privacy, compatibility: none touched; no lake/model/private data.
Residuals: full serial/parallel gates, ruff/mypy, catalog regeneration deferred to the
  frozen tranche boundary per section 9.
Next eligible stage: F01 (approved), then F02 (includes the section 3.2a tie fix).
```

```text
F01 / done / 2026-11-02T12:42Z:
Owner-approved scope and policy reference: section 3.1 fixed-share benchmark, conservative
  complete-panel missing-mark policy, order invariance, card-scope disclosure.
Dependency and accepted-ADR evidence: B00 commits 1b380af (ADR-0023) and d85b561 (planning);
  ADR-0010 market keys, ADR-0020 demo-lake surface, ADR-0023 read seam; no new ADR needed.
Stage-start snapshot and reviewed final diff: d85b561 clean tree; final diff sha256
  77302c66e8678f5a13b85d614a31e5b2f8f37317a318a8d6225d91294193a203 across exactly
  arena.py, report.py, tests/unit/test_arena.py and the two arena/backtesting guides.
Worker invocation/checkpoint: worker rounds 1-3 (ignored checkpoints
  20261102-f01-worker-{1,2,3}.md) resolved: non-empty frame without close now raises; the
  benchmark post-condition validates columns/unique/ascending/union-equal dates and finite
  equity; card/artifact equality proven from run_arena; caller scope can no longer overwrite
  the four mandatory disclosures; dead InstrumentKey branch removed.
Independent reviewer, verdict and issue resolution: reviewer read-only rounds 1-3
  (checkpoints 20261102-f01-reviewer-{1,2,3}.md). Round 1 BLOCKED (1 major, 3 minor), round 2
  BLOCKED (guard date-union gap), round 3 PASS with one inaccurate test-docstring residual,
  corrected by the coordinator.
Named regression and one-rule mutation/restore evidence: test_buyhold_uses_fixed_initial_shares
  fails on the restored daily-return-averaging rule (mutant terminal 112.5 vs required 100.0),
  arena.py backed up to /tmp/f01-arena-dirty.py.bak, restored with diff -q identical and the
  named test green again. Logs /tmp/f01-mutation.log, /tmp/f01-mutation-restored.log.
Focused tests / final gates / exact counts / retained log locations: tests/unit/test_arena.py
  57 passed / 0 failed / 0 skipped / 120.0 s serial (/tmp/f01-coordinator-arena.{log,xml});
  ruff check and format clean on the three code files. Full serial/parallel gates remain
  deferred to the frozen tranche boundary.
Docs, schema/catalog, privacy, compatibility and migration status: two user guides updated
  (fixed-share policy, complete-panel rejection, missing-close behaviour, inception float
  caveat, scope immutability); no schema, catalog, CLI or storage change; no lake/model write.
Residuals, explicit stop condition, next eligible stage: inherent one-ULP inception
  difference (documented, tolerance-pinned); ticker-only fixtures with a missing close column
  now raise by contract. Stage stops here; F02 is next and adds the section 3.2a tie fix.
Owner acceptance and authorized coordinator commit (if any): pending tranche acceptance; no
  stage commit made (commit plan (b) commits once after the full gates).
```

```text
F02 / done / 2026-11-02T13:10Z:
Owner-approved scope and policy reference: section 3.2 locality contract plus the section 3.2a
  deterministic tie policy the owner asked to fix now (not deferred).
Dependency and accepted-ADR evidence: F01 accepted; engine already sorts the as-of window by
  canonical identity and date; no new ADR, schema, catalog or dependency change.
Stage-start snapshot and reviewed final diff: stage start = F01-accepted tree
  (/tmp/f01-stage-final.diff, arena.py/report.py/test_arena.py verified byte-identical);
  final diff sha256 a06de87ba6c941db4cbd2bc777066842d69b7b62e780c4bc34f3db41c422209d over
  momentum.py, tests/unit/test_momentum_weights.py (new) and the two guides.
Worker invocation/checkpoint: worker rounds 1-2 (ignored 20261102-f02-worker-{1,2}.md).
  Round 1 implemented locality/warmup/order/duplicate rejection and the ordinal tie
  selection. Round 2 replaced the weak as-of test with a post-cutoff-tail perturbation
  invariance test after the reviewer showed the original could not detect an engine-side leak.
Independent reviewer, verdict and issue resolution: reviewer read-only rounds 1-3
  (20261102-f02-reviewer-{1,2,3}.md; round 3 persisted by the coordinator because the preset
  forbids repo writes). Round 1 BLOCKED (engine as-of test not load-bearing), round 2 BLOCKED
  (strategy-only probe cannot falsify engine truncation), round 3 PASS once the probe also
  widened the engine's as-of window in-process and the test failed at
  tests/unit/test_momentum_weights.py:784 (equity curves 108736.37 vs 28067.19).
Named regression and one-rule mutation/restore evidence: (A) shift moved outside the identity
  window -> test_momentum_lag_is_instrument_local and test_momentum_is_order_invariant fail;
  (B) ordinal selection replaced by average rank() -> test_momentum_tie_at_cut_keeps_full_investment
  fails on the top_count=1 identity tie-break. Both restored from
  /tmp/f02-momentum-dirty.py.bak with diff -q identical; logs /tmp/f02-mutation-{a,b}.log.
Focused tests / final gates / exact counts / retained log locations: tests/unit/test_momentum_weights.py
  + tests/unit/test_backtesting_regressions.py = 27 passed / 0 failed / 0 skipped / 3.4 s serial
  after restore (/tmp/f02-coordinator.{log,xml}); ruff check and format clean.
Docs, schema/catalog, privacy, compatibility and migration status: momentum bullets updated in
  backtesting.md and the arena/findings guide; output row order is now deterministically
  key-sorted (all consumers filter/sort by keys); min_stocks=10, rebalance schedule, ranking
  direction and the weight interface unchanged; no lake/model write.
Residuals, explicit stop condition, next eligible stage: initialize() still logs
  data["ticker"].n_unique() so a ticker-less composite frame would fail there (pre-existing,
  outside F02); pre-existing markdownlint MD029 in backtesting.md. Stage stops here; F03 next.
Owner acceptance and authorized coordinator commit (if any): pending tranche acceptance.
```

```text
F03 / done / 2026-11-02T13:40Z:
Owner-approved scope and policy reference: section 3.3 signed cost-delta verdict with 1e-12
  numerical tolerance, no division by Sharpe, no invented turnover, omit unavailable fields.
Dependency and accepted-ADR evidence: F01 accepted (fixed-share benchmark, mandatory scope keys);
  no new ADR, schema, catalog or dependency change.
Stage-start snapshot and reviewed final diff: stage start = F02-accepted tree
  (/tmp/f02-stage-final.diff); final diff sha256
  42a940efabf7cf9c8df6f6601ec7a874f4f78ecb7ff200863f450c6b7f0ab684 over report.py (cost
  block only), tests/unit/test_cost_card.py (new, fast) and the arena/findings guide.
Worker invocation/checkpoint: worker rounds 1-2 (ignored 20261102-f03-worker-{1,2}.md).
  Round 1 replaced the ratio rule with finite signed-delta logic. Round 2 added the
  finite-delta overflow guard, the ambiguous duplicate-regime policy and the boundary tests.
Independent reviewer, verdict and issue resolution: reviewer rounds 1-3
  (20261102-f03-reviewer-{1,2,3}.md; rounds 2-3 persisted by the coordinator). Round 1
  BLOCKED (finite operands overflowed the delta to inf -> null metric and unreadable card;
  duplicate-regime order dependence). Round 2 returned an incomplete progress-only report, so
  a bounded verdict round was re-dispatched: PASS with the overflow and duplicate checks
  reproduced end-to-end. The round-1 out-of-scope finding on docs/user-guide/backtesting.md
  was dismissed with evidence: that file's diff is byte-identical to the F02-accepted snapshot.
Named regression and one-rule mutation/restore evidence: restoring the ratio-based verdict
  plus the turnover sentence fails 19 of 20 cost-card tests, including
  test_cost_card_negative_sharpe_deterioration_is_negative and
  test_cost_card_does_not_infer_turnover; report.py restored from
  /tmp/f03-report-dirty.py.bak with diff -q identical; log /tmp/f03-mutation.log.
Focused tests / final gates / exact counts / retained log locations: tests/unit/test_cost_card.py
  20 passed / 0 failed serial (/tmp/f03-coordinator.{log,xml}); tests/unit/test_arena.py 57
  passed unchanged (/tmp/f03-review2-arena.log); ruff check/format clean.
Docs, schema/catalog, privacy, compatibility and migration status: cost-card section added to
  the arena/findings guide; card id/axis/claim/evidence_refs, regime metric keys and the F01
  mandatory scope keys preserved; `metrics` is a superset plus `cost_regime.sharpe_delta`; no
  consumer references the removed sentence; no lake/model write.
Residuals, explicit stop condition, next eligible stage: a finite extreme delta still formats
  `:.2f`; the stale on-disk data/findings/cost-regime.json keeps the old sentence until a run
  regenerates it (not rewritten by this stage); strategy-comparison and vs-benchmark cards still
  read the convenience property (F04 admission scope). Stage stops here; F04 next.
Owner acceptance and authorized coordinator commit (if any): pending tranche acceptance.
```

```text
F04 / done / 2026-11-02T15:05Z:
Owner-approved scope and policy reference: section 3.4 — per-run isolation, requested/completed/
  failed recording, zero-completion exit 1 with no writer call, any-failure PARTIAL exit 1,
  no allow-partial flag, `evaluated | no_trades | ineligible` vocabulary, evaluated-only card
  admission, unchanged `min_stocks=10`.
Dependency and accepted-ADR evidence: F01-F03 accepted; no new ADR, schema, catalog or
  dependency change.
Stage-start snapshot and reviewed final diff: stage start = F03-accepted tree
  (/tmp/f03-stage-final.diff); final diff covers arena.py, report.py, cli/commands/arena.py,
  tests/unit/test_arena.py, tests/unit/test_cli_unified.py, new
  tests/unit/test_arena_failure_policy.py and the arena/CLI guides; momentum.py,
  test_momentum_weights.py and test_cost_card.py verified byte-identical to their accepted
  states at every round.
Worker invocation/checkpoint: worker rounds 1-6 (ignored 20261102-f04-worker-{1..6}.md). Fixes:
  strategy construction moved inside the per-pair isolation boundary; sanitizer hardened twice
  (authorization header, bare bearer/basic scheme, key=value secrets, slash/backslash/drive
  paths, URL credentials, marker-safe truncation); vacuous 80-day card test reworked to
  evaluated runs with numeric metric pins; `min_stocks` type/boundary and BaseException
  propagation coverage; completion accounting moved to the requested set (duplicate dedupe,
  missing-pair naming, contradictory-pair diagnosis, unrequested pairs never published);
  zero-completion branch names every missing requested pair.
Independent reviewer, verdict and issue resolution: reviewer read-only rounds 1-6
  (20261102-f04-reviewer-*.md; later rounds returned inline and were persisted by the
  coordinator). Round 1 BLOCKED (strategy constructor outside the isolation boundary;
  sanitizer credential/path leak; vacuous card test). Round 2 BLOCKED (PARTIAL decided by
  failures, not the requested set). Round 3 BLOCKED (duplicate requested pairs printed
  "1 of 2"). Round 4 BLOCKED (zero-completion guard used all completed runs, so an
  unrequested run could be published). Round 5 BLOCKED (bare `Bearer` and backslash-relative
  paths still leaked; zero-completion branch did not name missing pairs). Round 6 PASS after
  an independent adversarial sanitizer battery (no leaks) and the full exit matrix.
Named regression and one-rule mutation/restore evidence: (A1) zero-completion `Exit(1)`
  replaced by `return` -> 4 failure-policy tests fail; (A2) partial `Exit(1)` removed -> 6
  tests fail including test_one_success_one_failure_is_partial_and_exits_one; (B) failure
  recording removed in run_arena -> 7 tests fail including
  test_run_arena_isolates_a_real_engine_exception while success-path tests still pass. All
  restored from /tmp/f04-{cli,arena}-dirty.py.bak with diff -q identical; logs
  /tmp/f04-mutation-{a1,a2,b}.log.
Focused tests / final gates / exact counts / retained log locations: 55 passed in
  tests/unit/test_arena_failure_policy.py; 238 passed in the test_arena + test_cost_card +
  test_cli_unified trio; logs /tmp/f04-worker6*.xml, /tmp/f04-review6*.log. Full serial and
  parallel suites, ruff/mypy, catalog regeneration and Markdown lint run at the frozen
  tranche boundary (see below).
Docs, schema/catalog, privacy, compatibility and migration status: arena/findings guide and
  CLI reference document the exit table, evaluation vocabulary, sanitizer rules and the
  requested-only publication invariant; positional construction of ArenaRun/ArenaOutcome and
  the public run_arena signature unchanged; card scope gains the four count keys as mandatory
  disclosures; no lake/model write, no artifact deletion, no catalog/schema change.
Residuals, explicit stop condition, next eligible stage: over-redaction tradeoff (a bare
  `Basic <word>` sequence loses the following token; path tokens lose adjacent punctuation) is
  documented and accepted; the `strategies: ... | regimes: ...` line still lists the whole
  registry (pre-existing). Tranche stops after F04; E00/P00/D00 each need their own gate.
Owner acceptance and authorized coordinator commit (if any): pending owner acceptance of the
  frozen tranche; one coordinator commit after the gates, per commit plan (b).
```

```text
F01-F04 frozen tranche gates / run 2026-11-02T16:20Z (serial then parallel, sequential):
uv sync --group dev --group backtesting            -> exit 0 (/tmp/gate-sync.log)
uv run pytest --junitxml=/tmp/portfolio-research-serial.xml   -> 1876 passed, 0 failed,
  0 errors, 17 skipped, 366.2 s (/tmp/portfolio-research-serial.log)
uv run pytest -n auto --junitxml=/tmp/portfolio-research-parallel.xml -> 1876 passed, 0 failed,
  0 errors, 17 skipped, 788.6 s (/tmp/portfolio-research-parallel.log)
uv run ruff check .                                -> exit 0, all checks passed
uv run ruff format --check .                       -> exit 0, 323 files already formatted
uv run mypy                                        -> exit 0, no issues in 174 source files
uv run equity catalog-generate                     -> exit 0, diff vs pre-run copy: unchanged
   (no pipeline/feature contract change, catalog not regenerated in the commit)
make lint-md                                       -> exit 0
git diff --check                                   -> exit 0
Tranche diff identity before commit: sha256 e91a83ebf2b5a59a3b58b291695903bb7f09a970e5bf3c7c30297b02fdeaeb5b.
The 17 skips are the pre-existing optional/network-marked selections, unchanged in count from
  the historical baseline; no skipped path is claimed as verified.
```

### Next-gate decisions recorded (2026-11-02, after the F01-F04 tranche)

```text
Owner decisions / 2026-11-02T17:10Z:
P00: proceed with the drafted recommendations for the accounting, persistence, identity,
  valuation, split, correction and privacy policies (ADR-0024, status Proposed).
  P00-1 remains OPEN: the single account's market and base currency. The draft records
  `us_equity` / USD as a PROPOSED selection for owner confirmation; P01 implementation stays
  blocked until it is confirmed.
E00: proceed with the drafted recommendations for layout, run ID, latest-attempt vs
  latest-success, compatibility, snapshot hashing, provenance vocabulary, read surfaces and
  no-migration policy (ADR-0025, status Proposed).
D00-1: the offline feasibility packet covers BOTH `us_equity` (yfinance) and `cn_ashare`
  (existing akshare/efinance integration), evidence only, no source calls.
D00-2: spend authorization is GenAI/LLM calls YES, market-data purchase NO. The packet must
  therefore treat licensed/paid data as out of scope and state what free-source evidence can
  and cannot certify; enrichment-side generation stays separately gated.
D00-3: confirmed - target root/isolation and any ingestion change stay out of scope until D01.
Next free ADR number checked against the index: 0024 (0023 is the latest accepted record).
Neither ADR number is reserved in advance; whichever record is accepted first takes it.
```

### Next-gate drafting outcome (2026-11-03)

```text
Records drafted (all uncommitted at the time of writing, all status Proposed):
docs/decisions/0024-private-portfolio-ledger-and-accounting.md  (P00, 3 open items)
docs/decisions/0025-immutable-research-evidence-and-run-manifests.md  (E00, 5 open items)
docs/plans/20261103-01-d00-offline-source-feasibility.md  (D00 evidence packet)
Index rows and docs/README links added; make lint-md exit 0.

Independent reviews (read-only, separate contexts):
- ADR fidelity round: initially NEEDS-REVISION on both. ADR-0024 had described the decided
  split-block policy as open, and carried unapproved normative backup/compaction/SQLite
  mechanics; ADR-0025 had made the export allowlist mechanism binding. Corrections: split
  wording distinguishes decided policy from a future amendment; SQLite mechanics demoted to a
  non-normative note; backup/restore moved to a new open item P00-4; the W&B allowlist
  mechanism demoted with a new open item E01-5. Final bounded round: ADR-0024 and ADR-0025
  both READY-FOR-OWNER-ACCEPTANCE, no unapproved normative decision left. The claimed
  "numbering conflict" was rejected: assigning 0024/0025 while both are Proposed is tentative,
  and the coordinator reconciles numbers if acceptance order differs.
- D00 citation round: initially NEEDS-CORRECTION with 11 findings, all of the
  overstatement kind (a synthetic demo proof generator exists; expiry *is* validated when a
  proof exists; CN `adj_close == close` is adapter-specific because the yfinance fallback can
  keep a distinct adjusted close; akshare concurrency is capped at ten workers; "free provable
  slice" and "needs a licensed feed" were unsupported; Gold refuses feature rows rather than
  every write; identity accepts provider MIC first; configured universes cannot establish
  survivorship; ADR-0011 is an assertion, not verified vendor behaviour; label finalization
  warns and then rewrites stored labels). Every finding was applied; the final bounded round
  returned D00 PACKET: ACCURATE with the finding that finalization is warn-then-rewrite
  rather than warn-only. Both BLOCKED-PENDING-EVIDENCE verdicts survived unchanged.

Still owed by the owner (P01/E01/D01 remain blocked until answered):
1. Accept or amend ADR-0024 and ADR-0025 (status is currently Proposed; acceptance is a
   prerequisite for P01/E01 implementation).
2. Confirm P00-1: the single account's market and base currency (`us_equity`/USD is only the
   proposed selection). P01 implementation is blocked on it.
3. Choose the D01 direction: accept the `us_equity` raw-price, explicitly-unadjusted slice as
   the candidate (subject to a licensing/retention check), or defer D01 while P00/E01 proceed.
```

```text
Owner acceptance / 2026-11-03:
1. ADR-0024 and ADR-0025 accepted as written (status flipped to Accepted; both index rows and
   the documentation map updated). ADR-0024's P00-1 is closed by the same acceptance:
   the single account is `us_equity` with base currency USD.
2. D01 direction approved: the `us_equity` raw-price, explicitly-unadjusted vertical slice is
   the D01 candidate, subject to the licensing/retention check named in the packet. This does
   not authorize ingestion, a target-root change or any source call; D01 still needs its own
   bounded plan and any required source/schema/storage ADR.
3. Still open and therefore NOT implemented: P00-2 (future split treatment), P00-3 (CLI surface
   freeze), P00-4 (migration/backup), E01-1..E01-5 (run-ID hash inputs, reader/legacy
   projection, W&B scope, fixture snapshot hashing, export allowlist).
Entry conditions now satisfied: P01 ("Approved ADR; fixtures only") is eligible for its core
   ledger/import scope; the P01 CLI sub-app waits on P00-3. E01 stays blocked until its five
   open items are frozen into an approved implementation plan.
```

```text
P01a / done / 2026-11-03T19:05Z (private ledger core: models, store, canonical CSV import, dry-run):
Owner-approved scope and policy reference: ADR-0024 (Accepted) decisions 1, 3-7, 9, 11, 12 and
  spec section 5.2; P00-1 closed as `us_equity` / USD at acceptance. Fixtures only.
Dependency and accepted-ADR evidence: ADR-0024 accepted (f726140); new top-level package work
  performed per the change matrix (LAYER_BOUNDARIES + architecture/project-structure docs).
Stage-start snapshot and reviewed final diff: f726140 clean tree; final diff = new
  src/equity_lake/portfolio/{__init__,models,store,csv_import}.py, core/paths.py (two additive
  constants), tests/unit/test_portfolio_{ledger,import}.py (new), test_import_boundaries.py
  (one LAYER_BOUNDARIES entry), and one entry each in the canonical architecture page and the
  project-structure guide.
Worker invocation/checkpoint: worker rounds 1-3 (ignored 20261103-p01a-worker-{1,2,3}.md).
  Round 1 built the package, store and import. Round 2 fixed the Decimal digest collision
  (`normalize()` context rounding), made unchanged stored events no-ops at validation time
  (re-imported SELL was falsely an oversell) and removed holdings from error text; round 3
  applied one redaction rule across models/csv_import/store (raw timestamp echo and Pydantic
  `input_value` could still leak private values).
Independent reviewer, verdict and issue resolution: reviewer read-only rounds 1-3 (the first two
  returned findings; round 3 returned the formal verdict). Round 1 BLOCKED (3 majors + 3 test
  warnings); round 2 confirmed digest/timeline fixes and flagged the remaining timestamp echo;
  round 3 PASS after an independent adversarial redaction probe (10 inputs, none leaked) and
  re-verification of digest injectivity, re-import no-op vs conflict vs genuine oversell.
Named regression and one-rule mutation/restore evidence: restoring context-rounded
  `Decimal.normalize()` canonicalisation fails
  test_content_digest_never_rounds_distinct_long_decimals_together and the absurd-magnitude
  bounds tests; models.py restored from /tmp/p01a-models-dirty.py.bak with diff -q identical and
  the 117-test pair green again (/tmp/p01a-mutation.log, /tmp/p01a-restored.log).
Focused tests / final gates / exact counts / retained log locations: 144 focused (117 portfolio +
  27 boundaries). Full gates: serial 1994 passed / 0 failed / 17 skipped (250.6 s), parallel
  1994 / 0 / 17 (355.4 s, sequential), ruff check/format clean, mypy clean (178 files), catalog
  regeneration unchanged, make lint-md and git diff --check clean. Logs: /tmp/p01a-{serial,
  parallel}.{log,xml}, /tmp/p01a-{mypy,ruff,format,catalog,lintmd}.log.
Docs, schema/catalog, privacy, compatibility and migration status: architecture and
  project-structure entries added; no catalog/schema/storage/CLI/Settings/.env change; no lake
  or real-account write; data/portfolio/ does not exist in the tree; the package imports only
  core/ and is forbidden from cli/dashboard/sources/ml/signals by the boundary test.
Residuals, explicit stop condition, next eligible stage: P01b (CLI sub-app, help-scan, user
  guide) stays blocked on the open P00-3 CLI-surface freeze; P02 projections, marks, reconciliation,
  split accounting and P00-4 backup/migration remain unimplemented by design. Real account import
  still needs separate explicit owner authorization.
Owner acceptance and authorized coordinator commit (if any): coordinator commit after the full
  gates, per commit plan (b).
```

```text
Owner approvals / 2026-11-03 (post-P01a):
P00-3 closed - CLI surface frozen as `equity portfolio import|positions|summary|reconcile` with
  **canonical CSV only**: no broker-export mapping and no plugin surface in P01/P02. `import`
  (with dry-run) lands in P01b; `positions`, `summary` and `reconcile` land with the P02
  projections they read, so the names are frozen but must not be stubbed early.
E01-1..E01-5 frozen as proposed (now recorded in ADR-0025): run ID hashes scope only with code
  identity kept in the manifest; legacy slugs resolve to the latest successful run and legacy
  cards keep resolving inside their own directory; W&B stays out of E01 scope; fixture snapshots
  are hashed by file content at the read seam; any future export allowlist is enforced at one
  export seam and E01 adds no export surface.
Remaining open after this: P00-2 (future split treatment) and P00-4 (migration/backup) only.
```

```text
P01b / done / 2026-11-03T21:20Z (`equity portfolio import` wired onto the P01a core):
Owner-approved scope and policy reference: ADR-0024 Decision 13 as frozen by P00-3 (four command
  names, canonical CSV only, no broker mapping) plus Decision 11 privacy and spec section 5.2.
Dependency and accepted-ADR evidence: P01a committed (0f6d771); P00-3 closed by owner 2026-11-03.
Stage-start snapshot and reviewed final diff: 9692ec9 clean tree; changed paths =
  cli/commands/portfolio.py (new), cli/_app.py, cli/__main__.py, tests/unit/test_portfolio_cli.py
  (new), tests/unit/test_cli_unified.py, docs/user-guide/20261103-private-portfolio-ledger.md
  (new), docs/README.md, mkdocs.yml (one nav line). src/equity_lake/portfolio/** untouched.
Worker invocation/checkpoint: worker rounds 1-2 (ignored 20261103-p01b-worker-{1,2}.md).
  Round 2 added the MkDocs nav entry and the privacy/log/isolation regressions the reviewer
  found missing.
Independent reviewer, verdict and issue resolution: reviewer read-only rounds 1-2. Round 1
  BLOCKED on the missing MkDocs nav entry plus two minor coverage gaps; round 2 PASS after the
  nav line (exactly one insertion) and five new tests, including a load-bearing control proving
  the `ensure_dirs()` skip rather than an unwritable environment.
Named regression and one-rule mutation/restore evidence: the round-1/round-2 review probes serve
  as the falsification (frozen registry inspected live; absent commands invoked and refused;
  dry-run and rejection paths exercised against tmp ledgers). No code mutation was run for this
  stage because the load-bearing logic is the P01a ledger core, already mutation-proved; the CLI
  is a thin seam whose tests assert registry, exit codes, echoes and log fields.
Focused tests / final gates / exact counts / retained log locations: 190 focused (26 CLI + 164
  unified CLI scans) plus 281 in the regression trio; full gates re-run on the final tree (serial
  and parallel suites, ruff check/format, mypy, catalog regeneration, Markdown lint,
  git diff --check) with logs under /tmp/p01b-*. Captured full-suite results: serial 2023
  passed / 17 skipped (354.4 s); parallel 2023 passed / 17 skipped (720.3 s), zero
  failures/errors (2040 total JUnit cases, including the skips).
Docs, schema/catalog, privacy, compatibility and migration status: user guide added with the 18
  canonical columns, seven event types, rejections, dry-run guarantees and an explicit
  not-implemented list; MkDocs nav and docs map updated; no schema/catalog/storage change; only
  `import` is registered, so positions/summary/reconcile stay unimplemented until P02.
Residuals, explicit stop condition, next eligible stage: the frozen surface has no `--verbose`,
  so the value-free append log line can appear on stdout during a commit; `make lint-md` does not
  validate `mkdocs.yml` (YAML was checked separately). Next: E01a (dated evidence + manifests +
  immutable run finalisation) under the frozen E01-1..E01-5.
Owner acceptance and authorized coordinator commit (if any): coordinator commit after the full
  gates, per commit plan (b).
```

```text
E01a / done / 2026-11-04 (dated evidence, run manifests, immutable run finalisation - write side):
Owner-approved scope and policy reference: ADR-0025 (Accepted) decisions 1-6 and 9 with the
  frozen E01-1 and E01-4; spec section 4. E01b (latest projection, legacy readers, W&B) excluded.
Dependency and accepted-ADR evidence: aeabe4b clean tree (ADR-0025 accepted at f726140, E01-1..5
  frozen at 9692ec9).
Stage-start snapshot and reviewed final diff: changed paths = backtesting/evidence.py (new),
  result.py (additive `equity_dates`), engine.py (backend date extraction), report.py (dated
  writer + arena caller + strict JSON), arena.py (recorded applied costs on ArenaRun),
  tests/unit/test_evidence_runs.py (new, 42 tests), ARCHITECTURE.md and the arena/findings guide.
Worker invocation/checkpoint: worker rounds 1-6 (ignored 20261104-e01a-worker-{1..6}.md).
  Round 1 built dated evidence + manifest + finaliser. Round 2 wired the one production caller
  (the correction: without a caller the ADR acceptance was unprovable). Round 3 added applied
  costs to the run-ID preimage and value comparison. Round 4 recorded the actually-applied costs
  on ArenaRun (the writer had been guessing defaults) and made curve refusal coherent for
  non-finite values. Round 5 stopped NaN metrics being persisted. Round 6 applied the same rule
  to dated trades/cards (refuse non-finite; null only for the engine's open-position sentinel).
Independent reviewer, verdict and issue resolution: reviewer rounds 1-5. Round 1 BLOCKED (cost
  omitted from run ID; values not compared; publish race; validator/CLI coverage gaps). Round 2
  BLOCKED (manifest recorded default costs, not applied ones; NaN curve inconsistent). Round 3
  BLOCKED (NaN metric persisted). Round 4 BLOCKED (NaN trade/card persisted). Round 5 PASS with
  the open-position exemption judged safe and narrow (other non-finite fields, including fee and
  quantity, still refuse).
Named regression and one-rule mutation/restore evidence: removing the applied costs from
  RunScope.scope_payload fails test_run_id_covers_the_applied_costs_and_ignores_their_order,
  test_run_id_ignores_code_identity_and_changes_with_scope and
  test_manifest_reverifies_the_run_id_against_the_applied_costs; evidence.py restored from
  /tmp/e01a-evidence-dirty.py.bak with diff -q identical and 42 tests green again
  (/tmp/e01a-mutation.log, /tmp/e01a-restored.log). Note: SCOPE_FIELD_ORDER is documentation-only
  (the payload is built explicitly), so an earlier mutation against that tuple was a no-op and
  was discarded rather than reported as proof.
Focused tests / final gates / exact counts / retained log locations: focused 42 evidence tests +
  143 arena/backtesting regressions. Full gates: serial 2065 passed / 0 failed / 17 skipped
  (421.3 s), parallel 2065 / 0 / 17 (723.2 s, sequential), ruff check/format clean, mypy clean
  (180 files), catalog regeneration unchanged, make lint-md and git diff --check clean. Logs:
  /tmp/e01a-{serial,parallel}.{log,xml}, /tmp/e01a-{mypy,ruff,format,catalog,lintmd}.log.
Docs, schema/catalog, privacy, compatibility and migration status: architecture page and arena
  guide document the layout, scope-only ID (incl. applied costs), refusal gates, atomic claim and
  the undated/unrecorded-cost skips; legacy `<strategy>__<regime>/` shapes unchanged; the Series
  API is unchanged; RUN_PROTOCOL bumped to equity-run-manifest/2 (no run directories exist in the
  repo to migrate); no schema/catalog/storage change; tests write only to tmp roots.
Residuals, explicit stop condition, next eligible stage: legacy write path still emits the
  engine's NaN open-position sentinel and uses permissive JSON (E01b owns it); `findings/writer.py`
  serialization ownership is E01b; SCOPE_FIELD_ORDER is not yet derived by construction from the
  payload (drift hazard for E01b); `equity report backtest` remains legacy-only. Stage stops
  here; E01b is the next eligible E01 subtask.
Owner acceptance and authorized coordinator commit (if any): coordinator commit after the full
  gates, per commit plan (b).
```

### Handoff reconciliation — 2026-11-06

Current code baseline `603fc8d`; no product edits in this refresh. The current
queue and [Nov06 handoff](../handoffs/20261106-01-research-readers-and-portfolio-pickup.md)
replace the spent B00/F01 starter. E01b remains pending; E01 as a whole is not
complete. Owner decisions in the dated ledger entries remain historical records;
the current queue and accepted ADRs govern continuation.

Count correction from existing `/tmp/{portfolio-research,p01a,p01b,e01a}-`
`{serial,parallel}.xml` and E01a pytest logs: earlier summaries called total
JUnit cases passed, including 17 skips. Correct passed counts are 1876
(F01–F04), 1994 (P01a), 2023 (P01b), 2065 (E01a), each with 17 skipped and zero
failures/errors in both modes. Corresponding records above are corrected; git
history is not rewritten. No Python suite rerun is claimed for this docs update.
Earlier ADR-0023 counts are retained as historical, not re-audited here.

Handoff-only verification: `make lint-md` and `git diff --check` passed; 71
relative links/anchors across the four changed documents resolved. A separate
read-only reviewer checked queue, authorization, source-observed E01 limitations
and corrected counts; its one finding (the stale §10 draft-status paragraph)
was corrected. No product/ADR edits, Python suite run or catalog regeneration.
Documentation remains uncommitted for pickup; preserve these four paths.

Draft validation on 2026-11-02: `make lint-md` and `git diff --check` passed;
relative links in the three new documents resolved; the embedded Python
counterexample parsed; Decimal arithmetic confirmed the proposed accounting
hand oracle. No product tests were rerun merely for drafting. Independent
implementation review and fresh implementation gates remain future work.

```text
E01b.1 / done / 2026-11-06 (attempt records, latest-success readers, arena legacy-write removal):
Owner-approved scope and policy reference: ADR-0025 (Accepted) decisions 2-5 and 7-9 with frozen
  E01-1..E01-5; spec section 4; handoff 20261106-01. Owner decisions of 2026-11-06: the legacy
  freeze is confirmed and extended — the arena path writes **no** legacy path at all instead of
  freezing a path mid-use; `/backtests` rows keep their keys and gain additive
  `last_success`/`last_attempt`; `/findings` resolves an id to the newest attempt-bound card and
  falls back to the legacy card; one attempt directory owns the comparison cards and the benchmark
  copy; E01 stays partial after this stage.
Dependency and accepted-ADR evidence: 603fc8d (E01a) plus the dirty docs-only handoff refresh,
  preserved untouched by every worker round.
Stage-start snapshot and reviewed final diff: source = backtesting/attempts.py (new),
  backtesting/evidence_view.py (new), backtesting/evidence.py (shared publish/card/digest helpers
  factored out; scope payload order derived from SCOPE_FIELD_ORDER), report.py (no legacy writes;
  attempt publication including a durable `write_failed`; bound card refs; config digest; strategy
  params from the instance that ran), arena.py (additive `ArenaRun.strategy_params`,
  `ArenaOutcome.markets`/`market_costs`), findings/writer.py (opt-in strict card write), api/deps.py
  (projections), cli/commands/arena.py (previous-evidence block, zero-completion attempt, attempt
  path output); tests = new test_evidence_attempts.py (34) and test_evidence_readers.py (15), plus
  updated test_evidence_runs/test_arena/test_arena_failure_policy/test_api; docs = arena/findings
  guide, read-api guide, ARCHITECTURE canonical page, notebooks/11-strategy-lab.ipynb reader import.
Worker invocation/checkpoint: worker rounds 1-4 (private /tmp/e01b-worker-checkpoint.md; sanitized
  summary in ignored .agent-progress/portfolio-research/). Round 2 fixed the three round-1 review
  findings (`write_failed` must record its failure evidence; card refs bound to run IDs; notebook
  read path). Round 3 fixed three warnings (explicit slug candidate set instead of shape matching;
  the writer returns the cards it persisted; the token vocabulary documented as display-only).
  Round 4 fixed a benchmark ref that could name an artifact the attempt never wrote.
Independent reviewer, verdict and issue resolution: reviewer rounds 1-3 returned NEEDS-REVISION
  (round 1: two majors plus a notebook consumer regression; round 2: three warnings; round 3: one
  warning) and every finding was fixed in the next worker round. Round 4's delta review reported no
  functional regression and one doc mismatch; the coordinator corrected the ARCHITECTURE token
  summary and the final verdict round returned PASS. Round 4's first response omitted the required
  verdict line and was re-requested with read-only verification allowed; the ledger records the
  PASS that was actually returned.
Named regression and one-rule mutation/restore evidence: (a) accepting non-finalized verdicts in
  `validated_runs` fails test_latest_success_ignores_a_damaged_newer_run[missing-manifest]
  (/tmp/e01b-mutation.log); (b) deleting the `write_failed` failure-evidence rule fails
  test_status_consistency_complete_partial_zero_completion_write_failed (/tmp/e01b-mutation2.log).
  Both files restored from /tmp/e01b-{evidence_view,attempts}-dirty.py.bak with `diff -q` identical
  and the 49 attempts+readers tests green again (/tmp/e01b-restored.log).
Focused tests / final gates / exact counts / retained log locations: focused 298 tests (296 passed,
  0 failed, 2 skipped: lightgbm absent). Full gates: serial 2119 passed / 0 failed / 17 skipped / 97
  deselected (467.5 s), parallel 2119 / 0 / 17 (476.1 s, run sequentially), ruff check/format clean,
  mypy clean (182 files), catalog regeneration byte-identical, `make lint-md` and `git diff --check`
  clean. Logs: /tmp/e01b-{serial,parallel}.{log,xml}, /tmp/e01b-{ruff,format,mypy,catalog,lintmd,
  lintmd2,diffcheck}.log. The 2065 → 2119 passed delta is +54 tests (34 attempts + 15 readers + 3
  runs + 2 api), consistent with the new modules rather than unexplained drift.
Docs, schema/catalog, privacy, compatibility and migration status: both guides document the
  runs+attempts layout, the absence of legacy writes, the frozen legacy view, last-attempt vs
  last-success, the display-only `run:`/`attempt:`/`unevidenced:` vocabulary and the
  `equity report backtest` legacy caveat; the canonical ARCHITECTURE page is updated. No
  catalog/schema/storage/Settings/.env change; no lake, private-account or runtime `data/` write
  (catalog regeneration byte-identical; all tests use tmp roots). `/findings` and `/backtests` keep
  their response shapes; the latter gains `last_success`/`last_attempt` additively.
Residuals, explicit stop condition, next eligible stage: E01b.2 (`equity report backtest` is the
  last legacy writer and needs the applied costs recorded from the built engine) and E01c (canonical
  `(market, MIC, symbol)` universe identity, read-seam input snapshot hashing, provenance upgrade)
  remain open, so E01 is partial and provenance stays `unknown`. W&B is untouched. Advisory checks
  on the changed paths are false positives: `int()` of a `timedelta` total and pre-existing literal
  parsing, plus anchored hex patterns on module constants rather than user input. Stage stops here.
Owner acceptance and authorized coordinator commit (if any): none. The stage and the 2026-11-06
  documentation refresh remain uncommitted for owner review.
```

```text
E01b.2 / done / 2026-11-06 (`equity report backtest` migrated off the legacy layout):
Owner-approved scope and policy reference: ADR-0025 decisions 2-5 and 7-9 with frozen
  E01-1..E01-5; owner instruction "proceed with the open items still"; contract
  /tmp/e01b2-contract.md. This removes the last production legacy writer.
Dependency and accepted-ADR evidence: uncommitted E01b.1 stage; no run or attempt directories existed
  before either stage, so there is no migration surface.
Stage-start snapshot and reviewed final diff: cli/commands/arena.py (`report_backtest` rewrites one
  run + one attempt under the findings root; undated results refused with exit 1 and nothing
  written), backtesting/arena.py (`_evaluation_status` made public, `_universe_size` moved out),
  backtesting/engine.py (shared `universe_size`; `loaded_universe_size` recorded when the engine
  itself loaded data), backtesting/report.py (docstring only: `write_backtest_report` has no
  production caller), tests/unit/test_report_backtest.py (new, 15 tests) and one tightened
  help-scan pin; docs = arena/findings guide (`report backtest` section and legacy table) and one
  ARCHITECTURE clause.
Worker invocation/checkpoint: worker round 1 (/tmp/e01b2-worker-checkpoint.md); the worker ran its
  own falsification checks and restored the sources before the final pass.
Independent reviewer, verdict and issue resolution: separate read-only reviewer, verdict PASS with
  no findings; it independently confirmed the observed-cost path, the undated/raising empty-root
  behaviour, the shared classifier and disclosure note, the no-legacy-writer claim, the help pins,
  and that E01c/P02/D01/W&B were untouched. (Its focused count of 432 differs from the worker's
  441 collection tally; the coordinator's full-suite counts below are authoritative.)
Named regression and one-rule mutation/restore evidence: replacing the recorded applied costs with a
  constant (`fee_ratio=0.0`/`tax_ratio=0.0`) fails
  test_report_backtest_records_the_applied_fee_and_tax_from_the_engine[high-0.005-0.003]
  (/tmp/e01b2-mutation.log); cli/commands/arena.py restored from
  /tmp/e01b2-arena-cli-dirty.py.bak with `diff -q` identical and the 15 focused tests green again
  (/tmp/e01b2-restored.log).
Focused tests / final gates / exact counts / retained log locations: focused 441 tests (0 failed, 0
  skipped). Full gates: serial 2134 passed / 0 failed / 17 skipped / 97 deselected (501.8 s),
  parallel 2134 / 0 / 17 (671.3 s, run sequentially), ruff check/format clean, mypy clean (182
  files), catalog regeneration byte-identical, `make lint-md` and `git diff --check` clean. Logs:
  /tmp/e01b2-{serial,parallel}.{log,xml}, /tmp/e01b2-{ruff,format,mypy,catalog,lintmd,diffcheck}.log.
  Delta vs E01b.1 is +15 passed, exactly the new report-backtest tests.
Docs, schema/catalog, privacy, compatibility and migration status: the guide now states that
  `report backtest` publishes run + attempt evidence, that `--output-dir` is the findings root, that
  undated results are refused, that a single report applies the static `COST_REGIMES` pair rather
  than the arena's per-market `realistic` defaults (the manifest records the pair actually applied),
  and that `write_backtest_report` has no production caller. No catalog/schema/storage/Settings
  change; no `data/` write (catalog regeneration byte-identical; tests use tmp roots); readers and
  `/findings`+`/backtests` shapes unchanged.
Residuals, explicit stop condition, next eligible stage: E01c remains — canonical
  `(market, MIC, symbol)` universe identity in the run scope (a `RUN_PROTOCOL` bump, so no earlier
  run is comparable), read-seam input snapshot binding (Delta version at read time; content hashes
  for non-Delta roots), and provenance, which must **stay `unknown`**: no persisted price-source
  marker exists and the action-coverage `source` column is not price provenance, so an upgrade by
  inference would violate ADR-0021/0022. A persisted price-source marker is a new open item (E01-6)
  needing its own storage/ADR decision. Advisory diagnostics on the touched path are the
  contract-mandated engine cost reads, refused downstream by `CostApplication`.
Owner acceptance and authorized coordinator commit (if any): none; the stage remains uncommitted.
```

```text
E01c / done / 2026-11-06 (canonical universe identity and read-seam snapshot binding):
Owner-approved scope and policy reference: ADR-0025 Decisions 3-4 and 9 with frozen E01-1/E01-4,
  plus ADR-0016 canonical identity and ADR-0021/0022 (no inference). Owner approved 2026-11-06:
  (a) RUN_PROTOCOL v3 (IDs change by design; nothing to migrate), (b) provenance stays `unknown`
  with the persisted price-source marker recorded as open item E01-6, (c) the additive
  `InputSnapshot.version` field and one aggregate content digest per non-Delta read root.
Dependency and accepted-ADR evidence: uncommitted E01b.1/E01b.2 stage; no run or attempt directory
  exists locally or in the repo, so no evidence is re-read under the new scope.
Stage-start snapshot and reviewed final diff: backtesting/{evidence,report,arena,data_loader,
  engine}.py, storage/delta.py, ingestion/corporate_actions.py, cli/commands/arena.py; tests =
  tests/unit/test_evidence_snapshots.py (new, 17), +2 in test_report_backtest.py, pins in
  test_evidence_runs/test_evidence_readers; docs = arena/findings guide, backtesting guide and the
  canonical ARCHITECTURE page. No new ADR: E01c implements accepted Decisions 3-4 and changes no
  boundary (additive reader functions only).
Worker invocation/checkpoint: worker rounds 1-3 (/tmp/e01c-worker-checkpoint.md). Round 2 fixed the
  reviewer's two warnings; round 3 fixed the regression the full gate found.
Independent reviewer, verdict and issue resolution: reviewer round 1 NEEDS-REVISION (loader-reuse
  could publish a stale snapshot version/digest; a new test reintroduced the `/2` literal);
  round 2 fixed both (per-load snapshot and action-cache reset with reuse tests; the old-protocol
  assertion is derived from `RUN_PROTOCOL`). Round 3 delta review PASS. The coordinator's full
  serial gate then caught a regression the focused set had missed: `read_delta` began demanding a
  `dt.version()` capability, breaking the legitimate versionless `_RecordingDeltaTable` double in
  tests/unit/test_feature_jobs.py. Worker round 3 made version capture explicitly opt-in
  (`capture_version`; the blind reader never calls `version()` and `read_delta_with_version`
  refuses a versionless table with `DeltaReadError`) and added two named tests; the final delta
  review returned PASS with no findings.
Named regression and one-rule mutation/restore evidence: (a) ignoring an explicit Delta `version=`
pin fails test_delta_price_read_records_the_version_it_read; (b) replacing the per-file content
  hash in the aggregate parquet digest with the file size fails
  test_non_delta_price_read_digests_exactly_the_scanned_files. Both files restored with `diff -q`
  identical and the focused snapshot tests green again (/tmp/e01c-mutation{,2}.log,
  /tmp/e01c-restored.log).
Focused tests / final gates / exact counts / retained log locations: focused 526 tests (0 failed)
  after the widened set; the earlier 460-test set was too narrow, which is why the full gate caught
  the versionless-table regression. Full gates: serial 2155 passed / 0 failed / 17 skipped / 97
  deselected (377.3 s), parallel 2155 / 0 / 17 (411.1 s, run sequentially), ruff check/format clean,
  mypy clean (182 files), catalog regeneration byte-identical, make lint-md and git diff --check
  clean. Logs: /tmp/e01c-{serial2,parallel}.{log,xml}, /tmp/e01c-{ruff,format,mypy,catalog,lintmd,
  diffcheck}.log, /tmp/e01c-r3-*.log. Delta vs E01b.2 is +21 passed (17 snapshot + 2 protocol + 2
  versionless-table tests).
Docs, schema/catalog, privacy, compatibility and migration status: the guides and ARCHITECTURE page
  document protocol v3, `universe_basis` (canonical vs `ticker_only`, derived by parsing, never by
  guessing a MIC), the snapshot table (`price_marks:<market>` / `action_events:<market>` /
  `action_coverage:<market>`, `lake:delta` with the read-seam version and a `(path, version)`
  reference digest, `lake:parquet` with one aggregate content digest over the sorted scanned file
  set), the extra non-Delta read pass, and that provenance stays `unknown` until E01-6. No
  catalog/schema/storage/Settings change; no `data/` write (catalog regeneration byte-identical;
  tests use tmp roots); `/findings` and `/backtests` shapes unchanged; `read_delta` and
  `load_corporate_action_inputs` keep their signatures and behaviour.
Residuals, explicit stop condition, next eligible stage: provenance stays `unknown` and E01 is not
  complete until E01-6 lands (a persisted price-source marker; ADR + storage/schema decision +
  backfill answer). In-memory fixture action inputs have no read seam and record no snapshot; a
  non-Delta file-set change after enumeration is not detected; the Delta digest binds
  `(path, version)` rather than file contents; `preloaded_data` records no snapshots. `equity-run-
  manifest/2` remains only in historical handoff/ledger text describing commit `603fc8d` and the
  E01a stage, which is intentionally not rewritten. Advisory diagnostics on the touched paths are
  the known unchecked-numeric-parser pattern on validated inputs.
Owner acceptance and authorized coordinator commit (if any): none; E01b.1, E01b.2, E01c and the
  2026-11-06 documentation refresh remain uncommitted for owner review.
```

### P02 plan approval and two ADR drafts — 2026-11-07

The owner approved the bounded fixture-only P02 plan in the current pickup
("approve on the plan"), then explicitly requested both ADR drafts ("we should
draft both"). This records plan approval, not ADR acceptance or implementation
completion. Current deliverable: documentation only; stop for review of the drafts
before code. No worker/reviewer invocation or implementation acceptance is claimed.

- [Proposed ADR-0026](../decisions/0026-portfolio-projection-precision-marks-and-reconciliation.md)
  records the P02 accounting/precision/mark/reconciliation amendment to ADR-0024:
  one invented US/USD account; sequence-ordered effective-date replay; Decimal
  precision 256, partial-sale basis rounded half-even to 38 fractional USD digits,
  full-close residual conservation; unknown basis never zero; local stored raw
  fixture marks with a one-completed-venue-session default and no widening;
  inclusive quantity/cash tolerances of 0.00000001 share / 0.01 USD; the frozen
  `positions`, `summary`, `reconcile` commands with honest unavailable/exit states.
  SQLite schema v1 stays unchanged. Gross/withholding fields unsupported by v1
  remain unavailable. Exact input models and conservative refusal rules are draft
  details for owner review, not silently accepted changes to ADR-0024.
- [Proposed ADR-0027](../decisions/0027-row-bound-price-source-provenance.md)
  separately proposes E01-6 row-bound price-origin fields, producer evidence,
  snapshot-bound complete-scope aggregation, an evidence-protocol change and the
  explicit answer of no retrospective backfill/relabeling. Its storage/schema,
  encoding and compatibility choices are proposed only. E01-6 implementation is
  not authorized; research provenance remains `unknown` on shipped paths and
  E01 cannot yet be declared complete.

Stage-start verification: HEAD `603fc8d`, 24 modified + 8 untracked, nothing
staged. The handoff's §10 seven-untracked count is stale; its header's eight
matches actual status. Preserve B00/F01–F04/P01a/P01b/E01a/b/c; do not restart
completed work. Accepted ADRs remain unchanged. This entry is appended to the
carried ledger rather than rewriting historical stage evidence.

Next gate: owner review/acceptance of ADR-0026 before P02 code; independently,
ADR-0027 acceptance plus its own approved bounded implementation plan before
E01-6 code. Existing one-writer/separate adversarial reviewer, formal verdict,
external checkpoints, coordinator mutation/restore and serialized-test contracts
remain required for implementation. D01, P00-2 and P00-4 remain gated. No commit,
private-account/broker access, provider/LLM call, real ingestion, refit/training,
migration/backfill/reset/backup automation, deployment, W&B change or freshness
widening is authorized. Nothing under runtime `data/` is part of this draft.

### ADR-0026/0027 acceptance and P02 continuation — 2026-11-07

The owner explicitly accepted both ADRs ("accept both") and requested a pickup
for another agent. [ADR-0026](../decisions/0026-portfolio-projection-precision-marks-and-reconciliation.md)
and [ADR-0027](../decisions/0027-row-bound-price-source-provenance.md) are now
**Accepted**. This entry supersedes the preceding same-day draft-status/ADR
acceptance gates without rewriting that historical drafting record. Earlier
§2/§10 planning-only status is superseded by this continuation decision.

The bounded fixture-only P02 plan was already approved; its policy gate is now
satisfied. Next-agent implementation scope is P02 only: deterministic Decimal
average-cost replay and honest unknown-basis P&L; stored raw fixture marks and
exact-venue session staleness; statement reconciliation; and working
`equity portfolio positions|summary|reconcile`, with tests and required docs.
Present the minimal file plan before code; obtain owner approval of deviations.
No P02 product code or implementation acceptance is claimed in this update.

Use the [P02 implementation pickup](../handoffs/20261107-02-p02-fixture-only-implementation-pickup.md)
for work packages, intended paths, frozen checks, the worker/reviewer protocol,
final gates and a copyable prompt. It replaces the Nov07-01 planning-only starter
for continuation, while preserving that file's carried inventory and evidence.
Expect 25 modified + 11 untracked after this documentation update, with nothing
staged; verify actual git status and preserve the dirty baseline outside the repo.
B00/F01–F04/P01a/P01b/E01a/b/c are done and must not be restarted.

ADR-0027 acceptance does not authorize E01-6 implementation: freeze exact
source/summary encodings and IDs, golden fixtures, v3/v4 compatibility and its
own bounded file plan at a separate approved gate. Existing research provenance
stays `unknown`, historical artifacts stay unchanged and E01 remains incomplete.
D01, P00-2/P00-4, P03 and operational use remain gated. No commit, private account,
provider/LLM processing, real ingestion, refit, migration/backfill/reset, backup
automation, deployment, W&B change or freshness widening is authorized.

### P02 implementation outcome — 2026-11-07

**Stage / state / recorded UTC:** P02a, P02b and P02c implemented, reviewed offline
and **accepted by the owner on 2026-11-07**; **not staged, not committed** (acceptance
is not commit authorization).

**Owner-approved scope and policy reference:** the bounded fixture-only P02 plan
plus accepted [ADR-0026](../decisions/0026-portfolio-projection-precision-marks-and-reconciliation.md)
under [ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md)
and §5.3. One invented `us_equity`/USD account, SQLite schema v1 unchanged, no
E01-6, no P03, no D01.

**Stage-start snapshot:** HEAD `603fc8d`, 25 modified + 11 untracked, nothing
staged; diff/hashes/tarball preserved under `/tmp/p02-baseline/`. A content-hash
comparison at the end proves only the 11 P02 paths differ from that snapshot, so
the carried E01b.1/E01b.2/E01c work is byte-identical.

**Delivered:** `portfolio/projections.py` (sequence-ordered effective-date replay,
local `prec=256` context with exactness refusal, exact single-rounding half-even
38-digit partial-sale basis, full-close residual conservation, unknown-basis
stickiness, fees counted once, oversell/negative-cash/monetary refusals,
order/restart invariance); `portfolio/marks.py` (strict version-1 mark envelope,
exact-MIC scheduled-close validation including early closes and holidays,
latest-eligible selection with same-instant conflict blocking and no fallback,
completed-session age, complete/partial valuation with named blockers and no
marked-only denominator); `portfolio/reconcile.py` (canonical statement CSV,
duplicate/multi-cash/mixed-account/mixed-instant rejection, inclusive 1e-8-share
and 0.01-USD tolerances, signed deltas, unavailable-precedence, no mutation);
additive `portfolio/__init__.py` exports; one `core/calendar.py` helper
(`instrument_session_close`); and the frozen `positions`, `summary` and
`reconcile` commands on the existing sub-app with exit-0-only-for-complete,
value-free stderr and no write on any read path.

**Independent reviewer, verdict and resolution:** three separate adversarial
reviewers (one per dirty package, read-only, formal verdict line). P02a: two
`NEEDS-REVISION` rounds → `PASS`; P02b: one round → `PASS`; P02c: three rounds →
`PASS`. Every finding was a real defect, fixed and regression-tested:
aggregate realized gain and the `net_external_flow`/`total_fees` properties (and
the whole valuation layer) previously performed Decimal arithmetic outside the
explicit context; `embedded_fees` was summed from surviving positions so fees
vanished after liquidation; duplicate statement header columns were silently
reinterpreted and a header-only statement raised `KeyError`; the CLI wrote
blocker identities, user-supplied filenames and store metadata (`user_version`)
to stderr. Root fix: `exact_add`/`exact_sub`/`exact_mul` now operate *through* the
passed context (`context.add/subtract/multiply`) so flags and precision always
match. Error text on read paths is fixed text plus an exception class only.

**Named regressions and mutation/restore evidence:** hand oracle (basis 1000 →
1605 → 1177, cash 924, value 1375, NAV 2299, unrealized 198, total gain 299),
half-even halfway cases both directions, 38-digit repeating cost, full-close
conservation, unknown-basis across liquidation/reopening, hostile-precision
exactness for totals and valuation, weekend/holiday/early-close age, conflict with
an older valid mark present, split-block withholding, statement tolerance
equality/just-over, duplicate-header and header-only rejection, stderr privacy
(identities, filenames, ledger metadata), and read no-write snapshots. Three
coordinator mutations, each killing its named test and restored byte-identically
from `/tmp` backups (`diff -q`): half-even tie → half-up; inclusive tolerance →
exclusive; reconcile mismatch no longer exiting 1.

**Focused tests / final gates / exact counts / retained logs:**
`/tmp/p02-focused3.log` 553 passed (widened portfolio, ledger/import, unified CLI,
calendar dependants and import boundaries). Final gates, sequentially:
`/tmp/p02-serial.log` + `.xml` **2356 passed, 17 skipped, 97 deselected** (460.5 s);
`/tmp/p02-parallel.log` + `.xml` **2356 passed, 17 skipped** (696.1 s);
`ruff check` and `ruff format --check` clean; strict `mypy` clean (185 source
files); catalog regeneration to an isolated `/tmp` path byte-identical
(sha `0536211e…516873`) with `data/` untouched; `make lint-md` clean;
`git diff --check` clean; LSP probe over the six changed source files: 0
diagnostics. JUnit totals are not quoted as passes; the counts above are the
runner's pass/skip lines.

**Docs, schema/catalog, privacy, compatibility and migration status:** portfolio
user guide rewritten for import + read reports + mark JSON + staleness +
reconciliation + accounting rules; scoped portfolio section in the CLI reference;
additive `ARCHITECTURE.md` portfolio line; `docs/README.md` status block and the
private-portfolio index bullet. No lake schema, migration, catalog row, Settings
key, dependency or `cli/_app.py` change. Schema v1 and its append-only/idempotent
import contract are untouched, so existing ledgers remain readable. Privacy: read
paths create nothing, log no private value, and print identities only inside the
requested local report. Upstream research provenance remains `unknown`.

**Residuals, explicit stop condition, next eligible stage:** no operational mark
producer, split accounting, backup/migration (P00-2/P00-4), FX, split discovery or
P03 console. Known pre-existing P01 observation, deliberately untouched: the
canonical *import* CSV parser still accepts a duplicated header column name. E01-6
stays unimplemented and gated behind its own bounded plan.

**Owner acceptance:** **accepted 2026-11-07** (owner: "accept on the delivered
items"). Acceptance covers the delivered P02 implementation as reviewed and gated
above. It does **not** authorize staging, a commit, or progression to E01-6, P03 or
D01: the stage remains uncommitted, and any staging/commit needs a separate
explicit owner instruction naming the files. Remaining gates are unchanged.

### E01-6 — row-bound price origin (implementation record)

```text
E01-6 / implemented, three sequential commits / 2026-11-07:
Owner-approved scope and policy reference: accepted ADR-0027 plus the bounded E01-6 plan
  in docs/handoffs/20261107-03-e01-6-implementation-pickup.md, decisions D1-D8 frozen.
Dependency and accepted-ADR evidence: ADR-0024/0025/0026/0027 accepted; stage 1 read
  support, stage 2 write boundary, stage 3 read-seam aggregation — each landed before the
  one that depends on it (decision D4).
Stage 1 (reader-first): protocol equity-run-manifest/4 parsing, PriceOriginSummary and the
  §4.4-§4.5 derivation, v3 evidence parsed under its own rules and never retro-invalidated;
  writers still minted v3, so every shipped run still derived `unknown`.
Stage 2 (storage + write boundary): nullable envelope (Int16 version, three strings) on the
  Bronze price tables, stamped and validated at write_delta, merge_delta and upsert_dataset;
  the demo generator — and only it — asserts `generator:synthetic_demo_seed@1`; malformed,
  partial, contradictory and unsupported claims are refused; a merge replaces the marker
  with the values instead of inheriting it; legacy rows stay null (D6); catalog regenerated.
Stage 3 (read seam + writers): the loader classifies the rows it consumed, before the column
  projection, from the same pinned Delta version it read; arena/report/`report backtest` pass
  those summaries and the writer derives `data_provenance` from them. New runs are v4.
Independent reviewer: separate adversarial reviewer per stage, formal verdict each time.
  Stage 1: NEEDS-REVISION (v3 retro-invalidation), then NEEDS-REVISION (name-only coverage
  and unsupported-protocol handling), then PASS. Stage 2: NEEDS-REVISION (dtype contract,
  datetime narrowing, retry/evolution coverage), then PASS with one test-flakiness fix.
  Stage 3: see the handoff-backed stage report in the commit.
Named regressions and mutation evidence: each rule is pinned by a test that fails when the
  rule is removed — verified with per-rule mutations restored from /tmp copies and compared
  with diff -q (unknown-dominance, counts coverage, claim refusal, v3 byte shape, snapshot
  binding, exact-reference coverage, protocol handling, empty_scope, merge replacement,
  source grammar, decimal rendering, line ordering, generator revision, column stamping,
  classification-before-projection, derivation-instead-of-literal).
Focused tests / final gates: recorded per stage; the stage-3 boundary runs the full serial
  and parallel suites plus ruff, strict mypy, catalog regeneration and markdown/whitespace
  checks, with logs retained under /tmp.
Docs, schema/catalog, privacy, compatibility: backtesting guide, ARCHITECTURE, this ledger
  and docs/README updated; catalog regenerated from STANDARD_COLUMNS (never hand-edited);
  no Settings key, dependency or ADR change; no private value, credential, path or hostname
  enters evidence; v3 runs stay readable and byte-identical.
Residuals, explicit stop condition: claims are self-asserted local lineage — not vendor
  authentication, accuracy, licensing, retention permission, adjustment correctness or PIT
  availability. No real-provider claim exists while D01 (licensing, retention, target root,
  spend) stays closed; `data_provenance` covers price origin only, never action, feature or
  prediction inputs. P03, D01, P00-2/P00-4, R00-R02 and H00 remain gated.
```

### P03 — private console (implementation record)

```text
P03 / implemented, four sequential stages / 2026-11-07:
Owner-approved scope and policy reference: spec §5.4, ADR-0018 §8-9, and the approved
  bounded plan docs/plans/20261107-04-p03-private-console-plan.md (owner answers recorded
  in §7: explicit UTC instant, one configured private path, one new module, reconciliation
  rendered, labels confirmed). Stages 2-4 were approved by the owner after stage 1.
Stage 1: `dashboard/panels.py` + four gated `PageSpec`s + one reusable fail-closed
  `require_loopback()`; Portfolio panel (as-of NAV or explicit unavailable, cash,
  positions with basis status/mark age/synthetic|unknown provenance, allocation only when
  complete, reconciliation differences, held/watch-only distinct from ledger positions);
  `Settings.console.private_dir` bound to `.env.example`.
Stage 2: Data Readiness panel over the app's per-market summaries plus the pipeline health
  report, with the explicit "a market-level union is not per-instrument coverage"
  disclosure; partial when freshness evidence is missing, unavailable when no market is.
Stage 3: Research Runs panel over validated immutable runs (protocol, the manifest's own
  derived price origin, age, last attempt kept distinct, legacy v3 labelled) and Signals
  panel (established candidates with reason/session/source, never an order or probability;
  unavailable rather than "no signal" when evidence is missing).
Stage 4: this entry, the private-console user guide, ARCHITECTURE, docs/README, and the
  next-agent handoff.
Independent reviewer: separate adversarial reviewer, formal verdict each round. Stage 1
  took four rounds - plan status contradiction; the global default ledger instead of the
  configured path; PARTIAL leaking P&L/allocation; the watchlist file escaping the
  configured path via its process-global default - then PASS. Stages 2-3 reviewed as one
  increment.
Focused tests / final gates: `tests/unit/test_dashboard_p03_console.py` (registry, gate
  before read for every private page, explicit-input states, partial-never-a-total,
  withheld P&L/allocation, reconciliation, no-write and no-created-file proofs,
  configured-path binding for ledger and watchlist, readiness/research/signals states);
  plus the dashboard IA parity guard, which now records the five app-only gated pages.
  Full serial and parallel suites, ruff, strict mypy, catalog byte identity and markdown /
  whitespace checks are run at the stage boundary with logs retained under /tmp.
Docs, schema/catalog, privacy, compatibility: user guide + ARCHITECTURE + this ledger +
  docs/README; no lake schema, catalog row, Settings group beyond `console`, dependency or
  ADR change. Private data stays out of static exports, Pages, the public API, logs and
  W&B by construction and by test.
Residuals, explicit stop condition: stage 1's populated-ledger render proof was deferred
  at this record and is closed by the P03 follow-up entry below; the console is not an
  authenticated service and must not be exposed beyond loopback.
  D01, P00-2/P00-4, R00-R02 and H00 remain gated.
```

### P03 follow-ups — private console (implementation record)

```text
P03 follow-ups / implemented, two scoped items / 2026-11-07 (no new gate: handoff §3 item 6):
Scope: the two small follow-ups named in the next-agent handoff, and nothing else - no
  ledger/marks/readiness/evidence contract change, no Settings key, dependency, ADR, API
  route, migration or catalog row. Owner rulings recorded: (A) reuse
  ``build_tracking_readiness``'s calendar-day cut-off and *label* it, rather than adding a
  second explicit as-of input to this page; (B) downgrade the page's ``SUCCESS`` to
  ``PARTIAL`` when per-instrument coverage is unavailable or incomplete.
1. Populated-ledger render proof (the stage 1 residual): a real private root is populated
  by the shipped writers (``LedgerStore.append`` with real ``LedgerEvent``s, a real
  ``marks.json`` and a canonical ``statement.csv``), and a full ``render_portfolio`` over
  it must produce a complete valuation, a matched reconciliation and a rendered table
  while the tree - directories included, so an empty directory counts - stays
  byte-identical. The snapshot is taken *before any console read*,
  because an earlier draft snapshotted after a probe read and a mutation (a write inside
  ``portfolio_panel``) survived it - the defect this proof was meant to catch.
2. Per-instrument coverage view on Data Readiness: ``panels.per_instrument_coverage`` reads
  ``TickerReadiness`` from ``signals.readiness.build_tracking_readiness`` with the
  configured ``console.private_dir`` bound for both the watchlist file and the tracking
  database, and the lake passed explicitly as the one non-private read. The panel shows a
  per-instrument table (price/feature status and sessions, ages, latest scan, signal rows)
  labelled as tracked-watchlist scope, never market-wide; an unconfigured path, unreadable
  state or an empty watchlist renders *unavailable* with its reason, never "0 instruments";
  an unobserved tracked identity, or a scan or signal history that is not ``available``
  (its columns render *unavailable*, never ``0``/``no scan``), keeps an otherwise-successful
  page from reporting success and makes it ``PARTIAL`` - an existing ``WARNING`` or
  ``UNAVAILABLE`` state is never upgraded. A green market summary therefore can never stand
  in for instrument coverage.
Mutation proofs: eleven rules removed in turn, each named test failed, file restored and
  ``diff -q`` identical - render-path file write, render-path empty directory, vacuous
  render, missing unobserved-instrument downgrade, missing unavailable-coverage downgrade,
  dropped watchlist binding, a coverage read that creates a file, the history-limitation
  downgrade, a straight-through history count, and each of the two early returns that had
  dropped already-read coverage.
Focused tests / gates: ``tests/unit/test_dashboard_p03_console.py`` (39 tests) plus the
  dashboard IA parity guard; full serial and parallel suites, ruff check/format and bare
  ``uv run mypy`` at the boundary. No catalog regeneration: no schema or DAG change.
Docs: private-console user guide (per-instrument coverage, tracked scope, state rules,
  directory-inclusive no-write proof), this ledger entry and the next-agent handoff.
Independent reviewer: round 1 ``NEEDS-REVISION`` (a corrupt/unreadable history was rendered
  as a measured ``0``/``no scan`` and could keep the page green; the no-write snapshot
  covered files but not directories; an early return dropped coverage that had already been
  read) - all three fixed with named tests and mutation proofs; round 2 ``NEEDS-REVISION``
  for documentation completeness only (history-gap rule and directory-inclusive proof not
  yet described); round 3 ``NEEDS-REVISION`` for documentation precision only (the guide
  over-scoped the ``PARTIAL`` rule, which the code applies to ``SUCCESS`` only); round 4
  ``PASS``.
Residual, explicit stop condition: the console remains display-only and loopback-only, not
  an authenticated service. Known cost, not a defect: the reuse path runs
  ``build_tracking_readiness``, whose synthetic-provenance observation counts rows across
  every dataset under the reset targets on each render - the same read ``equity readiness``
  already performs, and deliberately not cached (a cached coverage answer would be a
  freshness claim the page cannot support). D01, P00-2/P00-4, R00-R02 and H00 remain gated.
```

### D01 — real-source probe (evidence record)

```text
D01 / probe complete, ingestion not authorized / 2026-11-14:
Owner inputs used: `us_equity`/yfinance; free tier (USD 0.00); local-only, no redistribution;
  target root `data/lake/` for the eventual landing. Recorded in ADR-0028 (proposed on this
  date; **accepted 2026-11-14**).
Method: shipped `USEquityFetcher` (`auto_adjust=False`), 5 configured tickers, 2024-01-02..
  2024-12-31, actions `since=2024-01-01`, `sources.default_rpm=20`; 7 requests attempted
  (1 discarded by the identity guard), USD 0.00, no lake/schema/catalog/Settings change;
  artifacts and digests under `data/d01/probe-2026-11-14/`; full record in
  docs/plans/20261114-01-d01-source-probe.md.
Coverage: 1,260 price rows; 252/252 sessions with 0 missing and 0 extra for every ticker
  against `core.calendar.is_trading_day` (independent of the vendor); no null close/volume.
Identity: the provider supplied no MIC/exchange; the adapter refused the first attempt
  without the exact current ticker config and every row resolved as
  `identity_provenance=current_ticker_config` (configured, not vendor-verified).
Blocking finding (falsifies D00's "explicitly unadjusted"): vendor OHLC and volume are
  split-back-adjusted - NVDA's 10-for-1 split shows +0.746% close and 0.76x volume across
  the boundary - and only `adj_close` carries dividend adjustment. `with_price_adjustment`
  (ADR-0015) assumes unadjusted input at nine call sites, so ingesting these rows as-is
  would double-adjust every pre-split window. No real price row may enter `data/lake/`
  until P00-2's ADR accepts one shape: re-inflate at ingest, or store the vendor basis and
  stamp it so adjusted consumers fail closed (Bronze schema change).
Actions: 58 rows (`yahoo`), split value 0.1 in the lake convention, all 58 with a null
  `source_available_at`; `since=` is exclusive and the action window (2024-2026) is not
  bounded by the price window.
Claims allowed: reachability, adapter identity/retry/digest behaviour, 2024 sample coverage,
  action retrieval, USD 0. Claims still blocked: adjusted/PIT correctness, action
  completeness, survivorship, provider durability (A06 unchanged).
Residual, explicit stop condition: **ADR-0028 accepted 2026-11-14** by the owner, who
  selected landing shape (a) reconstruct-at-ingest. [ADR-0029](../decisions/0029-split-treatment-ledger-and-raw-price-basis.md)
  is also **accepted** (2026-11-14) and specifies that shape plus the ledger `SPLIT`
  semantics; its implementation is a separately authorized stage. The mixed-lake question is
  decided in [ADR-0030](../decisions/0030-second-real-source-lake-root.md) (proposed on this
  date, **Accepted 2026-11-15**): a
  second root `data/lake_real/` with the same medallion layout, never cataloged and never
  mixed. Ingestion stays gated on its own scoped authorization (ADR-0030 acceptance alone
  does not authorize a landing). No
  further network call is planned for this probe.
```

### D01 — landing (implementation and run record)

```text
D01 / ADR-0029 Part I implemented, one scoped landing run completed / 2026-11-17:
Authorization: owner message "authorize and proceed" over the recommended scope — `us_equity`
  only; tickers AAPL, MSFT, NVDA, JPM, KO; window 2024-01-02..2024-12-31; target
  `data/lake_real/`; Gold absent; ADR-0029 Part I first, one landing run, network for that
  run only.
Code (5 files + 2 new test modules, no schema/catalog/DAG/Settings/dependency change):
  `ingestion/price_reconstruction.py` (new) reconstructs raw prices at the write boundary;
  `core/paths.is_real_source_root` defines the gate; `orchestrator._write_market` applies the
  transform for `us_equity` into a real root only; `cli/commands/data.py` adds `--tickers` to
  `backfill` (plumbing already existed in `backfill_date_range`); the **serial** orchestrator
  branch now threads `lake_dir` into `_finalize_market` (defect: `backfill --lake-dir` was
  writing into the canonical root; the parallel branch already passed it).
Tests: `tests/unit/test_price_reconstruction.py` (19 tests: seam/volume/reciprocal/ex-date
  boundary/adj_close/column-order/no-split/determinism/per-ticker, four refusal reasons, a
  reverse split, and the three write-boundary wirings), `TestBackfillScope` in
  `test_cli_unified.py` (4), plus a serial-branch regression test in
  `test_lake_root_selection.py`. Mutation proofs: storing the vendor frame as fetched, an
  `ex_date >= date` boundary, a frame-wide factor, scaling volume with OHLC, any of the four
  refusals, the demo-root gate and the dropped `lake_dir` each fail a named test.
Actions first (full history, 5 tickers, 1 request per ticker): 703 events into bronze+silver,
  32 splits, `ex_date` 1962-03-13..2026-11-12; per-ticker watermarks AAPL 2026-11-12,
  JPM 2026-10-06, KO 2026-09-15, MSFT 2026-08-20, NVDA 2026-09-10 — all past the window, so
  `action_window_uncovered` did not fire. `coverage_written=0`: yfinance has no source-issued
  action clock, so no coverage proof exists (unchanged since the probe).
Refusal proven against the real store, not only in tests: `backfill --tickers TSLA` (absent
  from the action store) exited with `action_scope_incomplete`, wrote nothing and created no
  root; the writer recorded no frame.
Dry run (window): 252 market-dates, 252 `split_reconstruction_applied`, 0 rows written —
  `data/lake_real/01_bronze/` still held only `corporate_actions/` afterwards.
Landing run: 252 market-dates succeeded, 252 transforms, 0 failures, 1260 rows into
  `data/lake_real/01_bronze/market_data/us_equity/` — 252 sessions (2024-01-02..2024-12-31),
  5 rows per session, 252 rows per ticker, 0 null close/`adj_close`, volume Int64 preserved.
  Re-running one session reports `skip_existing_data` and rewrites nothing (idempotent).
Split seams checked: exactly one scale step in the whole window — NVDA 2024-06-10, ratio
  0.101 (raw close 1208.88 -> 121.79) — and exactly one split has an `ex_date` inside the
  window; the other four tickers show no scale step (`min_step` >= 0.935). NVDA's raw
  log-range 2.516 vs 0.229-0.451 for the others is consistent with one 10x step, not two.
Reciprocal proof on landed rows (decision 7(ii)): a fresh vendor fetch of 2024-06-06..06-11
  for AAPL+NVDA, re-adjusted with the stored ratios, reproduces the written rows with
  **0.0** relative error for close and volume; `adj_close` differs by <= 4.6e-05 (float32 vs
  float64 rounding) and so is stored as fetched (decision 4).
Isolation proof: `data/lake/` still 1413 files, 0 files newer than 2026-11-01;
  `data/catalog.jsonl` sha256 `ca4f81e1...67cca2` — byte-identical before and after.
Gates: focused suites green after the last edit; full serial and `-n auto` suites, ruff,
  format and bare mypy re-run before handoff (counts in the next handoff).
Findings raised, NOT fixed here (each needs its own decision):
  (a) `equity ingest --date ... --markets us_equity` cannot resolve identity — the CLI path
      passes `ticker_config=None`, so the yfinance rows fail `LegacyIdentityUnavailableError`
      (`backfill` passes a config and works). The user guide now documents `backfill` as the
      landing vehicle.
  (b) `equity monitor --lake-dir data/lake_real` FAILS on a `us_equity`-only root: the
      freshness and quality checks still union all five price markets (absent `cn_ashare`
      parquet errors) and require a Gold feature store, which a real slice does not have by
      design. Health is reported per root, but the per-market scoping for a real root is a
      health-contract change and was not made.
  (c) no `coverage` proof row exists for yfinance actions, so ADR-0015's adjusted read stays
      unavailable on real rows (A06 unchanged) and the reconstruction's only freshness
      evidence is the store watermark.
```

### Post-landing fixes — ingest identity and real-root monitoring

```text
Finding fix / ingest identity resolution / 2026-11-17:
Defect: `equity data ingest --date <D> --markets us_equity` raised
  `LegacyIdentityUnavailableError` for every row, because the CLI passed
  `ticker_config=None` and the provider supplies no MIC/exchange. `backfill` already passed
  the current config and worked, so the documented daily-ingest command was unusable while
  the landing vehicle was not.
Fix: `cli/commands/data.py` builds the current `TickerConfig` from settings and threads it,
  exactly as `backfill` does. `resolve_explicit_scope` returns `None` per market when no
  `--tickers` is given, so the configured scope and every fetcher default are unchanged; a
  ticker absent from `config/tickers.yaml` still fails closed rather than inventing a venue.
Proof: CLI test asserts a `TickerConfig` whose `config_path` is the settings path is passed;
  a companion test asserts an explicit `--tickers` scope still passes through unchanged; an
  adapter-level test resolves a MIC-less AAPL row to `XNAS` / `current_ticker_config`. Live:
  `ingest --date 2025-01-02 --tickers AAPL --lake-dir data/lake_real --dry-run` fetched,
  resolved identity, reconstructed, reported `dry_run` and wrote nothing.

Finding fix / real-root monitor scoping / 2026-11-17:
Defect: `equity monitor --lake-dir data/lake_real` FAILED on absent `cn_ashare`/`hk_sg`/
  `jpx`/`krx` parquet and on an absent Gold store — data a real root may not hold — so the
  verdict said nothing about the landed slice.
Fix: `core/paths.REAL_ROOT_PRICE_MARKETS` + `price_markets_for_root` are the single
  definition of decision 5's slice, read by both the `--lake-dir` write guard and the monitor.
  A real root sweeps `us_equity` only; Feature Store, Unstructured Freshness and a 7-day
  quality window with no rows report `not_applicable` **with a reason** (never `PASS`), and
  an absent price table still fails. `check_results` keeps its boolean shape for
  `dashboard/exporter.py`; the report adds `check_status`, `check_notes` and `scope`.
Proof: `tests/unit/test_monitor_root_scope.py` (10 tests) — slice-only sweep, canonical
  five-market sweep unchanged, empty real root still fails, three not-applicable verdicts,
  canonical missing-Gold still fails, canonical unverified-window pass pinned, report shape
  and scope. Live: the real root now reports `FAIL Data Freshness (687 days old, latest
  2024-12-31)` + three `N/A` rows, and `--max-age-days 800` reports HEALTHY; the canonical
  report is unchanged (5 markets, same verdicts, empty notes).
```

### P00-4 — restore and compaction (remainder implemented)

```text
P00-4 remainder / ADR-0031 decisions 4-5 implemented / 2026-11-17:
Scope: `restore_ledger` + `equity portfolio restore --from <verified backup> [--apply]`, and
  `compact_ledger` + `equity portfolio compact [--apply]`. Report-only by default; nothing
  runs automatically; no event is ever deleted.
Restore (decision 4): re-runs the five checks on the candidate; refuses a candidate that is
  the current file, a different account label (one private book, ADR-0024 d1), a `-journal`
  beside either file, or insufficient free space. `--apply` backs up the *current* file first
  (it may hold events the backup lacks), copies to a temp file in the same directory, proves
  the bytes, `os.replace`, re-applies `0600`/`0700`, re-verifies, and reports what it rolled
  back (event count + newest `recorded_at`).
Compaction (decision 5): `VACUUM INTO` a fresh file, verify it, require `user_version`, row
  count and the `(account, external_id, content_digest)` digest to match the pre-compaction
  state, back up the current file, swap it in through the restore procedure, re-verify.
  `row_id` renumbering carries no meaning, so nothing depends on it.
Tests: `tests/unit/test_portfolio_restore_compaction.py` (18) — report-only writes nothing for
  both mechanisms, the rollback report, atomic replacement with the newer state preserved as a
  verified backup, identical-candidate no-op, restore onto a missing ledger, all four
  refusals, free-space refusals (with no temp/backup left behind), record preservation across a
  rebuild, replay-after-compaction, and both CLI modes. Three defects the tests caught before
  shipping: the rebuilt `VACUUM INTO` file was left on disk, the space test never reached the
  pre-check because a byte-identical candidate short-circuits, and the CLI double had no
  `_init_logging`.
Not done, explicit: no run against a real ledger (`data/portfolio/` still does not exist), no
  retention/pruning/deletion (decision 6), no in-place `VACUUM` form (only the guarded
  `VACUUM INTO` path), no scheduled anything.
```

### D01 — landing widened (2020-01-02 .. 2026-08-20)

```text
D01 / widened landing run / 2026-11-17 (owner: "go with your recommendations"):
Scope: `us_equity` only, same five tickers (AAPL, MSFT, NVDA, JPM, KO), window extended along
time, target `data/lake_real/` (append), Gold absent.
Window end is bounded by evidence, not by preference: the ADR-0029 Part I refusals require each
ticker's action watermark to reach its price rows, and the watermarks are AAPL 2026-11-12,
JPM 2026-10-06, KO 2026-09-15, NVDA 2026-09-10, MSFT 2026-08-20 — so the honest end is the
minimum, **2026-08-20**. Landing past it would have refused four of five tickers rather than
write prices whose later splits are unproven. An action refresh first confirmed no new events
(`corporate_actions_no_new_events_unverified_coverage`, `coverage_written=0` as expected).
Dry run: 1667 market-dates, 1415 `split_reconstruction_applied`, 0 failures; the table was still
1260 rows / 252 sessions afterwards, so the dry run wrote nothing.
Run: 1666 succeeded, 1 failed — 2022-07-22 was refused by the pointblank quality gate
(open/high/low/close/volume/adj_close `> 0` expectations, one failing row). Diagnosis: a
re-fetch of that date is clean for all five tickers, and the same date landed on retry
(1/1, 0 failed), so it was a transient vendor row, not the reconstruction. The gate did exactly
what ADR-0007 asks: refuse a bad batch instead of landing it.
Result: **8335 rows, 1667 sessions, 2020-01-02..2026-08-20**, 1667 rows and 1667 sessions per
ticker, 5 rows per session, 0 nulls in OHLC/volume/`adj_close`.
Independent coverage check: every US session in the window derived from the repository's own
calendar (`core.calendar.is_trading_day`) is present — expected 1667, landed 1667, missing 0,
and nothing landed that is not a session.
Split seams: three splits have an `ex_date` inside the window (AAPL 2020-08-31 `0.25`, NVDA
2021-07-20 `0.25`, NVDA 2024-06-10 `0.1`) and exactly three scale steps exist — AAPL 0.2585,
NVDA 0.2478 and NVDA 0.1007 (each the stored ratio plus that session's own move). No other
ticker shows a step, and none of the pre-2020 splits (JPM 1982/1984, KO 1965-2012, MSFT's nine)
appears inside the window.
Isolation: `data/catalog.jsonl` sha256 `ca4f81e1...67cca2` (byte-identical), `data/lake/` still
1413 files with nothing newer than 2026-11-01, `data/portfolio/` still absent, no ledger or
`MUTATION` artifact. No code changed in this stage, so the previous green gate counts stand.
```

### ADR-0032 — implemented 2026-11-19 (decisions 1-9, no runs)

```text
ADR-0032 / accepted 2026-11-18 / nothing implemented:
Owner: "accept ADR 0032", taking the implementation on a new thread.
What was accepted: one explicitly weaker, opt-in coverage basis (`vendor_events_no_clock` —
  vendor-event completeness, no source clock) admitted only through
  `with_price_adjustment(coverage_basis=...)`; the strict source-clock basis stays the default
  and the only point-in-time basis; point-in-time research, the ML loaders, mixed demo/real
  evidence and mixed bases in one read stay refused; no clock is synthesized; origin stays
  `unknown` (ADR-0027); dividend adjustment is still out of scope; the demo lake's behaviour is
  unchanged (decision 7).
What changes in data when implemented: coverage proofs gain `coverage_basis` and a weak-proof
  model beside `ActionCoverageProof`, `ACTION_COVERAGE_SCHEMA_VERSION` 2 -> 3, and feature rows
  carry the stamp. `data/catalog.jsonl` is regenerated only if decision 9 requires it, never by
  hand.
What it unblocks, and only after implementation: a split-only adjusted read on the landed real
  slice, hence the feature half of R01. A Gold table in `data/lake_real/` still needs its own
  scoped run authorization (ADR-0030 decision 10).
Evidence used in drafting, verified against the working tree: all 703 Silver action rows carry a
  null `source_available_at` (written at `sources/base.py`, the fetch seam); the coverage table
  does not exist under `data/lake_real/`; exactly 9 `with_price_adjustment` call sites exist
  across `backtesting/data_loader.py` (2), `backtesting/engine.py` (2),
  `features/engineering.py` (2), `features/labels.py` (1), `signals/generators/backtest.py` (1)
  and `signals/tracking.py` (1).
Review of the draft: written by an independent doc-writer subagent, then spot-checked against the
  code for the two load-bearing claims (null clocks, nine call sites) — both confirmed.
Next: implementation stage with decision 8's tests and mutation proofs (handoff 20261118-01).
```

### ADR-0032 — implementation record

```text
ADR-0032 / implemented 2026-11-19 / nothing run:
Scope shipped (decisions 1-9): weak proof model + basis stamps; ACTION_COVERAGE_SCHEMA_VERSION
  2 -> 3, AVAILABILITY_SCHEMA_VERSION 4 -> 5, FEATURE_SCHEMA_VERSION 7 -> 8; opt-in
  with_price_adjustment(coverage_basis=...) with source_clock still the default; one basis per
  read; mixed bases and mixed demo/real roots refuse; weak rows carry null clocks/age and
  vendor_events_complete; strict PIT validators and ML loaders refuse the weak basis; legacy
  v2/v4/v7 rows read as source_clock; catalog regenerated.
Write/fetch seam: the clockless yfinance fetch emits one weak proof per ticker over the vendor
  list window (before the watermark filter); ingest persists typed null clocks; coverage schema
  and feature writer route on the stamp. The feature path threads the explicit opt-in
  (generate_features/run_feature_job -> _compute_point_in_time_technical); its default stays
  strict, and the weak branch stamps null clocks/age and vendor_events_complete.
Evidence: tests/unit/test_coverage_basis.py (32 tests) plus the landed-slice read-only check
  (8,335 rows / 1,667 sessions, three scale steps, NVDA 2024-06-10 seam, volume/OHLC
  reciprocity); one-rule mutation/restore proofs recorded in the same stage.
Not done: no ingest, no feature run, no R01/R02, no write under data/lake_real/; Gold in the
  real root still needs its own scoped authorization (ADR-0030 d10).
Mutation proofs (one rule at a time, each restored): reader one-basis gate, strict PIT
  validator, mixed demo/real-root gate, weak null-age stamping, weak proof parsing, weak
  coverage status, feature-writer routing and the feature-path opt-in (8/8); R00 absent freeze,
  changed freeze, manifest digest binding and canonical set-like fields (4/4) - each mutation
  made its named test fail.
```

### R00 — implemented 2026-11-19 (offline controls)

```text
R00 / implemented 2026-11-19 / offline, nothing run:
backtesting/protocol.py freezes universe, window, holdout, execution clock/price, label
  horizon/maturity, purge/embargo, costs, metrics, baselines, acceptance tolerances, synthetic
  generator/seeds and the numeric resource budget into one canonical payload; its SHA-256 is the
  protocol identity. freeze_protocol writes a dated manifest (protocol digest, code identity,
  non-secret config digest, input digests) under data/research/protocols/; require_frozen_protocol
  refuses an absent freeze and a changed one (naming the fields); load refuses a tampered record.
Evidence: tests/unit/test_research_protocol.py (23 tests); no ingestion, network, model fit or
  lake write. R01/R02 remain gated and will bind their E01 manifests to the frozen digest.
```

### Stage 4 — R02 implemented and evaluated (2026-11-21)

```text
ADR-0034 accepted 2026-11-21 by owner delegation: research-only ML on vendor-stamped rows,
  explicit weak-basis opt-in, no PIT claim, no Platinum writes, mandatory disclosure, nothing
  promoted. Real-root ML reader: FeatureLoader(lake_dir, coverage_basis) with defaults
  unchanged; strict still refuses weak; mixed bases refuse.
Protocol R02-ml-research-2024 frozen (digest 37fd6727..., prespec ae74513b...). One fit
  (XGBoost, fixed params) + logistic simple model + momentum-20 rule + base rate on matched
  pre-holdout dates; block bootstrap (5, fixed seed); holdout untouched (last prediction
  2024-09-23); artifacts under data/research/r02/ carry basis, exact disclosure and both
  digests (including predictions.parquet).
Result: test net return -9.99% (primary), Brier 0.3835, AUC 0.4269, DD -17.52%; simple model
  -7.77%, technical rule -7.20%, no-skill -4.88%; paired difference vs technical -2.79 pp,
  95% interval [-0.42 pp, +0.31 pp]; promotion criteria 1/5 met, promoted=false. Negative and
  inconclusive, recorded as such. Review PASS.
Gates at close: 2,834 collected / 2,818 passed / 16 skipped / 0 failed in serial and -n auto;
  ruff/format 373, mypy 193.
```

### Stage 5 — H00 built (2026-11-21)

```text
Owner decisions recorded (eight §7 answers). Sanitized demo-lake builder (dashboard/h00.py)
  with allowlist-only payload, in-memory compose, fail-closed negative scan (case-insensitive
  vendor attributions, exact other classes), demo-root containment (production canonical only),
  clean-destination + on-disk rescan, 30-day staleness banner, loopback-only preview.
  B1-B10 closed by allowlist/scope; B5 closed by retiring push/schedule Pages triggers
  (manual workflow_dispatch only, least privilege, SHA-pinned actions). 33 H00 tests + CLI help
  tests; artifact passed=true, demo_lake_only, clean scan. NOT published: publication needs its
  own explicit approval. Review PASS.
```

### Stage 4 — R02 blocked by the accepted contract (2026-11-20)

```text
R02 is a point-in-time ML experiment. The only Gold rows in the real root carry the weak
vendor_events_no_clock basis, and ADR-0032 decision 4 requires the ML loaders to refuse
vendor-stamped rows; decision 10 explicitly does not authorize "any point-in-time claim,
feature or label produced from vendor-stamped rows". Independently, ml/feature_loader.py is
canonical-root-only (no lake_dir) and strict-only. R02 therefore needs a NEW accepted decision
authorizing vendor-stamped ML with disclosures (or a source-issued action clock, which
yfinance does not publish), plus a real-root ML reader. Not implemented by design.
```

### Stage 5 — H00 gated on owner answers (2026-11-20)

```text
H00 needs the eight answers to docs/reports/20261117-h00-sanitized-demo-lake-page-review.md §7
(synthetic-metrics disclaimer, predictions display, hosting/privacy, allowlist membership, and
the rest). No agent may choose them; nothing built or published.
```

### Stage 3 — R01 baseline evidence (2026-11-20)

```text
Owner direction: complete the remaining stages; the scope below was adopted under it.
Protocol revisions (each an explicit new freeze, never an edit): v1 (5 names) left momentum
  ineligible (min_stocks=10); v2 (10 names, 2024-only) had no 200/252-session warmup for any
  registered strategy, so no trades; v3 adopted: 2023-01-03..2024-12-31, holdout
  2024-10-01..2024-12-31, 10 names (AAPL, MSFT, NVDA, GOOGL, QQQ, JPM, KO, BABA, SPY, GLD).
Frozen v3 (digest e53872973370..., manifest 20261120T130000Z-e53872973370): entry
  session_close/close (the engine's actual execution), costs realistic 0.001425/0.003 (the
  applied pair), metrics net_return/sharpe_ratio/max_drawdown, baselines buy_and_hold/
  equal_weight, tolerances, budget 900 s / 10 symbols / 504 sessions, catalog digest input.
Run: mean_reversion, 2023-01-03..2024-09-30 (pre-holdout), realistic costs, basis
  vendor_events_no_clock, bound to the digest before the engine was built. Evaluated: total
  return +22.79%, CAGR 24.59%, volatility 14.24%, Sharpe 1.49, max drawdown -8.22%, 229
  trades, 43.2% win rate. Momentum stayed ineligible on v3: BABA has <252 sessions by the
  rebalance dates, so each slice has 9 of 10 names (< min_stocks=10) - a scope fact.
Evidence: data/findings/runs/20261121T025630Z-b5dea12ef358; manifest parameters carry
  protocol_digest + protocol_experiment, dates end 2024-09-30 (holdout untouched), artifacts
  equity/drawdown/metrics/trades.
Untouched: data/lake (1,413), catalog (537bf5fb...), Gold (1,260), bronze (58,653); no commit.
Gates: 2,765 collected / 2,749 passed / 16 skipped / 0 failed in serial and -n auto;
  ruff/format clean (367), mypy clean (191).
```

### Stage 2a — backtest lake-root + weak-basis integration (2026-11-20)

```text
Implemented by a worker (two turns), reviewed PASS.
Engine/loader: lake_dir and coverage_basis on both (defaults canonical / source_clock), the
  engine's own loader constructed with them, both data_loader with_price_adjustment call sites
  threaded, decoded point-in-time action context carries the basis (unknown basis refuses),
  one-basis-per-load refusal, preloaded frames are never reinterpreted. Null-safe
  action_source_age_days (weak rows are null, not int(None)).
Evidence: InputSnapshot.coverage_basis (omitted when absent), only valid on
  action_coverage:<market> snapshots, mixed-basis manifest scope refuses; RunScope/report/arena
  thread the basis. Flat `equity backtest` gains --lake-dir/--coverage-basis with Exit(1).
Tests: tests/unit/test_backtest_basis.py (14) incl. weak read == direct with_price_adjustment and
  an end-to-end weak backtest; 9 mutation proofs killed (loader gate, context basis, ctor,
  one-basis-per-run, manifest mixed scope, snapshot placement, snapshot stamp, threading, CLI).
Gates: 2,741 collected / 2,725 passed / 16 skipped / 0 failed in serial and -n auto;
  ruff/format clean (367 files), mypy clean (191). One stale test double fixed (report stub).
Untouched: catalog 537bf5fb..., demo 1,413, Gold 1,260, bronze 58,653; nothing committed.
```

### Stage 2 — first real-root Gold run (2026-11-20)

```text
Owner-authorized scope: data/lake_real/, us_equity, AAPL/MSFT/NVDA/JPM/KO, 2024-01-02..
  2024-12-31, basis vendor_events_no_clock.
Pre-step: corporate-actions refresh for JPM and KO added 2 coverage proofs (0 new events);
  the table now holds 11 weak proofs covering all eleven instruments.
Dry run: 1,260 rows for 5 instruments, wrote nothing (Gold absent, demo 1,413, catalog sha
  537bf5fb..., no staging change).
Real run: 76 s, merge 1,260 rows (Delta v0). Verified read-only: 252 dates 2024-01-02..
  2024-12-31, 5 rows/date, 0 duplicate keys, 0 null closes; action_coverage_basis
  vendor_events_no_clock, status vendor_events_complete, null clocks/age, versions 8/5/3;
  next_day_return/label_available_date null only on 2024-12-31; macro columns null with
  macro_available=0 (no macro dataset) - no imputation. NVDA 2024-06-07/06-10 Gold returns are
  split-continuous against Bronze raw closes.
Reviewer PASS. Untouched: data/lake (1,413 files), catalog (537bf5fb...), no portfolio/research,
  HEAD ad74703, nothing committed.
Stage 3 readiness BLOCKED (found by the reviewer): backtesting/data_loader.py accepts lake_dir
  but engine.py:253-260 instantiates it with defaults and exposes no lake-root or coverage-basis
  option; the point-in-time path does not accept the weak basis. Stage 2a is required first.
```

### Stage 1 — real-root feature runner (2026-11-20)

```text
Implemented by a worker subagent; reviewed PASS (round 2, after one NEEDS-REVISION).
FeatureEngineer(db_path, *, lake_dir) reads one explicit root for price markets and the
  corporate-action store (canonical default, no discovery/fallback/mixing).
scope_action_inputs() loads (actions, coverage) per instrument with three refusals: no proof
  recorded, requested basis has no evidence, window past the coverage bound (never clamped);
  this fixes the subset read over the 11-ticker action store.
run_feature_job threads lake_dir/action_inputs/coverage_basis/dry_run through generate_features,
  preserve_finalized_labels, finalize_preceding_labels and upsert_dataset.
New flat `equity features` CLI: --tickers (required), --start, --end, --coverage-basis
  (source_clock default), --lake-dir, --dry-run; help-scan test present.
Evidence: tests/unit/test_feature_run_scope.py (9), CLI tests (8), 4 mutation proofs (strict/weak
  refusal, weak/strict branch, coverage-bound refusal, dry-run write seam); full gate 2,727
  collected / 2,711 passed / 16 skipped / 0 failed in serial and -n auto; ruff/format 366 files,
  mypy 191 sources. Review findings resolved: the pipeline-contracts row was missing (added) and
  the catalog dirtiness is the pre-stage ADR-0032 regeneration, not a Stage 1 write.
No feature/Gold run; no write under data/lake_real/, data/lake/ or the catalog.
```

### ADR-0033 — implemented 2026-11-20 (proven-empty scopes)

```text
ADR-0033 / implemented 2026-11-20 / offline contract + one authorized widening:
A successful, non-raising vendor fetch that returns no action rows is recorded as a weak
  `vendor_events_no_clock` proof over the exact price window; ADR-0029 Part I then accepts a
  ticker absent from the action store when such a proof covers its rows (split factor 1).
  A failed fetch never yields a proof; a strict proof never proves emptiness; a proof that
  does not reach the rows still refuses. Tests: 4 reconstruction + 2 widening; mutation
  proofs 6/6 (basis, window, failed fetch, non-empty list, plus the two corrected variants).
GLD landed through this path: 5,535 rows, 2004-11-18..2026-11-18, coverage proof 2004-01-01..
  2026-11-18 with null clocks.
```

### D01 widening — 2026-11-20 (authorized: MSFT, GOOGL, AAPL, NVDA, ADBE, BABA, GLD, SPY, QQQ)

```text
Range-widening path added because `equity backfill` is per trading day (~10k requests for
  forty years): `equity widen` fetches each ticker's window once, reconstructs once and writes
  through the same `_write_market` boundary; each ticker is clamped to its own coverage/action
  evidence. Initial AAPL per-day attempt was interrupted at 40 min after landing 4,040 rows;
  the range path then finished 49,784 rows for 8 tickers in one pass.
Identity: `ARCX` (NYSE Arca) added to the exchange/MIC vocabulary and calendar map; SPY and
  GLD configured (BABA/QQQ added earlier this stage). GLD has no corporate actions.
Result in data/lake_real/ (us_equity): 11 tickers, 58,653 price rows, 0 duplicates, 0 null
  closes. Windows: AAPL 1987-05-11..2026-11-12; MSFT 1987-09-21..2026-08-20; ADBE
  1987-03-12..2005-05-24 (last action); BABA 2023-12-20..2026-06-11 (vendor list start);
  GOOGL 2014-04-03..2026-09-04; GLD 2004-11-18..2026-11-18; NVDA 2000-06-27..2026-09-10;
  QQQ 2000-03-20..2026-09-21; SPY 1993-03-19..2026-09-18; JPM/KO unchanged 2020..2026-08-20.
Coverage: 9 weak proofs (all but JPM/KO, which keep their older data; GLD proven-empty).
Untouched: data/lake/ (1,413 files) and data/catalog.jsonl (537bf5fb...). No feature run.
Next: the real-root feature runner (FeatureEngineer is canonical-root-only) before queue item 3.
```

### P00-2 — split treatment and the second lake root (decision record)

```text
P00-2 / ADR-0029 accepted, not implemented / 2026-11-14:
Owner decisions: D01 landing shape (a) reconstruct raw prices at ingest; the ledger gains a
  first-class `SPLIT` event with quantity adjustment, exact reciprocal-reversal replacement
  and a pre-split-mark refusal, with **no migration** (the ratio travels in `quantity`
  because a `split_ratio` column would need P00-4). ADR-0024 decision 9 is amended by
  ADR-0029, which is Accepted.
Mixed-lake question: decided as a **second lake root** `data/lake_real/` with the same
  medallion layout, explicit `--lake-dir` only, not cataloged, never mixed with the demo
  root, and Gold absent there until a real action proof exists - ADR-0030 (proposed on this
  date, **Accepted 2026-11-15**). The
  write seam is small because `write_delta`/`merge_delta` already accept `lake_dir`; only
  `upsert_dataset` and the `ingest`/`backfill` commands need threading.
Implementation status recorded here: none at 2026-11-14. Updated 2026-11-15: both are
  implemented and green - see the ADR-0030 and P00-2 implementation records in §10; the
  ledger part is live, the D01 raw-price half stays deferred behind a landing authorization.
  Ingestion remains gated on its own authorization; no real row may
  enter `data/lake/`.
```

### ADR-0030 — second lake root (implementation record)

```text
ADR-0030 / implemented / 2026-11-15:
Scope: root selection at the write/read/monitor seams only. `resolve_lake_root` (core/paths)
  validates an explicit root: it must resolve inside `data/`, must not be `data/` itself, and a
  symlink escape is caught by `resolve()`. `REAL_LAKE_DIR` names the sibling `data/lake_real/`
  and is never a default.
Threading: `upsert_dataset(lake_dir=…)` -> `merge_delta`; `run_daily_ingestion`, the
  presence check (`_read_partition_coverage`), `_write_market`, `backfill_date_range` and
  `ingest_corporate_actions`/`max_stored_ex_dates` all carry the root; `--lake-dir` on
  `equity ingest`, `backfill`, `corporate-actions` and `monitor` validates at the CLI seam.
  Monitoring rebases its constants per instance and returns them unchanged with no explicit
  root, so runtime-patched test constants keep working.
Tests: `tests/unit/test_lake_root_selection.py` (16 tests) - containment, symlink escape, a
  root nested inside the canonical lake, write isolation with no fallback, writer threading
  (`None` stays `None`), the orchestrator presence read and its caller's skip decision,
  dry-run creates nothing, monitor rebasing, CLI flags + rejection, the scope guard on all
  three write commands and its enrichment-identifier refusal, constant identity. Mutation
  proofs: removing the coverage-root thread, the writer thread, the containment check, the
  alias check, the scope-helper rule, the monitor rebasing or the CLI validation each fails
  its named test (the writer mutation is falsified by the threading test, not the merge
  test). Round 2 added the falsifiers the first pass lacked: the guard's call sites and
  `_filter_markets_with_gaps`' coverage read.
Gates: serial and parallel suites, ruff, bare mypy. No schema, catalog, Settings or dependency
  change. Residual, explicit stop condition: no real row has been written; the D01 landing
  still needs its own authorization, and the real root is not cataloged by design.
Independent reviewer (round 1, 2026-11-15): ``NEEDS-REVISION`` - (a) a symlink or `..` path
  resolving to the canonical lake was accepted, so an explicit real-root write could land in
  the demo root; (b) no landing-scope guard, so `--lake-dir` accepted non-us_equity markets.
  Both fixed: aliasing is rejected unless the operator named the canonical path itself, and a
  non-canonical root refuses any market other than `us_equity`. New tests: alias rejection,
  the scope guard (helper-level only at this round; its call sites are covered from round 2).
  ADR-0030 was accepted 2026-11-15 with decision 5 amended to include the corporate-action
  tables the reconstruction needs.
Independent reviewer (round 2, 2026-11-15): ``NEEDS-REVISION`` - the round-1 fixes were
  re-checked by probe and by one-rule-removed mutation: the alias check, containment, the
  guard helper, writer threading, monitor rebasing and dry-run no-write all fail their named
  tests when mutated. Four defects remained. (a) The guard resolved every entry through
  `canonical_market`, which raises on the enrichment dataset identifiers `--markets` also
  accepts and which `ingest`'s configured default list contains - so `equity ingest
  --lake-dir data/lake_real` (no `--markets`) and `corporate-actions --market macro
  --lake-dir ...` died with an uncaught `ValueError` instead of the documented refusal.
  (b) Only exact equality with the canonical lake was refused, so `--lake-dir
  data/lake/01_bronze` was accepted and would have written a real row inside `data/lake/`
  (decision 10). (c) The guard's three call sites and `_filter_markets_with_gaps`' coverage
  read had no falsifier: removing any of them left 234 tests green. (d) The guide documented
  `equity ingest corporate-actions`, which is not a command. Fixed: the guard scopes on price
  markets and refuses enrichment identifiers cleanly, a root resolving inside the canonical
  lake is refused, four wiring/refusal tests were added, and the guide, CLI reference and
  ARCHITECTURE.md were corrected.
Independent reviewer (round 3, 2026-11-15): ``PASS`` - the round-2 delta was re-verified by a
  reviewer that wrote none of it. The inside-canonical refusal, the price-market scoping,
  each of the three scope-guard call sites, the orchestrator's coverage-root thread and the
  per-account reversal lookup all fail their named test when removed, and the round-1 rules
  still hold: a symlink or `..` alias of the canonical lake is refused while the canonical
  path itself is accepted and keeps its multi-market behaviour, and `data/lake/01_bronze` is
  refused. No new defect found. Focused suite 36 tests green; ruff, format and bare mypy
  clean. **Stage verdict: PASS** - the remaining step is owner acceptance, not another round.
Owner acceptance (2026-11-15): **accepted**. The second root, its CLI guard and its test set
  are the accepted implementation. This acceptance authorizes no landing: a D01 ingest still
  needs its own scoped authorization, Gold stays absent in the real root, and no real row may
  enter `data/lake/`.
```

### P00-2 — split treatment (implementation record)

```text
P00-2 / ADR-0029 implemented (ledger part) / 2026-11-15:
Scope: `SPLIT` joins the closed event set; `quantity` carries the share ratio (no migration,
  as ADR-0029 decided). Contract: ratio > 0 and != 1, `cash_effect` exactly 0, no price/fee/
  basis. Import accepts the bare kind and still rejects aliases (`STOCK_SPLIT`, ...).
Replay: quantity multiplies by the ratio, total basis is unchanged (average cost divides),
  cash/fees/realised gain untouched, unknown basis stays unknown, a split with no position is
  inert. `oversell_issues` applies the ratio so a post-split sale is not an oversell.
Replacement: reversal must exactly invert the split it references (exact fraction product 1),
  checked in `validate_batch` (import refuses) and again in `replay_ledger` (stored data).
Valuation: the replay exposes applied splits; a mark whose session predates a split is
  refused for a post-split instant (`MISSING_MARK` with an explicit split blocker). The
  ledger never edits marks.json or clears a valuation block; the import prints the affected
  instruments and that consequence.
Tests: `tests/unit/test_portfolio_splits.py` (20 tests) plus five old-contract tests updated
  (`test_split_is_the_only_quantity_adjusting_kind`, alias rejection, invalid-row list).
  Mutation proofs: replay no-op, oversell ignoring the ratio, skipped reversal exactness,
  accepted pre-split mark and a cash-moving SPLIT each fail their named test.
Independent reviewer (round 1, 2026-11-15): ``NEEDS-REVISION`` - (a) a reversal that arrives
  *before* the split it names passed the reciprocal check; (b) a stale pre-split mark lost
  the ``MISSING_MARK`` status to ``STALE_MARK``; (c) the dry-run preview summed split ratios
  into a "quantity delta"; (d) the guide still said split accounting did not exist; (e) the
  working diff also carries the root tranche (expected: two stages in one turn). Fixed (a)-(d)
  with new tests: reversal ordering, split-refusal precedence, `split_event_count` in the
  preview and a relabelled delta line, and three corrected guide statements.
Independent reviewer (round 2, 2026-11-15): ``NEEDS-REVISION`` - (a)-(e) confirmed fixed and
  mutation-proofed; deleting the ordering branch, the exact-reciprocal branch, the
  split-refusal precedence or the `split_event_count` increment each fails its named test.
  Note recorded: the preview's `continue` is redundant defence, because `quantity_delta`
  already returns 0 for `SPLIT` (removing it leaves the test green). One defect remained:
  `split_reversal_issues` looked the referenced split up by `external_id` alone, while IDs are
  unique per account, and because `event_order_key` starts with the account the same ledger
  was accepted in one CSV row order and rejected in the other - a verdict dependent on file
  order. Fixed by keying the lookup on `(account, external_id)`; a two-account test proves the
  verdict is row-order independent and still refuses a non-reciprocal reversal.
Independent reviewer (round 3, 2026-11-15): ``PASS`` - the per-account reversal keying,
  reversal ordering, exact reciprocity, split-refusal precedence over staleness and the
  preview split count each fail their named test when removed; cross-account, non-reciprocal
  and out-of-order reversals stay refused. No new defect found. Focused suite 36 tests green;
  ruff, format and bare mypy clean. **Stage verdict: PASS** - the remaining step is owner
  acceptance, not another round.
Owner acceptance (2026-11-15): **accepted**. The ledger `SPLIT` contract is the accepted
  implementation. The D01 price-basis half (ADR-0029 Part I) is still NOT implemented and
  remains separately authorized; no migration, schema or catalog change is implied.
Deferred, explicit: the D01 price-basis half (reconstruct raw at ingest) is NOT implemented;
  it needs the ADR-0030 root first. No migration, no schema change, no catalog change.
Post-acceptance defect (2026-11-16): **the persisted path rejects `SPLIT`.** `store.py`'s v1
  `CHECK (event_type IN (…))` still enumerates the seven pre-split kinds, so a batch that
  passes validation rolls back with a raw `sqlite3.IntegrityError`; measured against a temporary
  ledger, `OPENING` alone appends while `OPENING`+`SPLIT` and `SPLIT` alone both fail with
  "CHECK constraint failed". Measured end to end on a temporary ledger, `portfolio import`
  exits 1 with
  `the private ledger could not be read (IntegrityError)` / `another process may hold the
  ledger; retry once it is free` — atomic (nothing written, including the OPENING that would
  have succeeded) but misdiagnosed: the file is readable and a retry can never work. The 20
  split tests exercise the
  event contract, replay, marks and the preview - never the store write boundary - which is
  why rounds 1-3 missed it. This also corrects the record's "no migration" claim: SQLite
  cannot widen a `CHECK` in place, so persisting `SPLIT` needs the v1->v2 table rebuild that
  [ADR-0031](../decisions/0031-p00-4-ledger-backup-restore-migration-compaction.md) (Accepted 2026-11-16)
  specifies. The in-memory contract stands and stays accepted; only persistence is blocked,
  and the user guide now states the gap.
  Fixed 2026-11-16 by P00-4 (ADR-0031): the store is at schema v2 with `SPLIT` in the `CHECK`, a
  v1 ledger is refused on read and write with a message naming `equity portfolio migrate`, a
  constraint rejection is reported as a schema/contract mismatch instead of a read failure, and
  the explicit upgrade writes a verified backup first. `tests/unit/test_portfolio_store_migration.py`
  closes the coverage gap this note records: a real append, reload and replay of a split.

### P00-4 — ledger schema migration, verified backup and compaction (implementation record)

```text
P00-4 / ADR-0031 implemented (migration + backup half) / 2026-11-16:
Driver: ADR-0029 Part II was accepted with the SPLIT kind, but the store's v1 CHECK still listed
  only the seven pre-split kinds, so a validated batch rolled back with sqlite3.IntegrityError
  and the CLI reported "the private ledger could not be read (IntegrityError) ... retry once it
  is free". A CHECK is schema, so persisting SPLIT needed a table rebuild - the migration
  ADR-0029 believed it did not need.
Schema: PORTFOLIO_SCHEMA_VERSION 1 -> 2. The event_type CHECK includes SPLIT; fresh databases
  are created at v2; the upgrade rebuilds the same table body as `ledger_events_v2` and renames
  it. A v1 file is refused on read and write with a message naming `equity portfolio migrate`;
  other versions are refused with the supported pair. A constraint rejection raised by append is
  mapped to a LedgerStoreError naming the schema/contract mismatch - never a read failure, never
  retry advice.
Verification: `verify_ledger_file` (read-only URI) captures the file SHA-256, schema version,
  row count and a record digest over (account, external_id, content_digest), plus
  integrity_check. `migrate_ledger` is report-only by default and writes nothing; `--apply`
  checks free space, writes `<ledger>.<utc>.v<old>.bak`, verifies that copy against its source,
  rebuilds in one BEGIN IMMEDIATE transaction, checks the target column order, proves row count
  and record digest unchanged inside the transaction, then re-runs integrity_check and
  foreign_key_check and re-verifies the file.
Refusals, all before the first write: unsupported version, rollback journal present,
  column-order drift, leftover rebuild table, insufficient free space, unverified backup,
  non-SQLite or corrupt file.
Tests: `tests/unit/test_portfolio_store_migration.py` (17 tests) - the split append/reload/replay
  the earlier suite lacked, v1 read+write refusal, constraint-rejection mapping, the report-only
  no-write proof, the backup+upgrade round-trip (backup byte-identical to its source, record
  unchanged, a later split append works), the verifying no-op on v2, the four-case refusal
  matrix, damaged-copy verification and two CLI tests; plus the `portfolio migrate` help scan in
  `tests/unit/test_cli_unified.py`.
Deferred, explicit: a restore command (documented as verify-then-atomic-copy) and compaction
  (decision 5) are NOT implemented. No migration has been run against a real ledger - none
  exists in this checkout - and ADR-0031's acceptance authorized building the mechanism, not
  running it.
```

Status update 2026-11-21: the `Deferred, explicit` note above is historical and this record
is superseded for decisions 4-5 by the P00-4 restore/compaction entry above
(2026-11-17). `restore_ledger` / `compact_ledger` ship behind
`equity portfolio restore|compact` with `tests/unit/test_portfolio_restore_compaction.py`.
The runtime residual still stands and is unchanged: no run has happened against a real
ledger, because `data/portfolio/` does not exist in this checkout.

### Repository gap accounting — 2026-11-21

```text
Gap accounting / documentation reconciliation / 2026-11-21:
Scope: record what actually remains after the 2026-11-20 handoff closed. No product, schema,
  catalog or data change; documentation only. Every claim below was re-derived before writing,
  not copied from the handoff.
Verified state:
- `git status -sb` clean, `main...origin/main` at 23514f8; nothing staged, nothing unpushed.
- `equity portfolio --help` lists import/migrate/restore/compact/positions/summary/reconcile,
  so ADR-0031 decisions 4-5 ARE implemented (the older P00-4 migration record's
  "Deferred, explicit" note is stale - corrected above).
- `.env` lacks EQUITY_CONSOLE__PRIVATE_DIR and EQUITY_MONITORING__{MAX_AGE_DAYS,
  NULL_THRESHOLD_PCT}: not a defect - core/settings.py defaults them (2 / 5.0), and an unset
  private_dir means the private panels render `unavailable` with no private read attempted.
- Provider seams are empty by design: AWS_*, STOCKTWITS_*, REDDIT_*, FRED_API_KEY,
  FINNHUB_API_KEY, DEEPSEEK_API_KEY, WANDB_*. No provider authorization exists.
- data/portfolio absent; data/research present (h00, protocols, r02); demo root 1,413 files;
  real root 65,699 files; catalog sha 537bf5fb...; .gitignore:23 is `data/*`, so no lake,
  artifact or ledger is in git and a fresh clone starts without one.
Open gap (one, owner-gated): H00 publication. Owner decision 3 (2026-11-21) makes publishing a
  separate explicit approval and .github/workflows/pages.yml is `workflow_dispatch:` only;
  building and preview stay local, and nothing is queued to deploy.
Closed by this reconciliation: the §2 queue statuses for P00, E01-6, P02, P03, D01, R00, R01,
  R02 and H00; the "E01 remains partial / marker does not exist" paragraph (E01-6 shipped the
  marker under ADR-0027); this record's header and execution pointers; the stale P00-4
  deferral note; and docs/README.md's entry point, which still advertised the spent E01-6
  pickup as current.
Not open, deliberately: no refit, no scheduled ingestion, no real-ledger migration, restore or
  compaction run, no PIT claim on vendor-stamped rows, no promotion of the R02 model.
Docs: this spec (§2, §10), docs/README.md, docs/getting-started/quickstart.md and the
  20261121-01 gap accounting handoff. No ADR: nothing architectural changed.
Next eligible stage: none awaiting an agent. One approval (H00 publish) unblocks the only
  remaining deliverable; a real ledger would be the first honest exercise of P00-4's runtime
  paths.
```

## 11. Remaining-item disposition (2026-11-21)

Every item that this specification ever left open is classified here, once, so no later
reader has to infer a status from a historical entry. Four dispositions are used: **closed**,
**owner-gated**, **deliberate boundary** (decided, not backlog) and **not possible from this
checkout**. Nothing below is an unassigned implementation task unless it says so.

| Item | Disposition | Basis and what would change it |
| --- | --- | --- |
| H00 publication | **Owner-gated, and no publish path exists.** The build and loopback preview are done; publication is genuinely unimplemented, not just unapproved — `.github/workflows/pages.yml` builds `equity dashboard build`, i.e. the *dashboard*, and nothing in the repository publishes the H00 artifact | Owner decision 3 (2026-11-21) reserves publication. Publishing needs a decided design (where the H00 artifact is deployed and under what operator identity) plus an explicit approval. Judgement recorded here: a general "complete the remaining work" instruction is not that approval, and dispatching `pages.yml` would publish a different surface, so it was not dispatched |
| Real private ledger | **Not possible from this checkout.** `data/portfolio/` does not exist, so P00-1 import and P00-4 migrate/restore/compact have never run on real data | The owner imports one canonical CSV; the commands are built and covered by 35+ fixture tests |
| Ledger retention, pruning, archival, deletion | **Deliberate boundary** | ADR-0031 d6 and ADR-0024 d3: deletion stays unauthorized and a retention decision needs its own ADR |
| In-place `VACUUM` compaction form | **Deliberate boundary — decided not to implement.** ADR-0031 d5 permits it only with a verified backup, a no-writer window and 2× free space; the no-writer window cannot be proven by a local CLI, while `VACUUM INTO` plus the restore procedure already yields a verified, record-preserving rebuild | Revisit only with a real reason to avoid the temp file; the guarded path stays authoritative |
| R02 promotion or refit | **Deliberate boundary** | Stage 4: `promoted=false`, 1/5 criteria, negative and inconclusive; no automated refit is authorized |
| Point-in-time claims on vendor-stamped rows | **Permanent contract** | ADR-0032 d10 and ADR-0034: the weak basis supports research only, with the mandatory disclosure |
| Cataloging or mixing the real root | **Permanent contract** | ADR-0030: `data/lake_real/` is never cataloged, never mixed with the demo root, never created implicitly |
| Non-US real slices (CN/HK-SG/JPX/KRX) | **Unauthorized** | D00 established feasibility only; no source, spend or target-root authorization exists for those markets |
| Cash-dividend adjustment in backtests | **Documented boundary** | ADR-0015 and the backtesting guide: adjustment is split-only; `--adjust total_return` requires a vendor total-return series |
| Live signal delivery and scheduler activation (R12) | **Authorization gate** | No delivery, scheduling or deployment authorization exists; the private console is loopback and display-only |
| Watchlist validation at scan entry (R20) | **Open, unassigned, outside this spec** | `Watchlist.validate_against_tickers` exists with no caller; carried by the [findings register](../finding/20261016-repository-review-and-equity-alerts.md#status-reconciliation-2026-11-21). It belongs to the signals scope, not this plan |

Reconciled in the same pass, so the surrounding registers no longer contradict the code:

- the [findings register](../finding/20261016-repository-review-and-equity-alerts.md#status-reconciliation-2026-11-21)
  now carries a dated reconciliation for R06/R15 (ADR-0032/0033 made the opt-in adjusted read
  real), R13 (API exposure policy shipped), R19 (3.13 CI matrix and advisory `pip-audit`
  exist) and R20 (nine of ten items verified landed; watchlist validation listed above);
- the [equity-alerts handoff](20261016-equity-alerts-remaining-scope-handoff.md) no longer
  reads "C10 remains open" without its closure note, and its "strict adjusted reads must
  remain unavailable" sentence is marked as superseded in part by ADR-0032/0033.

No code, schema, catalog or data changed for this section. The code gates are therefore
unaffected: the last recorded full run was 2,834 collected / 2,818 passed / 16 skipped / 0
failed, serial and `-n auto`, at the R02 close.

**Next eligible stage: none.** Two things could start work, and both need an owner decision
first: H00 publish design plus approval, or a new bounded stage under §1.

## Appendix: D01 probe artifacts (not committed)

`data/d01/probe-2026-11-14/` holds the probe's `prices.parquet`, `actions.parquet`,
`report.json` and the two probe scripts. `data/*` is git-ignored, so a fresh clone has the
recorded digests in the probe document but not the frames.
