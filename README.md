# Equity EOD Data Pipeline

Bootstrap from S3 Parquet → sync once → append daily updates locally → query with DuckDB

## Goal

1. **Bootstrap** from a complete, partitioned Parquet dataset on AWS S3 (US equities)
2. **Sync once** to local disk (`data/lake/`)
3. **Append daily EOD updates** (US, China A-shares, HK, SG) using lightweight Python libraries
4. **Query everything** via DuckDB (SQL-on-Parquet)
5. **Orchestrate** via cron or Docker — fully local after initial sync

**No cloud runtime dependency** | **Minimal bandwidth** | **Scalable to 10k+ tickers**

---

## Architecture Overview

```
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

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) installed: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- AWS CLI (for S3 access) or s5cmd (recommended for faster sync)

### 1. Setup Environment

```bash
# Create virtual environment
uv venv
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt
```

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
# Or: bash scripts/sync_from_s3.sh
```

Expected structure after sync:
```
data/lake/us_equity/date=2020-01-01/2020-01-01.parquet
data/lake/us_equity/date=2020-01-02/2020-01-02.parquet
```

### 4. Run Daily Append

```bash
# Sequential mode
make daily

# Parallel mode (3x faster)
uv run python scripts/ingest_daily.py --parallel

# Parallel with custom worker count
uv run python scripts/ingest_daily.py --parallel --max-workers 2
```

### 5. Query with DuckDB

```bash
# Interactive SQL shell
make query

# Python query examples
uv run python scripts/query_example.py
```

---

## Project Structure

```
equity-eod/
├── data/lake/              # Unified data lake
│   ├── us_equity/          # From S3 (full history)
│   ├── cn_ashare/          # Local (daily appends)
│   └── hk_sg_equity/       # Local (daily appends)
├── scripts/
│   ├── sync_from_s3.sh     # One-time S3 sync
│   ├── ingest_daily.py     # Daily append
│   └── query_example.py    # Query examples
├── docs/                   # All documentation
│   ├── implementations/    # Implementation details
│   ├── analytics/          # Test results & performance
│   ├── guides/             # User & developer guides
│   ├── planning/           # Project planning docs
│   ├── education/          # Educational content
│   │   ├── concepts/       # Technical concepts
│   │   └── research/       # Research findings
│   └── archive/            # Archived documentation
├── README.md               # Project overview
└── DOCS_STRUCTURE.md       # Documentation structure guide
```

---

## Makefile Commands

```bash
make setup      # Create venv and install dependencies
make sync       # One-time S3 sync
make daily      # Run daily append
make query      # Open DuckDB interactive shell
make test       # Run tests
make clean      # Clean cache and temp files
make docker-up  # Start Docker container
```

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

```
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

## What's New (v0.2.0)

- **Parallel Market Fetching** - 3x faster daily ingestion
- **Structured Logging** - JSON logs with correlation IDs
- **CN Fetcher Optimization** - Parallel stock fetching (3-5x faster)
- **Better Observability** - Progress tracking and error counting

**Quick Start with Parallel Mode:**
```bash
# Sequential: ~15 seconds
python -m scripts.ingest_daily

# Parallel: ~5 seconds (3x faster)
python -m scripts.ingest_daily --parallel
```

See [Parallel Fetching Guide](docs/guides/parallel-fetching-guide.md) for details.

---

## Documentation

- **[User Guide](docs/guides/user-guide.md)** - Comprehensive usage documentation
- **[Parallel Fetching Guide](docs/guides/parallel-fetching-guide.md)** - Parallel fetching and structured logging
- **[Implementation Details](docs/implementations/)** - Technical implementation documentation
- **[Performance & Testing](docs/analytics/test-results.md)** - Test results and benchmarks
- **[Education](docs/education/)** - Concepts and research guides
- **[CLAUDE.md](claude.md)** - AI assistant development guide

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
3. Submit a pull request with tests

---

## Roadmap

- [ ] Real-time intraday data option
- [ ] More Asian markets (JP, KR)
- [ ] Data quality validation
- [ ] Web dashboard for monitoring
- [ ] Backtesting framework integration
