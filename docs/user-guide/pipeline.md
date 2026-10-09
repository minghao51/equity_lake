# Pipeline User Guide

This guide covers the current operational pipeline exposed by the unified `equity` CLI.

## Overview

The daily workflow has three core stages:

1. `equity ingest` fetches market data and writes date-partitioned Delta tables with Parquet data files
2. the feature stage builds derived datasets under `data/lake/03_gold/features/`
3. the ML stage runs inference for the requested tickers

The main command is:

```bash
dotenvx run -- uv run equity pipeline
```

## Core Commands

Run daily ingestion:

```bash
dotenvx run -- uv run equity ingest
dotenvx run -- uv run equity ingest --date 2026-06-06
dotenvx run -- uv run equity ingest --markets us_equity,cn_ashare
dotenvx run -- uv run equity ingest --tickers AAPL,MSFT,NVDA --markets us_equity
dotenvx run -- uv run equity ingest --dry-run --verbose
```

Run the full pipeline:

```bash
dotenvx run -- uv run equity pipeline
dotenvx run -- uv run equity pipeline --dry-run --verbose
dotenvx run -- uv run equity pipeline --date 2026-06-06
dotenvx run -- uv run equity pipeline --markets us_equity
dotenvx run -- uv run equity pipeline --tickers AAPL,MSFT,NVDA
dotenvx run -- uv run equity pipeline --skip-ingestion
dotenvx run -- uv run equity pipeline --skip-ingestion --skip-features
dotenvx run -- uv run equity pipeline --skip-ml
dotenvx run -- uv run equity pipeline --save-results
```

Monitor pipeline health:

```bash
dotenvx run -- uv run equity monitor
dotenvx run -- uv run equity monitor --verbose
dotenvx run -- uv run equity monitor --output-json logs/health_report.json
```

Query the lake with DuckDB:

```bash
dotenvx run -- uv run equity query
dotenvx run -- uv run equity query --query latest_summary
dotenvx run -- uv run equity query --query top_volume
dotenvx run -- uv run equity query --db data/equity_data.duckdb --verbose
```

## Supported Pipeline Flags

`equity pipeline` currently supports:

- `--date`
- `--days-back`
- `--markets`
- `--tickers`
- `--skip-ingestion`
- `--skip-features`
- `--skip-ml`
- `--dry-run`
- `--allow-history-backfill`
- `--verbose`
- `--save-results` — writes `logs/pipeline_results_<date>.json` (where the
  dashboard exporter looks for it); skipped under `--dry-run`

`equity ingest` currently supports:

- `--date`
- `--markets`
- `--tickers`
- `--dry-run`
- `--verbose`

## Scope, Coverage, and Result Truthfulness

`equity pipeline` resolves the requested ticker scope explicitly:

- With no `--tickers`, every configured active ticker in the requested price
  markets is processed — there is no hidden limit.
- `--tickers` is used exactly as given, deduplicated deterministically. An
  explicitly empty value (for example `--tickers ""`) is rejected with a
  non-zero exit; it never falls back to the configured universe.
- A request whose markets contain no price market (for example `--markets macro`)
  planned-skips features and ML with `reason: enrichment_only_no_price_scope`
  instead of inventing tickers.

The result JSON records `scope` (resolved tickers and markets), the ingestion
status per market, and feature `coverage`: requested and produced tickers plus
any missing ticker with a reason (`no_price_rows`, `insufficient_history`,
`missing_output_bar`, `invalid_output`).

Outcome semantics:

- `written` — fetched and persisted; `skipped_existing` — a valid partition
  already existed.
- `not_scheduled` — the market's exchange is closed on the resolved plan date.
  This is a planned skip, not a failure: it does not block markets that are
  open, never counts as `partial`, and the run still exits zero when the
  scheduled required work succeeds.
- `no_data` — the source returned zero rows on a scheduled session (not an
  error by itself, and not a verified partition; a required price market with
  no data still blocks features/ML).
- `dry_run` — a direct `equity ingest --dry-run` simulated the write; it never
  reports `written`.
- `failed` — the fetch, schema check, or write failed; the per-market cause is
  kept in the result and does not erase other markets' outcomes.

Session planning: each requested price market is classified against the
resolved plan date using its exchange calendar (`not_scheduled` when closed).
Features and ML are planned only for the scheduled scope, and an explicit closed
date is never replaced with an earlier session. `hk_sg_equity` uses union
semantics — it is scheduled when either Hong Kong or Singapore is open — so an
open instrument is never skipped on a single-exchange session; per-instrument
coverage is reported by the feature stage.

Optional enrichment failures stay visible: they are listed in
`optional_failures`, set `partial: true`, and do not block core features or the
process exit. A requested ticker missing from the feature output fails the
feature stage and exits non-zero while still reporting everything that was
produced. Per-ticker ML results carry `persisted` so inference success is not
mistaken for durability.

## Configuration

Primary config files:

- `config/settings.yaml`: application defaults
- `config/tickers.yaml`: ticker metadata by market
- `config/watchlist.yaml`: watchlists for signal scanning
- `config/signals.yaml`: signal thresholds and formatting
- `.env.example`: local environment template

Configuration precedence is:

1. command-line options
2. `EQUITY_` environment variables
3. `.env`
4. `config/settings.yaml`

Settings are grouped (`project`, `ingestion`, `schedule`, `dashboard`, `monitoring`) and
environment overrides use the `EQUITY_` prefix with `__` as the nested delimiter, so a
setting like `dashboard.output_dir` maps to `EQUITY_DASHBOARD__OUTPUT_DIR`. Because
`Settings` loads with `extra="forbid"`, a misspelled or flat unknown `EQUITY_*` variable
fails loudly at load time instead of being ignored. Common overrides include:

- `EQUITY_PROJECT__ENVIRONMENT` (development | production | testing)
- `EQUITY_INGESTION__DEFAULT_MARKETS`
- `EQUITY_INGESTION__RETRY_ATTEMPTS`
- `EQUITY_SCHEDULE__CRON`
- `EQUITY_SCHEDULE__TIMEZONE`
- `EQUITY_DASHBOARD__OUTPUT_DIR`
- `EQUITY_DASHBOARD__TITLE`
- `EQUITY_MONITORING__MAX_AGE_DAYS`
- `EQUITY_MONITORING__NULL_THRESHOLD_PCT`

## Data Layout

Important runtime directories:

- `data/lake/01_bronze/market_data/us_equity/`
- `data/lake/01_bronze/market_data/cn_ashare/`
- `data/lake/01_bronze/market_data/hk_sg_equity/`
- `data/lake/01_bronze/market_data/jpx_equity/`
- `data/lake/01_bronze/market_data/krx_equity/`
- `data/lake/03_gold/features/`
- `data/models/`
- `logs/`

Market data is stored as:

```text
data/lake/<layer>/<dataset>/_delta_log/
data/lake/<layer>/<dataset>/date=YYYY-MM-DD/*.parquet
```

## Typical Operating Flows

Bootstrap and first run:

```bash
dotenvx run -- uv run equity sync --bucket s3://your-bucket
dotenvx run -- uv run equity pipeline --dry-run --verbose
dotenvx run -- uv run equity pipeline
```

The bucket is a root URL whose remote tree mirrors the local numbered medallion layout
without a `data/lake/` prefix — each market is pulled from
`<bucket>/01_bronze/market_data/<market_dir>`.

Daily local run:

```bash
dotenvx run -- uv run equity ingest
dotenvx run -- uv run equity monitor
```

Daily full run:

```bash
dotenvx run -- uv run equity pipeline
dotenvx run -- uv run equity monitor --output-json site/health-report.json
dotenvx run -- uv run equity dashboard build --output-dir site
```

## Related Workflows

Signals:

```bash
dotenvx run -- uv run equity signal scan
dotenvx run -- uv run equity signal scan --watchlist config/watchlist.yaml
dotenvx run -- uv run equity signal scan --config config/signals.yaml
```

Backtesting:

```bash
dotenvx run -- uv run equity backtest --help
```

Dashboard:

```bash
dotenvx run -- uv run equity dashboard build --output-dir site
dotenvx run -- uv run equity dashboard serve --port 8501
```

## Scheduling

Example weekday cron jobs:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity ingest >> logs/cron-daily.log 2>&1
0 20 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity monitor >> logs/cron-monitor.log 2>&1
```

Or, if you prefer a single scheduled run:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity pipeline >> logs/cron-pipeline.log 2>&1
```

## Troubleshooting

Check the live CLI:

```bash
uv run equity --help
uv run equity ingest --help
uv run equity pipeline --help
uv run equity query --help
uv run equity monitor --help
```

Common checks:

- Verify `config/settings.yaml` and `.env` agree on paths and credentials
- Run with `--dry-run --verbose` before changing a scheduled workflow
- Export a health report with `equity monitor --output-json ...` before building the static dashboard

## Notes

- The unified `equity` command is the supported CLI surface.
- China ingestion currently defaults to the shipped `akshare` path.
- The pipeline is local-first after bootstrap; DuckDB reads directly from Parquet.
