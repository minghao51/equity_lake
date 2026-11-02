# Portfolio and Research — Implementation Plan and Specification

**Recorded:** 2026-11-02
**Status:** Draft for owner approval; no stage below is implemented by this document.
**Purpose:** Personal portfolio monitoring and a defensible raw-data → structured-data
→ features → research → optional ML-signal workflow.
**Evidence:** [A01–A11 audit](../finding/20261102-product-readiness-audit.md).
**Execution:** [Worker/reviewer pickup](../handoffs/20261102-01-portfolio-and-research-pickup.md).

## 1. Product direction and authorization

Deliver useful portfolio accounting independently of whether ML has skill.
First repair research correctness, then implement a narrow private portfolio
slice and prove one real-data path. Only then evaluate strategies and incremental
ML value. Do not prioritize operational refits or widen the seven-day age cap.
Controlled historical fitting for an approved experiment is distinct from
production retraining and must use isolated artifacts.

This draft specifies proposed behaviour, not accepted new architecture. Before
code changes, the owner approves the selected stage and its proposed contract.
Acceptance of this plan does **not** automatically accept an ADR, choose a broker,
market/currency or accounting method, authorize source/spend/private-data access,
or activate delivery/scheduling/deployment. Architecture/storage/schema changes
require accepted ADRs and the change-matrix work in `AGENTS.md` first.

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

The audit baseline is `00dad39` plus a **dirty** accepted ADR-0023 implementation
and `.gitignore` checkpoint exclusion. Preserve that work. Its historical full
gates were 1739 passed / 17 skipped, serial and parallel, with clean ruff/mypy,
unchanged generated catalog and passing Markdown lint. The audit's separate
78-test selection passed, but four diagnostic counterexamples still reproduced.
Neither result establishes real-market performance. Re-establish actual baseline
and dependency groups in the pickup thread; never copy historical counts as new.

| Stage | Deliverable | Depends on | Entry approval | Initial status |
| --- | --- | --- | --- | --- |
| B00 | Freeze/review the carried working tree and record baseline | — | Owner-approved handling of existing changes | Pending |
| F01 | True fixed-share, identity-safe benchmark | B00 | Approve §3.1 | Pending |
| F02 | Instrument-local momentum lag and deterministic ordering | B00 | Approve §3.2 | Pending |
| F03 | Honest signed cost comparison | F01 | Approve §3.3 | Pending |
| F04 | Failure-isolated arena with truthful completion/exit policy | F01–F03 | Approve §3.4 | Pending |
| E00 | Research-artifact/reader compatibility ADR | F04 | Owner accepts new ADR, §4 | Decision blocked |
| E01 | Immutable runs, dated curves and latest/read compatibility | E00 | Approved implementation plan | Blocked by E00 |
| P00 | Private accounting ADR and broker format contract | F04 | Owner decisions in §5.1 | Decision blocked |
| P01 | Validated private ledger and idempotent import | P00 | Approved ADR; fixtures only | Blocked by P00 |
| P02 | Positions, cash, marks, P&L and reconciliation | P01 | Approved valuation/accounting policy | Blocked by P01 |
| P03 | Private Streamlit portfolio/readiness console | P02 | Approve private UI scope; research panel waits for E01 | Blocked by P02 |
| D00 | Offline source-feasibility and lineage decision packet | F04 | Approve investigation scope; no source calls | Pending after F04 |
| D01 | One authorized real-data vertical slice | D00 | Source/scope authorization and any required ADRs | Decision blocked |
| R00 | Synthetic controls and computational scale benchmark | F04, E01 | Frozen experiment/budget, §7 | Blocked by dependencies |
| R01 | Real-data baseline strategy evidence | R00, D01 | Frozen universe/window/holdout/costs | Decision blocked |
| R02 | Incremental ML experiment, not deployment/refitting | R01 | Frozen ML hypothesis/timing/promotion criteria | Decision blocked |
| H00 | Single sanitized public Pages deployment | E01 | Separate hosting/privacy decision | Deferred |

**Owner approval (2026-11-02 UTC):** B00 commit plan (b) — separate commits for the carried
ADR-0023 work and the planning docs, then one F01–F04 tranche commit after the full gates.
F01 fixed-share benchmark with the conservative complete-panel missing-mark policy; F02
instrument-local locality contract **plus the added deterministic tie policy (§3.2a)**; F03
signed cost-delta verdict with `1e-12` numerical tolerance; F04 zero/partial-run `exit 1` with
the `evaluated | no_trades | ineligible` vocabulary. The owner asked for the reproduced
rank-tie defect to be fixed now, inside F02, rather than deferred. B00 is done; F01–F04 are
approved for implementation in the order below; P00/E00/D00/R00 remain unapproved.

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
probe above must sum to `1.0` with two names at `0.5`, and must fail on the old
rank), `test_momentum_tie_break_is_identity_deterministic` (same returns, venues
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

At drafting all stage states remain as recorded in §2. No implementation or new
ADR has been accepted here. Before the first worker, record the owner's approval
(or requested amendment) of F01's fixed-share/missing-mark policy, F02's locality
contract, F03's signed-delta verdict, and F04's partial-run exit policy. Record
P00/E00/provider/experiment decisions separately when their stages become eligible.

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

Draft validation on 2026-11-02: `make lint-md` and `git diff --check` passed;
relative links in the three new documents resolved; the embedded Python
counterexample parsed; Decimal arithmetic confirmed the proposed accounting
hand oracle. No product tests were rerun merely for drafting. Independent
implementation review and fresh implementation gates remain future work.
