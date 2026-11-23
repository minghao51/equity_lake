# Pipeline Contracts

This page is the operational contract for `equity pipeline` and the scoped `equity features` run.

## Policy decisions

| Decision | Contract |
|---|---|
| Dry-run | No persistence, backfill, LLM processing, or model inference. Stages are reported as planned/skipped with `reason: dry_run`. |
| Missing feature history | A 120-day recovery requires explicit `--allow-history-backfill`. |
| Backfill scope | Recovery uses the requested markets and explicit tickers; the resolved date range and scope are logged before execution, and the recovered date count is recorded in the feature stage (`history_backfilled_dates`). |
| Required source failure | A failed price source blocks dependent features and ML. |
| Optional enrichment failure | Core features may continue; the result records partial success and the failed enrichment. |
| Scoped feature run (`equity features`) | Explicit `--tickers`, `--start`, `--end` and one `--lake-dir` root (canonical by default); the price and corporate-action reads share that root, with no discovery, fallback or cross-root mixing. Action inputs are scoped per instrument so a subset read of a multi-instrument store is coherent. `--coverage-basis` defaults to `source_clock`; `vendor_events_no_clock` is the explicit weak opt-in (ADR-0032) and its Gold rows carry the weak stamp. The run refuses when a requested instrument has no coverage proof, the requested basis has no evidence, or the window passes the instrument's coverage bound — it never clamps the window and never widens the ticker scope. `--dry-run` computes and validates without writing Gold rows or finalizing labels. |
| Requested scope (D02) | With no ticker argument, every configured active ticker in the requested price markets is resolved — there is no implicit ticker cap. An explicit `--tickers` list is used exactly as given (deduplicated deterministically); an explicitly empty scope is rejected with a non-zero exit and is never broadened to the configured universe. |
| Enrichment-only requests | A request whose markets contain no price market planned-skips features and ML (`reason: enrichment_only_no_price_scope`); no default tickers are invented. |
| Feature coverage | `features.coverage` reports the requested and produced tickers plus every missing ticker with a stable reason code: `no_price_rows`, `insufficient_history`, `missing_output_bar`, `invalid_output`, or `intentional_limit` (reserved). `produced` means the ticker reached the computed output window — it is a computation fact, not a persistence claim (a failed write is reported by the stage's `success: false` and `error` while `produced`/`missing` still describe what was computed). Missing scheduled required ticker coverage fails the feature stage (D02) while successful tickers are still written and reported. |
| Source outcome statuses | `written` (fetched + persisted), `skipped_existing` (verified existing partition), `no_data` (successful fetch, zero rows), `not_scheduled` (the market's exchange is closed on the requested explicit date — a planned skip), `dry_run` (direct dry-run simulation — never `written`), `failed` (fetch/schema/write failure with its cause retained). |
| Explicit-date session scheduling (D04) | Each requested price market is classified once against the resolved plan date with `core/calendar.py` union semantics. A closed market is `not_scheduled`: a planned skip, never `failed` and never substituted with an earlier session. Features and ML are planned only for the scheduled scope, so a closed market never blocks an open one; required-market `no_data` blocking is confined to a genuinely empty scheduled session. For relative dates the plan date is resolved once and then classified per market. |
| HK/SG session union | `hk_sg_equity` is scheduled when *either* XHKG or XSES is open (`is_trading_day` union semantics), so an open instrument on a single-exchange session is never skipped. A scheduled mixed-market day does not imply market-wide coverage: per-instrument coverage is reported from the feature stage, and gap detection keeps its stricter intersection semantics (`trading_days_between`). |
| Optional failure visibility | An optional source that fails is reported as `failed`, listed in `optional_failures`, and sets `partial: true`; optionality controls blocking/exit status only, never visibility. A genuine `no_data` fetch is not a failure — but a `no_data` **required price market** on a scheduled session is treated as missing scheduled coverage: it blocks dependent features/ML and appears in `required_failures`. A `not_scheduled` market is a plan skip: it is excluded from `required_failures` and never appears in `optional_failures` or `partial`. |
| Persistence honesty | A stage reports success only when its output persisted. Per-ticker ML results carry `persisted` (true only after the batch write lands); a failed validation/write leaves it false while preserving prediction diagnostics. |
| Bronze-to-Silver no-work | Missing bronze corpus or an all-already-processed corpus is a planned skip (`no_bronze_work` / `already_processed`), not a source failure; processing and write failures stay failed with their cause. |
| Feature input validity | Invalid required price input (null keys, non-positive OHLC, negative volume, violated price relationships) fails the per-ticker feature computation — never silently drops rows (W01/D01). |
| EOD join keys | All feature/enrichment joins use `pl.Date` keys; malformed date keys raise. Undefined ratios (zero baselines) are null, never infinity. |
| Canonical shared identity (ADR-0016) | Shared feature/model/signal/watchlist keys are `(market, MIC, symbol)` (plus `date` for observations). MIC comes from explicit supported source/config mapping; ticker spelling is never used to infer venue. Unknown or ambiguous legacy rows/models fail closed unless trusted historical provenance proves one unique identity. No automatic key rewrite or collapse occurs. |
| Scoped backtest (`equity backtest`) | One lake root (`--lake-dir`, canonical default) and one action-coverage basis (`--coverage-basis`, `source_clock` default) per run; the basis is explicit and never inferred. The loader admits one basis per read and the engine refuses to reinterpret a preloaded frame on another basis. `action_coverage:<market>` snapshots record the basis read; a manifest scope mixing bases refuses. The weak basis requires the explicit opt-in and its rows carry null clocks/age; strict stays the default and the only point-in-time basis. |
| Feature schema and point-in-time adjustment (ADR-0015, ADR-0032) | `FEATURE_SCHEMA_VERSION = 8`, `availability_schema_version = 5`, and action-coverage proof schema version `3`. Strict Gold rows include `identity_schema_version`, `price_adjustment_version`, `action_coverage`, `action_source_age_days`, and persisted proof columns `action_coverage_market`, `action_coverage_mic`, `action_coverage_symbol`, `action_coverage_source`, `action_coverage_start`, `action_coverage_end`, `action_source_available_at`, `action_source_valid_until`, `action_coverage_status`, `action_coverage_schema_version`, and `action_coverage_basis`. The basis is `source_clock` (strict, point-in-time) or `vendor_events_no_clock` (explicit opt-in, no clock at all: null clocks/age and `vendor_events_complete`). One read admits one basis; mixed bases and mixed demo/real roots refuse; legacy v2/v4/v7 rows imply `source_clock`. Proof identity must equal the feature row's own `(market, MIC, symbol)`; its timezone-aware source clock maps to the first observable venue session and its source-issued expiry must cover the decision-session close. Missing/unverified proof makes adjusted output unavailable, not neutral or raw. Unknown/incompatible legacy features and models are unavailable, not rewritten or auto-retrained. Native yfinance corporate-action ingestion currently supplies no verified source clock, complete-coverage assertion, or source-issued expiry. No guessed TTL, ingestion-time substitute, raw fallback, or cash-dividend adjustment is used. |
| Label availability (ADR-0014) | Gold `label_available_date` records when `next_day_return` became computable. Training/backtests strict-exclude labels whose availability is unknown or after their cutoff; availability and target columns are excluded from predictors (`NON_FEATURE_COLUMNS`). |
| Label lifecycle (ADR-0014) | On session D the preceding observed session's label (`next_day_return`) is finalized from D's bar and stamped `label_available_date = D`. "Next observation" is the next exchange session with a valid bar for the instrument; a missing next-session bar leaves the label pending and is reported. Finalization is a matched-only, label-column-only Delta update — it never inserts keys, evolves the schema, or touches feature columns. `compute_target=False` neither stamps availability nor finalizes. |
| As-of eligibility (ADR-0014) | Training and backtests exclude labels whose `label_available_date` is after the cutoff; legacy rows with unknown availability (column absent or null) are strict-excluded by default. A documented tolerance is opt-in only and is not used by the default path. |
| Bronze-to-Silver failure | Only consumers of that Silver output are degraded. |
| CLI exit status | Non-zero for required source failure, missing required ticker coverage, or a failed/unauthorized feature-history recovery; zero for dry-run, planned skips (including `not_scheduled` and an all-closed scheduled scope), and successful optional degradation. |

## Inputs and outputs

`execute_eod_pipeline()` accepts a trading date, market identifiers, optional
ticker scope, stage skip flags, dry-run, and explicit history-backfill
authorization. Each returned stage is a dictionary containing `success` and,
when relevant, `skipped`, `reason`, `error`, or stage-specific details.

The result always contains a `scope` entry recording the resolved trading date,
requested markets, whether the ticker scope was explicit, the resolved
scheduled tickers, dry-run mode, whether the request was enrichment-only, and
the `scheduled`/`not_scheduled` price markets for the plan date. The ingestion
stage publishes the `markets` status map (kept for existing consumers), plus
`required_failures`, `optional_failures`, `not_scheduled`, `partial`, and
`errors` (per-market error strings). When there is no scheduled price scope
(every requested price market is closed, or the requested explicit tickers all
belong to closed markets), the stage planned-skips features/ML with
`reason: not_scheduled_scope`.
The feature stage publishes `rows` and `coverage`
(`requested`/`produced`/`missing`), and — when the target was computed — a
`labels` block with `eligible`/`finalized`/`pending`/`unmatched` counts plus the
pending and unmatched keys. The ML stage publishes per-ticker results
with a `persisted` flag. All added fields are additive: existing consumers of
the published dictionary remain compatible.

The durable layout is:

```text
data/lake/01_bronze/<dataset>/
data/lake/02_silver/<dataset>/
data/lake/03_gold/features/
data/lake/04_platinum/predictions/
```

Tables are Delta tables partitioned by `date`; the underlying table files are
Parquet. Shared price/feature/model/signal observations are keyed by canonical `market,MIC,symbol,date`; legacy `ticker` is only a compatibility alias, not a global key. Enrichments use the natural
keys defined in `ingestion/writers.py` (for example `indicator,date`,
`url`, or `ticker,date,filing_type`).

## Bounded Delta evolution and replay (ADR-0017)

The keyed Delta writer adds nullable columns through native schema evolution;
it does not read and rewrite the full table for additive evolution. Optimistic
concurrency conflicts refresh the committed snapshot and retry at most three
times. Unsupported evolution and exhausted conflicts remain visible failures.

Bronze-to-Silver replay requires an explicit source/date/market/instrument
scope, is capped at 100 selected items per invocation and 25 per batch, and
uses Pydantic-validated auxiliary checkpoint/spend-ledger state separate from
authoritative Silver. Silver durability is re-read before checkpoint advancement; the
checkpoint alone never suppresses work. Historical replay does not notify.
Paid work defaults to zero authorization and requires a finite cumulative
estimate, separate actual-spend cap, per-batch estimate, and provider-reported
actual cost. No current paid-provider adapter exists for unbounded LLM helpers;
`equity sec --process` deliberately exits nonzero before fetch. These paths are
not paid-runtime-ready.

## Execution and recovery

Fetchers validate their schema at the router/write boundary and use configured
retry/backoff behavior. Existing-date checks are Delta-table date checks in the
orchestrator. Writes use Delta merge/upsert and are therefore repeatable for
the configured natural key; this is not a generic claim about arbitrary
external Delta tables.

History recovery is never implicit. When separately authorized, the pipeline
logs the 120-day range, markets, ticker count, explicit tickers, and dry-run
mode before calling the scoped backfill. Do not run production migration,
repair, or backfill as a consequence of documentation or implementation review;
compatibility checks are report-first on scoped temporary copies. Post-run
verification is `equity monitor`, the pipeline result JSON, and the focused
contract tests.

## Label lifecycle, availability, and repair (ADR-0014)

Gold keeps the existing label column semantics (`next_day_return`); no separate
label table is introduced. `label_available_date` records the session on which
the target value became computable (the next observed session). When session D
is processed, the preceding observed session's label is finalized from D's bar
through `update_delta_columns` in `src/equity_lake/storage/delta.py`, a
matched-only, label-column-only update. Replaying an older session preserves an
already-finalized label instead of nulling it.

Migration and repair:

- **Schema.** The column is additive. The first post-upgrade feature write
evolves the Gold table, null-filling `label_available_date` for legacy rows;
old readers that select by name keep working, and readers that select all
columns tolerate the extra one.
- **Readers.** `FeatureLoader.load_features(..., require_available_by=cutoff)`
is the strict as-of read; `PriceForecaster.train_model` / `backtest` pass their
end date and therefore exclude later-finalized and unknown-availability rows.
Prediction loads leave it unset (inference needs the row regardless of label
availability).
- **Models.** Installed models are not retrained automatically; because
strict as-of training excludes unknown-availability legacy rows, affected
models must be retrained or explicitly validated before reuse (ADR-0014).
- **Repair.** `equity label-repair` is report-only by default: it inventories
finalizable rows (next-session bar present) and pending rows (no next-session
bar) without writing. `--repair` authorizes a bounded, matched-only label
update; a scope larger than `--max-keys` requires the separate owner
authorization `--authorize-broad` (D09). No broad or automatic repair runs.
- **Rollback.** Roll back code and schema-aware readers together; the
label-only update is key-scoped and replayable, and Delta snapshots are
preserved for an authorized repair rollback (no vacuum here).

## Source-clock qualification and root containment (RS-D01)

Point-in-time correctness is impossible on `vendor_events_no_clock` rows by
construction: those rows carry vendor-event completeness and no clock at all
(ADR-0032 decision 6). Their stamps and invariants are preserved as they are —
they are never relabelled, never back-filled with a synthesised clock, and the
strict basis still refuses them. Qualifying an *alternative* source is a separate,
evidence-bound procedure implemented in `src/equity_lake/core/source_clock.py`.

### What counts as evidence

A qualification record states one `(source, market, series)` and must carry:

- **Source-issued evidence.** `evidence_kind` is closed to `source_publication`
  and `source_vintage` — a notice, release note or vintage table issued by the
  source — and the document is referenced by `evidence_digest` (sha256) or
  `evidence_uri`. At least one is required and the document is never fetched here.
- **A source clock.** `source_available_at`, a timezone-aware UTC timestamp taken
  from that document, not from anything local.
- **A covered window and an expiry.** `coverage_start`..`coverage_end` (the sessions
  the qualification speaks for) and `expires_on`, which cannot precede
  `coverage_end`.
- **A rights reference.** `rights_reference` names the licence or entitlement that
  authorizes keeping the series locally; an unlicensed series is not qualified.
- **Provenance of the record itself.** `recorded_at`, and `supersedes` naming the
  record this one corrects.

An **ingest-time clock cannot be expressed as proof**. `ingest_time` appears in the
evidence vocabulary only so the refusal is explicit: constructing a record with it
raises, `source_available_at` may not postdate `recorded_at`, and
`source_vintage`/`source_publication` are the only admitted kinds. A fetch time, a
file-write time or a guessed TTL is exactly the weakness `vendor_events_no_clock`
names — writing it into a record would launder it, so the write boundary refuses it.

### Append-only records

Records live in an auxiliary artifact, `DATA_DIR/source_clock_qualification/records.jsonl`
(ADR-0006: not cataloged, not pointblank-validated, created only by an append):

- **Never restamped.** `record_id` is the sha256 of the record's own content, and a
  row that no longer matches it is *unverifiable*: the reader refuses the store and
  the gate refuses the answer rather than dropping or repairing the line.
- **A correction is a new record.** Appending the same record twice refuses, a
  correction names what it supersedes, and a record can only be superseded once —
  the correction chain is linear, so no edit ever rewrites history.
- **Expiry is a fact, not a promise.** Past `expires_on` the record no longer
  qualifies a read; refreshing evidence means appending a new record.

### The strict consumer gate

`qualify_source_clock(records, source=…, market=…, series=…, as_of=…)` answers
`present` only for exactly one verified, active, in-window and unexpired record. It
fails closed — `unknown` plus the named input packet — on: no record for the scope,
an unverifiable record, every candidate superseded, more than one active record (an
ambiguous trail), an `as_of` outside the covered window, and an `as_of` after expiry.
`require_qualified_source_clock` raises `SourceClockUnavailable` for the same cases;
it never falls back to the weak `vendor_events_no_clock` basis and never synthesises
a clock. The only wiring is an explicit opt-in on
`FeatureLoader.load_features(..., source_clock_source=…, source_clock_records=…)`;
every default read is unchanged.

### Real-root containment

`delta_table_path` is the single resolution seam every reader and writer passes
through, and `refuse_cross_root_symlink` runs there: a path that reaches the other
lake root — `data/lake_real/01_bronze` pointing at `data/lake/01_bronze`, or the
reverse — or that leaves its own root entirely is refused, and so is an uncontained
`--lake-dir`. The check is refusal-only: nothing is redirected, created or
discovered, so the join stays dumb while a canary symlink can no longer serve one
root's rows as the other's. Legitimate explicit `--lake-dir` use is unaffected, the
real root stays uncataloged and unmixed, and `resolve_lake_root` keeps its existing
rules.

### Status: `blocked-input`

No source is qualified. The records, the gate and the guard are exercised only by
synthetic fixtures in the test suite. **Passing those guards is not qualified data**,
it does not qualify any real source, it does not unblock a live consumer or any
ingest/feature/backtest run against `data/lake_real/`, and it does not change the
point-in-time limit on `vendor_events_no_clock` rows. Actual qualification stays
blocked until an operator supplies source-issued publication/vintage clocks,
coverage/expiry evidence and rights.
