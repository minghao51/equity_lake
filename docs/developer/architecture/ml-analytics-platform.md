# ML Analytics Platform

**Last Updated**: 2026-08-29

This document describes the current ML-oriented architecture in the repository.

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
- `src/equity_lake/ingestion/router.py` (market-to-fetcher routing)

Market coverage today:

- US via `yfinance`
- HK/SG via `yfinance`
- CN via `CNHybridFetcher` with `akshare` active by default in the current
  orchestrator path
- JPX via `JPXEquityFetcher` (`sources/jpx.py`, yfinance)
- KRX via `KRXEquityFetcher` (`sources/krx.py`, FinanceDataReader)

Optional adjacent workflows also exist for:

- macro indicators
- US news ingestion
- US social sentiment ingestion

### Feature Engineering

Feature generation lives in the `features` package — the Hamilton DAG modules
under `features/dag/` (`raw_01`, `clean_02`, `features_03`, `enrichments_04`)
driven by `features/pipeline.py` and `features/engineering.py`. The feature
stage writes into:

```text
data/lake/03_gold/features/
```

The public orchestration helper is:

```python
from equity_lake.features import run_feature_job
```

### ML Inference

The ML layer currently centers on price-forecast inference and related jobs.
The public orchestration helper is:

```python
from equity_lake.ml import run_prediction_job
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
data/lake/01_bronze/market_data/{us_equity,cn_ashare,hk_sg_equity,jpx_equity,krx_equity}/
        |
        v
run_feature_job
        |
        v
data/lake/03_gold/features/
        |
        v
run_prediction_job / equity forecast
```

## Operational Model

- Storage is local-first and file-backed
- Date-partitioned Delta tables with Parquet data files under `data/lake/` are
  the durable runtime artifact (ADR-0001)
- DuckDB is the query and analysis layer
- JSON logs in `logs/` are the main observability surface

## Dashboards

The primary dashboard surface is the static exporter:
`uv run equity dashboard build` renders a static HTML dashboard from lake data.
An optional Streamlit app (`dashboard/streamlit_app.py`) is available behind the
`dashboard` dependency group (`uv sync --group dashboard`) and served locally
with `uv run equity dashboard serve`.

## What Is Deliberately Out of Scope

The current repository does not ship these as implemented product features:

- integrated Monte Carlo portfolio simulator
- dedicated VaR/CVaR service layer

Those may still be reasonable future extensions, but they should be documented
as proposals rather than current behavior.

## Practical Guidance

If you want to work with the current ML stack:

1. install ML extras with `uv sync --group ml`
2. run `uv run equity pipeline --dry-run --verbose`
3. inspect generated artifacts under `data/lake/03_gold/features/` and `logs/`
4. use `uv run equity query` or notebooks for analysis

## Related Docs

- [Quick Start](../../getting-started/quickstart.md)
- [Pipeline User Guide](../../user-guide/pipeline.md)
- [Project Structure](../../developer-guide/project-structure.md)
- [Architecture](ARCHITECTURE.md)
