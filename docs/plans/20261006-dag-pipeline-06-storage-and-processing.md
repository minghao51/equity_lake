# W06 — Storage Safety and Bounded Unstructured Processing

**Status:** Not started; evolution/processing policy approval required  
**Depends on:** W02 outcomes and W04 identity/schema contracts; normally after [W05](20261006-dag-pipeline-05-catalog-and-monitoring.md)  
**Findings:** F20, F21, F22  
**Next:** [W07](20261006-dag-pipeline-07-maintainability-and-validation.md)

## Objective

Close write-key and evolution failure gaps, then bound Bronze-to-Silver work so
an ordinary EOD run cannot accidentally become a corpus-wide paid job. Treat
failed reads as failures, not proof that no processing ever occurred.

## Read and ownership

Read `src/equity_lake/storage/{delta,lake_reader}.py`,
`ingestion/{writers,bronze_silver,llm_base,llm_processor,sec_processor}.py`,
`validation/{pipeline,schemas}.py`, current schema/identity definitions, and
W03 label-update changes. Inspect Delta schema/merge/idempotency, article/SEC,
LLM-mock, and write-boundary tests; follow schema/verify/catalog skills.

Own Delta safeguards, generic write-key checks, bounded processing selectors and
outcomes, settings only if needed, associated tests/docs, and catalog companions
for any approved schema change. Do not redesign LLM prompts/processors, introduce
job queues, or merge storage and paid-work changes into one commit.

## Policy decisions before code

- Reject invalid/null natural keys and within-batch duplicate keys by default,
  with actionable diagnostics. Last-wins deduplication is allowed only when a
  dataset's approved contract explicitly defines precedence.
- Decide how intentional null article-ticker attribution is keyed: nullable
  business attribution is not automatically a valid Delta equality merge key.
  Preserve market-wide articles without creating duplicate-on-rerun rows; do not
  invent an ad hoc ticker sentinel without schema/reader approval.
- Prefer specific Delta schema incompatibility classification over message
  substring matching. Allowed evolution must be explicit additive/widening
  behavior, not arbitrary shrink/drop/coercion.
- D08: approved batch/item/byte budget, ordering, source/date/ticker scope, partial
  success, retry, and checkpoint policy. Recommend deterministic bounded batches
  and fail-safe unreadable Silver; expose remaining backlog without auto-draining.
- No implicit paid historical catch-up. If the owner wants backlog processing
  across dates, make it explicit and bounded in planning/results/CLI docs.

## Implementation checklist

### A. Natural-key and route enforcement

- Normalize supported temporal keys before validating the actual write frame.
  Malformed parsing must not create null keys after validation has already passed.
- Apply key-column presence, not-null policy, and duplicate checks to every
  dataset using the existing key registry, including initial table creation,
  not only existing-table merges.
- Respect approved nullable article attribution while ensuring a stable keyed
  upsert; test both first write and repeated write.
- Reuse W01's quality-route tests/fixes; this wave owns systematic natural-key
  enforcement, not a second routing rewrite. Reconcile required columns with
  actual frame schemas where key enforcement reveals a remaining mismatch.
  Derive reusable lists from existing core constants instead of another
  hardcoded table of columns. Retain W01's route fixes and Platinum write gates.
- Fail before committing invalid batches. Return sanitized key/count diagnostics
  through W02 results; do not hide rejection behind generic empty-frame messages.

### B. Safe schema evolution and partial update behavior

- Reproduce supported mismatches using real temporary Delta tables under the
  installed delta-rs version. Pin which exception types/messages arise before
  changing classification.
- An I/O/permission/conflict error containing column/schema must not initiate
  evolution. Unknown failures remain failures.
- Preserve all existing keys, columns, partitions, dtypes, and time-travel
  readability under approved additive/widening evolution. Reject unexpected
  narrowing/drop or incompatible type change with a clear migration requirement.
- Avoid eager full-history rewrites where the existing library supports a safe
  bounded/metadata operation. Measure any remaining rewrite; do not promise
  partition-local cost without evidence.
- Test failure before evolution, after an evolution commit, and during re-merge.
  A two-commit evolution/retry is not all-or-nothing; document intermediate state
  and recovery unless the implementation genuinely makes it atomic.
- Test concurrent evolution/write interleaving against a captured snapshot. A
  stale materialized seed must not overwrite a newer writer's committed rows.
  Use supported transaction/conflict semantics or a narrowly scoped approved
  single-writer gate; no hand-rolled distributed lock service.
- Preserve W03 label-only matched updates: no insertion of nonexistent historical
  keys unless explicitly permitted, no all-column overwrite/nulling, no unrelated
  schema evolution. Retry source/storage operations only under approved tenacity
  contracts, not blanket exception loops.

### C. Bounded processing selection and no-work behavior

- Select unprocessed Bronze rows with query pushdown/anti-join against the
  correct Silver article identity where possible; do not materialize the entire
  corpus and all IDs in Python for every EOD run.
- Distinguish first-run missing Silver from unreadable/corrupt Silver. Failed
  dedup reads block paid processing and return an error; they never reset state
  to an empty processed-ID set.
- Apply approved source/date/ticker filters and deterministic ordering, then
  budget before calling process_fn. A selected RSS branch must not unexpectedly
  process all other source types merely because they coexist in Bronze.
- Track selected, processed, persisted, rejected/failed, skipped-existing, and
  remaining work separately. No eligible work is an intentional successful
  no-work outcome, not optional enrichment failure.
- Preserve public processor compatibility with small wrappers if needed; reuse
  the existing shared unstructured pipeline. Do not introduce a scheduler or
  replace all bool APIs with a new generic hierarchy.
- Checkpoint only successfully persisted completion. Crash/retry must not lose
  unpersisted work or repeatedly reprocess already persisted rows.
- Define fallback/failed-LLM row retry eligibility; low-confidence fallback is
  not automatically final successful processing unless that policy is explicit.
- Use current Settings when adding limits; add nested BaseModel fields and
  `.env.example` examples together. Estimate costs honestly; item counts are not
  exact token billing. No new secret configuration names.
- Bounded planning/dry-run may inspect approved local metadata but cannot process,
  persist checkpoints, write profiles, or invoke models/LLM providers.

## Required tests

### Storage and validation

- One null key, malformed date, empty key, and within-batch duplicates fail
  according to dataset policy before create/merge commits.
- Duplicate article/analyst/SEC/macro batches have deliberate, documented
  behavior; initial create and update paths agree.
- Nullable market-wide article attribution remains idempotent under the approved
  natural-key design.
- Narrow/wide source, allowed new column, disallowed drop/type change, partition
  passthrough including ex_date, and repeat keyed upserts.
- Non-schema exception containing column/schema triggers no evolution write.
- Failed evolution/re-merge has documented row/schema/version state.
- Old snapshot time travel works before authorized vacuum.
- Concurrent writer/evolution test preserves newer committed keys or rejects
  safely; no silent stale-seed overwrite.
- Label-only update preserves unrelated feature columns and refuses unapproved
  inserts.

### Unstructured work

- Large synthetic Bronze/Silver tables: only eligible bounded rows reach LLM
  mocks; deterministic ordering across reruns.
- Missing Silver first run versus unreadable Silver: only the approved first-run
  case can process; read error never causes corpus-wide processing.
- Empty/no eligible Bronze produces no-work success; failed query is failure.
- Mixed RSS/social/SEC corpus respects selected source/date/ticker scope and the
  dedicated SEC processor boundary.
- Partial processing/write failure retains incomplete work and does not mark it
  completed; rerun selects only remaining eligible work.
- Batch budget and settings/env contract tests; no unexpected default expansion.
- Dry-run asserts no LLM, writes, checkpoint/profile persistence, or inference.

## Verification and docs

```bash
uv run pytest tests/unit/test_delta_schema.py tests/unit/test_delta_migration.py tests/unit/test_ingestion_writers.py tests/unit/test_validation.py tests/unit/test_corporate_actions_schema.py -m "not network and not integration"
uv run pytest tests/unit/test_bronze_silver.py tests/unit/test_sec_processor.py tests/unit/test_llm_processor.py tests/unit/test_llm_processor_mock.py tests/unit/test_config_contract.py -m "not network and not integration"
```

Add deterministic concurrency and selection-budget tests rather than relying
on sleep-based race tests. Run index gates and regenerate catalog where needed.
Document storage evolution limits, partial updates, idempotency/null-key policy,
processing budgets/backlog/failure semantics, recovery operator actions, and
settings in ingestion/pipeline/quality/Delta guides and canonical architecture.

## Historical repair and rollback

Inventory invalid-key/duplicate existing rows, potentially reprocessed articles,
and inconsistent schema versions on copies. No automatic dedup/delete/overwrite
of production tables. Approved repairs need exact scope, snapshot backup,
validated output schema, rerun tests, and rollback.

Budget changes are reversible config/code changes; persisted checkpoint semantics
must remain compatible across rollback. Any new auxiliary checkpoint artifact
requires a Pydantic write boundary and explicit retention/migration policy.
Do not vacuum audit/migration snapshots or claim two-step evolution is atomic.

## Acceptance

- [ ] Every writer route enforces its approved natural-key and quality contract.
- [ ] Allowed evolution is explicit, tested, and safe under failed/concurrent work.
- [ ] W03 target updates remain bounded and preserve feature history.
- [ ] Ordinary processing is scoped/budgeted; corrupt Silver cannot reset paid work.
- [ ] Completion/no-work/partial/error outcomes are observable and durable claims true.
- [ ] Config/docs/catalog companions and repair limitations complete.
- [ ] No live paid processing or production repair performed by default.
- [ ] Delivery packet, audit resolution table, and index ledger updated.

## Progress

- Implementation: not started.
- Evolution/null-attribution/D08 approvals: pending.
- Storage-version/concurrency/budget evidence: not recorded.
