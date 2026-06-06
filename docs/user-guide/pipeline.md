# Pipeline User Guide

This is the current operational guide for the installed CLI entrypoints in
`equity-lake`.

## What the Pipeline Does

The full pipeline runs three stages:

1. `equity-daily`: ingest market data into partitioned Parquet
2. feature stage: generate feature rows under `data/lake/features/`
3. ML stage: run price-forecast inference for the requested tickers

The main wrapper is:

```bash
uv run equity-pipeline
```

## Installation

Core install:

```bash
uv venv
source .venv/bin/activate
uv sync
```

Install ML dependencies for the full pipeline:

```bash
uv sync --extra ml
```

Optional:

```bash
uv sync --extra s3
uv sync --extra visualization
uv sync --extra backtesting
uv sync --group dev
```

If you want an env file, copy the checked-in template:

```bash
cp .env.example .env
```

## Core Commands

Daily ingestion:

```bash
uv run equity-daily
uv run equity-daily --date 2026-03-12
uv run equity-daily --markets us,cn
uv run equity-daily --parallel --max-workers 2
uv run equity-daily --dry-run --verbose
```

Ticker-config utilities:

```bash
uv run equity-daily --list-stats
uv run equity-daily --list-tickers --markets us --verbose
uv run equity-daily --tags blue-chip --min-priority 8
uv run equity-daily --groups faang
uv run equity-daily --tickers AAPL,MSFT,NVDA --markets us
uv run equity-daily --config config/tickers.yaml
```

Gap detection:

```bash
uv run equity-daily --detect-gaps --days-back 90
uv run equity-daily --coverage-stats --markets us,cn
```

Full pipeline:

```bash
uv run equity-pipeline
uv run equity-pipeline --dry-run --verbose
uv run equity-pipeline --date 2026-03-12
uv run equity-pipeline --markets us
uv run equity-pipeline --tickers AAPL,MSFT,NVDA
uv run equity-pipeline --skip-ingestion
uv run equity-pipeline --skip-ingestion --skip-features
uv run equity-pipeline --save-results
```

Monitoring:

```bash
uv run equity-monitor
uv run equity-monitor --verbose
uv run equity-monitor --output-json logs/health_report.json
```

Queries:

```bash
uv run equity-query
uv run equity-query --query latest_summary
uv run equity-query --query top_volume --days 14
uv run equity-query --query gainers_losers --days 14
uv run equity-query --query market_stats
uv run equity-query --query benchmark
```

## Configuration

Primary config files:

- `config/tickers.yaml`: market/ticker metadata, priorities, groups, tags
- `config/watchlist.yaml`: signal-scanner watchlist
- `config/signals.yaml`: signal thresholds and aggregation settings
- `config/settings.yaml`: default application settings
- `.env.example`: canonical env template for local `.env`

Configuration precedence is:

1. `config/settings.yaml` provides the default app settings.
2. `EQUITY_LAKE_*` variables in `.env` override matching YAML settings.
3. Feature-specific env vars such as API keys and object-storage credentials are
   read directly by the commands that need them.

Application settings that support `EQUITY_LAKE_*` overrides include:

- `EQUITY_LAKE_CONFIG_PATH`
- `EQUITY_LAKE_ENVIRONMENT`
- `EQUITY_LAKE_DATA_DIR`
- `EQUITY_LAKE_LAKE_DIR`
- `EQUITY_LAKE_LOGS_DIR`
- `EQUITY_LAKE_MODELS_DIR`
- `EQUITY_LAKE_DB_PATH`
- `EQUITY_LAKE_DASHBOARD_OUTPUT_DIR`
- `EQUITY_LAKE_DASHBOARD_TITLE`
- `EQUITY_LAKE_SCHEDULE_CRON`
- `EQUITY_LAKE_SCHEDULE_TIMEZONE`

Additional features such as macro, news, and social sentiment may read their own
API-specific env vars when invoked.

## Data Layout

Important runtime directories:

- `data/lake/us_equity/`
- `data/lake/cn_ashare/`
- `data/lake/hk_sg_equity/`
- `data/lake/features/`
- `data/models/`
- `logs/`

The package also creates:

- `data/lake/macro_indicators/`
- `data/lake/us_news/`
- `data/lake/us_social_sentiment/`

## China Market Note

China ingestion is routed through `CNHybridFetcher`, but the current
orchestrator constructs it with the default behavior from
`src/equity_lake/ingestion/sources/cn_hybrid.py`: `akshare` enabled and
`efinance` disabled unless the code path is changed. The docs should therefore
describe the current shipped behavior as:

- current default: `akshare`
- implemented fallback-capable fetcher exists in code
- `efinance` is not the default active source today

## Signals and Backtesting

Signals:

```bash
uv run equity signal scan
uv run equity signal scan --watchlist config/watchlist.yaml
uv run equity signal scan --config config/signals.yaml
uv run equity signal scan --format json --output signals.json
```

Forecast training:

```bash
uv run equity forecast --mode train --ticker AAPL --start 2024-01-01 --end 2024-12-31
uv run equity forecast --mode train --ticker AAPL --model-mode v2_meta_label --start 2024-01-01 --end 2024-12-31
```

Training writes model artifacts under `data/models/`. Each training run now produces:

- the model file, for example `AAPL_xgboost_v1_direction_2024-12-31.pkl`
- full metadata JSON: `*.training_metadata.json`
- concise training summary JSON: `*.training_summary.json`
- for `v2_meta_label`, an auditable candidate/label artifact: `*.training_audit.parquet`

The train command also prints a concise summary in the terminal with the model mode, row counts, fold count, and core validation metrics. `v2_meta_label` summaries include the barrier settings used for that run.

Backtesting:

```bash
uv run equity-backtest \
  --strategy sma_crossover \
  --tickers AAPL,MSFT \
  --start-date 2024-01-01 \
  --end-date 2024-12-31
```

Supported built-in strategy names in the CLI are:

- `sma_crossover`
- `momentum`
- `mean_reversion`

## Troubleshooting

Missing ML dependencies:

```bash
uv sync --extra ml
```

Check available CLI options:

```bash
uv run equity ingest --help
uv run equity pipeline --help
uv run equity forecast --help
uv run equity signal --help
```

Validate ticker config before a run:

```bash
uv run equity-daily --list-stats
uv run equity-daily --list-tickers --verbose
```

Inspect logs:

```bash
ls logs
tail -f logs/ingest_daily.log
tail -f logs/run_pipeline.log
tail -f logs/monitor_pipeline.log
```

## Scheduling

Example weekday cron jobs:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && uv run equity-daily >> logs/cron-daily.log 2>&1
0 20 * * 1-5 cd /path/to/equity-lake && uv run equity-monitor >> logs/cron-monitor.log 2>&1
```

Full three-stage pipeline instead of ingestion only:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && uv run equity-pipeline >> logs/cron-pipeline.log 2>&1
```

## Data Source Reality Check

Current shipped behavior:

- US, HK, SG pricing: `yfinance`
- China pricing: `CNHybridFetcher` with `akshare` enabled, `efinance` disabled by default
- Macro, news, and social sentiment are separate optional workflows

Older docs describing China as fully migrated to `efinance`, or a built-in
dashboard for monitoring, are not current.

## Common Fixes

Missing dependencies:

```bash
uv sync
uv sync --extra ml
```

Check available CLI options:

```bash
uv run equity-daily --help
uv run equity-pipeline --help
uv run equity-query --help
uv run equity-monitor --help
uv run equity-signal --help
```

Validate ticker config before a run:

```bash
uv run equity-daily --list-stats
uv run equity-daily --list-tickers --verbose
```

## What Is Not Included

- A built-in Streamlit and static dashboard ship with the current repo
- No `requirements.txt`-based install is the source of truth; use `uv sync`
- `config/example.env` has been removed; use `.env.example`
