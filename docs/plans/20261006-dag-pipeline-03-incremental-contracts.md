# W03 — Incremental Lifecycle, Completeness, and Scoped Recovery

**Status:** Not started; label/session policy approval required  
**Depends on:** [W02](20261006-dag-pipeline-02-outcomes-and-coverage.md)  
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

- Implementation: not started.
- Label/session/overlap decisions: pending.
- Migration and test evidence: not recorded.
