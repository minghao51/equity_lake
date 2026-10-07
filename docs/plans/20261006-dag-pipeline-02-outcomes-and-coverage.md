# W02 — Truthful Outcomes and Requested Coverage

**Status:** Not started; scoped implementation approval required  
**Depends on:** [W01](20261006-dag-pipeline-01-correctness.md) and D02 approval  
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

- Implementation: not started.
- D02 approval: pending.
- Tests/review/commit and compatibility evidence: not recorded.
