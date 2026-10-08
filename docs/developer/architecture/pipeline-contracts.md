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
| Source outcome statuses | `written` (fetched + persisted), `skipped_existing` (verified existing partition), `no_data` (successful fetch, zero rows), `dry_run` (direct dry-run simulation — never `written`), `failed` (fetch/schema/write failure with its cause retained). |
| Optional failure visibility | An optional source that fails is reported as `failed`, listed in `optional_failures`, and sets `partial: true`; optionality controls blocking/exit status only, never visibility. A genuine `no_data` fetch is not a failure — but a `no_data` **required price market** is treated as missing scheduled coverage: it blocks dependent features/ML and appears in `required_failures` (session-aware `not_scheduled` handling is W03/D04). |
| Persistence honesty | A stage reports success only when its output persisted. Per-ticker ML results carry `persisted` (true only after the batch write lands); a failed validation/write leaves it false while preserving prediction diagnostics. |
| Bronze-to-Silver no-work | Missing bronze corpus or an all-already-processed corpus is a planned skip (`no_bronze_work` / `already_processed`), not a source failure; processing and write failures stay failed with their cause. |
| Feature input validity | Invalid required price input (null keys, non-positive OHLC, negative volume, violated price relationships) fails the per-ticker feature computation — never silently drops rows (W01/D01). |
| EOD join keys | All feature/enrichment joins use `pl.Date` keys; malformed date keys raise. Undefined ratios (zero baselines) are null, never infinity. |
| Feature schema compatibility | `FEATURE_SCHEMA_VERSION = 4` (ADR-0014). The new Gold column `label_available_date` is additive: pre-W03 readers tolerate the extra column, and the training/backtest loaders strict-exclude rows whose availability is unknown or after the cutoff. Installed models do not treat `label_available_date` or the target columns as predictors (`NON_FEATURE_COLUMNS`). |
| Label lifecycle (ADR-0014) | On session D the preceding observed session's label (`next_day_return`) is finalized from D's bar and stamped `label_available_date = D`. "Next observation" is the next exchange session with a valid bar for the instrument; a missing next-session bar leaves the label pending and is reported. Finalization is a matched-only, label-column-only Delta update — it never inserts keys, evolves the schema, or touches feature columns. `compute_target=False` neither stamps availability nor finalizes. |
| As-of eligibility (ADR-0014) | Training and backtests exclude labels whose `label_available_date` is after the cutoff; legacy rows with unknown availability (column absent or null) are strict-excluded by default. A documented tolerance is opt-in only and is not used by the default path. |
| Bronze-to-Silver failure | Only consumers of that Silver output are degraded. |
| CLI exit status | Non-zero for required source failure, missing required ticker coverage, or a failed/unauthorized feature-history recovery; zero for dry-run, planned skips, and successful optional degradation. |

## Inputs and outputs

`execute_eod_pipeline()` accepts a trading date, market identifiers, optional
ticker scope, stage skip flags, dry-run, and explicit history-backfill
authorization. Each returned stage is a dictionary containing `success` and,
when relevant, `skipped`, `reason`, `error`, or stage-specific details.

The result always contains a `scope` entry recording the resolved trading date,
requested markets, whether the ticker scope was explicit, the resolved tickers,
dry-run mode, and whether the request was enrichment-only. The ingestion stage
publishes the `markets` status map (kept for existing consumers), plus
`required_failures`, `optional_failures`, `partial`, and `errors` (per-market
error strings). The feature stage publishes `rows` and `coverage`
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
Parquet. Market data is keyed by `ticker,date`. Enrichments use the natural
keys defined in `ingestion/writers.py` (for example `indicator,date`,
`url`, or `ticker,date,filing_type`).

## Execution and recovery

Fetchers validate their schema at the router/write boundary and use configured
retry/backoff behavior. Existing-date checks are Delta-table date checks in the
orchestrator. Writes use Delta merge/upsert and are therefore repeatable for
the configured natural key; this is not a generic claim about arbitrary
external Delta tables.

History recovery is never implicit. When authorized, the pipeline logs the
120-day range, markets, ticker count, explicit tickers, and dry-run mode before
calling the scoped backfill. Post-run verification is `equity monitor`, the
pipeline result JSON, and the focused contract tests.

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
