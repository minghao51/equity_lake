# W02 — Truthful Outcomes and Requested Coverage

**Status:** Landed — `8229eda` (implementation + tests), local `main`, unpushed

**Priority:** P1 first

**Depends on:** [Index and D02](20261006-dag-pipeline-00-handoff-index.md)

**Findings:** F06–F10, F25

**Next:** [W03](20261006-dag-pipeline-03-incremental-contracts.md)

## Objective

Make stage success mean what operators think it means: required work persisted
for its approved scope, optional failures remain visible, and one market's
exception does not erase other outcomes. Preserve the existing stage-dictionary
API and CLI policy; do not introduce an orchestration framework.

## Read and own

Read current `pipeline.py`, `ingestion/{types,orchestrator,router,parallel,writers}.py`,
`features/{__init__,engineering}.py`, `ml/__init__.py`,
`cli/commands/{pipeline,data}.py`, dashboard pipeline-result readers, monitoring
readers, and canonical pipeline contracts. Paths are under
`src/equity_lake/` unless stated otherwise. Read existing source-outcome,
orchestrator, parallel, feature-job, ML-job, unified CLI, and mocked integration
tests. Use the verify-pipeline skill.

Own orchestration/outcome files, necessary feature coverage reporting, result
readers/tests, and user guides. Do not change shared dataset identity, temporal
join semantics, or Delta evolution here. Those belong to later waves.

## Decisions before code

- D02 recommendation: resolve all configured active tickers in requested price
  markets, without the implicit ten-ticker cap. If the owner retains a limit,
  make it explicit/configurable and expose the resolved limit in results.
- Missing scheduled required ticker coverage fails the feature stage; successful
  tickers and reasons are still reported. Optional enrichment loss remains
  partial success with exit zero if required work succeeds.
- `None` means unspecified scope, while an explicit empty list must not silently
  broaden scope. Recommend rejecting an empty required scope with a clear error.
- Decide empty optional-fetch versus failed optional-fetch representation. Both
  must differ from a genuine already-present partition. No-work Silver processing
  is not a source failure; unreadable Silver is not no-work.
- For enrichment-only pipeline requests, recommend planned skipping of feature
  and ML stages when no price scope is requested, rather than invented default
  tickers or a missing-history error. Document this CLI behavior change.

## Implementation checklist

### A. Preserve truthful source outcomes

- Optional source exceptions remain failures in `SourceOutcome`; optionality is
  applied by the pipeline when deciding blocking/exit status.
- Extend the existing enum only as needed for explicit empty/no-work/dry-run
  outcomes. Reserve `SKIPPED_EXISTING` for a verified existing dataset scope.
  W03 owns session-aware `not_scheduled` behavior.
- Stop swallowing source exceptions into None without cause. Reuse current
  outcome/error seams or let the existing parallel wrapper capture exceptions.
  Do not add an unrelated fetch-result hierarchy.
- Expose status and reason/error details per source while retaining the current
  `markets` status map for compatible consumers. Audit all readers before adding
  or changing serialized fields.
- Keep required/optional classification in existing registries, not duplicated
  constants. Aggregate from actual final outcomes, including write failures,
  rather than treating successful fetch as successful ingestion.

### B. Equal isolation in serial and parallel paths

- Handle per-market conversion/validation/write exceptions identically in both
  modes and retain all other markets' outcomes.
- Handle skip-existing/read failures as bounded source errors rather than a
  pipeline traceback; do not suppress them into unjustified success.
- Keep structured root cause and stage timing. Avoid logging secret-bearing
  request payloads; errors should be actionable but sanitized.
- Review correlation IDs across thread workers: ContextVars are not automatically
  propagated by ThreadPoolExecutor. If correcting this, copy context per task
  using existing logging utilities, and test isolation between separate runs.

### C. Scope and coverage

- Remove the implicit `[:10]` only after D02 approval. Deduplicate requested
  tickers deterministically; preserve resolution origin and explicit scope.
- Carry requested scope unchanged across feature computation and retry. A
  narrower output must not replace the record of what was requested.
- Report requested/produced/missing tickers and reason counts, including no price
  rows, insufficient valid history, missing output-date bar, invalid output,
  intentional limit, and later W03 session exclusions.
- Preserve the public `run_feature_job()` DataFrame contract if possible; expose
  diagnostics through a small compatible return/result seam rather than a new
  feature framework. If the API must change, update every caller and tests in
  the same PR and document the compatibility decision.
- Do not infer success from row count alone. Inference receives only approved
  eligible outputs; required missing coverage fails predictably and is visible.
- W03 will make source completeness ticker-aware. This wave must not claim that
  a date-level skipped partition already proves complete feature history.

### D. Persistence, dry-run, and recovery detail

- Differentiate computed prediction/feature success from successful persistence.
  If the batch write fails, do not leave per-ticker results claiming persisted
  success. Preserve prediction diagnostics without overstating durability.
- Direct ingest/backfill dry-run results must not say written; the pipeline's
  existing skip-all dry-run remains side-effect free.
- Preserve enough result space for W03 backfill attempts, recovered scope,
  failed dates, and retry outcome. Do not discard recovery return values.
- A new durable run manifest is optional, not required for this fix. If introduced,
  store it outside the lake and validate with Pydantic at its write boundary.
  Update existing result readers instead of creating a second competing manifest.

## Required result invariants

- Every returned stage has `success`; deliberate skips have stable reason codes.
- Every requested source receives an outcome even if another source fails.
- Optional failure appears in `optional_failures` and `partial=true`, while
  required success can retain exit zero.
- Required failure or required coverage failure prevents dependent output.
- Existing-data skips, no-work, no-session, dry-run, empty fetch, failed fetch,
  and failed write are not conflated.
- Public compatibility fields remain usable; avoid coupling CLI success logic
  to an unshared free-form error message.

## Required tests

1. Parallel two-market fetch: one write raises; the other writes and both outcomes
   survive. Repeat serially; statuses/causes are equivalent.
2. Optional timeout, missing key, schema rejection, and empty fetch are distinct
   and visibly degrade the run without blocking independent core computation.
3. Required source failure blocks features and ML; missing outcome is failure.
4. Ten-plus configured tickers across markets: approved scope is not silently
   sliced to the first market. Results contain the exact resolved scope.
5. Three requested tickers with one insufficient history and one missing current
   bar: all exclusions reported; approved required-coverage policy enforced.
6. Prediction batch validation/write failure: no persisted-success claims.
7. Enrichment-only invocation and explicit empty scope follow approved policy.
8. Dry-run with save-results and missing history calls no write-capable stages;
   direct ingest/backfill dry-run does not report written.
9. Existing pipeline JSON consumers accept added fields; result round-trip and
   deterministic ordering are tested.
10. CLI exits: full success, optional degradation, required source failure,
    required coverage failure, explicit skips, and dry-run.

Use actual feature output fixtures from W01, not only mocked row counts.

## Focused commands and accompanying docs

```bash
uv run pytest tests/unit/test_ingestion_types.py tests/unit/test_ingestion_orchestrator.py tests/unit/test_parallel.py tests/unit/test_router.py -m "not network and not integration"
uv run pytest tests/unit/test_feature_jobs.py tests/unit/test_ml_jobs.py tests/unit/test_unified_pipeline.py tests/unit/test_cli_unified.py -m "not network and not integration"
uv run pytest tests/integration/test_pipeline_orchestrator.py -m "not network"
```

Inspect the last file's mocks before execution. Run shared offline gates and
regenerate catalog for pipeline changes. Update canonical pipeline contracts,
data-flow, pipeline/ingestion user guides, CLI reference/help-scan tests for any
new options, and monitoring/dashboard result documentation where consumed.

## Acceptance and deployment

- [ ] Outcome invariants hold in serial, parallel, and direct CLI paths.
- [ ] Exact resolved scope and omissions are observable.
- [ ] D02 approval and compatibility decisions documented.
- [ ] Published result dictionary remains compatible or has an approved migration.
- [ ] No required failure is hidden behind a narrower successful output set.
- [ ] No dry-run persistence or inferred unapproved scope.
- [ ] Delivery packet, audit register, and ledger updated.

Deploy only after readers understand added statuses/fields. Roll back code and
reader changes together; no lake migration is expected in this wave. Any newly
requested scope that increases ingestion/inference cost must be disclosed in
release notes before scheduled jobs are enabled.

## Progress

- Implementation: **complete and landed (2026-10-08)** as `8229eda`
  (implementation + tests) on local `main` (unpushed); this documentation
  commit records the delivery evidence.
  1. **Truthful source outcomes (F08/F10/F25).** `SourceStatus` gains `NO_DATA`
     and `DRY_RUN`; `SKIPPED_EXISTING` is reserved for a verified existing
     partition. Optional failures map to `FAILED` with their cause and are
     classified by the pipeline (`optional_failures` + `partial=true`), never
     hidden as success-shaped skips. The router no longer swallows fetch or
     schema-rejection errors into `None` (unknown markets raise, mirroring
     `normalize_markets`; empty fetch returns the empty frame). The ingestion
     stage publishes `errors` alongside the retained `markets` status map.
  2. **Equal serial/parallel isolation (F09).** Both branches share
     `_finalize_market`; write/conversion failures are isolated per market with
     the root cause retained and every sibling outcome preserved. Correlation
     IDs are copied per task (`contextvars.copy_context()`) in both parallel
     helpers, with run/sibling isolation tests.
  3. **Scope (F06, D02).** The hidden `[:10]` cap is removed;
     `_resolve_configured_tickers` resolves all configured active tickers of the
     requested price markets, deduplicated deterministically. Explicit scope is
     deduplicated and preserved; an explicit empty scope raises at the API and
     exits 1 at the CLI, never broadening. Enrichment-only requests planned-skip
     features/ML (`enrichment_only_no_price_scope`). The resolved scope is
     published under `results["scope"]`.
  4. **Coverage (F07).** `run_feature_job` keeps its DataFrame contract and
     accepts an optional `diagnostics` out-dict; per-ticker exclusions carry
     stable reasons (`no_price_rows`, `insufficient_history`, `invalid_output`,
     `missing_output_bar`; `intentional_limit` reserved). `features.coverage`
     reports requested/produced/missing; missing required coverage fails the
     feature stage while produced tickers and reasons are still reported, and
     ML is not run. A `run_feature_job` write failure keeps the computed
     coverage instead of fabricating data-gap reasons.
  5. **Persistence/dry-run/recovery (F25).** Direct ingest/backfill dry-runs
     report `DRY_RUN` (never `written`); per-ticker ML results carry `persisted`
     (true only after the batch write lands); the recovery date count is
     recorded (`history_backfilled_dates`); bronze/SEC no-work is a planned skip
     (`no_bronze_work`/`already_processed`) while real failures keep their cause.
  - Files: `ingestion/{types,orchestrator,router,parallel,bronze_silver,sec_processor}.py`,
    `features/{__init__,engineering}.py`, `ml/__init__.py`, `pipeline.py`,
    `cli/commands/{pipeline,data}.py`; tests in `tests/unit/`
    (`test_ingestion_types.py`, `test_ingestion_orchestrator.py`,
    `test_parallel.py`, `test_router.py`, `test_feature_jobs.py`,
    `test_ml_jobs.py`, `test_unified_pipeline.py`, `test_cli_unified.py`) and
    `tests/integration/` (`test_pipeline_orchestrator.py`,
    `test_dashboard_exporter.py`); docs `pipeline-contracts.md`,
    `data-flow.md`, `INTEGRATIONS.md`, `auto-backfill-and-gap-detection.md`,
    `user-guide/{pipeline,ingestion}.md`.
- D02 approval: **granted by the owner on 2026-10-07** (dispatch: “approve on
  D02 … W03 to W07 per your judgement”) for the recommended policy: no hidden
  cap; explicit empty scope rejected and never broadened; enrichment-only
  requests planned-skip features/ML; missing scheduled required ticker coverage
  fails the feature stage. Recorded in the [handoff index](20261006-dag-pipeline-00-handoff-index.md)
  D02 row and ledger. No ADR is required: this is a pipeline operational
  contract (decision order item 1 / change-matrix “pipeline-stage change”), not
  an architectural boundary change.
- Regression proof: 15 load-bearing new tests fail against the pre-change
  source (`git stash` of `src/` only): optional failure visibility, parallel
  write isolation, correlation propagation, router exception/schema
  propagation, feature coverage diagnostics, ML `persisted`, bronze no-work,
  CLI empty-scope rejection, full-scope resolution, empty-scope rejection,
  enrichment-only skip, coverage-failure blocking, and no-work skip mapping.
  `test_serial_write_exception_matches_parallel_isolation` is an equivalence
  pin (the serial path already isolated *required*-market write exceptions);
  the changed serial behavior (optional failures no longer hidden) is proven by
  `test_optional_failure_is_failed_not_skipped_existing`.
- Tests/review/gates: focused suites — 53 passed (`types/orchestrator/parallel/router`),
  147 passed (`feature_jobs/ml_jobs/unified_pipeline/cli_unified`), 32 passed
  (`integration/test_pipeline_orchestrator.py`), 2 passed
  (`integration/test_dashboard_exporter.py`). Shared gates:
  `uv run pytest -m "not network and not integration"` → **1047 passed, 22 skipped,
  3 failed** (the pre-existing `test_arena.py` set, F28/W07); `-n auto` parity
  1047 passed / same 3; `ruff check` and `ruff format --check` clean; `mypy src`
  at the 3-error pre-existing `ml/registry.py` baseline; catalog regenerated
  unchanged (17 datasets / 52 nodes / 372 edges, zero diff); `mkdocs build`
  succeeds with only the 3 documented pre-existing warnings;
  `git diff --check` clean.
- Catalog: pipeline change acknowledged; regeneration was a no-op (no Hamilton
  topology/tag change), committed `data/catalog.jsonl` unchanged.
- Compatibility/rollback: all added result fields are additive (`scope`,
  `errors`, `coverage`, `history_backfilled_dates`, per-ticker `persisted`); the
  `markets` status map is preserved; no in-repo reader parses status strings
  (dashboard exporter round-trips the new fields verbatim, covered by test).
  Rollback reverts code and readers together; no lake migration is expected.
- Cost disclosure: removing the cap increases the default ingestion/inference
  scope from ten tickers to every configured active ticker in the requested
  price markets. Scheduled jobs must not be enabled until operators accept
  that increased source/API and inference cost.
- Residual limitations (owner-visible): a required-market `NO_DATA` still blocks
  features/ML and appears in `required_failures` until session policy lands
  (W03/D04); ML `persisted` is batch-granular (a mid-batch failure under-claims,
  never over-claims); bronze/SEC enrichment *processing* failure does not set
  `partial` (the nested sub-stage `success: false` is the signal); a
  config-with-no-active-tickers request and infrastructure aborts
  (`normalize_markets`, duckdb connect) still fail loudly outside per-market
  isolation (pre-existing); out-of-repo JSON consumers of the `markets` status
  map should accept the new `no_data`/`dry_run` values before deployment.
- Review outcome: three read-only reviewers (`opencode-go/mimo-v2.6-pro`).
  Outcomes/orchestration: **READY WITH WARNINGS** — all warnings addressed
  (explicit-scope dedup, unknown-market loud failure, isolation seam hardening,
  coverage retained on write failure, stale test reason). Coverage/feature jobs:
  **NOT READY** on one critical (exception-path coverage hardcoded `produced=[]`,
  fabricating reasons on a whole-job crash) — **fixed** with
  `_coverage_from_diagnostics`, plus backfill-failure/retry coverage and a
  compute-failure `invalid_output` test. Governance/docs/catalog: **READY WITH
  WARNINGS** — D02 record, `--tickers` help text, `data-flow.md` pointer, and
  the exit-status/`NO_DATA` contract wording addressed as part of this update.

### Acceptance

- [x] Outcome invariants hold in serial, parallel, and direct CLI paths.
- [x] Exact resolved scope and omissions are observable.
- [x] D02 approval and compatibility decisions documented.
- [x] Published result dictionary remains compatible (additive fields only).
- [x] No required failure is hidden behind a narrower successful output set.
- [x] No dry-run persistence or inferred unapproved scope.
- [x] Delivery packet, audit register, and ledger updated.

### W03 entry conditions

W03 requires the W02 commit on local `main` **and** D03 (label lifecycle) + D04
(session policy) owner approval. W03 inherits: ticker-aware source completeness
(this wave must not claim a date-level skipped partition proves complete feature
history), session-aware `not_scheduled` for closed markets (which also fixes the
required-market `NO_DATA` blocking limitation), scoped recovery plumbing, and
label finalization.
