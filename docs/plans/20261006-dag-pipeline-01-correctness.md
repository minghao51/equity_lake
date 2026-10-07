# W01 — Immediate DAG and Vendor Correctness

**Status:** Implemented — pending review (post-review fixes applied); commit authorization required  
**Priority:** P1 first  
**Depends on:** [Index and D01](20261006-dag-pipeline-00-handoff-index.md)  
**Findings:** F01, F02, F13, F15; simultaneous-event part of F14 and routing checks from F20  
**Next:** [W02](20261006-dag-pipeline-02-outcomes-and-coverage.md)

## Objective

Make the currently requested Hamilton graph actually enforce its boundaries,
make its output compatible with every enrichment branch, and correct vendor
corporate-action parsing. This wave must not expand feature/model scope,
launch historical repair, or enable adjusted feature prices.

## Read before proposing changes

- `docs/README.md`, `AGENTS.md`, canonical pipeline contracts and data-flow.
- `src/equity_lake/features/pipeline.py` (`TECHNICAL_FEATURES`, both compute methods).
- `src/equity_lake/features/dag/{raw_01,clean_02,features_03,enrichments_04,schemas,polars_validators}.py`.
- `src/equity_lake/features/{engineering,indicators}.py`, `features/__init__.py`.
- `src/equity_lake/ingestion/writers.py`, `validation/schemas.py`.
- `src/equity_lake/sources/base.py`, `core/schemas.py`, ADR-0011.
- Existing DAG, feature job, corporate-action, router, and write-boundary tests.
- Skills: `verify-equity-pipeline`, `change-equity-schema`, `catalog-generator`.

## Ownership and suggested commits

Own the DAG/feature files above, `sources/base.py`, affected schema/validator
files, associated tests, and accompanying feature/source docs. Coordinate any
changes to `pipeline.py` or outcome types with W02; do not change them casually.

1. Date-key contract plus full two-phase enrichment tests.
2. Connected validation and finite-value enforcement.
3. Vendor-shaped actions and simultaneous-event parsing.
4. Schema routing fix only if the new route tests demonstrate a defect.

Use separate PRs if review becomes broad. Catalog regeneration is coordinator
owned after DAG integration; frontend/schema changes beyond necessary node
updates belong to W05.

## Approval gate: D01

Recommend `pl.Date` for all EOD join keys, matching persisted Gold and date
partition semantics. Preserve true event timestamps as separate fields.
Recommend failure of invalid required input rather than silent row deletion;
optional enrichment failure must degrade visibly rather than fail independent
technical features. Owner must approve exact invalid-row policy first.

Do not change a public compute method's dtype silently: update compatibility
notes and tests that currently assert Datetime. Do not globally cast every
timestamp to Date or destroy event-time provenance.

## Implementation checklist

### A. Normalize and exercise the real enrichment path

- Build a fixture with at least 80 valid observations using the public technical
  compute method, then pass its output into the public enrichment method.
- Normalize EOD keys at the shared feature boundary and each external table
  query boundary, including SEC filing-date asof keys. Cover Date, Datetime,
  and supported string input; reject malformed keys instead of creating nulls.
- Preserve one row per feature identity/date and existing row order semantics.
- Every enrichment branch must isolate expected availability/query errors.
  Do not catch all programming errors and call them no-data. Preserve schema
  defaults where intended, but emit availability/error detail for W02 to expose.
- Avoid enabling Hamilton cache as a workaround or changing query date bounds
  here; state-carry rules are W04.

### B. Connect one cleaned input to all technical nodes

- Inspect the actual upstream closure of `TECHNICAL_FEATURES` and targets.
- Make all column extractors, returns, indicators, calendar features, and targets
  consume the same validated/ordered frame. Validate identity/date ordering and
  duplicate handling before rolling/shift calculations.
- Do not return filtered series from isolated nodes with different row counts;
  that produces positional misalignment. Merely adding validation nodes to the
  requested outputs is insufficient.
- Replace claimed enforcement through 100 sampled rows with full vectorized
  checks at the real boundary. Existing Pydantic sampling can remain a clearly
  labeled diagnostic, without dead index bookkeeping.
- Validate the emitted frame after documented warm-up filtering. Null warm-up
  values and the unavailable last target are allowed; invalid identity, required
  emitted values, invalid price relationships, and infinity are not.
- Maintain an explicit distinction between bad data and insufficient history.
  Do not trigger history backfill for a programming/schema error.

### C. Numeric and quality-route checks

- Audit all percentage-change/division nodes, including social mentions,
  volume ROC, bands, and intraday/overnight returns.
- Choose documented undefined-ratio semantics (prefer null plus reason over an
  invented finite value), then verify no infinity enters persisted emitted
  features or ML matrices. NaN/null handling must match model support.
- Add real-schema writer-route tests for price, news, social, macro, articles,
  analyst/SEC, features, and predictions. `_quality_data_type` currently groups
  social with news; verify against actual required columns before claiming the
  route is correct. If incompatible, add the smallest dedicated schema and
  registry mapping, with source/write/validation tests and catalog companions.
- F20 ownership boundary: W01 tests quality-schema routing and fixes any demonstrated
  registered-schema incompatibility. W06 owns systematic natural-key presence,
  not-null, within-batch duplicate, and nullable-key enforcement across writers;
  it reuses these route tests rather than duplicating W01. Do not label all
  untyped datasets as validated merely because route diagnostics pass.

### D. Parse actual corporate-action events

- Normalize yfinance `Stock Splits` explicitly; accept alternative names only
  if intentionally supported and tested. Use production-shaped pandas fixtures.
- Expand simultaneous dividend/split rows into two lake events, preserving the
  existing split-ratio inversion and `(ticker,ex_date,action)` identity.
- Use the documented UTC convention for `ingested_at`; preserve timestamp dtype.
- Do not silently discard valid historical rows because one future event is
  present. First pin current all-or-nothing behavior; any changed rejection policy
  needs approval and clear rejected-row reporting.
- Reuse tenacity/throttle seams. Do not add a source retry loop or change the
  existing US-only corporate-action support policy.

## Required regression matrix

| Case | Required assertion |
|---|---|
| Production technical output + each populated enrichment | Matching values survive; key dtype equal; unchanged feature identities |
| Disabled/missing/empty/query-error enrichment | Intended columns/dtypes stable; missing and failed distinguishable |
| SEC asof with Date/Datetime keys | No SchemaError; no later filing joins backward |
| Invalid open/high/low with otherwise valid close/volume | Actual compute path enforces approved policy |
| Invalid row beyond first 100 observations | Sampling cannot hide it |
| Unsorted/duplicate input | Deterministic documented policy before rolling features |
| Warm-up rows and newest target null | Legitimate nulls do not reject a valid output batch |
| Zero-volume/count/band denominator | No infinity in emitted features; undefined semantics pinned |
| Vendor Stock Splits layout | Split-only event appears with correct ratio |
| Same-day split and dividend | Two events survive write/read and existing adjustment helper |
| Dataset writer routing | Actual route frames validate under their registered schemas |
| Pipeline dry-run | No validation profile persistence, processing, backfill, features, or inference |

Tests should verify values and dtypes, not merely column names or mock-call
counts. Add a topology assertion proving the real requested graph traverses
the boundary. Golden technical values on valid ordered input should retain
parity except intentionally approved changes.

## Focused verification

Inspect marker/fixture safety first; no live provider calls.

```bash
uv run pytest tests/unit/features/dag tests/unit/test_hamilton_feature_pipeline.py tests/unit/test_feature_jobs.py tests/unit/test_div_by_zero_guards.py -m "not network and not integration"
uv run pytest tests/unit/test_corporate_actions_ingest.py tests/unit/test_corporate_actions_schema.py tests/unit/test_price_adjustment.py -m "not network and not integration"
uv run pytest tests/unit/test_router.py tests/unit/test_ingestion_writers.py tests/unit/test_validation.py -m "not network and not integration"
```

Then run the index's offline lint/type/catalog/docs gates. Update
`docs/user-guide/20260829-features-and-enrichments.md`, source ingestion docs,
and actual validation wording in canonical architecture/data-flow. Regenerate
catalog after node/schema changes; do not claim W05's catalog semantics are fixed.

## Acceptance and repair handoff

- [ ] Real default DAG validates before all dependent computations.
- [ ] Technical/enrichment temporal keys agree and populated data is preserved.
- [ ] All affected optional branches degrade predictably, without hiding bugs.
- [ ] Undefined numeric values cannot silently poison persisted ML inputs.
- [ ] Vendor split and simultaneous events are correctly mapped and tested.
- [ ] Required schema-route changes have constants, validators, catalog, and docs.
- [ ] Historical repair need recorded: affected zero-filled features/events,
  bounded scope, potential model invalidation. No repair executed automatically.
- [ ] Delivery packet, audit register, and index ledger updated.

Rollback: revert the code and generated catalog changes; do not delete lake
partitions or restore production data automatically. If output semantics/schema
changed, document the version compatibility boundary before deployment.

## Progress

- Implementation: **complete (2026-10-06)** — all four scoped changes landed on
  the working tree (no commit; owner authorization pending).
  1. Date-key contract: `raw_01.date` normalizes to `pl.Date` (malformed keys
     raise); `compute_enriched` and every external query boundary normalize
     keys (`news`/`social`/`enriched`/`analyst`/`SEC filing_date`/`macro`).
  2. Connected validation: `validated_ohlcv` enforces the D01 policy with
     full-frame vectorized checks (raise on invalid required input; stable
     sort + deterministic keep-first dedupe; Pydantic sampling diagnostic-only);
     every `features_03` node consumes `validated_ohlcv`; `validated_features`
     and `compute_technical` enforce the emitted frame via the shared
     `validate_emitted_frame` (identity, finite, row-defined completeness;
     warm-up/last-target nulls allowed). `volume_roc_5` and `social_momentum`
     use null-for-undefined zero-baseline semantics.
  3. Corporate actions: vendor `Stock Splits` layout mapped (legacy `Splits`
     kept); simultaneous dividend+split expands to two events; `ingested_at`
     is naive UTC; future-`ex_date` all-or-nothing behavior pinned by test
     (policy change deferred to W03/F14).
  4. Quality routing: dedicated `SocialSentimentSchema` + `social` registry
     route; writer mapping and route tests fixed (`test_ingestion_writers.py`
     previously asserted social→news).
- D01 approval: **resolved via owner dispatch ("verify and proceed with the
  plan")** — EOD keys `pl.Date`; invalid required price input fails the
  per-ticker compute; optional enrichment failures degrade visibly (zero-fill
  + error log, A5 schema parity); duplicates keep-first after stable sort.
- Tests/review/commit: focused suites green (see delivery packet in the wave
  review notes); `uv run pytest -m "not network and not integration"` →
  1009 passed / 22 skipped / 3 pre-existing `test_arena.py` failures (fail on
  the untouched baseline; unrelated to W01 — missing backtest runs).
  Catalog regenerated via `uv run equity catalog-generate` (edge growth
  100→372 reflects the pre-existing ancestry-based edge extraction, F19/W05,
  applied to the connected topology). ruff/format/mypy at baseline parity
  (mypy: 3 pre-existing `ml/registry.py` errors, W07/F28; the pre-existing
  `corporate_actions.py` format failure was incidentally resolved by the
  format run). No commit made.
- Historical repair need recorded: enrichments that silently zero-filled on
  Datetime/Date key mismatch (news, social, analyst, SEC before W01) may have
  zero-valued enrichment columns in stored gold history for affected dates;
  splits were never ingested before W01 (yfinance `Stock Splits` mapping was
  dead). No repair executed — inventory and scoped repair require separate
  owner authorization (D09).
- Remaining blockers/deviations for W02: none from W01. W02 additionally
  needs D02 (default-scope/cap policy) approval before changing the default
  ticker scope.

### Review outcome and post-review fixes (2026-10-07)

Three read-only reviewers (zai/glm-5.3-flash) returned: (A) DAG/validation —
blocked on one critical; (B) corporate actions/routing — ready; (C) governance
— ready with accepted limitations. All critical/warning items were addressed:

1. **Critical (A), fixed:** `compute_technical` zipped raw passthrough columns
   (input order) against indicator columns (validated frame order) — silent
   row misalignment for unsorted input (26/30 rows demonstrated) and a
   ShapeError on duplicate identities. Identity and price columns are now
   emitted from `validated_ohlcv` itself (single row-order authority; see
   `_RAW_PASSTHROUGH_NODES` in `features/pipeline.py`), with public-path
   unsorted/duplicate alignment regression tests added.
2. **(C) Repair inventory extended** with the two missing historical impacts:
   inf-poisoned stored gold (`volume_roc_5`/`social_momentum` pct-change
   could persist `inf`; `validate_emitted_frame` protects only new emissions)
   and historically blocked social writes (the news-schema routing rejected
   every social frame pre-W01, so silver social history may be missing
   outright — distinct from key-mismatch zero-fills).
3. **(A) enriched-sentiment populated two-phase coverage added** (real Delta
   `processed_articles` table through the public path).
4. **(B) Round-trip test now routes through `upsert_dataset`** so the
   pointblank corporate-action schema gates the simultaneous two-event frame
   exactly as production does; `ingested_at` dtype pinned to
   `pl.Datetime("us")`.
5. **(A) Silver-boundary NaN rejection added** (NaN is not null in Polars; it
   now fails at Silver naming the raw column). **(C)** monkeypatch cleanup,
   stale status header fixed, pipeline-contracts migration note added,
   direct-DAG-caller macro note added to the user guide.

**Recorded limitations (accepted, owner-visible):**

- Missing-vs-failed enrichment distinction exists only in structured logs;
  zero-filled results for query-error and no-data are intentionally identical
  (handoff-08 A5 schema parity). Surfacing availability/error detail in run
  results is W02's outcome-reporting scope (F08/F10/F25). W02 should also
  note: direct DAG callers bypassing `compute_enriched` with Datetime-keyed
  frames lose macro columns on the caught error path (schema instability,
  pre-existing).
- Catalog `technical_features`/`enriched_features` column descriptions still
  document `date` as `datetime` (F18/W05); W01's `pl.Date` output deepens
  that mismatch — cheap alignment when W05 lands.

**Post-fix gate re-run (2026-10-07, implementer-attested):** focused suites
374 passed; `uv run pytest -m "not network and not integration"` → 1013
passed / 22 skipped / 67 deselected / 3 failed (`test_arena.py`, same
pre-existing set, verified failing on the untouched baseline); `-n auto`
parity; ruff check/format clean; mypy at the 3-error pre-existing
`ml/registry.py` baseline; catalog regenerated unchanged (52 nodes / 372
edges); mkdocs build with only the 3 documented pre-existing warnings;
`git diff --check` clean. Diff: 25 files, +1617/−313.
