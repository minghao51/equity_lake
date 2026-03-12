# Project Structure

The repository is organized so a new contributor can quickly distinguish source code from runtime artifacts.

## Top Level

- `src/equity_lake/`: application package
- `tests/`: unit and integration coverage
- `config/`: checked-in configuration assets
- `docs/`: audience-based documentation
- `examples/`: runnable sample scripts only
- `archive/`: historical and superseded documentation
- `data/`: local runtime data and trained models
- `logs/`: local runtime logs

## Package Layout

- `cli/`: stable console entrypoints (`equity-daily`, `equity-query`, `equity-pipeline`, etc.)
- `core/`: runtime configuration, filesystem paths, logging
- `ingestion/`: market fetchers, orchestration, gap detection, parallel ingestion helpers
- `storage/`: S3 sync and DuckDB querying
- `features/`: feature engineering and feature jobs
- `ml/`: forecasting and prediction jobs
- `monitoring/`: health checks and monitoring reports
- `devtools/`: synthetic data generation and developer-only helpers
- `config/`: ticker models, loading, selection, and validation helpers

## Compatibility Modules

- The package root still exposes a small set of flat-module compatibility shims
  such as `feature_jobs.py`, `ml_jobs.py`, `ingestion_jobs.py`, and
  `run_pipeline.py`.
- New code should prefer the domain packages (`equity_lake.features`,
  `equity_lake.ml`, `equity_lake.ingestion`, `equity_lake.backtesting`, etc.)
  and treat the flat modules as backward-compatibility surfaces.

## Import Policy

- Use the domain packages directly (`equity_lake.ingestion`, `equity_lake.features`, `equity_lake.ml`, etc.).
- New code should not rely on historical flat-module names.
