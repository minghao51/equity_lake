# W04 — Instrument Identity and Temporal Provenance

**Status:** Blocked on accepted decisions; no migration authorized  
**Depends on:** [W03](20261006-dag-pipeline-03-incremental-contracts.md)  
**Findings:** F12, F16, F17; compatibility of W03 labels and W02 results  
**Next:** [W05](20261006-dag-pipeline-05-catalog-and-monitoring.md)

## Objective

Make multi-market identity and feature availability explicit across computation,
persistence, loading, training, inference, signals, backtesting, and read APIs.
Correct stateful enrichment joins without claiming historical point-in-time
accuracy that the source data cannot support.

This is a schema/compatibility wave, not a cleanup wave. Do not hide key changes
inside refactoring or silently reinterpret existing data/models.

## Mandatory ADR gates

### D05 Identity ADR

Recommend `(market,ticker,date)` in shared Gold/Platinum tables and
market-qualified instrument/model lookup, with canonical long market keys.
Price tables remain per-market unless a different storage design is approved.
Document US-only enrichment attribution; do not join US sentiment/analyst data
to a same-named instrument from another market.

ADR must specify request/API compatibility, source ticker normalization,
ambiguous bare-symbol behavior, natural keys, model filenames/metadata,
migration, affected readers, and rollback. A single-market ticker-only call may
remain supported if unambiguous; ambiguous calls must fail loudly. Do not
encode market into ticker strings ad hoc or create a new instrument service.

### D06 Temporal and missingness contract

Record event date/time, source release availability when known, processing
availability, label availability, carry-forward rules, staleness policy, EWMA
timebase, and historical replay cutoff. Recommend explicit missingness/age
metadata alongside compatible numeric defaults, rather than treating unavailable
VIX or sentiment as observed zero.

Macro series lacking historical release/vintage data cannot be advertised as
as-was point-in-time. Owner may choose documented limitation or separately
approve vintage acquisition/storage. New source/table/metadata boundaries need
an accepted ADR and the full change matrix. Do not invent availability from
observation date or introduce live new feeds in this wave without approval.

## Read and impact inventory

Inspect these files plus their callers/tests before settling migration:

- `src/equity_lake/core/{paths,schemas,config_models}.py` and ticker config.
- `features/{engineering,pipeline,__init__}.py`, all feature DAG modules.
- `ingestion/{types,writers,bronze_silver,sec_processor}.py`, relevant source outputs.
- `storage/{delta,lake_reader,duckdb,examples}.py`.
- `ml/{feature_loader,forecasting,candidates,labeling,comparison,ablation,__init__}.py`.
- `signals/` request/model lookup, history/formatters, and on-demand inference.
- `backtesting/data_loader.py`, API dependencies/routers, dashboard exporters.
- CLI feature/forecast/pipeline/backtest/API commands and help-scan tests.
- Catalog definitions, validators, model metadata/skew checks, import boundaries.
- ADRs 0001–0007, 0010–0011; verify status of later Proposed ADRs separately.

Produce a checked reader/writer inventory in the PR, not just a list of edited
files. Do not implement the unrelated Proposed ML/signals dependency-direction
ADR unless it has separate acceptance and assignment.

## Implementation checklist

### A. Identity propagation and consumer compatibility

- Preserve market from source selection through technical grouping, enrichments,
  Gold writes, feature loading, ML requests, predictions, and public results.
- Group rolling/shift calculations and deduplication by approved instrument
  identity; never mix same-named symbols across markets.
- Update Gold/Platinum natural keys and key validation together. Forecast model
  lookup and metadata must distinguish the market; legacy filenames must not
  silently resolve to a different instrument.
- Keep market and provenance columns excluded from numeric model matrices.
- Decide cross-sectional normalization universe explicitly: per-market/date or
  approved mixed universe. Do not silently alter it while adding a market column.
- Bind market/ticker values as query parameters; reuse existing registry aliases
  at boundaries rather than multiplying hardcoded translation tables.
- Update signals, read API, dashboard, backtests, comparison/ablation, and CLI
  consumers in the same compatibility release or behind an approved reader bridge.
- Preserve public single-market behaviors where possible; add explicit market
  arguments/options only when necessary and accompany help/tests/user docs.

### B. Correct stateful enrichment joins

- For SEC and other carried state, query all events up to the end cutoff plus
  at least the last eligible event per requested instrument before start.
  Avoid arbitrary lookback windows that can miss annual filings.
- Use backward-asof from known availability to feature session. A later filing
  or release must never populate earlier features. Normalize temporal units
  without destroying true timestamps.
- For macro, retain actual observation/release dates before projecting to feature
  sessions; include preceding state and off-calendar observations. Bound queries
  and preserve per-indicator eligibility/staleness.
- Define deterministic handling of multiple same-date events/revisions. Query
  event/availability keys deliberately, not whichever partition field happens
  to be convenient.
- Define per-branch no-event behavior, neutral actual values, missing/error,
  age/staleness, disabled branch, and resulting column/dtype parity.
- Standardize or document whether EWMA half-life means observations, sessions,
  or elapsed calendar days. Changing a feature meaning requires schema/model
  version compatibility and golden tests, even if column names stay unchanged.
- Route branch diagnostics into W02 results; an optional failure must not be
  silently indistinguishable from an observed neutral signal.

### C. Version/model provenance

- Reconcile `FEATURE_SCHEMA_VERSION`, catalog schema, model feature lists,
  dtypes, identity, and available input snapshots. A constant version alone is
  not evidence of input reproducibility.
- Persist only the minimum approved metadata needed to identify feature semantics,
  availability rules, and source snapshots. Auxiliary model/run manifests stay
  outside the lake and have Pydantic write-boundary models.
- Detect incompatible existing models before inference; do not pad changed
  semantics to null and pretend compatibility. Owner chooses fail/retrain policy.
- Keep label availability from W03 out of predictors and enforce as-of training
  eligibility. Preserve latest-row null labels for scoring.

## Migration plan required before implementation PR approval

1. Inventory existing Gold/Platinum rows and model artifacts on a copy; report
   count and ambiguous identities without modifying production.
2. Derive market from reliable original paths/config/provenance only. Bare
   ticker presence in current config is not sufficient historical proof when
   a symbol is ambiguous or has moved markets.
3. Reject/report ambiguous rows for explicit owner resolution; never guess a
   market to make migration pass.
4. Describe versioned snapshot backup, new schema/key creation or rewrite,
   compatible-reader rollout, verification, and rollback ordering. Existing
   duplicates that become distinguishable require a deliberate reconstruction
   policy; absent source data cannot be recreated by metadata alone.
5. Test migration on temporary/copy tables. Production execution requires
   separate authorization with market/ticker/date scope and no dry-run writes.
6. Identify affected models, predictions, reports, and historical feature repair
   needs. Do not automatically retrain or rewrite all history.
7. Retain rollback snapshots; do not vacuum in the migration window.

## Required tests

- Same ticker string in two markets with different price paths: independent
  indicators/targets, two Gold and Platinum identities, separate model lookup.
- US-only enrichment does not cross into the other market's same-named ticker.
- Bare-symbol ambiguity fails; unambiguous legacy single-market call behaves as
  documented. Parameterized reads isolate requested identity.
- Legacy Gold/Platinum/model fixtures: approved reader compatibility or actionable
  refusal; no invented identity or timestamp.
- Migration rerun idempotency, rollback, ambiguous-row report, and row/key counts.
- SEC filing before window start carries forward; later filing is invisible;
  multiple same-day records deterministic.
- Macro observation on a non-feature date carries to the eligible next session;
  prior-state leading edge correct; future-release observations excluded.
- Actual neutral/zero versus missing/error/stale/disabled branch distinguishable.
- EWMA golden cases across no-event sessions/weekends; no column/dtype drift.
- Label availability and provenance columns never become predictors.
- Pipeline dry-run/migration plan persists nothing and performs no processing.

## Verification and accompanying work

Run the affected feature, writer/schema, loader/forecasting, ML job, signal,
backtest, API, CLI, catalog, and import-boundary suites with the index's safe
marker selections. New cross-module migration tests use temporary paths and
mocked models/providers. Install optional groups deliberately; no live fetch.

Required docs: accepted ADRs, migration note, canonical architecture/data-flow/
pipeline contracts, feature/ML/user guides, ingestion identity rules, API/CLI
compatibility, model-reuse policy, and catalog regeneration. Follow the schema
skill's full reader/validator/catalog/migration checklist.

## Acceptance and stop conditions

- [ ] Identity and temporal ADRs accepted before boundary-changing code.
- [ ] Consumer inventory complete; no ticker-only hidden shared lookup remains.
- [ ] Migration/reader/model behavior tested on copies, not production.
- [ ] Temporal joins use available preceding state without future leakage.
- [ ] Missingness/age/semantics explicit and version-compatible.
- [ ] Historical vintage limitations stated; unverified feeds not certified.
- [ ] Catalog regenerated; W05 receives final physical schema and relationships.
- [ ] Findings closed only for evidence-supported guarantees; owner-approved
  provider limitations remain tracked rather than falsely marked solved.

Stop and return a decision packet if approval, reliable historical identity,
release availability, or rollback material is missing. A safe diagnostic and
blocked migration are preferable to silently destructive completion.

## Progress

- Implementation: not started.
- Accepted ADR links: none yet for this wave.
- Migration authorization/test evidence/model rollout: not recorded.
