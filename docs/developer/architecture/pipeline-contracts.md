# Pipeline Contracts

This page is the operational contract for `equity pipeline`.

## Policy decisions

| Decision | Contract |
|---|---|
| Dry-run | No persistence, backfill, LLM processing, or model inference. Stages are reported as planned/skipped with `reason: dry_run`. |
| Missing feature history | A 120-day recovery requires explicit `--allow-history-backfill`. |
| Backfill scope | Recovery uses the requested markets and explicit tickers; the resolved date range and scope are logged before execution, and the recovered date count is recorded in the feature stage (`history_backfilled_dates`). |
| Required source failure | A failed price source blocks dependent features and ML. |
| Optional enrichment failure | Core features may continue; the result records partial success and the failed enrichment. |
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
| Feature schema and point-in-time adjustment (ADR-0015) | `FEATURE_SCHEMA_VERSION = 7`, `availability_schema_version = 4`, and action-coverage proof schema version `2`. Strict Gold rows include `identity_schema_version`, `price_adjustment_version`, `action_coverage`, `action_source_age_days`, and persisted proof columns `action_coverage_market`, `action_coverage_mic`, `action_coverage_symbol`, `action_coverage_source`, `action_coverage_start`, `action_coverage_end`, `action_source_available_at`, `action_source_valid_until`, `action_coverage_status`, and `action_coverage_schema_version`. Proof identity must equal the feature row's own `(market, MIC, symbol)`; its timezone-aware source clock maps to the first observable venue session and its source-issued expiry must cover the decision-session close. Missing/unverified proof makes adjusted output unavailable, not neutral or raw. Unknown/incompatible legacy features and models are unavailable, not rewritten or auto-retrained. Native yfinance corporate-action ingestion currently supplies no verified source clock, complete-coverage assertion, or source-issued expiry. No guessed TTL, ingestion-time substitute, raw fallback, or cash-dividend adjustment is used. |
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
