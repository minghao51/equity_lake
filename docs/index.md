# Equity Lake

Local-first equity EOD data pipeline with S3 bootstrap and daily updates.

## Quick Links

- [Quickstart Guide](getting-started/quickstart.md) - Get started in 5 minutes
- [Pipeline Guide](user-guide/pipeline.md) - Ingestion, features, ML, monitoring, and scheduling
- [CLI Reference](user-guide/20260406-cli-reference.md) - Config, loader, update, and dashboard commands
- [Architecture](developer/architecture/ARCHITECTURE.md) - System design and data flow
- [Dashboard Hosting](user-guide/20260406-dashboard-hosting.md) - Static site build and Pages deployment

## Features

- **Multi-market ingestion** - US, China A-shares, HK/SG, JPX, KRX
- **Hive-partitioned Parquet** - Efficient columnar storage with DuckDB query engine
- **Feature engineering** - Hamilton DAG-based technical indicators
- **ML inference** - XGBoost price forecasting
- **Signal scanning** - Configurable watchlist-based signal generation
- **Backtesting** - Vectorized engine via polars-backtest (requires `uv sync --extra backtesting`)
- **News + Sentiment** - Finnhub news ingestion with VADER sentiment analysis
- **Static dashboard** - Build and deploy via GitHub Pages
