# equity-lake

*local first, setup once*
Bootstrap from S3 Parquet → sync once → append daily updates locally → query with DuckDB

## Goal

[ External Market APIs / S3 Ingest ]
                       │
                       ▼
           ┌──────────────────────┐
           │   Ingestion Block    │ ──(Write Parquet)──> [ DuckDB Raw / Data Lake ]
           └──────────────────────┘
                       │
                       ▼
           ┌──────────────────────┐
           │ Feature Engineering  │ <──(SQL / Polars Engine)
           └──────────────────────┘
                       │
                       ▼
           ┌──────────────────────┐
           │  ML / Signal Engine  │ ──(Append History)──> [ DuckDB Signals Layer ]
           └──────────────────────┘
                       │
                       ▼
           ┌──────────────────────┐
           │ Backtest / Dashboard │ ──(Static Export)──> [ GitHub Pages Site ]
           └──────────────────────┘

1. **Bootstrap** from a complete, partitioned Parquet dataset on AWS S3 (US equities)
2. **Sync once** to local disk (`data/lake/`)
3. **Append daily EOD updates** (US, China A-shares, HK, SG) using lightweight Python libraries
4. **Query everything** via DuckDB (SQL-on-Parquet)
5. **Orchestrate** via cron or Docker — fully local after initial sync

**No cloud runtime dependency** | **Minimal bandwidth** | **Scalable to 10k+ tickers**

---

## Architecture Overview

```text
AWS S3 Bucket → Local Data Lake (Parquet) → DuckDB (SQL Queries)
(full historical)   data/lake/
                       ├── us_equity/  ← from S3
                       ├── cn_ashare/  ← local
                       └── hk_sg_equity/ ← local

Daily EOD Append (cron)
• US/HK/SG: yfinance
• CN A-shares: akshare
```

---

## Recommended Stack

| Component | Tool(s) |
|-----------|---------|
| **Package Manager** | `uv` (ultra-fast Python package installer) |
| **Initial Sync** | `aws-cli` or `s5cmd` (fast S3 sync) |
| **Data Sources** | `yfinance` (US/HK/SG), `akshare` (CN A-shares) |
| **Storage** | Hive-partitioned Parquet (`date=YYYY-MM-DD/`) |
| **Query Engine** | [DuckDB](https://duckdb.org/) |
| **Orchestration** | `cron` or `docker compose` |
| **Language** | Python (ingest) + SQL (analysis) |

---

## Quick Start

### Prerequisites

- Python 3.12 or 3.13
- [uv](https://github.com/astral-sh/uv) installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- AWS CLI (for S3 access) or s5cmd (recommended for faster sync)

### 1. Setup Environment

```bash
# Create virtual environment and install all dependencies
uv sync --all-extras

# Create local env file
cp .env.example .env
```

Credential setup and optional API keys are documented in
[docs/20260406-api-keys.md](docs/20260406-api-keys.md).
Core app defaults live in `config/settings.yaml`, and `EQUITY_*`
variables in `.env` override them when set.

### 2. Configure S3 Access

You need read access to a bucket containing partitioned Parquet (e.g., `s3://my-equity-lake/us_equity/date=YYYY-MM-DD/`).

**Options:**
- Public bucket (no creds needed)
- Private bucket: configure AWS credentials via `~/.aws/credentials` or env vars
- Use [s5cmd](https://github.com/peak/s5cmd) for 10x faster sync

```bash
# Install s5cmd (recommended)
curl -L https://github.com/peak/s5cmd/releases/latest/download/s5cmd_$(uname -s)_$(uname -m).tar.gz | tar xz
sudo mv s5cmd /usr/local/bin/
```

### 3. Initial Sync from S3

```bash
make sync
# Or: dotenvx run -- uv run equity sync
```

Expected structure after sync:

```text
data/lake/us_equity/date=2020-01-01/2020-01-01.parquet
data/lake/us_equity/date=2020-01-02/2020-01-02.parquet
```

### 4. Run Daily Append

```bash
dotenvx run -- uv run equity ingest
dotenvx run -- uv run equity ingest --markets us,cn --verbose
```

### 5. 🚀 Run Full ML Pipeline (New!)

Automated pipeline: **Ingestion → Feature Engineering → ML/AI**

```bash
# Default entrypoint
dotenvx run -- uv run equity pipeline --verbose

# For a specific date
dotenvx run -- uv run equity pipeline --date 2024-12-01

# Dry run (test without writing)
dotenvx run -- uv run equity pipeline --dry-run
```

**What it does:**
1. **Stage 1**: Fetches EOD data from US, CN, HK, SG markets (2-5 min)
2. **Stage 2**: Computes 40+ technical indicators & features (1-3 min)
3. **Stage 3**: Runs ML predictions for your tickers (1-2 min)

**Total time**: 4-10 minutes for 10 tickers

**Custom usage:**

```bash
# Custom tickers
dotenvx run -- uv run equity pipeline --tickers AAPL,GOOGL,MSFT

# US markets only
dotenvx run -- uv run equity pipeline --markets us

# Skip to ML only (if data exists)
dotenvx run -- uv run equity pipeline --skip-ingestion --skip-features
```

See [Pipeline Usage Guide](docs/user-guide/pipeline.md) for complete documentation.
See [CLI Reference](docs/user-guide/20260406-cli-reference.md) for the newer
`equity-config`, `equity-loader`, `equity-update`, and `equity-dashboard`
commands.

### 6. Monitor Pipeline Health

```bash
# Quick health check
dotenvx run -- uv run equity monitor

# Verbose mode with detailed metrics
uv run equity-monitor --verbose

# Save health report to JSON
uv run equity-monitor --output-json health_report.json
```

### 7. Query with DuckDB

```bash
# Interactive SQL shell
make query

# Python query helpers
uv run equity-query
```

### 8. Build The Static Dashboard

```bash
uv run equity-monitor --output-json site/health-report.json
uv run equity-dashboard build --output-dir site
```

Hosting and Pages workflow details live in
[docs/user-guide/20260406-dashboard-hosting.md](docs/user-guide/20260406-dashboard-hosting.md).

---

## Project Structure

```text
equity-lake/
├── src/equity_lake/    # Application package
│   ├── cli/               # Unified CLI entrypoints
│   ├── core/              # Runtime, paths, logging
│   ├── ingestion/         # Market ingestion workflows
│   ├── storage/           # DuckDB + S3 sync
│   ├── features/          # Feature engineering
│   ├── ml/                # Forecasting and ML jobs
│   ├── backtesting/       # Strategy backtesting (loop + vectorbt)
│   └── dashboard/         # Static + Streamlit dashboards
├── tests/                  # Unit and integration tests
├── config/                 # Config files and examples
├── docs/                   # Audience-based documentation
│   ├── getting-started/
│   ├── user-guide/
│   ├── developer/           # Architecture, history, decisions
│   └── reports/
├── data/                   # Local runtime artifacts (ignored)
├── logs/                   # Local runtime logs (ignored)
└── README.md               # Project overview
```

---

## Makefile Commands

```bash
make setup      # Create venv and install core dependencies
make dev-setup  # Install ALL dependencies (equivalent to uv sync --all-extras)
make sync       # One-time S3 sync
make daily      # Run daily append
make pipeline   # Run full ML pipeline (ingestion → features → ML)
make monitor    # Run pipeline health checks
make query      # Open DuckDB interactive shell
make test       # Run tests
make clean      # Clean cache and temp files
make docker-up  # Start Docker container
```

**Unified CLI (recommended):**

```bash
equity --help              # See all available commands
equity ingest --help       # Ingest market data
equity pipeline --help     # Run full ML pipeline
equity bootstrap sample    # Generate sample data for testing
equity signal scan         # Scan watchlist for signals
equity backtest --help     # Backtest strategies
equity dashboard serve     # Launch local Streamlit dashboard
```

Legacy standalone command wrappers are no longer part of the supported CLI
surface. Use `equity ...` commands directly.

---

## Docker Deployment

```bash
# Build and run one-time sync
docker compose build
docker compose run --rm sync

# Start daily cron job
docker compose up -d daily
```

Edit `docker-compose.yml` to configure S3 bucket path, AWS credentials, and cron schedule.

---

## S3 Bucket Requirements

Your S3 bucket must contain partitioned Parquet with this layout:

```text
s3://your-bucket/
└── us_equity/
    ├── date=2020-01-01/
    │   └── part-00000.parquet
    ├── date=2020-01-02/
    │   └── part-00000.parquet
    └── ...
```

**Required Schema:**

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | STRING | e.g., `"AAPL"` |
| `date` | DATE | Partition key + column |
| `open` | DOUBLE | |
| `high` | DOUBLE | |
| `low` | DOUBLE | |
| `close` | DOUBLE | |
| `volume` | BIGINT | |

---

## What's New (v0.4.0)

- **📊 Signal Scanner** - Generate buy/sell/hold signals for watchlists
- **🎯 3 Signal Types** - Backtest strategies, news sentiment, ML predictions
- **📝 Multi-Format Output** - JSON, Markdown, terminal tables
- **💾 Signal History** - Track past signals in Parquet storage

**Quick Start:**

```bash
# Configure watchlist in config/watchlist.yaml
# Generate signals
equity-signal scan --format md
```

See [Signal Scanner Guide](docs/user-guide/signals.md) for details.

---

## What's New (v0.3.0)

- **🚀 Full ML Pipeline** - Automated ingestion → feature engineering → ML inference
- **📊 40+ Technical Indicators** - RSI, MACD, Bollinger Bands, ATR, momentum features
- **🤖 XGBoost Models** - Price forecasting and risk analysis
- **🔍 Health Monitoring** - Data quality checks and freshness alerts
- **📈 Real-time Dashboard** - Streamlit-based visualization (ports 8502/8503)
- **⚡ Parallel Optimization** - 3x faster multi-market fetching

**Quick Start:**

```bash
# Run full ML pipeline
uv run equity-pipeline --verbose

# Check pipeline health
uv run equity-monitor
```

**Previous Releases:**
- **v0.2.0** - Parallel Market Fetching + Structured Logging
- **v0.1.0** - Initial release with S3 sync and daily ingestion

---

## Documentation

### Quick Start Guides

- **[Quick Start Guide](docs/getting-started/quickstart.md)** - Get started in 5 minutes
- **[Pipeline Usage Guide](docs/user-guide/pipeline.md)** - Commands, config, scheduling, monitoring
- **[Signal Scanner Guide](docs/user-guide/signals.md)** - Signal scanning and generation

### Comprehensive Guides

- **[Backtesting Guide](docs/user-guide/backtesting.md)** - Strategy testing and examples
- **[CLI Reference](docs/user-guide/20260406-cli-reference.md)** - Config, dashboard, loader, and update commands
- **[API Keys And Credentials](docs/20260406-api-keys.md)** - Optional API keys by feature
- **[Dashboard Hosting](docs/user-guide/20260406-dashboard-hosting.md)** - Static build and GitHub Pages deployment
- **[Architecture Docs](docs/developer/architecture/)** - System structure and subsystem design
- **[Project Structure](docs/developer-guide/project-structure.md)** - Package layout and import policy
- **[Technical Roadmap](docs/technical_roadmap.md)** - Phased enhancement plan
- **[Roadmap Coverage](docs/20260406-roadmap-coverage.md)** - Current beta vs. roadmap status
- **[Documentation Index](docs/README.md)** - Entry point for all active project docs
- **[Reports](docs/reports/README.md)** - Current analyses and operational writeups
- **[Historical Archive](docs/developer/history/)** - Superseded implementation notes and decision records

---

## Additional Resources

- [DuckDB Documentation](https://duckdb.org/docs/)
- [yfinance GitHub](https://github.com/ranaroussi/yfinance)
- [akshare Documentation](https://akshare.readthedocs.io/)
- [uv Documentation](https://github.com/astral-sh/uv)
- [Parquet Format Spec](https://parquet.apache.org/docs/)

---

## License

MIT License - See LICENSE file for details

---

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Run `make check-all`
4. Submit a pull request with tests

---

## Roadmap

- [ ] Real-time intraday data option
- [x] More Asian markets (JP, KR)
- [x] Data quality validation (Streamlit dashboard)
- [x] Web dashboard for monitoring (Streamlit + static HTML)
- [x] Backtesting framework integration (vectorbt)
- [x] Unified CLI
- [x] Sample data generator
