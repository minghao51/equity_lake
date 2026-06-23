# Notebooks

Interactive Jupyter notebooks exploring the equity-lake pipeline end to end.
Each notebook is standalone-runnable (no execution order required beyond the
recommended sequence below).

## Running

```bash
uv sync --group dev          # installs jupyter + ipykernel
uv run jupyter lab           # open the notebooks/ folder
```

Each notebook bootstraps its own import path via `sys.path.insert(0, "../src")`,
so no editable install is required.

## Recommended Reading Order

| # | Notebook | Topic |
|---|----------|-------|
| 01 | [setup-and-config](01-setup-and-config.ipynb) | Project config, settings, YAML validation |
| 02 | [data-ingestion](02-data-ingestion.ipynb) | Multi-market EOD ingestion pipeline |
| 03 | [storage-and-queries](03-storage-and-queries.ipynb) | Hive-partitioned Parquet + DuckDB queries |
| 04 | [hamilton-features](04-hamilton-features.ipynb) | Hamilton DAG feature engineering |
| 05 | [feature-engineering-deep-dive](05-feature-engineering-deep-dive.ipynb) | Technical indicators in depth |
| 06 | [ml-prediction](06-ml-prediction.ipynb) | XGBoost price forecasting |
| 07 | [signal-scanning](07-signal-scanning.ipynb) | Watchlist-based signal generation |
| 08 | [backtesting](08-backtesting.ipynb) | Vectorized backtesting (requires `uv sync --extra backtesting`) |
| 09 | [sentiment-analysis](09-sentiment-analysis.ipynb) | News ingestion + VADER sentiment |
| 10 | [validation-and-quality](10-validation-and-quality.ipynb) | pointblank schema validation & profiling |

The `data/` subfolder holds small sample datasets used by some notebooks.
