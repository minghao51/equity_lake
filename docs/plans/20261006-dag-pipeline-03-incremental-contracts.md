# W03 — Incremental Lifecycle, Completeness, and Scoped Recovery

**Status:** In progress — PR1 landed (`c0f37ee`), PR2 landed (`457ca6d`, 2026-10-08); PR3 (sessions, D04) implemented + reviewed (READY WITH WARNINGS; owner sign-off pending); PR4 (action overlap) next

**Depends on:** [W02](20261006-dag-pipeline-02-outcomes-and-coverage.md) (landed) and D03/D04 approval

**Findings:** F03–F05, F11, overlap portion of F14, recovery portion of F25

**Next:** [W04](20261006-dag-pipeline-04-schema-and-provenance.md)

## Objective

Make repeated daily runs accumulate usable labels and complete only the
requested market/instrument scope. Closed sessions, partial existing partitions,
new ticker additions, and recovery failures must be explicit and bounded.

## Read and ownership

Read `src/equity_lake/pipeline.py`, `features/__init__.py`, feature/target DAG,
`ml/{feature_loader,forecasting,ablation,comparison}.py`,
`ingestion/{orchestrator,router,backfill,auto_backfill,gap_detection,corporate_actions}.py`,
`sources/{base,cn_hybrid,hk_sg,jpx,krx}.py`, `storage/delta.py`, `core/{calendar,dates,config_models}.py`,
CLI date/scope parsers, and existing incremental/backfill/label/calendar tests.
Read ADR-0011 and the schema/verify/catalog skills.

Own these incremental call paths, necessary label write/reader compatibility,
and tests/docs. Shared identity migration belongs to W04; broad Delta evolution
hardening belongs to W06. If label-safe partial updates require a storage seam
change, scope and test that narrowly here, then hand it to W06 for hardening.

Suggested PRs: scope/completeness; label lifecycle; market-session behavior;
corporate-action overlap. Do not bundle all four into an unreviewable migration.

## Mandatory decisions before implementation

### D03 Label lifecycle and training cutoff

**Recorded (owner-approved 2026-10-08; see [ADR-0014](../decisions/0014-label-lifecycle-and-availability.md)).**
Keep the existing Gold target columns and names. Finalize only the preceding
observed session's labels on a later session, adding `label_available_date`
availability metadata (null/unknown for legacy rows, never fabricated).
"Next observation" is the next exchange session with a valid bar; missing next
bars leave the label pending and are reported. Finalization is a bounded,
key-scoped, label-only update through the canonical storage seam (no nulling,
shrinking, evolution, or row insertion). As-of training/backtest excludes labels
whose availability postdates the cutoff; unknown-availability legacy rows are
strict-excluded by default. `compute_target=False` never finalizes labels;
historical completion is an explicit label-only path. Corrections are
idempotent; broad historical repair is report-first and separately authorized
(D09). `FEATURE_SCHEMA_VERSION` is bumped with the metadata change, catalog
regeneration, and model feature-list exclusion. The remaining recorded
questions below are answered by this decision.

Recommend keeping `next_day_return` in Gold for initial reader compatibility,
but finalizing only target fields for the preceding observed session once the
next bar is available. Retain/introduce approved availability metadata so
training with an historical as-of date cannot consume a label that was unknown
then. A separate label table is an alternative only with an accepted ADR;
do not add one by default.

Record:

- What is a next observation: next exchange session with a valid bar versus
  arbitrary next stored row across a gap? Recommend no silent gap-spanning
  labels; incomplete next-session data remains pending and is reported.
- How corrections update finalized labels and when retraining is required.
- How nullable newest labels and old unlabeled data are treated by training.
- How explicit `compute_target=False` and historical output ranges behave.
- The allowed historical repair range and dry-run/no-inference semantics.

If storage/update/availability contracts change, write an ADR and get it
accepted before code. Bump feature schema version only with the corresponding
reader/catalog/model compatibility plan; do not increment a constant alone.

### D04 Session scheduling

**Recorded (owner-approved 2026-10-08; operational contract, no ADR required).**
Exact explicit-date semantics: markets closed on that date are `not_scheduled`
(a distinct outcome status), never failed and never silently substituted with an
earlier session. `not_scheduled` does not block markets that are open; features
and ML are planned only for scheduled scope, and the run exits zero when all
scheduled required work succeeds. This also confines required-market `NO_DATA`
to a genuinely empty scheduled session. For relative dates, the plan date is
resolved once and each market is classified scheduled/not_scheduled;
per-market "latest session" dates would be a different output contract and
require a separate accepted design. HK/SG keeps union semantics (scheduled if
either exchange is open) with per-instrument coverage reported and the
per-exchange session limitation documented; intersection semantics are not used
to skip a legitimately open instrument. Dry-run reports planned versus
`not_scheduled` scope and writes nothing.

Recommend exact explicit-date semantics: markets closed on that date are
`not_scheduled`; no silent substitution of prior sessions. For relative dates,
resolve and report the planning calendar/market and every scheduled scope.
If the owner wants latest-session-per-market dates instead, stop and design the
per-market output/result contract before implementation.

Record mixed HK/SG session behavior. Existing `is_trading_day` uses market union
semantics, whereas gap detection uses exchange intersection. Do not indiscriminately
reuse intersection to skip a legitimately open instrument's session. Missing
session resolution remains an explicit limitation until mapped appropriately.

## Implementation checklist

### A. Exact ticker scope through providers and recovery

- Make explicit scope reach US, CN, HK/SG, JPX, and KRX factory/fetcher seams.
  Verify CN hybrid primary/fallback fetchers receive the same effective list.
- Retain a distinction between None and an empty list. Do not expand empty
  lists or unsupported overrides to configured/full-universe pulls.
- For multi-market explicit ticker lists, resolve membership from approved
  metadata. Until W04 adds market-qualified requests, reject ambiguous/unresolved
  membership rather than forwarding every ticker to every provider or guessing.
  Preserve supported single-market arbitrary ticker overrides.
- Freeze the resolved recovery scope before the date loop. Log and return range,
  markets, instruments, authorization, attempted/succeeded/failed counts, and
  retried feature coverage. Count trading dates and market-date work separately.
- Zero recovered dates is not success by assertion; use the post-recovery
  completeness check and expose its result. Retry only the affected scope.
- Prefer exchange-session iteration to warning for every weekend, without
  changing existing calendar semantics silently.

### B. Ticker-aware existing-data and history checks

- Verify requested ticker presence for the relevant scheduled date, plus the
  approved usable-bar criteria. Do not use row count or LIMIT 100 as completeness.
- Reuse write-contract validation semantics; do not duplicate a second notion
  of valid price rows. Bound scans by requested keys and dates.
- Fetch only missing/rejected scoped instruments where the provider supports it;
  otherwise explicitly refuse or document a scoped fallback, never silently
  broaden. Add late-added ticker coverage tests.
- Detect insufficient history per ticker, not only an all-empty feature frame.
  Recovery requires the existing explicit authorization even for partial history.
- Preserve W02 requested coverage before/after recovery. Missing scheduled data
  after recovery remains a required failure, with per-ticker reasons.

### C. Finalize labels without rewriting historical features

- Add a multi-session fixture exercising public daily feature jobs and real
  temporary Delta writes. Do not rely solely on computing targets on a range.
- On session D, finalize eligible session D-1 labels using information available
  by D. Keep session D pending. Respect instrument calendars, not calendar-day
  arithmetic across weekends/holidays.
- Preserve the original D-1 technical/enrichment feature values and provenance.
  Never regenerate past enrichments against today's mutable source state as a
  side effect of label completion.
- Use the canonical storage seam for a bounded, key-scoped target update. If
  partial update support is needed, make matched-column updates explicit and
  reject invalid/missing target rows; do not use an all-column merge with a
  narrower source that nulls unrelated fields or causes schema rewrite.
- Apply training/backtest availability cutoffs before building labels/targets.
  Persisted labels finalized later must not become retrospectively available
  merely because the row's feature date precedes training end.
- Keep target/availability metadata excluded from inference feature selection;
  pin both v1 direction and v2 meta-label behavior.
- Define a bounded repair/report mechanism for existing unlabeled rows. A new
  CLI requires native Typer/help/tests/user docs. No automatic broad repair.

### D. Sessions and corporate-action overlap

- Plan which requested price work is scheduled before treating empty fetch as
  failure. A scheduled source failure blocks dependent work; a closed market
  does not become a fake successful write.
- Features/ML must be planned only for scheduled scope. Existing skip-features
  inference must verify suitable stored features for the requested session.
- Replace market-global exclusive action watermarks with per-ticker scope or
  an owner-approved bounded overlap. New tickers require their own history;
  a recent event on another ticker cannot suppress it.
- Include late corrections/older events in bounded overlap and rely on natural-key
  idempotency. Surface provider limitations outside the overlap; do not promise
  unlimited historical correction detection.
- Retain US-only actions unless expansion is separately approved. No automatic
  feature adjustment or extra corporate-action source is introduced.

## Required regression scenarios

| Scenario | Acceptance assertion |
|---|---|
| One ticker through three EOD sessions | Prior labels finalize; newest stays pending; prior feature columns unchanged |
| Friday to Monday / exchange holiday | Next session, not next calendar day, controls label availability |
| Missing expected next-session bar | Label remains pending or approved gap policy is explicit |
| Historical training as-of before label availability | Later-finalized target is excluded |
| Rerun D / replay older D | Idempotent keys and stable prior feature values; no future information introduced |
| Label-only update source | No feature-column nulling/schema shrink and no extra rows |
| One valid ticker already stored, second absent | Only complete scope skips; missing ticker gets scoped fetch |
| Five price-market factory routes | Provider constructor and fallback receive exact scoped tickers |
| Explicit empty/ambiguous multi-market list | Rejected or approved no-work; never unscoped fetch |
| Partial history with and without authorization | No unauthorized recovery; approved recovery targets missing scope |
| Zero/partial recovery success | Counts and remaining coverage visible; no fabricated success |
| US holiday with another market open | Closed source not scheduled; open required work remains eligible |
| HK/SG differing sessions | Documented instrument/session policy, no false market-wide coverage claims |
| New action ticker / late correction / overlapping rerun | History not suppressed by another ticker; stable key upsert |
| Pipeline and repair dry-run | No writes, recovery, paid processing, inference, or repair persistence |

## Verification and docs

```bash
uv run pytest tests/unit/test_backfill.py tests/unit/test_auto_backfill.py tests/unit/test_gap_detection.py tests/unit/test_calendar.py tests/unit/test_router.py -m "not network and not integration"
uv run pytest tests/unit/test_feature_jobs.py tests/unit/test_price_forecasting.py tests/unit/test_ml_ablation.py tests/unit/test_ml_comparison.py tests/unit/test_corporate_actions_ingest.py -m "not network and not integration"
uv run pytest tests/integration/test_pipeline_orchestrator.py -m "not network"
```

Add a dedicated incremental-session integration test if the existing module is
insufficient. Run shared gates; regenerate catalog for target/schema/pipeline
changes. Update pipeline contracts, data-flow, ingestion/pipeline/feature guides,
ML rigor guide, auto-backfill architecture, and any new CLI help-scan coverage.

## Migration, rollback, and exit gate

Document target metadata additions and how existing Gold rows with unknown
availability are handled; do not fabricate timestamps or labels. Reader
compatibility must distinguish legacy unknown availability from known available.
Inventory repair scope and expected model invalidation before owner approval.

Roll back code and schema-aware readers together. Preserve Delta snapshots
needed for approved repair rollback; do not vacuum them. Additive columns must
not break old readers, but changed label semantics may require model-version
rejection/retraining, not transparent reuse.

- [ ] D03/D04 decisions and necessary ADRs accepted.
- [ ] Actual daily writes produce a usable, as-of-safe target lifecycle.
- [ ] Requested scope never broadens through any recovery route.
- [ ] Partial partitions/new tickers are handled correctly.
- [ ] Session/no-work/source-failure semantics are distinct.
- [ ] Action overlap policy and late-event limitations documented.
- [ ] No production repair/retraining performed without separate authorization.
- [ ] Audit/ledger/delivery packet complete; W04 knows interim legacy limits.

## Progress

- Decisions: **D03/D04 approved by the owner 2026-10-08**; ADR-0014 accepted
  (label lifecycle and availability metadata).
- Implementation: PR1 (scope/completeness) landed as `c0f37ee`; PR2 (label
  lifecycle, availability metadata, as-of cutoff, bounded report-first repair)
  landed as `457ca6d`. **PR3 (explicit-date session scheduling, D04/F11)
  implemented-pending-review** (not closed — the read-only reviewer pass and
  owner sign-off remain). PR4 (corporate-action overlap) not started.

### PR1 — exact scope through all five price markets + ticker-aware completeness

1. **Explicit scope resolver (F04).** `ingestion/types.py` adds
   `resolve_explicit_scope(markets, tickers, ticker_config)`: `None` keeps every
   market's configured default; exactly one requested price market receives
   every explicit ticker (arbitrary overrides preserved for US, CN, HK/SG, JPX,
   and KRX); multiple requested price markets resolve membership from
   `TickerConfig` via `PRICE_MARKET_CONFIG_KEYS` (`hk_sg_equity` spans the
   separate `hk`/`sg` sections), rejecting unresolved or ambiguous tickers with
   an actionable `ValueError` and no fan-out. `[]` is preserved and never
   becomes `None`. Ticker-scoped enrichment markets keep today's raw explicit
   list; non-ticker-scoped enrichment markets stay `None`.
2. **Orchestrator (F04/F25).** `run_daily_ingestion` resolves the scope once
   before any fetch and passes the per-market list to both the serial and
   parallel branches; `EXPLICIT_TICKER_MARKETS` now covers all five price
   markets (single definition moved to `ingestion/types.py`, re-exported for
   compatibility). The resolved scope is logged and `_run_ingestion_stage`
   re-derives it with the same pure resolver into the ingestion stage payload
   (`ticker_scope`). `run_daily_ingestion` keeps its
   `dict[str, SourceOutcome]` return and W02 outcome semantics.
3. **Router/factories (F04).** CN/HK-SG/JPX/KRX factories forward
   `explicit_tickers`; HK/SG split `.HK`/`.SI` by suffix and reject a ticker
   matching neither; the seven `if not explicit_tickers:` fallbacks became
   `if explicit_tickers is None:` so an explicit empty list never broadens.
4. **CN hybrid (F04).** `CNHybridFetcher` accepts `tickers` and forwards the same
   effective list to the akshare primary, efinance, and yfinance fallback;
   `CNAshareFetcher`/`CNEfinanceFetcher` gain the matching constructor seam. An
   explicit list is not truncated by `stock_limit`, and the fallback-threshold
   baseline uses the effective list.
5. **Ticker-aware existing-data check (F05).** `_market_has_date` +
   `_partition_is_valid` are consolidated into one bounded
   `_read_partition_coverage` read (one row per ticker via
   `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker ...)`). A price market is
   `SKIPPED_EXISTING` only when the partition is schema-valid **and** every
   requested ticker is present (`requested` = resolved per-market list, or the
   configured active tickers when unspecified); otherwise only the missing
   requested tickers are re-fetched (full scope when corrupt). An unspecified
   (``None``) incomplete request fetches with ``None`` — the fetcher's own
   filtered/fallback default — so coverage decides *whether* to fetch without
   ever widening what the fetcher would have pulled (review warning 1 fix). An
   empty requested list cannot prove completeness: explicit ``[]`` stays ``[]``;
   an empty configured universe defers to the fetcher default (warning 4 fix:
   the pre-W03 fallback behavior is preserved). Enrichment markets keep today's
   always-fetch behavior.
6. **Recovery reporting (F25 recovery-detail).** `backfill_date_range` returns a
   frozen `BackfillResult` (range, markets, instruments, `authorized`, trading
   dates, market-date attempts, succeeded/failed/recovered counts, per-market
   counts; `trading_dates` counts calendar days iterated, not exchange
   sessions — the loop keeps pre-W03 calendar semantics and session iteration
   stays future work) with a `total_dates` property preserving the historical successful
   market-date return. `_backfill_feature_history` returns it;
   `_handle_missing_feature_history` keeps `history_backfilled_dates`
   (via `.total_dates`) and adds a structured `recovery` payload; the CLI
   backfill command prints the richer counts. Post-recovery missing coverage
   still fails through W02's coverage check.
- Files: `ingestion/{types,orchestrator,router,backfill}.py`,
  `sources/{cn,cn_efinance,cn_hybrid}.py`, `pipeline.py`,
  `cli/commands/data.py`; tests in `tests/unit/` (`test_ingestion_types.py`,
  `test_router.py`, `test_ingestion_orchestrator.py`, `test_fetchers.py`,
  `test_backfill.py`) and `tests/integration/test_pipeline_orchestrator.py`.
- Regression proof: the new load-bearing tests were run against the pre-change
  source via `git stash push -- src/` and fail there (resolver `None`/`[]`
  preservation, five-market factory forwarding, HK/SG suffix rejection,
  CN primary/fallback sharing, partial-partition scoped fetch, corrupt-partition
  full fetch, dry-run no-write, and recovery payload/counts); the stash was
  restored immediately. Existing partition-check tests were retargeted from the
  removed `_market_has_date`/`_partition_is_valid` to the consolidated
  `_read_partition_coverage` seam (no assertion relaxed).
- Tests/gates: focused suites pass (166 in the
  `backfill/auto_backfill/gap_detection/calendar/router/ingestion_types/ingestion_orchestrator/fetchers`
  selection; 32 in `integration/test_pipeline_orchestrator.py`). Shared gates
  (re-run after the review fixes):
  `pytest -m "not network and not integration"` -> **1090 passed, 22 skipped,
  85 deselected, 3 failed** (the pre-existing `test_arena.py` set, F28/W07);
  `-n auto` -> **1090 passed, 22 skipped, 3 failed** (same baseline);
  `ruff check`/`ruff format --check` clean; `mypy src` at the 3-error
  `ml/registry.py` baseline; catalog regenerated unchanged (17 datasets / 52
  nodes / 372 edges, zero diff) and the three catalog tests pass (67);
  `mkdocs build` succeeds with only the documented pre-existing warnings;
  `git diff --check` clean.
- Catalog: pipeline/CLI change acknowledged; regeneration was a no-op (no
  Hamilton topology/tag change).
- Compatibility/rollback: all added payload fields are additive (`ticker_scope`,
  `recovery`); `run_daily_ingestion`'s return type is unchanged;
  `history_backfilled_dates` keeps its meaning; `EXPLICIT_TICKER_MARKETS` is
  re-exported from its old module. The only behavioral changes are the intended
  ones: explicit scope reaches all five markets, an empty explicit list no
  longer broadens, and a partial partition is no longer treated as complete.
  No lake migration; rollback reverts code and readers together.
- Review/sign-off: **reviewer pass complete (2026-10-08,
  `opencode-go/mimo-v2.6-pro`, read-only): READY WITH WARNINGS — 0 critical,
  4 warnings, all addressed before handoff.** (1) Filters/None-scope interplay
  could over-broaden the skip-existing fetch — fixed: an unspecified incomplete
  request now fetches with `None` (fetcher default), never an explicit
  over-broad list; pinned by
  `test_unspecified_scope_incomplete_partition_fetches_fetcher_default`
  (fails on pre-change source). (2) `trading_dates` naming overstated —
  documented as calendar-day count on the dataclass and here (rename rejected:
  the field name comes from the unit spec). (3) Gate-count arithmetic fuzz —
  resolved: gates re-run directly post-fix (1090/22/3). (4) Degenerate
  empty-config narrowing — fixed by the same `None`-default fetch: pre-W03
  fetcher fallback behavior is preserved; noted above. Residual reviewer notes
  accepted as documented limitations: per-ticker schema sampling on duplicate-
  ticker partitions (same class as the old LIMIT 100), case-sensitive `.HK`/
  `.SI` suffix matching (rejection, not normalization), mutable `per_market`
  dict inside the frozen dataclass, `dates_recovered` = all-markets-succeeded
  day (documented + tested). **Owner authorized the commit 2026-10-08
  (`c0f37ee`), closing F04/F05 and the F25 recovery detail.**
- Migration and test evidence: no production repair, migration, vacuum, or
  retraining was performed; temporary-lake and mocked-provider tests only.
- Remaining in W03: F03 labels (PR2), F11 market sessions (PR3), F14 action
  overlap (PR4).

### PR2 — label finalization, availability metadata, and as-of cutoff (ADR-0014)

1. **Label finalization (F03).** `features/labels.py` adds
   `add_label_availability` (the next observed session per ticker;
   `label_available_date`), `finalize_preceding_labels` (finalize the preceding
   observed session's label from the next session's bar; missing next-session
   bars stay pending and are reported), and `preserve_finalized_labels` (a
   normal rewrite of an older session never nulls a label a later session
   already finalized). `run_feature_job` stamps availability only when
   `compute_target=True`, preserves finalized labels on the output rows, and
   finalizes the preceding session after the output write; the pipeline feature
   stage publishes a `labels` block (`eligible`/`finalized`/`pending`/`unmatched`
   plus keys). `compute_target=False` performs no finalization.
2. **Label-only storage seam.** `storage/delta.py` adds
   `update_delta_columns(..., update_columns=[...])`: a matched-only merge that
   never creates the table, never inserts unmatched keys, never evolves the
   schema, and never touches columns outside `update_columns`. It is used only
   for the two label columns; broad storage hardening remains W06.
3. **As-of eligibility (label portion of F17).** `filter_as_of_available`
   excludes labels whose `label_available_date` postdates the cutoff and
   strict-excludes legacy unknown-availability rows; `FeatureLoader.load_features`
   accepts `require_available_by` and `PriceForecaster.train_model`/`backtest`
   pass their end date. Prediction reads stay unfiltered. `label_available_date`
   and the target columns are excluded from predictors (`NON_FEATURE_COLUMNS`,
   and the feature-skew "extra" set now uses that exclusion set).
4. **Schema + catalog.** `FEATURE_SCHEMA_VERSION` 3 → 4; the Gold catalog entry
   describes `label_available_date`; the catalog was regenerated (17 datasets /
   52 nodes / 372 edges unchanged; one column added).
5. **Report-first repair (label-carrying portion of F25, D09).** New native
   Typer command `equity label-repair` (docstring help text, `Annotated` options
   with help, help-scan test, user docs). Default is report-only; `--repair`
   performs a bounded, matched-only label update; a scope over `--max-keys`
   requires the separate owner authorization `--authorize-broad`. No automatic
   or broad repair.
- Files: `features/labels.py` (new), `features/__init__.py`,
  `features/pipeline.py`, `features/engineering.py`, `storage/delta.py`,
  `ml/feature_loader.py`, `ml/forecasting.py`, `pipeline.py`,
  `cli/commands/data.py`, `catalog/datasets.py`, `data/catalog.jsonl`; tests in
  `tests/unit/test_label_lifecycle.py` (new) plus `test_cli_unified.py`,
  `test_hamilton_feature_pipeline.py`, `test_price_forecasting.py`.
- Regression proof: the new load-bearing tests run against the pre-change
  source via `git stash push -u -- src/` and fail there — the label module
  fails to collect (`ModuleNotFoundError: equity_lake.features.labels`), and the
  version assertion, `label-repair` help scan, and committed-catalog drift test
  fail (3 failed, 13 passed in the unaffected subset); the stash was restored
  immediately.
- Tests/gates: `pytest -m "not network and not integration"` -> **3 failed,
  1105 passed, 22 skipped, 85 deselected** (the pre-existing `test_arena.py`
  set, F28/W07); `-n auto -m "not network and not integration and not serial"`
  -> **3 failed, 1105 passed, 22 skipped** (same baseline); `ruff check`/`ruff
  format --check` clean; `mypy src` at the 3-error `ml/registry.py` baseline;
  `equity catalog-generate` changed exactly one Gold column (see below) and the
  three catalog tests pass; `mkdocs build --site-dir
  /tmp/equity-lake-remediation-site` succeeds with only the documented
  pre-existing warnings; `git diff --check` clean.
- Catalog: additive only — `technical_features.label_available_date`
  (`dtype: "date"`, description of the next-session availability marker).
  Dataset/node/edge counts are unchanged (17/52/372).
- Compatibility/rollback: `FEATURE_SCHEMA_VERSION` is 4; the column is additive
  and old readers tolerate it; the first post-upgrade feature write evolves the
  Gold table (legacy rows get null availability). Installed models are not
  retrained; strict as-of training/backtest excludes unknown-availability legacy
  rows, so affected models must be retrained or validated before reuse.
  Rolling back means reverting code and schema-aware readers together; the
  label-only update is key-scoped and replayable and Delta snapshots are
  preserved (no vacuum).
- Migration/repair: no production repair, migration, vacuum, or retraining was
  performed; `equity label-repair` is report-only by default and was exercised
  only against temporary lakes.
- Review/sign-off: **reviewer pass complete (2026-10-08,
  `opencode-go/mimo-v2.6-pro`, read-only): READY WITH WARNINGS — 0 critical,
  6 warnings, all addressed before handoff.** (1) `ml compare`/`ml ablate`
  bypassed the as-of cutoff — fixed: the harness feature load now passes
  `require_available_by=end_date` (ADR-0014 §5 applies to every training
  path). (2) `filter_as_of_available` tolerance was inconsistent when the
  availability column was present — fixed: `strict=False` now keeps
  unknown-availability rows (pinned by test). (3) A replay recomputing a
  different non-null finalized label rewrote it silently — surfaced: both the
  preserve path (`label_replay_divergence`) and the finalization path
  (`label_finalization_divergence`) log the divergent count (pinned by a
  source-revision replay test). (4) Legacy target-present/availability-unknown
  rows were invisible to the repair inventory — fixed: `scan_label_repair`
  reports a third `availability_unknown` category (CLI report line + plan
  field); they remain untouched by the bounded repair. (5) Dead
  `_availability_lookahead` helper — now used by the repair scan. (6)
  `.pi/agents/w03-reviewer.md` — deleted after the pass. Post-fix gates
  (final tree): **1108 passed / 22 skipped / 3 failed** (the pre-existing
  `test_arena.py` set), `-n auto` parity, ruff/format clean, mypy at the
  3-error baseline, `git diff --check` clean, catalog unchanged by the review
  fixes (the one-line Gold diff from the unit itself stands). **Owner
  authorized the commit 2026-10-08 (`457ca6d`), closing F03 and the label
  portions of F17/F25.**
- Remaining in W03: F11 market sessions (PR3, implemented-pending-review) and
  F14 action overlap (PR4). F03 and the label portions of F17/F25 are closed
  (owner-authorized `457ca6d`).

### PR3 — explicit-date session scheduling (D04/F11)

1. **`not_scheduled` status (F11).** `ingestion/types.py` adds
   `SourceStatus.NOT_SCHEDULED` (serialized `not_scheduled`), distinct from
   `NO_DATA`/`FAILED`; `SourceOutcome.succeeded` treats it as a planned skip, so
   it is excluded from `required_failures` and never enters
   `optional_failures`/`partial`. `core/calendar.py` adds
   `classify_market_sessions(markets, d)`, returning
   `(scheduled, not_scheduled)` with the `is_trading_day` union semantics.
2. **Orchestrator (F11).** `run_daily_ingestion` classifies the requested
   price markets once, before the coverage check and any fetch. A closed market
   is recorded as `NOT_SCHEDULED` and removed from the active fetch list, so it
   is never fetched, never `NO_DATA`/`FAILED`, and never substituted with an
   earlier session. Enrichment markets stay calendar-independent.
3. **Pipeline planning (D04).** `execute_eod_pipeline` classifies once at plan
   time: `scope` gains `scheduled`/`not_scheduled`; configured tickers resolve
   from scheduled markets only and explicit tickers are filtered by the same
   resolver (`_scheduled_explicit_tickers`); features/ML run only for the
   scheduled scope; when every requested price market is closed the stages
   planned-skip with `reason: not_scheduled_scope`. A scheduled required
   `NO_DATA` still fails (W02 blocking confined to true data absence). The
   ingestion stage publishes `not_scheduled`; dry-run reports
   `scheduled`/`not_scheduled` and still writes nothing.
4. **HK/SG union (D04).** Union semantics retained: scheduled if either XHKG or
   XSES is open. The mixed-session limitation and the per-instrument coverage
   requirement are documented in the pipeline contracts and
   `auto-backfill-and-gap-detection.md`; intersection is not used to skip an
   open instrument.
- Files: `core/calendar.py`, `ingestion/types.py`,
  `ingestion/orchestrator.py`, `pipeline.py`; tests in `tests/unit/`
  (`test_calendar.py`, `test_ingestion_orchestrator.py`, `test_cli_unified.py`)
  and `tests/integration/test_pipeline_orchestrator.py`.
- Regression proof: the new load-bearing tests run against the pre-change
  source via `git stash push -- src/` (restored immediately) and fail there:
  classification + session-scheduling + `not_scheduled` CLI selection ->
  **12 failed / 4 passed**, and `TestPipelineSessionScheduling` -> **7 failed**.
  (Two generic pre-change session tests — an empty fetch and a union session —
  pass on the old source; the failures are in the closed-market,
  scheduled-`NO_DATA`, dry-run scope, and all-closed-plan assertions.)
- Tests/gates: focused wave suites
  (`backfill/auto_backfill/gap_detection/calendar/router/ingestion_orchestrator`)
  -> **105 passed, 5 deselected**;
  `tests/integration/test_pipeline_orchestrator.py -m "not network"` ->
  **45 passed**; `pytest -m "not network and not integration"` ->
  **3 failed, 1124 passed, 22 skipped, 92 deselected** (the pre-existing
  `test_arena.py` set, F28/W07);
  `-n auto -m "not network and not integration and not serial"` ->
  **3 failed, 1124 passed, 22 skipped** (same baseline); `ruff check` /
  `ruff format --check` clean; `mypy src` at the 3-error `ml/registry.py`
  baseline (163 files); catalog regenerated unchanged (17 datasets / 52 nodes /
  372 edges, byte-identical); the three catalog tests -> **67 passed**;
  `mkdocs build --site-dir /tmp/equity-lake-remediation-site` succeeds with only
  the documented pre-existing warnings; `git diff --check` clean.
- Catalog: no Hamilton topology/tag change; regeneration was a no-op.
- Compatibility/rollback: additive only — a new enum value, new stage/scope
  keys (`scheduled`, `not_scheduled`), and a new calendar helper. No schema,
  catalog, or lake change and no migration; rollback reverts the code together
  and there is no persisted state to unwind.
- Migration/repair: none; mocked providers/calendars and temporary lake paths
  only. No live source, LLM processing, retraining, or production data was
  touched.
- Backfill interaction: `backfill_date_range` counts a `not_scheduled` outcome
  in `market_date_attempts` but excludes it from `succeeded`/`failed`/
  `dates_recovered`, so weekends and holidays do not inflate
  `history_backfilled_dates` or post-recovery failure counts.
- Review/sign-off: **review pass complete (2026-10-08, performed in-session by
  the coordinator at owner direction after the pinned-reviewer subagent
  timed out): READY WITH WARNINGS — 0 critical.** Coordinator re-verified the
  gates on the final tree (1124/22/3, `-n auto` parity, ruff/format clean,
  mypy baseline, `git diff --check` clean) and re-ran the pre-change stash
  experiment (12 unit + 8 integration failures on stashed `src/`; restored).
  Confirmed in code: closed price markets classify before the coverage
  check/fetch and are never fetched, never substituted, never failed;
  enrichment markets stay calendar-independent; `required_failures` excludes
  `not_scheduled` (planned skip → exit zero); scheduled `NO_DATA` still
  blocks; features/ML plan only the scheduled scope (`not_scheduled_scope`
  planned-skip stages are success); dry-run publishes `scheduled`/
  `not_scheduled` and writes nothing; backfill recovery counts exclude closed
  days. Warnings/residuals (accepted, no code change): (1) ambiguous
  multi-market explicit tickers now raise at plan time instead of fetch time —
  same pre-existing uncaught-`ValueError` class at the CLI, ownership W04/D05;
  (2) tickers owned solely by a closed market are dropped from the feature/ML
  plan and are visible only via `scope["not_scheduled"]` plus resolver logs,
  not named per-ticker — observability residual, revisit if ops needs it.
  **Owner sign-off pending — it closes F11.**
