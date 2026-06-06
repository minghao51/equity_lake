# Equity Lake

Local-first equity EOD data pipeline with S3 bootstrap and daily updates.

## Quick Links

- [Quickstart Guide](getting-started/quickstart.md) - Get started in 5 minutes
- [CLI Reference](user-guide/cli-reference.md) - All `equity` commands
- [Architecture](developer/architecture/ARCHITECTURE.md) - System design and data flow

## Features

- **Multi-market ingestion** - US, China A-shares, HK/SG, JPX, KRX
- **Hive-partitioned Parquet** - Efficient columnar storage with DuckDB query engine
- **Feature engineering** - Hamilton DAG-based technical indicators
- **ML inference** - XGBoost price forecasting
- **Signal scanning** - Configurable watchlist-based signal generation
- **Backtesting** - Loop-based and vectorized (vectorbt) engines
- **News + Sentiment** - Finnhub news ingestion with VADER sentiment analysis
- **Static dashboard** - Build and deploy via GitHub Pages
