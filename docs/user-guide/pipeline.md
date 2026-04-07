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
cp config/example.env .env
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
- `config/example.env`: optional runtime env template

The runtime config loader currently reads these env vars:

- `DB_PATH`
- `LOG_LEVEL`
- `LOG_DIR`
- `DATA_DIR`
- `MARKETS`
- `DEV_MODE`
- `USE_TEST_DATA`
- `API_RETRY_ATTEMPTS`
- `API_RETRY_DELAY`

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
uv run equity-signal scan
uv run equity-signal scan --watchlist config/watchlist.yaml
uv run equity-signal scan --config config/signals.yaml
uv run equity-signal scan --format json --output signals.json
```

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

Inspect logs:

```bash
ls logs
tail -f logs/ingest_daily.log
tail -f logs/run_pipeline.log
tail -f logs/monitor_pipeline.log
```

## What Is Not Included

- No built-in Streamlit or web dashboard ships with the current repo
- No `requirements.txt`-based install is the source of truth; use `uv sync`
- No `.env.example` file exists at the repository root; use `config/example.env`
