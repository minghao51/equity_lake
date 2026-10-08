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
| Feature schema compatibility | W01 requires no stored-schema migration: gold `date` was already persisted as `pl.Date` pre-W01 (`run_feature_job` casts before write), `SOCIAL_COLUMNS` are unchanged, and `FEATURE_SCHEMA_VERSION` stays 3. |
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
(`requested`/`produced`/`missing`). The ML stage publishes per-ticker results
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
