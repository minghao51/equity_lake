# W07 — DRY, Measured Optimization, and Release Closure

**Status:** Not started; performance work must follow correctness integration  
**Depends on:** [W01–W06](20261006-dag-pipeline-00-handoff-index.md)  
**Findings:** F26–F28; final closure evidence for every finding  
**Deliverable:** Release/remaining-risk packet and updated audit resolution register

## Objective

Make the corrected system easier to extend and operate without adding a new
framework. Repair unreliable test/tooling gates, remove evidenced duplication,
measure local optimizations, and prove the complete pipeline across daily runs.

Test-marker and baseline tooling fixes may be dispatched early as a standalone
preflight PR, with disjoint ownership. DRY/performance changes remain last and
must not bundle or obscure correctness fixes.

## Read and ownership

Read `pyproject.toml`, `tests/conftest.py`, `tests/README.md`, `Makefile`,
`.github/workflows/{quality,catalog-check,data-validation}.yml`, canonical
TESTING/CONVENTIONS/architecture pages, `AGENTS.md`,
`src/equity_lake/ml/registry.py`, feature pipeline/engineering/catalog builders,
existing schema/market registries, W02 result paths, and W01–W06 delivery packets.

Own test-selection/tooling fixes, small consistency/DRY changes, performance
fixtures/measurements, final cross-module tests, and release documentation.
Do not reopen schema/session/label decisions or migrate production data here.
Use the verify-pipeline skill and catalog/schema skills as relevant.

## A. Restore trustworthy development gates

- Audit every test that can access a provider, external service, real API key,
  or LLM. Apply explicit network markers; directory and integration markers are
  not substitutes. Mock boundaries in default fast tests.
- Make default pytest exclude network tests as promised. Explicit opt-in must
  remain possible and documented; never silently authorize real calls merely
  because an environment variable happens to exist.
- Ensure serial tests cannot accidentally run under xdist. Align the default
  parallel command, collection behavior, Makefile, CI, and docs. If explicitly
  requesting serial tests with parallel workers, fail clearly rather than
  running unsafe tests. Avoid a complex plugin for a small selection rule.
- Add collection/subprocess meta-tests proving default, network opt-in, offline
  integration, serial, and parallel selection. Verify they themselves use no
  real source calls.
- Add/retain format checking in the relevant CI gate. Fix the observed formatting
  issue with a targeted formatting change, not unrelated repository churn.
- Diagnose W&B typing in base and ML-group environments: optional import policy,
  installed package/types, and local namespace discovery. Fix the real mismatch;
  do not blanket-ignore all registry errors, declare untyped fake APIs, or make
  W&B mandatory for base training. Preserve best-effort/no-key behavior.
- Audit optional backend skips. Install declared groups intentionally for their
  CI jobs; keep base environment lazy-import tests. Do not claim LightGBM or
  polars-backtest correctness from a suite that skipped all their tests.
- Keep source retries in tenacity. Do not change retry policies just to speed
  tests; mock waits/time where appropriate.

## B. Small DRY improvements

1. Replace hardcoded feature market-view paths/aliases with the existing
   `core/paths.py` registry and approved W04 identity mapping.
2. Use one existing schema definition for required columns/keys where feasible;
   add consistency tests for requested output names, model exclusions, fallback
   defaults, catalog versions, and emitted dtypes. Do not turn all DAG nodes or
   intermediate DataFrames into numeric features by automatic introspection.
3. Reuse the current driver assembly seam or module list for catalog/runtime
   construction. Preserve caching options and topology without adding a builder
   framework. If two builders remain simpler, pin their module/topology parity.
4. Remove duplicated normal/recovery feature invocation arguments using the
   smallest local flow change; preserve exception classification, authorization,
   scope, availability flags, and retry outcome reporting.
5. Derive enrichment defaults/fill definitions from approved branch schema
   metadata or add parity tests if refactoring is not worth the risk. Disabled,
   populated, empty, and failed branches must have intentional schemas.
6. Remove genuinely dead imports/constants and stale legacy-method references
   only after searching callers. Do not implement the unrelated Proposed
   ML/signals direction ADR in a dead-code sweep.
7. Standardize stable reason codes and reuse existing status helpers so CLI exit
   handling does not depend on fragile message strings.

Keep each cleanup behavior-preserving and testable. Broad generic registries,
protocols, rewritten package layouts, and frameworks are out of scope.

## C. Measure before optimizing

Prepare deterministic synthetic benchmarks with representative instrument/day
counts, multiple markets, warm-up windows, optional enrichment density, and
large Bronze/Silver backlogs. Capture environment, dependency versions, input
sizes, wall time, peak memory if available, and scan/write/processed-row counts.
Do not commit real market data or unstable hardware-specific thresholds.

- Partition/group the loaded frame once by approved instrument identity instead
  of filtering all rows separately for each ticker. Preserve chronology and
  row/value parity, including zero-row and mixed-market cases.
- Query only planned market/instrument/date scope using parameter binding and
  existing Delta scan helpers. Inspect scan/merge metrics before claiming
  partition pruning or reduced full-table work.
- Measure W06 bounded selection versus the previous full-corpus materialization;
  prove LLM mock call count is budgeted even as total corpus grows.
- Measure driver-build cost separately from indicator work. Reuse within a job
  only if lifecycle, concurrent safety, and output parity are proven.
- Keep enrichment caching disabled until cache keys include all relevant source
  snapshots, availability cutoffs, schema versions, and configuration. A mutable
  DuckDB connection is not a reproducible cache input. Cache design is a separate
  approved follow-up if the measured need justifies it.
- Do not add more threads/distributed execution until deterministic single-job
  behavior and measured bottlenecks justify it.

Deliver before/after tables with value/schema parity assertions. If a change
has no measured benefit or increases complexity, retain the simpler path and
record the decision instead of forcing a refactor.

## D. Final end-to-end regression matrix

Use mocked provider/LLM/model seams, temporary Delta tables, fixed clocks, and
explicit configuration. Run through the public pipeline/CLI where possible.

| Scenario | Required evidence |
|---|---|
| Three consecutive EOD sessions | Correct scoped writes, prior-label finalization, current inference, pending newest labels |
| Real technical → populated enrichment path | Correct temporal dtypes, values, row alignment, branch availability |
| Required failure | No dependent feature/prediction writes; useful outcomes retained |
| Optional failure | Core outputs can persist; partial/error details and exit policy correct |
| Parallel write exception | Other sources survive; no traceback replacing structured results |
| Partial existing partition/new ticker | Scoped completeness/recovery; no skipped missing ticker |
| Unauthorized missing history | No recovery and clear remediation result |
| Authorized recovery in all five markets | Exact provider scope, bounded dates, no universe expansion |
| Different exchange sessions | Closed versus scheduled missing scope distinguished |
| Same ticker in two markets | Independent technical/ML identities and no US enrichment cross-attribution |
| Label availability/cutoff | Historical training cannot see later-finalized targets |
| Migration compatibility | Legacy/ambiguous data and models handled as approved; rerun/rollback tested on copies |
| Invalid keys/evolution/concurrency | Safe refusal or approved evolution; no lost keys/unrelated feature updates |
| Large backlog/unreadable Silver | Budgeted work; corrupt dedup read never launches paid corpus processing |
| Catalog and monitoring | Actual schemas/relationships and missing expected data agree with run results |
| CLI/result readers | Help/options, exit codes, JSON compatibility, dashboard/API consumers verified |
| Dry-run on all recovery/processing paths | No persistence, backfill, LLM work, feature output, or inference |

Use spy assertions on every write-capable boundary plus filesystem assertions
under the temporary root. A dry-run test checking only the final return value
is insufficient.

## Final verification sequence

Package installation is allowed setup, not permission for live financial data.
Never load real credentials into default offline tests unnecessarily.

```bash
uv sync --group dev --group ml --group backtesting --group docs
uv run pytest -m "not network"
uv run pytest -n auto -m "not network and not serial"
uv run pytest -m "serial and not network"
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run equity catalog-generate
uv run pytest tests/unit/test_catalog_builder.py tests/unit/test_catalog_datasets.py tests/unit/test_catalog_writer.py tests/unit/test_catalog_cli.py
uv run mkdocs build --site-dir /tmp/equity-lake-remediation-site
git diff --check
```

Repeat intended offline gates under Python 3.12 and 3.13 in isolated uv
virtualenvs or existing CI matrix; do not repeatedly swap a shared agent's
active environment. Include base-only import/degradation tests separately from
ML/backtesting-group tests.

Once default selection has been repaired, also verify the documented shorthand
commands `uv run pytest` and `uv run pytest -n auto` select only the intended
safe suite. Every mocked integration regression must actually run; do not
satisfy the gate solely by deselecting integration tests.

Use existing Markdown tooling on changed docs and validate relative links/nav.
For strict MkDocs failures, separate new warnings from the known vendored/legacy
documentation baseline; never suppress all warnings to label the build clean.
A network smoke test is a separate owner-authorized operation with exact source,
market/ticker/date scope and recorded unverified assumptions.

## Release and historical remediation packet

- Finding-by-finding closure with tests, commits/PRs, approvals, and explicit
  remaining limitations. A passing suite is not a live provider certification.
- Feature/model/schema versions and reader/API/CLI compatibility disclosures.
- Inventory of affected historical dates/instruments: lost enrichments, pending
  labels, missed actions, invalid/ambiguous keys, and possible model impact.
- Proposed authorized repair procedure with dry-run scope report, copied-lake
  trial, snapshots/rollback, validation, and expected cost. Production repair and
  retraining require separate approval; this release does not auto-run either.
- Operational rollout: reader compatibility first where needed, then writers,
  smoke checks, monitor/result inspection, and rollback triggers.
- Performance results with reproducible synthetic inputs and no overclaimed
  live-scale guarantees.
- Final documentation map/MkDocs navigation and active canonical pages aligned;
  audit remains historical evidence with updated resolution status.

## Definition of done

- [ ] F01–F28 closed with evidence or specifically owner-deferred with impact,
  reason, owner, and follow-up. No unverified temporal claim marked solved.
- [ ] Default offline/parallel/serial/network selection is enforced and documented.
- [ ] Base and optional dependency environments, Python matrix, format/lint/type,
  mocked integration, catalog, docs, and diff gates verified.
- [ ] DRY changes remain small; every optimization has parity and measurement.
- [ ] Accepted ADRs, migrations, model compatibility, repair/rollback plan complete.
- [ ] No unapproved live fetch, paid processing, production data mutation, or
  retraining during validation.
- [ ] Audit resolution register and index ledger reflect actual delivery evidence.

## Progress

- Implementation: not started.
- Early preflight assignment: unassigned.
- Full regression/performance/release evidence: not recorded.
- Owner-approved deferrals: none recorded.
