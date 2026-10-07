# W05 — Catalog and Operational Truth

**Status:** Not started  
**Depends on:** [W03](20261006-dag-pipeline-03-incremental-contracts.md) label/availability contracts and [W04](20261006-dag-pipeline-04-schema-and-provenance.md) final identity/temporal contracts  
**Findings:** F18, F19, F23, F24  
**Next:** [W06](20261006-dag-pipeline-06-storage-and-processing.md)

## Objective

Make generated metadata and health checks describe the physical/runtime system,
not layer diagrams or unreachable checks. Either implement explicitly approved
drift baselines or stop claiming writer-boundary automatic drift detection.

Catalog freshness CI already exists. Do not add a redundant regeneration job
and call semantic accuracy solved.

## Read, ownership, and independent PRs

Read `src/equity_lake/catalog/{models,datasets,builder,writer}.py`, actual DAG
request/dependency topology, `ingestion/writers.py`, corporate-action writers,
`storage/{delta,lake_reader}.py`, `monitoring/{health,alerting}.py`,
`validation/{pipeline,profiling}.py`, catalog frontend consumers under
`docs/catalog/src/`, catalog CI, and associated tests. Read catalog architecture,
pipeline contracts, monitoring/data-quality/Delta maintenance guides and catalog
skill.

Own catalog definitions/build/tests/frontend compatibility, health checks/tests,
explicit drift policy, and accompanying docs. Avoid broad writer changes owned
by W06. Split catalog, health, and drift into separate commits/PRs.

Parallel dispatch is possible only with disjoint tests/docs and a single catalog
regeneration owner. W04/W06 schema changes require coordinated regeneration.

## Implementation checklist

### A. Physical schema and partition accuracy

- Establish actual writer contracts per dataset: table versus collection of
  market tables, path, partition columns, natural keys, physical dtypes, required
  columns, and optional/versioned enrichment columns.
- Corporate-action collection roots must not be described as one root Delta
  table if physical tables are market children. Describe that distinction and
  set ex_date partition metadata on both raw and Silver entries.
- Gold includes target/availability/identity additions from W03/W04. Date and
  integer metadata must reflect emitted/persisted schemas rather than guessed
  small integer types. Predictions include actual mode/version metadata.
- Document optional enrichment schemas explicitly; catalog definitions must not
  imply a branch is present when disabled. Use approved version/optional semantics
  without making each transient optional column a new dataset.
- Derive shared columns from existing schema constants where practical. Add
  consistency tests against representative real output frames and writer
  contracts, including create/merge/optional-branch differences.
- Regenerate `data/catalog.jsonl` through the CLI only.

### B. Actual lineage and node-edge semantics

- Replace all-to-all adjacent-layer dataset lineage with actual consumption:
  raw price to Gold, appropriate Bronze article to Silver processed content,
  selected Silver enrichments/macro to Gold, Gold to predictions, and actions
  to explicitly opted-in consumers only.
- Keep static dataset dependency metadata beside existing dataset definitions;
  do not introduce a universal provenance engine or infer dependencies just
  from directory numbers.
- Distinguish physical dataset inputs, DAG computation dependencies, optional
  dependencies, and transitive ancestry. Determine how current node-edge API
  represents upstream variables before changing edge extraction.
- If converting ancestry edges to direct edges, preserve Hamilton wrapper
  filtering without disconnecting public nodes; test and update frontend
  expectations in the same PR. Otherwise label transitive edges honestly.
- Do not pretend untagged injected inputs or disconnected validators are actual
  runtime consumers. W01's graph changes must appear accurately.
- Audit catalog CI path triggers against all new feature/schema/writer metadata
  sources. Extend triggers where necessary, retaining the existing freshness job.

### C. Market-aware health checks

- Compare queried recent markets/identities against expected scheduled scope;
  GROUP BY over a filtered window cannot yield a zero-row group.
- Isolate missing/broken table scans per market and retain healthy-market results.
  A missing required market is an actionable alert; a market not configured for
  the deployment or not scheduled needs approved semantics, not a fake failure.
- Check the requested instrument coverage and freshness, not just one row per
  market; reuse W03 session/completeness contracts.
- Apply relevant null/invalid-value thresholds consistently to documented
  critical fields; do not select open/high/low quality metrics and ignore them
  while claiming all OHLCV fields are checked.
- Test monitors reused across runs: resolved conditions should not resend stale
  alerts forever. Scope alert deduplication and accumulation deliberately.
- Preserve JSON metrics/result reader compatibility. Record expected market and
  instrument scope so missing observations are meaningful.

### D. Drift policy (D07)

Recommended smallest fix: explicitly disable automatic drift checks at the
writer boundary and document that no baseline is installed there. Retain
explicit comparison/profile APIs and CLI behavior.

If the owner requires real automatic drift, stop for approved baseline scope,
versioning, persistence/retention, market/schema compatibility, and failure
policy. Baselines/profiles are auxiliary artifacts outside the lake with
Pydantic write-boundary models; no undocumented catalog table or implicit
profile persistence. New configuration requires matching Settings models and
`.env.example`.

Whichever option is approved, test the deployed path rather than merely calling
`set_baseline()` on an unrelated in-memory test instance. If baseline comparison
is retained, include schema shrink, near-zero means, and zero standard-deviation
cases; do not silently misrepresent unstable percentage ratios as drift signal.

## Required tests

| Area | Required checks |
|---|---|
| Dataset metadata | Paths/table collections, actual partition columns, natural keys, required/optional column dtypes |
| Gold/Platinum output | Representative real emitted frames match approved catalog versions and metadata |
| Lineage | Known true edges present; unrelated market/news relationships absent; optional versus core edges explicit |
| DAG topology | Requested graph and published lineage agree; direct/transitive semantics tested |
| Serialization | Deterministic generation; generated catalog comparison passes |
| Frontend | New metadata/edge schema renders; no duplicate dataset names or broken key lookup |
| No recent rows | Expected market missing from grouped query alerts instead of passing |
| One broken/missing market | Other market results survive; error target/cause preserved |
| Session/scope | Closed session and missing scheduled ticker follow W03 policy |
| Repeated monitor run | Resolved alert not re-sent; dedupe stable within each run |
| Drift | Actual writer behavior reflects approved enabled/disabled policy; no dry-run profile writes |

## Verification

```bash
uv run pytest tests/unit/test_catalog_builder.py tests/unit/test_catalog_datasets.py tests/unit/test_catalog_writer.py tests/unit/test_catalog_cli.py -m "not network and not integration"
uv run pytest tests/unit/test_monitoring_health.py tests/unit/test_alerting.py tests/unit/test_validation.py tests/unit/test_ingestion_writers.py -m "not network and not integration"
uv run equity catalog-generate
```

Inspect `docs/catalog/package.json` before choosing its existing frontend build
command; do not invent a new frontend build system or dependency upgrade. Run
shared lint/type/docs gates. If installing documentation dependencies, use
`uv sync --group dev --group docs` deliberately and report environment changes.

Update canonical catalog/data-flow/architecture/contract pages, monitoring and
quality guides, Delta maintenance partition claims, and documentation counts
only where mechanically verified. Fix the architecture page's stale claim that
`equity_lake.pipeline` is unsupported even though it is the active orchestrator.
Keep root `ARCHITECTURE.md` as its existing pointer.

## Acceptance, compatibility, and rollback

- [ ] Catalog matches actual writer/output contracts and consumer relationships.
- [ ] Generation is deterministic and existing freshness CI still works.
- [ ] Catalog frontend compatibility tested before changing serialized semantics.
- [ ] Expected missing recent data is detected; failures isolated per market.
- [ ] Drift behavior is explicit and tested at the real writer seam.
- [ ] All changed public metadata/metrics documented with compatible readers.
- [ ] No new baseline artifacts or data mutations without approval.
- [ ] Audit register/index/delivery packet updated.

Rollback generated catalog and frontend consumers together. Monitoring-only
rollback requires no lake mutation. Do not delete baseline artifacts as an
incidental rollback; follow any approved retention policy.

## Progress

- Implementation: not started.
- D07 drift choice: pending.
- Verified schema/relationship inventory and test evidence: not recorded.
