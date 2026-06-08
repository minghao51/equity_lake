# Pipeline User Guide

This guide covers the current operational pipeline exposed by the unified `equity` CLI.

## Overview

The daily workflow has three core stages:

1. `equity ingest` fetches market data and writes Hive-partitioned Parquet
2. the feature stage builds derived datasets under `data/lake/features/`
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
dotenvx run -- uv run equity ingest --markets us,cn
dotenvx run -- uv run equity ingest --tickers AAPL,MSFT,NVDA --markets us
dotenvx run -- uv run equity ingest --dry-run --verbose
```

Run the full pipeline:

```bash
dotenvx run -- uv run equity pipeline
dotenvx run -- uv run equity pipeline --dry-run --verbose
dotenvx run -- uv run equity pipeline --date 2026-06-06
dotenvx run -- uv run equity pipeline --markets us
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
dotenvx run -- uv run equity query --date 2026-06-06
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
- `--verbose`
- `--save-results`

`equity ingest` currently supports:

- `--date`
- `--markets`
- `--tickers`
- `--dry-run`
- `--verbose`

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

Common settings overrides include:

- `EQUITY_CONFIG_PATH`
- `EQUITY_ENVIRONMENT`
- `EQUITY_DATA_DIR`
- `EQUITY_LAKE_DIR`
- `EQUITY_LOGS_DIR`
- `EQUITY_MODELS_DIR`
- `EQUITY_DB_PATH`
- `EQUITY_DASHBOARD_OUTPUT_DIR`
- `EQUITY_DASHBOARD_TITLE`
- `EQUITY_SCHEDULE_CRON`
- `EQUITY_SCHEDULE_TIMEZONE`

## Data Layout

Important runtime directories:

- `data/lake/us_equity/`
- `data/lake/cn_ashare/`
- `data/lake/hk_sg_equity/`
- `data/lake/jpx_equity/`
- `data/lake/krx_equity/`
- `data/lake/features/`
- `data/models/`
- `logs/`

Market data is stored as:

```text
data/lake/<market>/date=YYYY-MM-DD/*.parquet
```

## Typical Operating Flows

Bootstrap and first run:

```bash
dotenvx run -- uv run equity sync --bucket s3://your-bucket/us_equity
dotenvx run -- uv run equity pipeline --dry-run --verbose
dotenvx run -- uv run equity pipeline
```

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
