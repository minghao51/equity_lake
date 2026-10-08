# DAG Pipeline Remediation — Agent Dispatch Prompts

**Recorded:** 2026-10-08

**Purpose:** self-contained, copyable prompts for dispatching one agent per work
unit of the [DAG pipeline remediation](20261006-dag-pipeline-00-handoff-index.md)
on a new thread. Each prompt assumes no conversation history and references the
canonical documents rather than duplicating them.

## How to use

- **Sequence:** dispatch one unit at a time. Shared files (`pipeline.py`,
  `writers.py`, `features/engineering.py`, generated catalog) make uncoordinated
  parallel workers unsafe. W03 PR1→PR2→PR3→PR4 in order; then W04, W05, W06,
  W07. Exactly one agent regenerates the catalog per integrated unit.
- **Agents:** implementation work → the `worker` agent
  (`opencode-go/deepseek-v4.1-flash`). Review → a read-only reviewer pinned to
  `opencode-go/mimo-v2.6-pro` (create a temporary project agent at
  `.pi/agents/<name>.md` with `model: opencode-go/mimo-v2.6-pro` and
  `tools: read, grep, find, ls, bash`, then delete it after the review).
- **Approval gates are real:** a prompt that says STOP must stop and return a
  decision packet instead of editing code.
- **No commit without explicit authorization.** Each prompt ends by returning
  the delivery packet; the coordinator decides on committing.
- **Baseline facts (do not re-litigate, do not bundle):** 3 `test_arena.py`
  failures and 3 `mypy` errors in `ml/registry.py` are pre-existing and owned by
  W07/F28; `SKIPPED_EXISTING` zero-fill schema parity in `enrichments_04` is
  intentional (handoff-08 A5).
- **Current state:** W01 landed (`e44bd5d`, `2906477`, `da71d54`), W02 landed
  (`8229eda`, `a78a6e8`), D02 approved, **D03/D04 approved and ADR-0014 accepted
  (`b815000`)**. Next free ADR number: **0015**.

## Shared preamble (prepend to every implementation prompt)

```text
You are implementing ONE unit of the DAG Pipeline Remediation in the
equity_lake repository. Work from the repository root.

Read, in order, before proposing anything:
1. AGENTS.md (project contracts; decision order)
2. docs/README.md (documentation map)
3. docs/finding/20261006-dag-pipeline-audit.md (findings + resolution register)
4. docs/plans/20261006-dag-pipeline-00-handoff-index.md (waves, decisions,
   non-negotiable constraints, delivery-packet format)
5. The wave page and any ADR named in your unit prompt
6. The current source you will touch (re-read it; audit line numbers are
   navigation aids, not patch instructions)

Verify your entry conditions before any edit and stop if incomplete. Then
present a scoped code-change plan for approval before editing. Change as little
as possible; no new abstractions, frameworks, registries, schedulers, or domain
trees. Keep Polars-first, native Typer, tenacity retries, the numbered medallion
layout, five fixed price markets, and import boundaries intact.

Hard constraints: never run live sources, paid LLM work, production repair,
migrations, vacuum, or retraining as default validation; mock provider/LLM/model
boundaries and use temporary lake paths; dry-run must never persist, process,
compute features, or infer; do not stage or commit unrelated files, generated
site output, runtime data, logs, credentials, or caches; do not commit without
explicit authorization.

After the code changes: run your unit's focused commands and the shared offline
gates from the handoff index (`pytest -m "not network and not integration"`,
`-n auto`, `ruff check`, `ruff format --check`, `mypy src`,
`uv run equity catalog-generate` where a pipeline/feature/schema change
requires it, `mkdocs build`, `git diff --check`). Inspect integration-test mocks
before running them.

Return the delivery packet specified in the handoff index: finding IDs
addressed; files actually changed; policy/decision links; new regression tests
with proof they detect the old defect via `git show HEAD:<path>`; exact
commands with pass/skip/deselection counts; catalog diff evidence;
compatibility and rollback notes; remaining findings and the next unit's entry
conditions. Update the wave progress, audit resolution register, and index
ledger with evidence (not merely completed edits). Then run a read-only
reviewer pass and address critical/warning findings before handing off.
```

---

## P1 — W03 PR1: exact scope and ticker-aware completeness

**Entry:** W02 landed (`8229eda`, `a78a6e8`); D03/D04 approved and ADR-0014
accepted (`b815000`).

**Wave page:** `docs/plans/20261006-dag-pipeline-03-incremental-contracts.md`
(sections A and B). **Findings:** F04, F05, and the recovery-detail part of F25.

```text
<shared preamble>

Unit: W03 PR1 — explicit scope through all five price markets + ticker-aware
existing-data and recovery reporting.

Confirmed current defects (re-verify in source):
- ingestion/orchestrator.py EXPLICIT_TICKER_MARKETS covers only us_equity plus
  Finnhub/SEC markets; CN/HK-SG/JPX/KRX never receive explicit_tickers.
- ingestion/router.py _make_cn_fetcher/_make_hk_sg_fetcher/_make_jpx_fetcher/
  _make_krx_fetcher ignore the explicit_tickers argument; sources/cn_hybrid.py
  CNHybridFetcher.__init__ has no ticker parameter, so primary and fallback
  cannot be scoped.
- Factories use `if not explicit_tickers:` so an explicit empty list broadens to
  configured/US defaults instead of staying empty.
- orchestrator._filter_markets_with_gaps checks only date presence plus a
  LIMIT 100 schema sample; one valid row suppresses a needed scoped fetch.
- ingestion/backfill.py backfill_date_range returns one int (total_dates) and
  discards per-market/date attempted/succeeded/failed and recovered-date detail.

Plan (confirm with the owner, then implement):
1. ingestion/types.py: add `resolve_explicit_scope(markets, tickers,
   ticker_config) -> dict[str, list[str] | None]`. None -> all markets None
   (configured default). Exactly one requested price market -> every explicit
   ticker assigned to it (preserves arbitrary overrides, extended to all five).
   Multiple markets -> resolve membership via TickerConfig.get_ticker_metadata;
   unresolved or ambiguous tickers raise ValueError listing them (no fan-out).
   [] is preserved and never becomes None. Ticker-scoped enrichment markets keep
   today's behavior.
2. ingestion/orchestrator.py: resolve scope once, before the fetch and recovery
   loops; pass the per-market list to each fetch; extend
   EXPLICIT_TICKER_MARKETS to the added price markets; keep serial/parallel
   isolation and W02 outcome semantics; record resolved scope in the stage
   payload/logs.
3. ingestion/router.py: forward explicit_tickers in the CN/HK-SG/JPX/KRX
   factories (split HK/SG into hk_tickers/sg_tickers from the market-scoped
   list); change every `not explicit_tickers` fallback to `is None`.
4. sources/cn_hybrid.py: accept `tickers` and forward the same effective list to
   the primary and fallback fetchers.
5. ingestion/backfill.py: return a small frozen BackfillResult dataclass with
   the frozen scope (range, markets, instruments, authorization) and
   trading_dates / market_date_attempts / succeeded / failed / dates_recovered /
   per_market, keeping a `total_dates` property for existing callers.
6. pipeline.py: carry the structured recovery result (do not discard it);
   post-recovery missing coverage still fails through W02's coverage check.
7. orchestrator existing-data check: consolidate into one bounded scoped read
   for the partition/date, then assert schema validity AND requested-ticker
   presence (reuse validate_schema / write-contract semantics). Complete scope
   -> SKIPPED_EXISTING; otherwise fetch the missing scoped instruments.
8. cli/commands/data.py: print the richer backfill counts; no new flags.

Tests (use temporary Delta tables and mocked fetchers; no live sources):
- Five price-market factories receive the exact scoped tickers (capture
  constructor args); CN primary and fallback receive the same list.
- None vs [] preserved; empty explicit list never broadens; ambiguous
  multi-market list rejects with an actionable error and performs no fetch.
- One ticker present + one absent in an existing partition -> not skipped,
  scoped fetch targets the missing ticker; late-added ticker covered.
- Partial history with and without --allow-history-backfill; approved recovery
  targets missing scope only.
- Zero/partial recovery reports counts and never asserts success; missing
  coverage after recovery fails.
- Direct backfill/ingest dry-run writes nothing.
- Prove each new test fails on the pre-change source via `git stash -- src/`.

Out of scope: market-qualified identity / ambiguous bare-symbol API (W04/D05),
per-exchange HK/SG session mapping and not_scheduled (W03 PR3), label/schema
work (W03 PR2/ADR-0014), storage evolution hardening (W06). No new CLI flags;
no ADR required (operational contract); no lake migration.

Suggested commit split: scope plumbing + recovery result in one commit; the
ticker-aware existing-data check as a second small commit if review is easier.
```

---

## P2 — W03 PR2: label lifecycle and availability (ADR-0014)

**Entry:** PR1 landed on local `main`.

```text
<shared preamble>

Unit: W03 PR2 — label finalization, availability metadata, and as-of cutoff,
implementing the accepted ADR-0014 (docs/decisions/0014-label-lifecycle-and-
availability.md). Read that ADR first; it is binding. Findings: F03, the label
portion of F17, and the label-carrying part of F25.

Current defect: features/__init__.py writes only the requested output window, so
the newest target is null and the preceding session's label is never revisited.
features/dag/features_03.py derives the target from the next observation.

Plan:
- Finalize only the preceding observed session's label fields on session D,
  using only information available by D. Keep existing target column names and
  semantics (no separate label table).
- Add `label_available_date: Date` to the Gold contract (null/unknown for legacy
  rows; never fabricate timestamps).
- "Next observation" is the next exchange session with a valid bar for that
  instrument; missing next-session bars leave the label pending and reported.
- Implement the update as a bounded, key-scoped, label-only update through the
  canonical storage seam: no nulling/shrinking unrelated feature columns, no
  schema evolution, no insertion of nonexistent keys. If the existing merge
  cannot express a matched-column update safely, add the smallest seam needed
  and hand hardening to W06 (do not build a framework).
- Enforce as-of eligibility in training/backtest/model loaders: exclude labels
  whose label_available_date is after the cutoff; unknown-availability legacy
  rows are strict-excluded by default. Exclude availability/label metadata from
  predictor matrices.
- compute_target=False performs no finalization. Historical completion is an
  explicit label-only path.
- Bump FEATURE_SCHEMA_VERSION together with catalog regeneration, reader
  compatibility, and model feature-list exclusion (installed models must not
  treat the new column as a predictor).
- Provide a bounded, report-first repair path for existing unlabeled rows
  (native Typer + help-scan test + user docs if a new command is added); broad
  repair requires separate owner authorization (D09). No automatic repair.

Tests (multi-session fixture with real temporary Delta writes, not mocked row
counts): three EOD sessions finalize prior labels and leave the newest pending;
Friday->Monday and exchange-holiday next-session behavior; missing
next-session bar stays pending; historical training as-of before availability
excludes the later-finalized target; rerun/replay idempotent with stable prior
feature values; label-only update does not null feature columns, shrink schema,
or add rows; legacy unknown-availability reader behavior; dry-run finalizes
nothing.

Docs: ADR-0014 is the decision record; update pipeline contracts, data-flow,
features/ML guides, catalog definitions, and the migration/repair note. No
production repair or retraining.

Out of scope: session policy (PR3), corporate-action overlap (PR4), identity
(W04), storage evolution hardening (W06).
```

---

## P3 — W03 PR3: market-session policy (D04)

**Entry:** PR1 (and ideally PR2) landed.

```text
<shared preamble>

Unit: W03 PR3 — explicit-date session scheduling with a distinct not_scheduled
outcome (D04, recorded in the W03 page and handoff index). Find: F11.

Plan:
- Add a `not_scheduled` source status/outcome distinct from NO_DATA/FAILED.
- On an explicit requested date, classify each market scheduled/not_scheduled
  using its exchange calendar (core/calendar.py is_trading_day, union semantics
  for hk_sg_equity). A closed market is not_scheduled, never failed and never
  substituted with an earlier session.
- Features and ML are planned only for scheduled scope; a closed market does not
  block markets that are open. Exit is zero when all scheduled required work
  succeeds. A genuinely empty fetch on a scheduled required session remains a
  failure (this confines W02's required NO_DATA blocking to true data absence).
- For relative dates, resolve the plan date once and classify each market;
  per-market "latest session" dates are out of scope (would need a separate
  accepted design).
- Document the HK/SG union limitation: scheduled if either exchange is open,
  per-instrument coverage reported; do not use intersection to skip an open
  instrument.
- Dry-run reports planned vs not_scheduled scope and writes nothing.

Tests: US holiday while another requested market is open (closed market
not_scheduled, open required work remains eligible, exit zero); explicit date
for a closed market; scheduled session with no data still fails; HK/SG mixed
session; dry-run planned scope; CLI exit matrix updated for not_scheduled.

Docs: pipeline contracts (status vocabulary + exit policy), data-flow, pipeline
and ingestion user guides, calendar limitations. No ADR (operational contract).
Out of scope: per-market latest-session dates; identity; label work.
```

---

## P4 — W03 PR4: corporate-action overlap

**Entry:** PR1 landed.

```text
<shared preamble>

Unit: W03 PR4 — per-ticker action watermarks or an approved bounded overlap.
Find: the overlap/watermark portion of F14 (simultaneous-event mapping was fixed
in W01). ADR-0011 is binding: keep adjustment opt-in deferred and US-only
support unchanged.

Current defect: ingestion/corporate_actions.py uses a market-wide max ex_date
and the fetcher accepts only dates strictly above it, so a newly scoped ticker,
a late correction, or an older event can be lost because of a recent event on a
different ticker.

Plan: replace the market-global exclusive watermark with per-ticker scoped
watermarks, or an explicitly bounded overlap window (owner-approved). Include
late corrections inside the overlap and rely on the (ticker, ex_date, action)
natural-key idempotency. New tickers get their own history. Surface provider
limitations outside the overlap; do not promise unlimited correction detection.
Do not alter feature prices, add sources, or extend to other markets.

Tests: new action ticker not suppressed by another ticker's recent event; late
correction within the overlap is picked up; overlapping rerun is idempotent;
vendor-shaped fixtures through the quality gate.

Docs: ingestion guide + auto-backfill/actions architecture notes.
```

---

## P5 — W04: instrument identity and temporal provenance

**Entry (HARD GATE):** W03 landed **and** an accepted D05 identity ADR and D06
temporal/missingness ADR exist. If either is missing, STOP and return a decision
packet with the recommended ADR text — do not edit code.

```text
<shared preamble>

Unit: W04 — instrument identity (market-qualified) and temporal provenance.
Findings: F12, F16, F17. Wave page:
docs/plans/20261006-dag-pipeline-04-schema-and-provenance.md.

STOP CONDITION: this is a schema/compatibility/migration wave. Do not edit code
until the D05 identity and D06 temporal ADRs are accepted and recorded (next free
ADR numbers start at 0015). If they are not, produce the ADR drafts and a
migration decision packet and stop.

When unblocked, the wave page is the plan: propagate (market,ticker,date)
identity through computation, Gold/Platinum keys, loaders, models, signals,
backtesting, API, dashboard, and CLI; group rolling/shift/dedupe by identity;
keep identity/provenance out of predictor matrices; correct backward-asof
carried state for SEC/macro with explicit availability/staleness and no future
leakage; standardize EWMA timebase; reconcile FEATURE_SCHEMA_VERSION, catalog,
model feature lists, and input snapshots.

Produce a checked reader/writer inventory in the PR. Migration is inventory and
test-on-copy first: derive market only from reliable provenance, report
ambiguous rows rather than guessing, define snapshot/backup/reader-rollout/
verification/rollback order, and require separate owner authorization for any
production execution. No automatic retraining or history rewrite (D09). Keep
US-only enrichment attribution explicit; do not join US enrichment to a
same-named instrument in another market.

Tests are listed in the wave page (same ticker in two markets, enrichment
non-crossover, ambiguous bare symbol fails, legacy fixture compatibility,
migration idempotency/rollback, asof carry-forward and future invisibility,
macro non-feature-date carry, missing vs neutral vs stale vs disabled, EWMA
golden cases, labels/provenance never predictors, dry-run no writes).
```

---

## P6 — W05: catalog and operational truth

**Entry (HARD GATE):** W03/W04 landed and the D07 drift policy is approved.
Recommended D07 default: explicitly disable automatic drift detection at the
writer boundary and document that no baseline is installed there.

```text
<shared preamble>

Unit: W05 — catalog physical truth, real lineage, market-aware health checks,
and explicit drift policy. Findings: F18, F19, F23, F24. Wave page:
docs/plans/20261006-dag-pipeline-05-catalog-and-monitoring.md.

Split into separate commits: catalog, health, drift. Only one agent regenerates
data/catalog.jsonl (via `uv run equity catalog-generate`).

Catalog: describe actual physical writers (table vs market-child collection,
paths, partitions incl. ex_date for corporate actions, natural keys, real
dtypes, required/optional enrichment columns, target/availability/identity
additions from W03/W04, prediction mode/version metadata). Replace all-to-all
adjacent-layer lineage with actual dataset consumption; distinguish physical
inputs, DAG computation dependencies, optional dependencies, and transitive
ancestry; keep frontend compatibility and test serialization determinism.
Health: compare queried markets/identities against expected scheduled scope
(GROUP BY over a filtered window cannot yield a zero-row group); isolate broken
market scans; check requested instrument coverage/freshness, not one row per
market; apply documented null/invalid thresholds consistently; scope alert
deduplication across runs; preserve JSON metrics readers.
Drift: implement the approved D07 policy; if baselines are retained, they are
auxiliary artifacts outside the lake with Pydantic write boundaries and Settings
models; test the deployed path, not an unrelated in-memory instance.

Tests are listed in the wave page (metadata/partition/key/dtype, real output
frames, true/absent lineage edges, DAG topology agreement, serialization,
frontend, no-recent-rows, one broken market, session/scope, repeated monitor
run, drift enabled/disabled, dry-run profiles).
```

---

## P7 — W06: storage safety and bounded processing

**Entry (HARD GATE):** W02/W04 landed (normally after W05) and D08
(processing budget) is approved.

```text
<shared preamble>

Unit: W06 — natural-key enforcement, safe Delta evolution, bounded Bronze→Silver
processing. Findings: F20, F21, F22. Wave page:
docs/plans/20261006-dag-pipeline-06-storage-and-processing.md.
D08 and the approval gates in the wave page must be settled first; stop and
return a decision packet if they are not.

Storage: reject null/invalid natural keys and within-batch duplicates before
create or merge, with actionable diagnostics, using the existing key registry;
reconcile required columns with actual frames where enforcement reveals a
mismatch; preserve intentional nullable article attribution with an idempotent
keyed upsert (no ad hoc ticker sentinel without approval); classify schema
incompatibility specifically instead of substring matching; allowed evolution is
explicit additive/widening only; never rewrite full history on a non-schema
error; test create/merge paths, evolution failure states, and concurrent
interleaving against captured snapshots; preserve W03 label-only matched
updates.
Processing: select unprocessed Bronze rows with pushdown/anti-join instead of
materializing the whole corpus; a failed Silver dedup read blocks paid
processing and never resets to an empty processed set; apply approved
source/date/ticker filters and deterministic ordering with a budget before
process_fn; track selected/processed/persisted/rejected/skipped/remaining
separately; no eligible work is an intentional successful no-work outcome;
checkpoint only persisted completion; add Settings models + .env.example entries
together for any new limits; dry-run cannot process or persist.

Tests are listed in the wave page (null/malformed/duplicate keys, market-wide
article idempotency, allowed/disallowed evolution, non-schema error, failed
evolution state, time travel before vacuum, bounded selection vs corpus growth,
failed Silver read, no-work outcome, budget/deterministic order, fallback retry
eligibility, dry-run planning).
```

---

## P8 — W07: DRY, measured optimization, release closure

**Entry:** W01–W06 integrated.

```text
<shared preamble>

Unit: W07 — trustworthy gates, evidenced DRY, measured optimization, and final
closure. Findings: F26–F28 plus final closure evidence for every finding. Wave
page: docs/plans/20261006-dag-pipeline-07-maintainability-and-validation.md.

This wave owns the pre-existing baseline failures: the 3 test_arena.py failures
and the 3 mypy errors in ml/registry.py.

Gates: audit every test that can reach a provider/API key/LLM and apply explicit
network markers; make the default pytest selection exclude network/serial as
documented; add meta-tests proving default/network-opt-in/offline-integration/
serial/parallel selection; keep format checking in CI; fix the W&B typing
mismatch without blanket ignores or making W&B mandatory; audit optional-backend
skips; keep tenacity retries.
DRY: replace hardcoded market-view paths with core/paths.py + W04 identity; use
one schema definition with consistency tests; reuse the existing driver
assembly; remove duplicated normal/recovery feature arguments; standardize
reason codes/status helpers; remove genuinely dead imports only after checking
callers. Behavior-preserving and testable.
Optimization: measure before optimizing with deterministic synthetic benchmarks
(environment, dependency versions, input sizes, wall time, memory, row counts);
partition once by approved identity instead of per-ticker filtering; query only
planned scope with parameter binding; measure W06 bounded selection vs full
corpus; measure driver build vs indicator work; keep caches disabled until keys
cover snapshots/availability/schema/config. Deliver before/after tables with
value/schema parity; retain the simpler path when there is no measured benefit.
Closure: run the full end-to-end mocked matrix from the wave page (three
consecutive sessions, mixed markets, recovery, dry-run, optional degradation,
required failure) and close every finding ID with evidence or an owner-approved
deferral; update the audit resolution register and the index ledger.

Optional early preflight: the test-marker/default-selection and W&B typing fixes
may be dispatched as a standalone PR before the rest of W07 (owner decision).
Do not bundle optimization with correctness fixes.
```

---

## R — Reviewer prompt template (read-only)

Run after each unit's code/tests are complete and before handoff. Create a
temporary project agent pinned to `opencode-go/mimo-v2.6-pro`; delete it
afterwards.

```text
You are performing a read-only verification of <unit> in the equity_lake
repository. Never edit or create files; use bash only for read-only inspection
(git status/diff/show, grep, find, ls, sed). Do not run builds or tests.

Read AGENTS.md, the handoff index, the unit's wave page, the relevant ADRs, and
the current source in your scope. The uncommitted working tree is the change;
`git diff` and `git show HEAD:<path>` show old vs new.

Judge only against the unit's required outcomes, its approval decisions, and the
shared non-negotiable constraints. Do not re-litigate earlier waves or request
out-of-scope future-wave work. Distinguish verified defects from residual risk.

Report:
1. Verdict: READY / READY WITH WARNINGS / NOT READY.
2. Critical findings (blocks handoff) with file:line, exact evidence, impact.
3. Warnings with file:line and the minimal fix.
4. Confirmations: which required outcomes you verified in code/tests.
5. Residual risks / accepted limitations to record.

Open every read-only review with an explicit scope (the files and questions you
will judge) and keep it bounded. Do not accept "tests pass" as proof that the
missing contracts are covered.
```

Suggested review scopes per unit (mirroring W01/W02):
- W03 PR1: scope resolution/None-vs-empty, per-market factory plumbing, ticker-aware existing-data/recovery counts.
- W03 PR2: label semantics/availability/as-of cutoff, label-only update safety, predictor exclusion, legacy handling.
- W03 PR3: session classification/exit policy, mixed markets, no false coverage claims.
- W03 PR4: watermark/overlap correctness and idempotency.
- W04: identity propagation/reader inventory/migration safety/temporal leakage.
- W05: catalog metadata truth/lineage semantics/health reachability/drift policy.
- W06: key enforcement/evolution classification/bounded processing/no-work.
- W07: gate selection correctness/DRY parity/measured claims/closure evidence.
