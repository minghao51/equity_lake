# Quick Start

This guide is the fastest path to a working local install of `equity-lake`.
It reflects the current CLI entrypoints and package layout in this repository.

## Prerequisites

- Python 3.12 or 3.13
- `uv`
- Git

Optional extras:

- `uv sync --extra ml` for the full ingestion -> features -> ML pipeline
- `uv sync --extra s3` if you plan to use S3 sync
- `uv sync --group dev` for local development and tests

## Setup

```bash
git clone <your-repo-url>
cd equity-lake

uv venv
source .venv/bin/activate

uv sync
uv sync --extra ml
```

If you want the checked-in env template:

```bash
cp config/example.env .env
```

## First Commands

Verify the main CLI entrypoints:

```bash
uv run equity-daily --help
uv run equity-pipeline --help
uv run equity-query --help
uv run equity-monitor --help
```

Inspect the active ticker configuration:

```bash
uv run equity-daily --list-stats
uv run equity-daily --list-tickers --markets us --verbose
```

## Run the Pipeline

Daily ingestion only:

```bash
uv run equity-daily --dry-run --verbose
```

Full pipeline:

```bash
uv run equity-pipeline --dry-run --verbose
uv run equity-pipeline --verbose
```

Useful variants:

```bash
uv run equity-pipeline --markets us
uv run equity-pipeline --tickers AAPL,MSFT,NVDA
uv run equity-pipeline --skip-ingestion --skip-features
```

## Query and Monitor

Run a query example:

```bash
uv run equity-query --query latest_summary
uv run equity-query --query top_volume --days 14
```

Run a health check:

```bash
uv run equity-monitor --verbose
uv run equity-monitor --output-json logs/health_report.json
```

## Signals and Backtests

Signal scan using the checked-in configs:

```bash
uv run equity-signal scan
uv run equity-signal scan --format md --output signals.md
```

Backtest a built-in strategy:

```bash
uv run equity-backtest \
  --strategy sma_crossover \
  --tickers AAPL,MSFT \
  --start-date 2024-01-01 \
  --end-date 2024-12-31
```

## Notes

- The repository no longer ships a built-in dashboard. Use `equity-query`,
  notebooks, or your own UI against the generated Parquet and DuckDB data.
- China ingestion uses `CNHybridFetcher`, but the current orchestrator
  instantiates it with the `akshare` path enabled and `efinance` disabled by
  default.
- The canonical configuration files live under `config/`, especially
  `config/tickers.yaml`, `config/watchlist.yaml`, and `config/signals.yaml`.

## Next Reading

- [Pipeline Usage](../user-guide/pipeline.md)
- [Operations Guide](../user-guide/operations.md)
- [Signals Guide](../user-guide/signals.md)
- [Project Structure](../developer-guide/project-structure.md)
