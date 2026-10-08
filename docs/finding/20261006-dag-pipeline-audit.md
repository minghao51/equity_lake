# DAG Pipeline Audit — Findings and Resolution Register

**Date:** 2026-10-06
**Baseline:** `522d8fa8a2c23d59d217fab37a2f9a42fb5d7f36`
**Status:** Open findings; documentation only, no fixes implemented
**Implementation:** [Agent handoff index](../plans/20261006-dag-pipeline-00-handoff-index.md)

## Scope and interpretation

Read-only review of the Hamilton feature DAG and its EOD orchestration,
ingestion, storage, validation, ML loading, catalog, monitoring, tests, and
canonical docs. This engineering-audit directory was requested by the owner;
it is unrelated to application FindingCard artifacts under `data/findings/`.

P1 means fix before relying on affected production outputs. P2 means an
important contract, operability, or maintainability gap. Evidence labels:

- **Reproduced:** exercised locally with synthetic frames/mocked boundaries.
- **Code-confirmed:** the path and condition were read; not a live-provider or
  production-data reproduction.
- **Risk / policy gap:** an assumption needs a decision or targeted test; no
  assertion that production corruption has already occurred.

Line numbers are from the review baseline. Re-read named functions before
patching. The repository's full live-source reliability and end-to-end
point-in-time correctness were not certified. Passing existing tests is not
proof that the missing contracts are covered.

## Existing strengths to preserve

- Local-first numbered Delta medallion storage, shared merge/write seams, and
  mostly explicit module boundaries.
- Hamilton technical decomposition and parameterized indicator families.
- Pipeline dry-run skips persistence, backfill, LLM processing, features, and ML.
- Required price-source failures block downstream features and inference.
- Existing tests cover many source, Delta, CLI, and orchestration paths.
- Catalog freshness CI already exists; in-memory regeneration matched the
  committed catalog. The catalog issues below concern meaning, not staleness.
- Technical targets are identified and excluded from ML feature lists. This
  does not establish historical source-vintage or availability correctness.

## Findings inventory

All IDs below are **Open** at publication. Wave links resolve through the
[handoff index](../plans/20261006-dag-pipeline-00-handoff-index.md).

| ID | Priority | Evidence | Finding | Owner wave |
|---|---|---|---|---|
| F01 | P1 | Reproduced | Technical Datetime keys mismatch enrichment Date keys | W01 |
| F02 | P1 | Reproduced | Boundary validators are disconnected; sampled invalid rows are not removed | W01 |
| F03 | P1 | Reproduced + code-confirmed | Daily persisted targets never finalize after the next session | W03 |
| F04 | P1 | Code-confirmed | Non-US explicit ticker recovery scope is dropped | W03 |
| F05 | P1 | Code-confirmed | Existing-date checks mistake partial ticker partitions for complete scope | W03 |
| F06 | P1 | Code-confirmed | Hidden default ten-ticker cap biases multi-market coverage | W02 |
| F07 | P1 | Code-confirmed | Missing feature tickers/rows can disappear from successful stages | W02 |
| F08 | P1 | Reproduced | Optional failures appear as successful existing-data skips | W02 |
| F09 | P1 | Reproduced | Parallel write exceptions escape per-market isolation | W02 |
| F10 | P2 | Code-confirmed | Fetch errors, rejected schemas, and empty data collapse to None | W02 |
| F11 | P2 | Code-confirmed / policy gap | Different market sessions are treated as one required date | W03 |
| F12 | P2 | Code-confirmed / risk | Market provenance is dropped; shared identity is ticker-only | W04 |
| F13 | P1 | Reproduced | yfinance Stock Splits column is not recognized | W01 |
| F14 | P2 | Code-confirmed / risk | Corporate-action overrides and global watermark lose events | W01/W03 |
| F15 | P2 | Reproduced for social expression | Zero-baseline percentage changes can create infinity | W01 |
| F16 | P2 | Code-confirmed | Enrichment windows discard preceding state and off-calendar observations | W04 |
| F17 | P2 | Policy gap | Missingness, availability, EWMA timebase, and historical vintages are underspecified | W04 |
| F18 | P2 | Code-confirmed | Catalog partition/column/dtype descriptions disagree with output contracts | W05 |
| F19 | P2 | Code-confirmed | Dataset lineage is layer adjacency rather than actual consumption | W05 |
| F20 | P2 | Reproduced for price keys; other gates need tests | Key not-null/uniqueness and dataset-quality routing need enforcement | W01/W06 |
| F21 | P2 | Code-confirmed / risk | Broad schema-error detection triggers full-history evolution rewrite | W06 |
| F22 | P2 | Code-confirmed / risk | Unbounded corpus processing; Silver read failures reset dedup state | W06 |
| F23 | P2 | Code-confirmed | No-recent-data monitoring branch cannot be reached | W05 |
| F24 | P2 | Code-confirmed | Fresh validation instances cannot detect writer-boundary drift | W05 |
| F25 | P2 | Code-confirmed | Run/recovery results lack actionable scope, availability, and persistence detail | W02/W03 |
| F26 | P2 | Code-confirmed | Repeated filtering, duplicated schemas/market lists, and repeated feature calls | W07 |
| F27 | P2 | Observed in test run | Network/serial/default test-selection policy does not match documentation | W07 |
| F28 | P2 | Observed in tool run | Formatting and type gates are not clean in audited environment | W07 |

## Detailed evidence and required outcomes

### F01 — Enrichment date mismatch

`src/equity_lake/features/dag/raw_01.py:36–41` converts Date to Datetime.
`enrichments_04.py:232,285,379,437,512` joins use temporal keys without the
normalization already present in the macro path (`:607–611`).

Reproduction: compute technical features, provide a matching Date-typed news
result, and merge. The `SchemaError` is caught and `news_count` becomes zero.
Analyst merge on the same input raises outside its query try block. News/social
therefore lose data while some optional enrichments fail core computation.
Existing enrichment fixtures use Date, unlike real technical outputs.

Required outcome: consistent EOD keys; populated/no-data/error tests for every
branch; pipeline failure/partial reporting distinguishes missing from broken.

### F02 — Validation does not gate the real graph

`src/equity_lake/features/pipeline.py:33–74` requests raw series and technical
nodes. Upstream inspection for that request list found neither
`validated_ohlcv` nor `validated_features`. `clean_02.py:28–61` additionally
calculates invalid sampled rows and returns the original frame unchanged.
`features_03.py:235` samples and warns rather than enforcing output validity.

A frame with `open=0` survives the helper and default computation. Connecting
only a validation terminal would not make its output feed other indicators.
Required outcome: one shared cleaned input, row alignment through all nodes,
and checks on the persisted output; sampling remains diagnostic only.

### F03 — Incremental targets remain null

`src/equity_lake/features/__init__.py:58–59` sets the query end to output end;
`:83–96` persists only the requested output window. The target in
`features/dag/features_03.py:291` uses the next observation. Three progressively
longer histories each produced a null last target. The daily writer never
revisits yesterday's label. `ml/forecasting.py:523` trains direction targets
from the persisted column.

Range-generated interior labels can exist, so this is not a claim that every
current Gold row is null. Required outcome: finalize historical labels after
availability, preserve old feature values, honor training as-of cutoff, and
make label-only repair scoped and explicit.

### F04/F05 — Recovery and completeness scope

`src/equity_lake/ingestion/orchestrator.py:41–52` honors explicit ticker lists
for US but not the other four price markets. The corresponding factories in
`ingestion/router.py:85–154` also omit that argument. Logging the scoped
recovery call is insufficient proof of scoped provider calls.

`orchestrator.py:96–144` checks date presence and sampled column validity, not
requested ticker presence. One valid row can suppress a necessary scoped fetch.
Required outcome: exact provider scope and ticker-aware completeness, including
newly added tickers, corrupt rows, empty explicit lists, and zero recovered dates.

### F06/F07 — Requested feature coverage is not enforced

`src/equity_lake/pipeline.py:295` slices flattened configured tickers to ten.
`features/engineering.py:137–154` skips short-history tickers and drops critical
null rows. `pipeline.py:185` reports success and derives inference scope from
surviving rows, without surfacing omitted requested tickers.

Required outcome: approved default scope, explicit requested/produced/missing
coverage, stable reasons, and no loss of requested scope after retry. A user
choosing a deliberate limit must see it in the plan and results.

### F08–F10 — Failure handling hides causes or aborts aggregation

`src/equity_lake/ingestion/orchestrator.py:55–60` maps optional failures to
`SKIPPED_EXISTING`, whose `succeeded` property is true. A reproduced macro timeout
became `partial=false`, with no optional failures. `pipeline.py:87–96` exposes
only statuses and loses `SourceOutcome.error` details.

The default parallel path's `_write_market` call (`orchestrator.py:239–242`)
lacks the exception handling of the serial path (`:244–275`); a synthetic
write `ValueError` escaped the entire run. `router.py:424–432` catches fetch
exceptions and returns None, causing `empty_frame` outcomes and misleading
fetch-success summaries.

Required outcome: equivalent isolation in both modes; source outcomes retain
causes; optionality controls blocking/exit status rather than hiding failure.

### F11/F12 — Multi-market scheduling and identity

The EOD pipeline passes one date to every required market without first
classifying sessions. A closed market's empty response can block an open one.
This is code-confirmed; no live holiday run was performed.

`src/equity_lake/features/engineering.py:74` adds market to its union view,
then `:109–120` projects it away. Rolling calculations, Gold/Platinum merge
keys, model filenames, and feature loading are ticker-only. If symbols overlap,
interleaved observations can contaminate indicators and shared-table identity.
No actual production collision was demonstrated.

Required outcome: session policy and unambiguous identity. Changing shared keys
requires an accepted ADR, reader/migration compatibility, and model handling.

### F13/F14 — Corporate actions

`src/equity_lake/sources/base.py:450` checks `Splits`, while installed yfinance's
`get_actions()` emits `Stock Splits`. The mocked test helper in
`tests/unit/test_corporate_actions_ingest.py:50` repeats the wrong layout. A
real-vendor-shaped split-only fixture produced zero rows without a network call.

The same fetcher overrides a dividend with a split on a simultaneous event.
`ingestion/corporate_actions.py:39–53` uses a market-wide max ex-date, and the
fetcher accepts only dates strictly above it. Newly scoped tickers, corrections,
and late older events can be lost. These overlap conditions need new tests.

Required outcome: one row per action, vendor-shaped fixtures, per-ticker scoped
watermarks or an explicitly bounded overlap strategy. Adjustment opt-in remains
deferred by ADR-0011; do not silently alter feature prices.

### F15–F17 — Feature numerics and temporal semantics

`src/equity_lake/features/dag/enrichments_04.py:296` computes five-observation
percentage change in mention counts; zero-to-positive creates infinity.
`ml/forecasting.py:760` casts numeric data without a finite-value gate. Audit
other percentage-change/division nodes, not just that expression.

SEC query bounds (`enrichments_04.py:480`) omit the earlier filing needed for
backward-asof state. Macro processing (`:613–615`) keeps only feature dates
before forward filling, potentially discarding observations on other dates.
Leading missing values often become numeric zero, which is not evidence of
neutral sentiment or actual VIX=0.

Sparse event EWMA half-lives, calendar-day versus session timebases, same-day
release cutoffs, processing time versus event time, and revised FRED vintages
are not end-to-end specified. No future-label feature inclusion was found in
technical indicators, but historical vendor revisions and availability were
not certified. Required outcome: carry/staleness/availability contracts and
honest historical limitations, with metadata or vintage acquisition only after
approval.

### F18/F19 — Catalog meaning

`src/equity_lake/catalog/models.py:23` defaults to `date=`. Corporate-action
entries at `catalog/datasets.py:231,304` do not override it although writers
partition by ex_date. Gold definitions at `:337–368` omit the target and contain
physical dtype descriptions that differ from persisted Date/int outputs and
optional enrichment columns.

`catalog/builder.py:48–67` assigns every preceding layer's datasets as upstream.
For example news sentiment lists all five price datasets, macro, articles, and
raw actions as inputs. Hamilton node extraction also uses upstream ancestry;
direct dependency versus transitive ancestry should be clearly distinguished.

Required outcome: true writer/schema metadata, actual dataset relationships,
explicit lineage semantics, and frontend compatibility. Existing regeneration
CI catches stale serialization, not wrong definitions.

### F20/F21 — Write-boundary and evolution gaps

`src/equity_lake/validation/schemas.py:60–83` lacks non-null price keys. Direct
schema checks accepted a frame with one null ticker and one with a null date.
Writer column-presence/all-null checks do not reject individual null keys.

Natural-key uniqueness is uneven outside price/news; untyped table paths need
per-dataset tests. Re-audit `_quality_data_type` and schema registration against
actual social/article/analyst/SEC frames before assuming all routes are valid.
Duplicate merge behavior was not reproduced uniformly across delta-rs versions;
pin actual create and update semantics before choosing reject/dedupe policy.

`src/equity_lake/storage/delta.py:172–193` treats any error containing schema or
column as a mismatch and then materializes and overwrites full history before
retry. This is broad and expensive; narrow-source and concurrent-evolution
behavior needs tests. Concurrent data loss was not reproduced and remains a
risk, not a demonstrated fact. Required outcome: specific error handling,
validated keys, bounded/authorized evolution, and snapshot-safe operations.

### F22–F24 — Processing and monitoring

`src/equity_lake/ingestion/bronze_silver.py:85,165–185` loads the Bronze corpus
and all processed IDs. A failed Silver read returns an empty set, potentially
resubmitting the corpus to paid processing. Empty Bronze is also reported as
failure rather than an intentional no-work outcome. Required outcome: bounded
selection and safe dedup-read failures, with progress and no-work reporting.

`monitoring/health.py:257–277` groups rows after filtering to a recent window;
SQL never emits a zero-row group. Its intended no-recent-data branch is
unreachable. The freshness check can mask this independently; do not claim the
entire monitoring system always passes stale markets. Missing/broken market
scans also deserve per-market isolation tests.

`ingestion/writers.py:141` constructs a fresh ValidationPipeline per call;
`validation/pipeline.py:79` requires an existing named baseline for drift.
Thus writer-boundary automatic drift is inactive. Required outcome: explicit
policy, not an accidental promise of drift detection.

### F25/F26 — Operability and maintainability

Recovery return counts are discarded, run results omit resolved instrument
scope and per-ticker exclusion reasons, and inference success can be distinct
from persistence success without clear per-ticker labeling. Direct ingestion
and backfill dry-runs can also report written despite no write. Preserve the
published dictionary result shape while adding meaningful details and testing
existing dashboard/result readers.

Hardcoded market-view paths duplicate existing registries. Feature output names,
catalog columns, fallback defaults, and model exclusions can drift. Technical
computation repeatedly filters all rows for each ticker. Normal/recovery feature
calls duplicate arguments. Required outcome: small consistency tests and
measured local DRY changes, not a new framework or premature cache.

### F27/F28 — Test/tooling contracts

The broad unit-path run selected two macro tests requiring network because they
were integration-marked but not network-marked. Pytest defaults do not enforce
all network/serial exclusions described by repository docs. Explicit selection
and mocked provider boundaries are required until repaired.

Formatting failed for `src/equity_lake/ingestion/corporate_actions.py`. Mypy
reported missing W&B `init`, `Table`, and `log` attributes in
`src/equity_lake/ml/registry.py`. Optional dependency/type-discovery conditions
must be diagnosed in the intended environments; no blanket ignore is justified.

## Verification record from the audit

These are historical audit observations, not results of a remediation run.

| Check | Result |
|---|---|
| Focused DAG/pipeline/storage selection, excluding network | 214 passed, 5 deselected |
| `uv run pytest tests/unit -m 'not network and not slow'` | 933 passed, 22 skipped, 30 deselected; not a guaranteed offline selection because of marker gaps |
| `uv run ruff check .` | Passed |
| `uv run ruff format --check .` | One file would reformat: ingestion/corporate_actions.py |
| `uv run mypy src` | Three errors in ml/registry.py; 162 source files checked |
| In-memory `catalog_to_jsonl(build_catalog())` comparison | Equal to committed data/catalog.jsonl |
| Synthetic checks | Missing validator ancestors; retained zero-open row; failed temporal joins; null price keys accepted; escaping parallel writer; misleading optional outcome; missing vendor split row |

Skipped optional tests included LightGBM and polars-backtest. No broad production
backfill, paid processing, lake mutation, migration, or model retraining was
performed by the audit. Synthetic writes, where needed by tests, use temporary
fixtures. The tracked working tree remained unchanged during the audit.

## Resolution register

Implementers update this table without deleting the original evidence. A closed
finding requires regression coverage, docs, compatibility/repair notes, and
review. An owner-approved deferral must name remaining impact and next action.

| IDs | State | Implementation / tests / approval | Remaining impact |
|---|---|---|---|
| F01, F02, F13, F15 | **Fixed (W01, 2026-10-06; reviewed + post-review fixes 2026-10-07)** — Date-key normalization at all enrichment boundaries; connected `validated_ohlcv`/`validated_features` enforcement + `validate_emitted_frame` at `compute_technical` (row-order authority fixed after review); vendor `Stock Splits` mapping; null-for-undefined zero-baseline semantics (`volume_roc_5`, `social_momentum`). Regression: two-phase real-Delta enrichment suite, topology-closure/alignment/invalid-input tests, vendor-shaped corporate-action fixtures through the quality gate. D01 approved via owner dispatch. | Historical repair scope: zero-filled enrichment history, never-ingested splits, **inf-poisoned stored gold** (`volume_roc_5`/`social_momentum` may hold `inf` from pre-W01 pct-change), and **missing silver social history** (news-schema routing blocked every social write pre-W01). Bounded repair needs owner authorization (D09); reviewer verdicts + fresh gate re-run recorded before commit |
| F03–F05, F11, F14 | **F04/F05 and the recovery-detail portion of F25 Fixed (W03 PR1, 2026-10-08; read-only review READY WITH WARNINGS, 0 critical / 4 warnings, all addressed; owner-authorized commit `c0f37ee`); F03 Fixed (W03 PR2, ADR-0014, commit `457ca6d`; read-only review READY WITH WARNINGS, 0 critical / 6 warnings, all addressed 2026-10-08 — as-of in compare/ablate, strict=False tolerance, divergence surfaced, availability-unknown inventory, dead helper, agent cleanup); the label-carrying portion of F25 Fixed in the same commit; the label-availability portion of F17 likewise (broader enrichment availability/vintage/missingness contract remains W04); F11 and the F14 overlap policy remain Open** (F14 simultaneous-event mapping **fixed in W01**; overlap/watermark policy remains W03) — PR1: `ingestion.types.resolve_explicit_scope` assigns explicit scope to all five price markets (single-market arbitrary overrides preserved; multi-market membership resolved from `TickerConfig`; unresolved/ambiguous rejected, no fan-out; `None` vs `[]` preserved); CN/HK-SG/JPX/KRX factories forward the exact scoped list (HK/SG split by suffix, unknown suffix rejected) and the CN primary/fallback/yfinance fallback share one effective list; `_read_partition_coverage` replaces the date-only + sampled-schema checks with one bounded per-ticker read, so a valid partition is skipped only when every requested ticker is present, a partial partition fetches only the missing tickers, and an unspecified incomplete request fetches the fetcher's own default (review warning 1/4 fix); `BackfillResult` freezes range/markets/instruments/authorization and reports calendar trading dates, market-date attempts, succeeded/failed/recovered counts and per-market counts; the feature-recovery stage publishes a structured `recovery` payload while the post-recovery coverage check still fails missing scope. PR2 (F03): `features/labels.py` finalizes the preceding observed session's label on the next observed session (`label_available_date` availability), never spans a missing bar, updates only the label columns through a matched-only seam (no insert, no evolution, no feature-column rewrite), preserves an already-finalized label on replay, and the feature stage reports `labels` (`eligible`/`finalized`/`pending`/`unmatched`). Regression: resolver/factory/CN-seam/real-Delta partial-partition/recovery suites (PR1) and multi-session/real-Delta label suites (PR2). | F11 session policy and F14 corporate-action overlap |
| F06–F10, F25 | **Fixed (W02, 2026-10-08; reviewer pass + post-review fixes 2026-10-08); the label-carrying portion of F25 Fixed in W03 PR2 (`457ca6d`: `labels` block in the feature stage + report-first `equity label-repair`, ADR-0014/D09)** (was: Open, W02/W03 not started) — Hidden cap removed and configured scope resolved in full/reported (`results["scope"]`); explicit empty scope rejected (API `ValueError`, CLI exit 1), never broadened; enrichment-only requests planned-skip features/ML. `run_feature_job` publishes requested/produced/missing with stable reasons and missing scheduled required coverage fails the feature stage. Optional source failures are `FAILED` with cause, surfaced in `optional_failures`+`partial=true` (optionality no longer hides failures); `NO_DATA`/`DRY_RUN` distinct from `WRITTEN`/`SKIPPED_EXISTING`; router no longer swallows fetch/schema errors into `None`; serial/parallel per-market isolation equivalent with retained root causes and per-task correlation contexts. Dry-runs never report written; per-ticker ML `persisted`; recovery counts retained; bronze/SEC no-work is a planned skip. Regression: real-Delta coverage fixtures, isolation/correlation/CLI-exit suites; 15 load-bearing tests fail on the pre-change source. Three read-only reviewers (opencode-go/mimo-v2.6-pro); one critical (exception-path coverage fabrication) found and fixed before handoff. D02 approved by owner 2026-10-07. | W03 continues source completeness: ticker-aware requested-scope checks (a date-level existing partition does not prove complete feature history) and session-aware `not_scheduled`; a required-market `NO_DATA` currently blocks features/ML until D04 session policy lands. W05/W06 own catalog semantics and storage/processing repairs. |
| F12, F16, F17 | Open (the **label-availability portion of F17 Fixed in W03 PR2** (`457ca6d`), ADR-0014: `label_available_date` + strict as-of training/backtest cutoff; the broader enrichment availability/vintage/missingness contract remains W04) | W04, decisions required | Identity and temporal provenance |
| F18, F19, F23, F24 | Open | W05, not started (catalog regenerated by W01 for topology only; semantics unchanged) | Misleading metadata and monitoring |
| F20–F22 | Open (F20 quality-route diagnostics **done in W01**: dedicated `social` schema + registry mapping + real-frame route tests; natural-key/not-null enforcement remains W06) | W06, not started | Write keys, evolution, paid processing |
| F26–F28 | Open | W07, not started (W01 incidentally resolved the `corporate_actions.py` format failure) | Maintenance, performance, gate reliability |

## Historical remediation caution

Existing zero-filled enrichments, unlabeled daily Gold rows, missed action
history, or ambiguous instrument rows may need repair after fixes. Do not
repair automatically while landing code. Inventory affected scope and snapshots,
get authorization, test on a copy, disclose model compatibility, and preserve
rollback. An event-day field alone is not proof that historical data was known
on that day.
