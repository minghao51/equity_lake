# Portfolio and Research — Implementation Plan and Specification

**Recorded:** 2026-11-02
**Status (updated 2026-11-06):** Active specification and outcome ledger. B00,
F01–F04, P00/E00/D00, P01a/P01b, E01a, E01b.1, E01b.2 and E01c are complete for
their recorded scope; whole E01 still needs E01-6. P02 is the next eligible
planning stage. Future implementation still needs its bounded approval gate.
**Purpose:** Personal portfolio monitoring and a defensible raw-data → structured-data
→ features → research → optional ML-signal workflow.
**Evidence:** [A01–A11 audit](../finding/20261102-product-readiness-audit.md).
**Execution:** [Current pickup and next-thread queue](../handoffs/20261107-01-portfolio-projections-and-open-items.md).
The [original Nov02 pickup](../handoffs/20261102-01-portfolio-and-research-pickup.md)
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

| Stage | Deliverable | Depends on | Entry approval / remaining gate | Current status (2026-11-06) |
| --- | --- | --- | --- | --- |
| B00 | Preserve/freeze carried ADR-0023 work | — | Completed separately | Complete: `1b380af`, `d85b561` |
| F01–F04 | Benchmark, momentum/ties, signed costs, arena completion | B00 | Approved and reviewed | Complete: `df4e423` |
| P00 | Private accounting/privacy ADR and account/import policy | F04 | ADR-0024 accepted; US/USD, canonical CSV only | Complete; future split and backup policies remain open |
| E00 | Research-artifact/reader compatibility ADR | F04 | ADR-0025 accepted; E01-1..5 frozen | Complete |
| D00 | US and CN offline feasibility packet | F04 | No source calls; adjusted/PIT evidence blocked | Complete: packet reviewed |
| P01a | Validated private ledger and atomic idempotent import | P00 | Fixtures only | Complete: `0f6d771` |
| P01b | Import CLI, help scans, user guide | P01a | P00-3 closed | Complete: `aeabe4b` |
| E01a | Dated evidence and immutable per-pair write side | E00 | Approved write-side scope | Complete: `603fc8d`; not full E01 |
| E01b.1 | Attempt records, last attempt/latest success, arena legacy-write removal | E01a | Owner-approved bounded plan (2026-11-06) | Complete (uncommitted): attempt dir, validated-run readers, frozen legacy view |
| E01b.2 | Migrate `equity report backtest` off the legacy layout | E01b.1 | Owner-authorized continuation (2026-11-06) | Complete (uncommitted): run + attempt evidence, no production legacy writer |
| E01c | Canonical universe identity and read-seam snapshot binding | E01b.1/E01b.2 | Owner-approved 2026-11-06: protocol v3, provenance stays `unknown` | Complete (uncommitted): protocol v3, universe basis, Delta/parquet read-seam snapshots |
| E01-6 | Persisted price-source marker (the only honest route to `real`/`synthetic`) | E01c | Needs its own ADR + storage/schema decision and backfill answer | Open; blocks declaring E01 complete |
| P02 | Positions, cash, marks, P&L, reconciliation and three read CLI commands | P01a/P01b | Approve bounded fixture-only plan under ADR-0024/§5.3 | Eligible for planning |
| D01 | Authorized US raw/unadjusted data slice | D00 | Candidate chosen; licensing/retention, source-call and target-root authorization still required | Decision gated |
| P03 | Private Streamlit portfolio/readiness console | P02 | Approve private UI scope; research panel waits for E01 | Blocked by P02 |
| R00 | Synthetic controls and computational scale benchmark | F04, completed E01 | Frozen experiment and numeric resource budget, §7 | Blocked by dependencies |
| R01 | Real-data baseline strategy evidence | R00, D01 | Frozen universe/window/holdout/costs | Decision gated |
| R02 | Incremental ML experiment, not deployment/refitting | R01 | Frozen ML hypothesis/timing/promotion criteria | Decision gated |
| H00 | Single sanitized public Pages deployment | E01 | Separate hosting/privacy decision | Deferred |

The in-session todo list is a convenience, not the cross-thread source of
truth. Use this table, §10 and the current handoff. Whole E01 remains partial
until E01c (and any further identified write-side gaps) satisfy ADR-0025; the
provenance upgrade needs a persisted price-source marker that does not exist yet.

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
Residuals, explicit stop condition: stage 1's populated-ledger render proof is deferred
  (the current no-write proof covers tree byte-identity and the unreadable-ledger case);
  the console is not an authenticated service and must not be exposed beyond loopback.
  D01, P00-2/P00-4, R00-R02 and H00 remain gated.
```
