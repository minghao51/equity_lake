# Pipeline Contracts

This page is the operational contract for `equity pipeline`.

## Policy decisions

| Decision | Contract |
|---|---|
| Dry-run | No persistence, backfill, LLM processing, or model inference. Stages are reported as planned/skipped with `reason: dry_run`. |
| Missing feature history | A 120-day recovery requires explicit `--allow-history-backfill`. |
| Backfill scope | Recovery uses the requested markets and explicit tickers; the resolved date range and scope are logged before execution. |
| Required source failure | A failed price source blocks dependent features and ML. |
| Optional enrichment failure | Core features may continue; the result records partial success and the failed enrichment. |
| Bronze-to-Silver failure | Only consumers of that Silver output are degraded. |
| CLI exit status | Non-zero for required-stage failure; zero for dry-run and successful optional degradation. |

## Inputs and outputs

`execute_eod_pipeline()` accepts a trading date, market identifiers, optional
ticker scope, stage skip flags, dry-run, and explicit history-backfill
authorization. Each returned stage is a dictionary containing `success` and,
when relevant, `skipped`, `reason`, `error`, or stage-specific details.

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
