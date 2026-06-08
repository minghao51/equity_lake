# ML Analytics Platform

This document describes the current ML-oriented architecture in the repository.
It replaces the older design draft that assumed a built-in Streamlit dashboard
and a larger risk-analysis surface that the repo does not currently ship.

## Current State

The implemented ML path is:

```text
market ingestion -> feature generation -> price-forecast inference
```

Primary entrypoints:

- `uv run equity pipeline`
- `uv run equity forecast`
- `uv run equity query`
- `uv run equity monitor`

## Implemented Components

### Ingestion

Implemented in the ingestion package and orchestrated by:

- `src/equity_lake/ingestion/orchestrator.py`
- `src/equity_lake/core/dag.py`

Market coverage today:

- US via `yfinance`
- HK/SG via `yfinance`
- CN via `CNHybridFetcher` with `akshare` active by default in the current
  orchestrator path

Optional adjacent workflows also exist for:

- macro indicators
- US news ingestion
- US social sentiment ingestion

### Feature Engineering

Feature generation is exposed through the pipeline helpers and feature jobs.
The feature stage writes into:

```text
data/lake/features/
```

The public orchestration helper is:

```python
from equity_lake.pipelines.features import run_feature_pipeline
```

### ML Inference

The ML layer currently centers on price-forecast inference and related jobs.
The public orchestration helper is:

```python
from equity_lake.pipelines.ml import run_ml_inference
```

The main user-facing wrapper is:

```bash
uv run equity pipeline --skip-ingestion --skip-features
```

## Data Flow

```text
config/tickers.yaml
        |
        v
equity ingest / run_daily_ingestion
        |
        v
data/lake/{us_equity,cn_ashare,hk_sg_equity}/
        |
        v
run_feature_pipeline
        |
        v
data/lake/features/
        |
        v
run_ml_inference / equity forecast
```

## Operational Model

- Storage is local-first and file-backed
- Parquet under `data/lake/` is the durable runtime artifact
- DuckDB is the query and analysis layer
- JSON logs in `logs/` are the main observability surface

## What Is Deliberately Out of Scope

The current repository does not ship these as implemented product features:

- built-in Streamlit dashboard
- integrated Monte Carlo portfolio simulator
- dedicated VaR/CVaR service layer
- web UI for monitoring or model exploration

Those may still be reasonable future extensions, but they should be documented
as proposals rather than current behavior.

## Practical Guidance

If you want to work with the current ML stack:

1. install ML extras with `uv sync --extra ml`
2. run `uv run equity pipeline --dry-run --verbose`
3. inspect generated artifacts under `data/lake/features/` and `logs/`
4. use `uv run equity query` or notebooks for analysis

## Related Docs

- [Quick Start](../../getting-started/quickstart.md)
- [Pipeline User Guide](../../user-guide/pipeline.md)
- [Project Structure](../../developer-guide/project-structure.md)
