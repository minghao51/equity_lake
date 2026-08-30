# Project Structure

The repository is organized so a new contributor can quickly distinguish
source code from runtime artifacts. This page is the canonical layout
reference for `AGENTS.md`.

## Top Level

- `src/equity_lake/`: application package
- `tests/`: unit and integration coverage
- `config/`: checked-in YAML configuration (tickers, settings, signals, watchlist)
- `docs/`: audience-based documentation (map in `docs/README.md`)
- `notebooks/`: standalone runnable Jupyter notebooks (01–10)
- `examples/`: runnable sample scripts only
- `data/`: local runtime data — `data/lake/` medallion tables plus auxiliary outputs
- `models/`: trained model artifacts
- `logs/`: local runtime logs

## Package Layout

```
src/equity_lake/          # Source
├── backtesting/          # Backtesting framework (engine.py with polars-backtest)
├── cli/                  # Typer-based CLI (`equity` command, native Typer — no passthrough)
│   ├── __main__.py       # App entrypoint (wires sub-apps, imports command modules)
│   ├── _app.py           # Typer app factory, logging init
│   ├── commands/         # Command modules (admin, analysis, catalog, data, intelligence, pipeline)
│   └── bootstrap.py      # Sample-data bootstrap + shared CLI helpers
├── config/               # YAML config validators (CI/CD)
│   └── validators.py     # tickers.yaml / watchlist.yaml / signals.yaml validators
├── core/                 # paths.py (dirs), logging.py (structlog), schemas.py (columns)
├── dashboard/            # Dashboard/export components
├── devtools/             # Test data generators
├── features/             # Feature engineering (Hamilton-based); run_feature_job lives in __init__.py
├── ingestion/            # Data ingestion pipeline (orchestrator, writers, backfill)
├── ml/                   # ML inference; run_prediction_job lives in __init__.py
├── monitoring/           # Pipeline health checks
├── sentiment/            # Sentiment analysis
├── signals/              # Signal generators
├── sources/              # Market data fetchers (us, cn, hk_sg, jpx, krx, news, sentiment, macro)
├── storage/              # DuckDB (EquityDataDB), S3 sync, Delta Lake
├── validation/           # pointblank-based data validation (schema contracts at ingestion)
└── pipeline.py           # PipelineOrchestrator + stage helpers (ingestion/feature/ml)
```

Runtime data layout:

```
data/lake/                # Partitioned Delta tables (market/date= partitions)
├── 01_bronze/ … 04_platinum/
data/<name>/              # Auxiliary outputs (signals, findings, reports) — not cataloged
```

## Import Policy

- Use the top-level packages directly (`equity_lake.ingestion`,
  `equity_lake.features`, `equity_lake.ml`, …). There is no `domain/` tree.
- Import boundaries are enforced by
  `tests/unit/test_import_boundaries.py`: `core/` must not depend on `cli/`,
  `dashboard/`, or `sources/`. New top-level packages extend
  `LAYER_BOUNDARIES` there.
- Historical flat-module names and one-off CLI entrypoint modules are
  unsupported.
