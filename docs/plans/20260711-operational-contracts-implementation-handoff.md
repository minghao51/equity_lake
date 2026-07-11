# Operational Contracts and Documentation — Implementation Handoff

**Date:** 2026-07-11
**Status:** Ready for implementation
**Scope:** Pipeline safety, storage/source contracts, canonical documentation, repository guardrails, and CI enforcement.

## Purpose

Bring the operational surface into agreement with the current medallion implementation and make risky pipeline behavior explicit, safe, tested, and documented. This work must not silently change data-processing policy while correcting documentation.

## Current evidence

The runtime uses numbered medallion paths through `core/paths.py`, `ingestion/types.py`, feature output, and catalog definitions. Several active guides still describe the older flat `data/lake/<market>/date=...` layout.

`execute_eod_pipeline()` currently continues after partial ingestion failure, conditionally performs Bronze-to-Silver processing, and retries feature generation after a missing-history error by launching a 120-day backfill. The recovery call hard-codes `dry_run=False`; therefore a command run with `equity pipeline --dry-run` can reach a write-capable backfill path. This is the first implementation priority.

`MARKET_DIR_MAP` is the practical routing-to-destination reference. `VALID_MARKETS`, the market type, routers, source configuration, catalog definitions, and docs must be checked against it rather than independently assumed correct.

`pip-audit` runs in CI with `|| true`, but is not declared in the dev dependency group. It currently provides advisory signal, not an enforceable gate.

## Non-goals

- Do not refactor the two Bronze-to-Silver processors into a shared framework in this work.
- Do not add new data providers, models, or datasets.
- Do not migrate existing lake data.
- Do not hand-edit `data/catalog.jsonl`.
- Do not change unrelated untracked work such as `data/.gitkeep`.

## Required decisions before runtime changes

Record the selected answers in `docs/developer/architecture/pipeline-contracts.md` and implement tests for them.

| Decision | Recommended policy | Why it matters |
|---|---|---|
| Dry-run semantics | No persistence, no backfill, no LLM processing, no model inference; report planned/skipped work | The current option can trigger a real history backfill |
| Missing feature history | Require explicit `--allow-history-backfill` | A 120-day recovery is material network and storage work |
| Backfill scope | Limit to requested markets and explicit tickers; otherwise show the resolved scope before execution | Avoids expanding a targeted run into a full-universe operation |
| Required source failure | Block dependent features and ML | Prevents derived outputs built from incomplete price inputs |
| Optional enrichment failure | Continue core features without that enrichment; record partial success | Preserves useful work without hiding degradation |
| Bronze-to-Silver failure | Continue only paths independent of that Silver output | Makes enrichment availability predictable |
| CLI exit status | Non-zero for required-stage failure; zero for deliberate dry-run and successful partial optional-source runs | Enables automation and monitoring |

If a different policy is chosen, update this handoff and the contract document before implementing it.

## Work package 1 — Pipeline safety and result contract

### Files

- `src/equity_lake/pipeline.py`
- `src/equity_lake/ingestion/backfill.py`
- `src/equity_lake/cli/commands/pipeline.py`
- `tests/integration/test_pipeline_orchestrator.py`
- `tests/unit/test_backfill.py`
- New targeted CLI tests if command-option coverage is missing

### Implementation

1. Add `allow_history_backfill: bool = False` to the pipeline executor and a matching Typer option.
2. Preserve the existing dictionary result shape, but normalize every stage result to include `success`; add `skipped` and `reason` where applicable. Do not introduce a new cross-cutting result abstraction.
3. Treat dry-run as a hard no-write execution mode:
   - do not call Bronze-to-Silver or SEC processors;
   - do not call feature generation when it writes output;
   - do not call prediction generation when it writes output;
   - do not invoke recovery backfill;
   - return planned/skipped stage outcomes with the reason `dry_run`.
4. When missing history is detected outside dry-run:
   - return a failed feature stage with a clear remediation message unless `allow_history_backfill` is set;
   - when set, log date range, markets, ticker count, and the explicit authorization before backfill;
   - pass the effective dry-run value rather than hard-coding `False`;
   - thread explicit ticker scope through backfill/ingestion or deliberately reject unsupported ticker-scoped recovery with a clear error.
5. Classify sources used by the invocation as required price inputs or optional enrichments. Gate features and ML only on required prerequisites.
6. Update `_pipeline_succeeded()` so required failure, optional degradation, deliberate skip, and dry-run follow the agreed exit policy.

### Acceptance criteria

- No code path from `equity pipeline --dry-run` can write lake data, invoke a write-capable processor, or launch a backfill.
- Missing history never initiates a backfill without explicit user authorization.
- A failed required price source prevents dependent feature/ML output.
- A failed optional source is visible in results and logs without incorrectly reporting full success.
- Every CLI outcome has deterministic exit status and result JSON.

### Required tests

- Dry-run with missing features asserts that feature, ML, processors, and backfill are not called.
- Missing history without authorization returns a clear failed result and no backfill call.
- Authorized recovery forwards exact markets, tickers, date range, and dry-run value.
- Required price-source failure blocks features and ML.
- Optional enrichment failure keeps core features eligible and reports partial success.
- Bronze-to-Silver and SEC processing failures only disable dependent enrichments.
- CLI exit status is tested for success, required failure, optional degradation, and dry-run.

## Work package 2 — Source, storage, and lineage contract

### Files to inspect and update as evidence requires

- `src/equity_lake/ingestion/types.py`
- `src/equity_lake/ingestion/orchestrator.py`
- `src/equity_lake/ingestion/writers.py`
- `src/equity_lake/storage/`
- `src/equity_lake/core/paths.py`
- `src/equity_lake/catalog/datasets.py`
- `src/equity_lake/catalog/`
- Query, sync, monitoring, and feature readers
- Associated unit/integration tests

### Implementation

1. Produce a source inventory from executable code, not from existing prose. For every routable identifier, capture fetcher/router, credentials, ticker policy, destination, format, partitioning, validation point, and optionality.
2. Reconcile the `Market` literal, `VALID_MARKETS`, `MARKET_DIR_MAP`, router dispatch, configuration, and catalog entries. Remove stale entries or make intentionally supported entries consistent across all of them.
3. Audit each writer/reader pair to establish whether its durable artifact is partitioned Parquet, Delta, or both. Correct docstrings and docs only after that audit.
4. Determine the real `skip_existing` algorithm per writer. Document its exact scope and add tests for the supported behavior; do not claim generic Delta idempotency when detection is partition-file based.
5. Keep catalog generation flow as `uv run equity catalog-generate`. Catalog definitions and Hamilton tags are editable; `data/catalog.jsonl` is generated output only.

### Acceptance criteria

- Every accepted source identifier resolves to an intentional route and storage destination.
- Every catalog dataset has a valid medallion layer and actual runtime path.
- Writer/reader/idempotency behavior is documented with matching tests.
- No active architecture or user documentation describes a storage format or path contradicted by runtime code.

## Work package 3 — Canonical documentation

### New files

- `docs/developer/architecture/data-flow.md`
- `docs/developer/architecture/pipeline-contracts.md`
- `docs/user-guide/ingestion.md`

### Existing files to update

- `README.md`
- `docs/user-guide/pipeline.md`
- `docs/getting-started/quickstart.md`
- `docs/developer/architecture/ARCHITECTURE.md`
- `docs/developer/architecture/STRUCTURE.md`
- `docs/developer/architecture/INTEGRATIONS.md`
- `docs/README.md`
- `mkdocs.yml`
- `AGENTS.md`

### Required content

1. `data-flow.md`
   - Mermaid diagram covering structured market data, unstructured sources, SEC, validation, Bronze-to-Silver processing, Gold features, Platinum predictions/signals, DuckDB queries, catalog, and monitoring.
   - Clear conditions for optional branches and stage dependencies.
2. `pipeline-contracts.md`
   - The resolved policy table from this handoff.
   - Inputs, outputs, validation boundaries, deduplication/partition keys, statuses, retry behavior, idempotency, and post-run verification.
3. `ingestion.md`
   - Supported source identifiers, credentials, ticker selection, output destinations, dry-run, retries, parallelism, backfills, and troubleshooting.
4. Existing guides
   - Replace live legacy-path examples with numbered medallion paths.
   - Keep legacy paths only in migration/history documents, clearly labelled historical.
   - Explain that feature work requires warm-up history and that automatic recovery, if enabled, is explicit.
   - Provide commands using `dotenvx run -- uv run` where secrets/configuration are needed.
5. MkDocs and naming policy
   - Add the three canonical pages to navigation.
   - State that established canonical pages and MkDocs content are exceptions to the date-prefixed Markdown rule; dated plans, audits, and handoffs remain date-prefixed.

### Acceptance criteria

- The active documentation contains one canonical source and storage reference.
- No active guide presents `data/lake/<market>/date=...` as the current layout.
- New pages are reachable from MkDocs navigation.
- Commands, documented behavior, and tests agree.

## Work package 4 — Repository and agent guardrails

### Files

- `AGENTS.md`
- `.agents/skills/run-equity-pipeline/SKILL.md`
- `.agents/skills/add-equity-data-source/SKILL.md`
- `.agents/skills/change-equity-schema/SKILL.md`
- `.agents/skills/verify-equity-pipeline/SKILL.md`
- `.agents/skills/catalog-generator/SKILL.md`

### Implementation

1. Add the change matrix below to `AGENTS.md`.

| Change type | Required accompanying work |
|---|---|
| New source | Router, type/map, schema/validation, config, tests, source docs, catalog |
| Schema change | Schema constants, validators, catalog, reader compatibility, migration note |
| DAG feature change | Hamilton tags, catalog regeneration, feature tests |
| Storage change | Writer, reader, health checks, idempotency tests, architecture docs |
| CLI change | Help text, CLI test, user guide |
| Pipeline-stage change | Failure contract, orchestration test, data-flow update |

2. Document generated artifacts, dry-run expectations, network-test markers, catalog-generation rules, and documentation naming exceptions.
3. Create concise skills that point back to canonical docs and tests rather than restating implementation detail. Each must prevent broad backfills and require scoped verification.
4. Preserve `catalog-generator` unless audit evidence identifies an actual inconsistency; its documented `catalog-generate` command is currently aligned with code and CI.

### Acceptance criteria

- A future source/schema/storage change has a short, executable checklist.
- Skills make no unsupported assumptions about providers, storage format, or policy.
- Skill instructions keep generated artifacts and live docs synchronized.

## Work package 5 — Automated checks and CI policy

### Files

- `.github/workflows/quality.yml`
- `.github/workflows/catalog-check.yml`
- `pyproject.toml`
- Relevant test modules and documentation tooling configuration

### Implementation

1. Add source/storage contract tests with direct assertions over accepted identifiers, routes, map entries, and catalog paths.
2. Add a catalog drift check only for changes that should affect generated output; retain the existing generation command.
3. Add Markdown lint, MkDocs build, and internal-link validation. Exclude historical migration documents from stale-current-path checks where appropriate.
4. Add `pip-audit` to the dev dependency group and run it in CI as a named policy gate.
5. Remove `|| true` only after recording the baseline result and agreeing whether known vulnerabilities block merges. Do not turn a non-blocking advisory into an undisclosed release blocker.

### Acceptance criteria

- Contract drift fails locally and in CI with an actionable message.
- Docs build and links validate in CI.
- Security auditing has a declared dependency and intentional enforcement policy.

## Verification sequence

Run focused checks after each work package, then the complete non-integration suite before review:

```bash
dotenvx run -- uv run pytest tests/integration/test_pipeline_orchestrator.py tests/unit/test_backfill.py
uv run pytest tests/unit/test_catalog_datasets.py tests/unit/test_catalog_cli.py
uv run pytest -q -m "not integration"
uv run ruff check .
uv run mypy src
uv run equity catalog-generate
git diff --check
```

Run the repository’s MkDocs/link-validation command once selected by the implementation pass. Use `dotenvx run --` for commands that require local secrets or `.env` configuration. Do not run live network ingestion as a default verification step.

## Suggested pull request breakdown

1. **Pipeline safety contract** — Work package 1 plus focused tests and the initial contract document.
2. **Storage/source truth and docs** — Work packages 2 and 3, catalog regeneration where needed, MkDocs navigation.
3. **Guardrails and CI** — Work packages 4 and 5, including skills and deliberate security-audit policy.

Each PR must leave the tree clean apart from pre-existing user changes, identify any unverified live-provider assumptions, and avoid bundling runtime policy changes with unrelated cleanup.

## Resume checklist

1. Confirm the six policy decisions above with the product owner.
2. Start Work package 1; do not begin broad doc replacement before the runtime semantics are settled.
3. Run its focused tests and record results in this handoff under a dated progress section.
4. Continue in package order and update this file with landed commits, verification evidence, deviations, and remaining blockers.

## Progress — 2026-07-11

- Work Package 1 implemented: dry-run is plan-only, history recovery requires
  `--allow-history-backfill`, recovery remains scoped, required price failures
  block derived stages, optional failures are partial success, and stage results
  are normalized with deterministic CLI status.
- Work Packages 2–4 implemented: unsupported derived market aliases were
  removed from the accepted source contract; routing/destination/catalog tests,
  canonical architecture/user pages, active path corrections, change matrix,
  and scoped operational skills were added.
- Work Package 5 implemented: `pip-audit` is declared in the dev group, CI has
  named security, Markdown, MkDocs, link, and catalog checks. Security remains
  explicitly advisory because the baseline audit reports existing findings.
- Verification: focused pipeline/backfill/CLI/source tests passed (54 tests);
  `uv run pytest -q -m "not integration"` passed; Ruff and mypy passed;
  catalog generation produced no diff; targeted Markdown lint passed;
  non-strict MkDocs build completed with warnings from pre-existing third-party
  files under `docs/catalog/node_modules/`.
- Remaining repository baseline: strict MkDocs fails on those vendored
  third-party README links; `pip-audit` reports existing vulnerabilities in
  transitive dependencies. Neither was silently converted into a merge blocker.
