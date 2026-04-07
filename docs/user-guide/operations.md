# Operations Guide

This guide covers day-to-day operation of the ingestion and monitoring commands.

## Daily Ingestion

The operational entrypoint is:

```bash
uv run equity-daily
```

By default it targets:

- `us`
- `cn`
- `hk_sg`

Useful examples:

```bash
uv run equity-daily --date 2026-03-12
uv run equity-daily --markets us,cn
uv run equity-daily --parallel
uv run equity-daily --parallel --max-workers 2
uv run equity-daily --dry-run --verbose
```

## Ticker Selection

The ingestion command can either:

- use the checked-in ticker config from `config/tickers.yaml`
- filter that config by tags, groups, sectors, and priority
- override config selection with explicit `--tickers`

Examples:

```bash
uv run equity-daily --list-stats
uv run equity-daily --list-tickers --markets us --verbose
uv run equity-daily --tags blue-chip --min-priority 8
uv run equity-daily --groups faang
uv run equity-daily --tickers AAPL,MSFT,NVDA --markets us
```

## Gap Detection

Operational data checks are built into `equity-daily`:

```bash
uv run equity-daily --detect-gaps --days-back 90
uv run equity-daily --coverage-stats --markets us,cn
uv run equity-daily --detect-gaps --include-weekends
```

## Monitoring

Health monitoring is exposed through:

```bash
uv run equity-monitor
uv run equity-monitor --verbose
uv run equity-monitor --max-age-days 2
uv run equity-monitor --null-threshold-pct 5
uv run equity-monitor --output-json logs/health_report.json
```

## Logging

Current commands write structured logs under `logs/`.
Common files include:

- `logs/ingest_daily.log`
- `logs/run_pipeline.log`
- `logs/monitor_pipeline.log`

Quick inspection:

```bash
tail -f logs/ingest_daily.log
tail -f logs/run_pipeline.log
```

## Scheduling

Example weekday cron jobs:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && uv run equity-daily >> logs/cron-daily.log 2>&1
0 20 * * 1-5 cd /path/to/equity-lake && uv run equity-monitor >> logs/cron-monitor.log 2>&1
```

If you want the full three-stage pipeline instead of ingestion only:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && uv run equity-pipeline >> logs/cron-pipeline.log 2>&1
```

## Data Source Reality Check

Current shipped behavior:

- US, HK, and SG pricing flows use `yfinance`
- China pricing flows go through `CNHybridFetcher`
- the current orchestrator does not enable `efinance` by default
- macro, news, and social sentiment are separate optional workflows

That means older docs describing China as fully migrated to `efinance`, or a
built-in dashboard for monitoring, are not current.

## Common Fixes

Missing dependencies:

```bash
uv sync
uv sync --extra ml
```

Need to inspect supported flags:

```bash
uv run equity-daily --help
uv run equity-monitor --help
```

Need to confirm config contents:

```bash
sed -n '1,200p' config/tickers.yaml
sed -n '1,200p' config/example.env
```
