# Equity Lake

Local-first, multi-market equity EOD data pipeline with S3 bootstrap and daily pipeline runs.

## Quick Links

- [Quickstart Guide](getting-started/quickstart.md) - Get started in 5 minutes
- [Pipeline Guide](user-guide/pipeline.md) - Ingestion, features, ML, monitoring, and scheduling
- [CLI Reference](user-guide/20260406-cli-reference.md) - Every command on the unified `equity` CLI
- [Architecture](developer/architecture/ARCHITECTURE.md) - System design and data flow
- [Dashboard Hosting](user-guide/20260406-dashboard-hosting.md) - Static site build and Pages deployment

## Features

- **Multi-market ingestion** - US, China A-shares, HK/SG, JPX, KRX
- **Delta Lake medallion storage** - Date-partitioned Delta tables (`01_bronze`–`04_platinum`) with Parquet data files, queried via DuckDB
- **Feature engineering** - Hamilton DAG-based technical indicators
- **ML inference** - XGBoost/LightGBM price forecasting
- **Signal scanning** - Configurable watchlist-based signal generation
- **Backtesting** - Vectorized engine via polars-backtest (requires `uv sync --group backtesting`)
- **News + Sentiment** - Finnhub news ingestion with VADER/FinBERT sentiment analysis
- **Static dashboard** - Build and deploy via GitHub Pages
