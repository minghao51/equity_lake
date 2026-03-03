# 🚀 Pipeline User Guide

Complete guide for running the equity data pipeline, from daily EOD ingestion through feature engineering to ML inference and signal scanning.

---

## 📑 Table of Contents

- [Quick Start](#quick-start-5-minute-setup)
- [Installation & Setup](#installation--setup)
- [Pipeline Overview](#pipeline-overview)
- [CLI Commands Reference](#cli-commands-reference)
- [Configuration](#configuration)
- [Advanced Usage](#advanced-usage)
- [Automation & Scheduling](#automation--scheduling)
- [Monitoring & Troubleshooting](#monitoring--troubleshooting)
- [Performance Optimization](#performance-optimization)
- [Best Practices](#best-practices)
- [Appendix](#appendix)

---

## 🎯 Quick Start (5-minute setup)

### Prerequisites Checklist

- [ ] Python 3.12+ installed
- [ ] uv package manager installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- [ ] Git (for cloning the repo)
- [ ] 2GB free disk space minimum

### Five-minute Installation

```bash
# 1. Clone repository
git clone https://github.com/your-org/equity-lake.git
cd equity-lake

# 2. Install dependencies
uv sync

# 3. Generate test data (optional, for quick start)
make generate-test-data

# 4. Verify installation
uv run pytest tests/ -v

# 5. Run your first pipeline
uv run equity-pipeline
```

### Verification Steps

```bash
# Check data was ingested
ls -la data/lake/us_equity/date=*/

# Query latest data
uv run equity-query --query latest_summary

# Check health
uv run equity-monitor
```

### Common First Commands

```bash
# Daily EOD ingestion (yesterday)
uv run equity-daily

# Full pipeline (ingestion → features → ML)
uv run equity-pipeline

# Scan for signals
uv run equity-signal scan

# Health check
uv run equity-monitor
```

---

## 🛠️ Installation & Setup

### Environment Setup

#### Option 1: Using uv (Recommended)

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
uv sync

# Install optional dependencies
uv sync --extra ml           # ML and XGBoost
uv sync --extra s3           # AWS S3 support
uv sync --extra visualization # Plotting libraries
uv sync --extra backtesting  # Backtesting framework
```

#### Option 2: Using pip

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install core dependencies
pip install -r requirements.txt

# Install optional dependencies
pip install -r requirements.txt  # Includes all extras
```

### Configuration File Setup

#### 1. Environment Variables (`.env`)

```bash
# Copy example configuration
cp .env.example .env

# Edit with your settings
nano .env
```

**Key configuration options:**

```bash
# =============================================================================
# AWS Configuration (for S3 sync - optional)
# =============================================================================
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
S3_BUCKET=s3://your-bucket/path/

# =============================================================================
# Data Configuration
# =============================================================================
DB_PATH=equity_data.duckdb
DATA_DIR=data
LOG_DIR=logs

# =============================================================================
# Market Configuration
# =============================================================================
# Comma-separated list of markets to ingest: us,cn,hk,sg
MARKETS=us,cn,hk,sg

# =============================================================================
# API Retry Configuration
# =============================================================================
# Number of retry attempts for failed API calls (default: 3)
API_RETRY_ATTEMPTS=3

# Base delay between retries in seconds (default: 1.0)
API_RETRY_DELAY=1.0

# =============================================================================
# Logging Configuration
# =============================================================================
LOG_LEVEL=INFO
# Options: DEBUG, INFO, WARNING, ERROR, CRITICAL

# =============================================================================
# Development Mode
# =============================================================================
DEV_MODE=false
USE_TEST_DATA=false

# =============================================================================
# FRED API Configuration (for macro indicators)
# =============================================================================
# Register for free API key: https://fred.stlouisfed.org/docs/api/api_key.html
FRED_API_KEY=your_fred_api_key_here

# =============================================================================
# Macro Indicators Configuration (for gold ETF analysis)
# =============================================================================
ENABLE_MACRO_INDICATORS=true
MACRO_INDICATORS=dxy,treasury_10y,tips_yield,breakeven_inflation,vix,gld,iau,policy_uncertainty

# =============================================================================
# News & Sentiment Configuration
# =============================================================================
# Finnhub API for news and sentiment data
# Register for free API key: https://finnhub.io/
FINNHUB_API_KEY=your_finnhub_api_key_here

# Optional: Alpha Vantage API as backup
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_api_key_here
```

#### 2. YAML Configuration Files

##### `config/tickers.yaml` - Ticker Definitions

Defines all tickers to fetch, along with metadata for filtering, grouping, and validation.

**Schema:**
```yaml
markets:
  us:
    currency: USD
    tickers:
      - symbol: AAPL
        name: Apple Inc.
        exchange: NASDAQ
        sector: Technology
        tags: [FAANG, blue-chip, S&P 500, technology, growth]
        active: true
        priority: 10  # 1-10, higher = fetch first

  cn:
    currency: CNY
    tickers:
      - symbol: "000001"
        name: 平安银行
        exchange: SZSE
        sector: Finance
        tags: [blue-chip, banking, major]
        active: true
        priority: 10

groups:
  faang:
    description: "Big 5 tech companies"
    markets: [us]
    tickers: [AAPL, GOOGL, MSFT, AMZN, META]
```

##### `config/signals.yaml` - Signal Configuration

```yaml
# Backtest strategy signals
backtest:
  enabled: true
  strategies:
    - name: "momentum"
      lookback_days: 20
      buy_threshold: 0.02
      sell_threshold: -0.01
  min_win_rate: 0.55

# News sentiment signals
sentiment:
  enabled: true
  sources:
    - "yahoo"
  buy_threshold: 0.5
  sell_threshold: -0.3
  min_articles: 3
  lookback_days: 7

# ML prediction signals
ml:
  enabled: true
  model_dir: "data/models"
  horizon_days: 5
  buy_probability_threshold: 0.60
  sell_probability_threshold: 0.40
  min_confidence: 60

aggregation:
  agreement_boost: 10
  unanimous_boost: 20
```

##### `config/watchlist.yaml` - Custom Watchlists

Create custom watchlists for signal scanning:

```yaml
watchlists:
  tech_stocks:
    name: "Technology Stocks"
    tickers: [AAPL, GOOGL, MSFT, META, NVDA]

  asian_banks:
    name: "Asian Banks"
    markets: [cn, hk, sg]
    tickers:
      cn: ["600036", "000001", "601398"]
      hk: ["0939.HK", "1398.HK", "2318.HK"]
      sg: ["D05.SI", "O39.SI", "U11.SI"]
```

### Directory Structure Overview

```
equity-lake/
├── data/
│   ├── lake/                    # Parquet data lake
│   │   ├── us_equity/          # US market data
│   │   ├── cn_ashare/          # China A-shares
│   │   ├── hk_sg_equity/       # HK/SG markets
│   │   ├── features/           # Feature store
│   │   ├── macro_indicators/   # Macro data
│   │   └── signals/            # Signal history
│   ├── models/                  # ML models
│   └── predictions/             # ML predictions
├── logs/                        # Application logs
├── config/                      # Configuration files
│   ├── tickers.yaml
│   ├── signals.yaml
│   └── watchlist.yaml
├── src/equity_lake/            # Source code
└── docs/                       # Documentation
```

### Initial Data Bootstrap Options

#### Option 1: Generate Test Data (Quick Start)

```bash
make generate-test-data

# Generates last 90 days for 50 tickers across all markets
# Perfect for development and testing
```

#### Option 2: S3 Sync (Historical Data)

```bash
# Sync from S3 bucket
make sync
# or
uv run equity-sync --bucket s3://your-bucket/us_equity/

# Sync specific date range
uv run equity-sync --bucket "s3://bucket/us_equity/date=2024*/"
```

#### Option 3: Recent Dates Only

```bash
# Fetch last 30 days for top 10 US stocks
for i in {1..30}; do
  date=$(date -d "$i days ago" +%Y-%m-%d)
  uv run equity-daily --date $date --tickers AAPL,GOOGL,MSFT,NVDA,TSLA --markets us
done
```

---

## 🏗️ Pipeline Overview

### Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     EQUITY DATA PIPELINE                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐  │
│  │   STAGE 1    │      │   STAGE 2    │      │   STAGE 3    │  │
│  │  Ingestion   │  →   │  Features    │  →   │  ML/AI       │  │
│  │              │      │              │      │  Inference   │  │
│  └──────────────┘      └──────────────┘      └──────────────┘  │
│         ↓                      ↓                      ↓          │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐  │
│  │ Raw OHLCV    │      │ 40+ Technical│      │ Price        │  │
│  │ Data (EOD)   │      │ Indicators   │      │ Forecasts    │  │
│  │              │      │ Returns      │      │              │  │
│  │ • US (yf)    │      │ Volume       │      │ XGBoost      │  │
│  │ • CN (aks)   │      │ Time-based   │      │ Models       │  │
│  │ • HK (yf)    │      │              │      │              │  │
│  │ • SG (yf)    │      │              │      │              │  │
│  └──────────────┘      └──────────────┘      └──────────────┘  │
│         ↓                      ↓                      ↓          │
│  data/lake/            data/lake/features/    data/predictions/  │
│  {market}/*            date=YYYY-MM-DD/       {ticker}_{date}.  │
│  date=YYYY-MM-DD/      *.parquet              parquet           │
│  *.parquet                                                          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
                           ↓
┌─────────────────────────────────────────────────────────────────┐
│                    SIGNAL SCANNING                              │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │
│  │  Technical  │  │  Sentiment  │  │     ML      │             │
│  │  Indicators │  │  Analysis   │  │  Forecasts  │             │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘             │
│         │                │                │                     │
│         └────────────────┴────────────────┘                     │
│                        ↓                                        │
│              ┌───────────────┐                                  │
│              │   Aggregated  │                                  │
│              │    Signals    │                                  │
│              └───────┬───────┘                                  │
│                      ↓                                          │
│            data/signals/date=YYYY-MM-DD/                        │
│            signals.parquet                                      │
└─────────────────────────────────────────────────────────────────┘
```

### Stage Descriptions

#### Stage 1: Data Ingestion (2-5 min)

**Purpose:** Fetch end-of-day OHLCV data from multiple markets

**Data Sources:**
- US Equities: yfinance (NYSE, NASDAQ)
- China A-shares: akshare + efinance (SSE, SZSE)
- Hong Kong: yfinance (HKEX)
- Singapore: yfinance (SGX)

**Output Format:**
- Hive-partitioned Parquet files
- Schema: `ticker`, `date`, `open`, `high`, `low`, `close`, `volume`, `adj_close`
- Compression: Snappy
- Location: `data/lake/{market}/date=YYYY-MM-DD/*.parquet`

**Performance:**
- Sequential: 3-5 min for all markets
- Parallel: 1-2 min for all markets (3x speedup)

#### Stage 2: Feature Engineering (1-3 min)

**Purpose:** Compute technical indicators and features for ML

**Feature Categories:**

1. **Technical Indicators** (40+)
   - Trend: SMA, EMA, MACD
   - Momentum: RSI, Stochastic, Williams %R
   - Volatility: Bollinger Bands, ATR
   - Volume: OBV, Volume Rate of Change

2. **Return Features**
   - 1-day, 5-day, 10-day, 20-day lagged returns
   - Log returns
   - Percentage returns

3. **Volume Features**
   - On-Balance Volume (OBV)
   - Volume ratio (5-day, 20-day)
   - Volume-weighted average price (VWAP)

4. **Time Features**
   - Day of week
   - Month
   - Quarter
   - Day of month

**Output Format:**
- Hive-partitioned Parquet files
- Location: `data/lake/features/date=YYYY-MM-DD/*.parquet`
- One row per ticker per date

#### Stage 3: ML Inference (1-2 min)

**Purpose:** Generate price movement predictions using XGBoost models

**Models:**
- Algorithm: XGBoost (gradient boosting)
- Target: Next-day price direction (up/down)
- Features: All features from Stage 2
- Training: Walk-forward validation with 3-year rolling window

**Output:**
- Predictions: `data/predictions/{ticker}_{date}.parquet`
- Format: ticker, date, predicted_direction, probability, confidence

### Data Flow Visualization

```
Trading Date: 2024-12-01

┌──────────────────────────────────────────────────────────────┐
│ INPUT: Tickers                                               │
│ AAPL, GOOGL, MSFT, NVDA, TSLA (10 tickers)                   │
└────────────────────┬───────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│ STAGE 1: Ingestion                                          │
│ ├─ Fetch US market data from yfinance                       │
│ ├─ Fetch CN market data from akshare                        │
│ ├─ Fetch HK/SG data from yfinance                           │
│ └─ Write to: data/lake/{market}/date=2024-12-01/*.parquet   │
└────────────────────┬───────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│ STAGE 2: Feature Engineering                                 │
│ ├─ Read raw data from Stage 1                               │
│ ├─ Compute 40+ technical indicators                          │
│ ├─ Calculate returns, volume features, time features        │
│ └─ Write to: data/lake/features/date=2024-12-01/*.parquet   │
└────────────────────┬───────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│ STAGE 3: ML Inference                                       │
│ ├─ Load features from Stage 2                               │
│ ├─ Load XGBoost models for each ticker                      │
│ ├─ Predict next-day price direction                         │
│ └─ Write to: data/predictions/{ticker}_2024-12-01.parquet   │
└────────────────────┬───────────────────────────────────────┘
                     ↓
┌──────────────────────────────────────────────────────────────┐
│ OUTPUT: Signals                                             │
│ ├─ Technical signals (RSI, MACD, etc.)                      │
│ ├─ Sentiment signals (news, social)                         │
│ ├─ ML predictions (XGBoost)                                 │
│ └─ Aggregated signals (all sources combined)                │
└──────────────────────────────────────────────────────────────┘
```

### Output Formats and Locations

```
data/
├── lake/
│   ├── us_equity/
│   │   └── date=2024-12-01/
│   │       └── 2024-12-01.parquet          # Raw OHLCV
│   ├── cn_ashare/
│   │   └── date=2024-12-01/
│   │       └── 2024-12-01.parquet
│   ├── hk_sg_equity/
│   │   └── date=2024-12-01/
│   │       └── 2024-12-01.parquet
│   ├── features/
│   │   └── date=2024-12-01/
│   │       └── features_2024-12-01.parquet # 40+ indicators
│   ├── signals/
│   │   └── date=2024-12-01/
│   │       └── signals_2024-12-01.parquet  # Aggregated signals
│   └── macro_indicators/
│       └── date=2024-12-01/
│           └── macro_2024-12-01.parquet   # DXY, VIX, etc.
├── models/
│   ├── AAPL_model.json                 # XGBoost model
│   ├── GOOGL_model.json
│   └── ...
└── predictions/
    ├── AAPL_2024-12-01.parquet         # Predictions
    ├── GOOGL_2024-12-01.parquet
    └── ...
```

---

## 📋 CLI Commands Reference

This section provides comprehensive documentation for all CLI commands.

### Command Overview

| Command | Purpose | Entry Point |
|---------|---------|-------------|
| `equity-pipeline` | Main pipeline orchestrator | Full pipeline (all stages) |
| `equity-daily` | EOD data ingestion | Daily OHLCV fetching |
| `equity-signal` | Signal scanning | Technical + sentiment + ML |
| `equity-query` | DuckDB queries | Data exploration |
| `equity-monitor` | Health monitoring | Pipeline health checks |
| `equity-backtest` | Backtesting | Strategy validation |
| `equity-sync` | S3 sync | Historical data bootstrap |
| `equity-macro` | Macro indicators | Economic data fetching |
| `equity-news` | News fetching | News with sentiment |
| `equity-sentiment` | Social sentiment | Reddit/Twitter sentiment |
| `equity-backfill` | Data backfilling | Fill missing dates |
| `equity-generate-test-data` | Test data generation | Development/testing |

---

### 1. `equity-pipeline` - Main Pipeline Orchestrator

**Purpose:** Run complete pipeline (ingestion → features → ML) in one command

**Basic Usage:**
```bash
uv run equity-pipeline
```

**Date Selection Options:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--date` | string | Trading date (YYYY-MM-DD) | Yesterday |
| `--days-back` | int | Days back from today | 1 |

**Examples:**
```bash
# Specific date
uv run equity-pipeline --date 2024-12-01

# 3 days ago
uv run equity-pipeline --days-back 3

# Today's data (if available)
uv run equity-pipeline --date $(date +%Y-%m-%d)
```

**Market and Ticker Selection:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--markets` | string | Comma-separated markets | us,cn,hk_sg |
| `--tickers` | string | Comma-separated tickers | Top 10 US stocks |

**Examples:**
```bash
# US markets only
uv run equity-pipeline --markets us

# US + China
uv run equity-pipeline --markets us,cn

# All markets
uv run equity-pipeline --markets us,cn,hk_sg

# Custom tickers
uv run equity-pipeline --tickers AAPL,GOOGL,MSFT --markets us

# Single ticker
uv run equity-pipeline --tickers AAPL
```

**Stage Control Flags:**

| Argument | Description | Default |
|----------|-------------|---------|
| `--skip-ingestion` | Skip Stage 1 (data ingestion) | False |
| `--skip-features` | Skip Stage 2 (feature engineering) | False |
| `--skip-ml` | Skip Stage 3 (ML inference) | False |
| `--stop-on-error` | Stop if any stage fails | True |
| `--continue-on-error` | Continue even if stages fail | False |

**Examples:**
```bash
# Skip ingestion (data already exists)
uv run equity-pipeline --skip-ingestion

# Run only ML inference
uv run equity-pipeline --skip-ingestion --skip-features

# Continue even if ingestion fails
uv run equity-pipeline --continue-on-error

# Run only ingestion + features (no ML)
uv run equity-pipeline --skip-ml
```

**Execution Options:**

| Argument | Description | Default |
|----------|-------------|---------|
| `--dry-run` | Simulate without writing data | False |
| `--verbose, -v` | Enable verbose logging | False |
| `--save-results` | Save results to JSON | False |

**Examples:**
```bash
# Test without writing
uv run equity-pipeline --dry-run --verbose

# Save execution results
uv run equity-pipeline --save-results
# Results saved to: logs/pipeline_results_YYYY-MM-DD.json

# Full verbose mode
uv run equity-pipeline --verbose --dry-run
```

**Complete Command Reference Table:**

| Scenario | Command |
|----------|---------|
| Full pipeline (yesterday) | `uv run equity-pipeline` |
| Full pipeline (specific date) | `uv run equity-pipeline --date 2024-12-01` |
| US markets only | `uv run equity-pipeline --markets us` |
| Custom tickers | `uv run equity-pipeline --tickers AAPL,GOOGL,MSFT` |
| Skip ingestion | `uv run equity-pipeline --skip-ingestion` |
| ML inference only | `uv run equity-pipeline --skip-ingestion --skip-features` |
| Dry run | `uv run equity-pipeline --dry-run --verbose` |
| Continue on error | `uv run equity-pipeline --continue-on-error` |
| Save results | `uv run equity-pipeline --save-results` |

**Error Handling:**

By default, the pipeline stops on first error (`--stop-on-error`). To continue:
```bash
uv run equity-pipeline --continue-on-error
```

Exit codes:
- `0` - All stages successful
- `1` - One or more stages failed

---

### 2. `equity-daily` - EOD Data Ingestion

**Purpose:** Fetch end-of-day OHLCV data for multiple markets

**Basic Usage:**
```bash
uv run equity-daily
```

**Date Selection:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--date` | string | Trading date (YYYY-MM-DD) | Yesterday |

**Examples:**
```bash
# Specific date
uv run equity-daily --date 2024-12-01

# Yesterday (default)
uv run equity-daily
```

**Market Selection:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--markets` | string | Comma-separated markets | us,cn,hk_sg |
| `--macro` | flag | Also fetch macro indicators | False |

**Examples:**
```bash
# US and China only
uv run equity-daily --markets us,cn

# All markets
uv run equity-daily --markets us,cn,hk_sg

# Include macro indicators
uv run equity-daily --macro
```

**Ticker Filtering (from config):**

| Argument | Type | Description |
|----------|------|-------------|
| `--tickers` | string | Explicit ticker list (overrides config) |
| `--tags` | string | Filter by tags (e.g., blue-chip,FAANG) |
| `--sectors` | strings | Filter by sectors (space-separated) |
| `--groups` | string | Predefined groups (e.g., faang,sp500_top_10) |
| `--min-priority` | int | Minimum priority level (1-10) |
| `--match-all-tags` | flag | Require ALL tags instead of ANY |
| `--config` | string | Custom config file path |

**Examples:**
```bash
# Blue-chip stocks only
uv run equity-daily --tags blue-chip

# FAANG stocks
uv run equity-daily --groups faang

# Technology and healthcare sectors
uv run equity-daily --sectors Technology Healthcare

# High priority stocks (8+)
uv run equity-daily --min-priority 8

# Combine filters
uv run equity-daily --sectors Technology --min-priority 9

# All blue-chip tags required
uv run equity-daily --tags blue-chip,dividend --match-all-tags

# Custom config file
uv run equity-daily --config /path/to/custom_tickers.yaml

# Explicit tickers (overrides config)
uv run equity-daily --tickers AAPL,GOOGL,MSFT --markets us
```

**Parallel Execution:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--parallel, -p` | flag | Enable parallel fetching | False |
| `--max-workers` | int | Maximum parallel workers | # of markets |

**Examples:**
```bash
# Enable parallel mode (3x speedup)
uv run equity-daily --parallel

# Custom worker count
uv run equity-daily --parallel --max-workers 4
```

**Gap Detection:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--detect-gaps` | flag | Detect missing data points | False |
| `--coverage-stats` | flag | Show coverage statistics | False |
| `--days-back` | int | Days to check for gaps | 90 |
| `--include-weekends` | flag | Include weekends | False |

**Examples:**
```bash
# Detect gaps in last 90 days
uv run equity-daily --detect-gaps

# Check coverage statistics
uv run equity-daily --coverage-stats

# Check last 30 days
uv run equity-daily --detect-gaps --days-back 30

# Include weekends in gap check
uv run equity-daily --detect-gaps --include-weekends
```

**Utility Commands:**

| Argument | Description |
|----------|-------------|
| `--list-tickers` | List all available tickers from config |
| `--list-stats` | Show config statistics |
| `--dry-run` | Skip actual writes |
| `--verbose, -v` | Enable verbose logging |

**Examples:**
```bash
# List all tickers
uv run equity-daily --list-tickers

# List US market tickers
uv run equity-daily --list-tickers --markets us

# Show config statistics
uv run equity-daily --list-stats

# Dry run with verbose output
uv run equity-daily --dry-run --verbose
```

**Complete Command Reference Table:**

| Scenario | Command |
|----------|---------|
| Fetch yesterday's data | `uv run equity-daily` |
| Fetch specific date | `uv run equity-daily --date 2024-12-01` |
| US market only | `uv run equity-daily --markets us` |
| Blue-chip stocks | `uv run equity-daily --tags blue-chip` |
| FAANG stocks | `uv run equity-daily --groups faang` |
| Tech + healthcare sectors | `uv run equity-daily --sectors Technology Healthcare` |
| Priority 8+ stocks | `uv run equity-daily --min-priority 8` |
| Explicit tickers | `uv run equity-daily --tickers AAPL,GOOGL,MSFT` |
| Parallel fetching | `uv run equity-daily --parallel` |
| Detect gaps | `uv run equity-daily --detect-gaps` |
| Coverage stats | `uv run equity-daily --coverage-stats` |
| List tickers | `uv run equity-daily --list-tickers` |
| List statistics | `uv run equity-daily --list-stats` |
| Dry run | `uv run equity-daily --dry-run --verbose` |

**Gap Detection Output Example:**

```bash
$ uv run equity-daily --detect-gaps --days-back 30

======================================================================
Gap Detection: 2024-11-01 to 2024-12-01
Markets: us_equity, cn_ashare, hk_sg_equity
Business days only: true
======================================================================

US Market:
  Missing dates: 2024-11-15, 2024-11-28
  Coverage: 28/30 trading days (93.3%)

CN Market:
  Missing dates: 2024-11-22
  Coverage: 29/30 trading days (96.7%)

HK/SG Market:
  Missing dates: None
  Coverage: 30/30 trading days (100.0%)

======================================================================
```

---

### 3. `equity-signal` - Signal Scanning

**Purpose:** Scan watchlists and generate trading signals

**Basic Usage:**
```bash
uv run equity-signal scan
```

**Subcommands:**
- `scan` - Scan watchlist and generate signals

**Scan Subcommand Options:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--format, -f` | string | Output format (json, md, table) | table |
| `--date, -d` | string | Target date (YYYY-MM-DD) | Yesterday |
| `--watchlist, -w` | string | Path to watchlist config | Default |
| `--config, -c` | string | Path to signal config | Default |
| `--output, -o` | string | Save output to file | None |
| `--dry-run` | flag | Don't save to history | False |
| `--verbose, -v` | flag | Enable verbose logging | False |

**Examples:**
```bash
# Basic scan
uv run equity-signal scan

# Specific date
uv run equity-signal scan --date 2024-12-01

# JSON format
uv run equity-signal scan --format json

# Markdown format
uv run equity-signal scan --format md

# Save to file
uv run equity-signal scan --output signals.txt

# Custom watchlist
uv run equity-signal scan --watchlist config/my_watchlist.yaml

# Custom signal config
uv run equity-signal scan --config config/my_signals.yaml

# Dry run (don't save history)
uv run equity-signal scan --dry-run

# Verbose mode
uv run equity-signal scan --verbose
```

**Output Formats:**

**Table (default):**
```
┌──────────┬──────────┬──────────┬──────────┬──────────┐
│ Ticker   │ Date     │ Signal   │ Price    │ Strength │
├──────────┼──────────┼──────────┼──────────┼──────────┤
│ AAPL     │ 2024-12-01│ BUY     │ 189.50   │ 85      │
│ GOOGL    │ 2024-12-01│ HOLD    │ 141.20   │ 55      │
│ MSFT     │ 2024-12-01│ SELL    │ 378.90   │ 25      │
└──────────┴──────────┴──────────┴──────────┴──────────┘
```

**JSON:**
```json
[
  {
    "ticker": "AAPL",
    "date": "2024-12-01",
    "signal": "BUY",
    "price": 189.50,
    "strength": 85,
    "sources": ["technical", "ml"]
  }
]
```

**Markdown:**
```markdown
# Signals for 2024-12-01

| Ticker | Signal | Price | Strength |
|--------|--------|-------|----------|
| AAPL   | BUY    | 189.50| 85       |
| GOOGL  | HOLD   | 141.20| 55       |
| MSFT   | SELL   | 378.90| 25       |
```

**Complete Command Reference Table:**

| Scenario | Command |
|----------|---------|
| Basic scan | `uv run equity-signal scan` |
| Specific date | `uv run equity-signal scan --date 2024-12-01` |
| JSON output | `uv run equity-signal scan --format json` |
| Save to file | `uv run equity-signal scan --output signals.json` |
| Custom watchlist | `uv run equity-signal scan --watchlist config/tech_stocks.yaml` |
| Dry run | `uv run equity-signal scan --dry-run` |

---

### 4. `equity-query` - DuckDB Queries

**Purpose:** Query data with DuckDB SQL

**Basic Usage:**
```bash
uv run equity-query
```

**Available Queries:**

| Query | Description |
|-------|-------------|
| `latest_summary` | Latest data summary by market |
| `top_volume` | Top stocks by volume (last N days) |
| `gainers_losers` | Top gainers and losers |
| `volatility` | Most volatile stocks |
| `market_stats` | Market summary statistics |
| `price_range` | Price range analysis |
| `benchmark` | Performance benchmarks |

**Options:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--query` | string | Query name | (run all) |
| `--days` | int | Number of days back | 14 |
| `--ticker` | string | Filter by ticker | None |
| `--output` | string | Export to CSV | None |

**Examples:**
```bash
# Run all queries
uv run equity-query

# Specific query
uv run equity-query --query top_volume

# Custom date range
uv run equity-query --query top_volume --days 30

# Filter by ticker
uv run equity-query --query volatility --ticker AAPL

# Export to CSV
uv run equity-query --query gainers_losers --output results.csv

# Performance benchmark
uv run equity-query --query benchmark
```

---

### 5. `equity-monitor` - Health Monitoring

**Purpose:** Run pipeline health checks

**Basic Usage:**
```bash
uv run equity-monitor
```

**Health Check Options:**

| Argument | Type | Description | Default |
|----------|------|-------------|---------|
| `--max-age-days` | int | Maximum data age (days) | 2 |
| `--null-threshold-pct` | float | Max null percentage | 5.0 |
| `--output-json` | string | Save report to JSON | None |
| `--verbose, -v` | flag | Verbose logging | False |

**Examples:**
```bash
# Basic health check
uv run equity-monitor

# Verbose mode
uv run equity-monitor --verbose

# Custom thresholds
uv run equity-monitor --max-age-days 1 --null-threshold-pct 3.0

# Save report
uv run equity-monitor --output-json health_report.json
```

**What it Checks:**

1. **Data Freshness** - Are markets up-to-date?
2. **Data Quality** - Null values, missing data
3. **Pipeline Logs** - Errors, warnings
4. **Feature Store** - Recent features available

**Output Example:**
```
======================================================================
PIPELINE HEALTH MONITOR
======================================================================

✅ PASS       Data Freshness
✅ PASS       Data Quality
✅ PASS       Pipeline Logs
✅ PASS       Feature Store

======================================================================
✅ Pipeline is HEALTHY
======================================================================
```

---

### 6. `equity-backtest` - Backtesting

**Purpose:** Backtest trading strategies

**Basic Usage:**
```bash
uv run equity-backtest --strategy sma_crossover --tickers AAPL,MSFT --start-date 2023-01-01 --end-date 2023-12-31
```

**Options:**

| Argument | Type | Description | Required |
|----------|------|-------------|----------|
| `--strategy, -s` | string | Strategy name | Yes |
| `--tickers, -t` | string | Comma-separated tickers | Yes |
| `--start-date` | string | Start date (YYYY-MM-DD) | Yes |
| `--end-date` | string | End date (YYYY-MM-DD) | Yes |
| `--initial-cash` | float | Initial capital | 100000 |
| `--walk-forward` | flag | Walk-forward validation | False |
| `--output, -o` | string | Output JSON path | None |

**Available Strategies:**
- `sma_crossover` - Simple moving average crossover
- `momentum` - Cross-sectional momentum
- `mean_reversion` - Bollinger Band mean reversion

**Examples:**
```bash
# SMA crossover strategy
uv run equity-backtest --strategy sma_crossover \
    --tickers AAPL,MSFT \
    --start-date 2023-01-01 \
    --end-date 2023-12-31

# Walk-forward validation
uv run equity-backtest --strategy momentum \
    --tickers AAPL,GOOGL,MSFT \
    --start-date 2023-01-01 \
    --end-date 2023-12-31 \
    --walk-forward

# Save results
uv run equity-backtest --strategy mean_reversion \
    --tickers AAPL \
    --start-date 2023-01-01 \
    --end-date 2023-12-31 \
    --output results.json
```

---

### 7. Other Commands

#### `equity-sync` - S3 Sync

**Purpose:** Sync historical data from S3

```bash
# Sync from S3
uv run equity-sync

# Custom bucket
uv run equity-sync --bucket s3://my-bucket/us_equity/

# Dry run
uv run equity-sync --dry-run --verbose
```

#### `equity-macro` - Macro Indicators

**Purpose:** Fetch macro economic indicators

```bash
# Fetch all configured indicators
uv run equity-macro

# Specific date
uv run equity-macro --date 2024-12-01
```

#### `equity-news` - News Fetching

**Purpose:** Fetch news with sentiment analysis

```bash
# Fetch news
uv run equity-news

# Dry run
uv run equity-news --dry-run --verbose

# Specific date
uv run equity-news --date 2024-12-01
```

#### `equity-sentiment` - Social Sentiment

**Purpose:** Fetch social sentiment (Reddit/Twitter)

```bash
# Fetch sentiment
uv run equity-sentiment

# Dry run
uv run equity-sentiment --dry-run --verbose
```

#### `equity-backfill` - Data Backfilling

**Purpose:** Fill missing dates

```bash
# Backfill last 30 days
uv run equity-backfill --days-back 30

# Parallel mode
uv run equity-backfill --days-back 30 --parallel
```

#### `equity-generate-test-data` - Test Data Generation

**Purpose:** Generate realistic test data

```bash
# Generate 90 days of test data
uv run equity-generate-test-data
```

---

### Makefile Shortcuts Summary

```bash
# Environment and Setup
make setup              # Create venv and install deps
make dev-setup          # Install dev dependencies
make validate           # Validate project setup

# Data Pipeline Commands
make sync               # S3 sync
make daily              # Daily EOD ingestion
make query              # Run query examples
make pipeline           # Full pipeline
make monitor            # Health checks
make fetch-macro        # Fetch macro indicators
make generate-test-data # Generate test data

# News & Sentiment
make news               # Fetch news with sentiment
make news-dry           # Test news fetching (dry run)
make sentiment          # Fetch social sentiment
make sentiment-dry      # Test sentiment fetching (dry run)

# Testing
make test               # Run all tests
make test-unit          # Unit tests only
make test-integration   # Integration tests only
make test-slow          # Slow tests only

# Code Quality
make lint               # Run ruff linting
make format             # Format code
make check              # Run mypy type checking
make check-all          # Run all checks
make clean              # Clean cache and temp files

# Docker
make docker-up          # Start containers
make docker-down        # Stop containers
make docker-logs        # Show logs
make docker-build       # Build image

# Backtesting
make quick-test         # Quick validation
make test-backtest      # Full backtesting test suite

# CI
make ci                 # Run all CI checks
make dev-test           # Format + lint + test
```

---

## ⚙️ Configuration

### Environment Variables (`.env`)

#### AWS/S3 Settings

```bash
AWS_ACCESS_KEY_ID=your_key
AWS_SECRET_ACCESS_KEY=your_secret
S3_BUCKET=s3://your-bucket/path/
```

#### API Keys

```bash
# FRED API (free)
FRED_API_KEY=your_fred_api_key

# Finnhub API (free tier)
FINNHUB_API_KEY=your_finnhub_api_key

# Alpha Vantage API (free tier)
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_api_key
```

#### Data Paths

```bash
DB_PATH=equity_data.duckdb
DATA_DIR=data
LOG_DIR=logs
```

#### Market Configuration

```bash
# Default markets to ingest
MARKETS=us,cn,hk,sg
```

#### Retry Configuration

```bash
# Number of retry attempts
API_RETRY_ATTEMPTS=3

# Base delay between retries (seconds)
API_RETRY_DELAY=1.0
```

#### Logging Levels

```bash
# Options: DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_LEVEL=INFO
```

#### Development Mode

```bash
DEV_MODE=false
USE_TEST_DATA=false
```

---

### YAML Configuration Files

#### `config/tickers.yaml` - Ticker Definitions

**Structure:**
```yaml
version: "1.0"

metadata:
  description: "Ticker configuration"
  last_updated: "2025-01-24"

markets:
  us:
    currency: USD
    description: "US Equities"
    tickers:
      - symbol: AAPL
        name: Apple Inc.
        exchange: NASDAQ
        sector: Technology
        tags: [FAANG, blue-chip, S&P 500]
        active: true
        priority: 10

groups:
  faang:
    description: "Big 5 tech"
    markets: [us]
    tickers: [AAPL, GOOGL, MSFT, AMZN, META]

validation:
  market_formats:
    us: "^[A-Z]{1,5}(-[A-Z]{1,2})?$"
    cn: "^\\d{6}$"
```

**Key Fields:**
- `symbol` - Ticker symbol (market-specific format)
- `name` - Company name
- `exchange` - Exchange code
- `sector` - Business sector
- `tags` - List of tags for grouping
- `active` - Set to false to skip
- `priority` - 1-10, higher = fetch first

#### `config/signals.yaml` - Signal Configuration

```yaml
# Backtest strategy signals
backtest:
  enabled: true
  strategies:
    - name: "momentum"
      lookback_days: 20
      buy_threshold: 0.02
      sell_threshold: -0.01
  min_win_rate: 0.55

# News sentiment signals
sentiment:
  enabled: true
  sources: ["yahoo"]
  buy_threshold: 0.5
  sell_threshold: -0.3
  min_articles: 3
  lookback_days: 7

# ML prediction signals
ml:
  enabled: true
  model_dir: "data/models"
  horizon_days: 5
  buy_probability_threshold: 0.60
  sell_probability_threshold: 0.40
  min_confidence: 60

aggregation:
  agreement_boost: 10
  unanimous_boost: 20
```

#### `config/watchlist.yaml` - Custom Watchlists

```yaml
watchlists:
  tech_stocks:
    name: "Technology Stocks"
    tickers: [AAPL, GOOGL, MSFT, META, NVDA]

  asian_banks:
    name: "Asian Banks"
    markets: [cn, hk, sg]
    tickers:
      cn: ["600036", "000001", "601398"]
      hk: ["0939.HK", "1398.HK", "2318.HK"]
      sg: ["D05.SI", "O39.SI", "U11.SI"]
```

---

### Examples for Common Customizations

#### 1. Add New Tickers

Edit `config/tickers.yaml`:
```yaml
markets:
  us:
    tickers:
      - symbol: NEW ticker
        name: New Company Inc.
        exchange: NASDAQ
        sector: Technology
        tags: [growth]
        active: true
        priority: 8
```

#### 2. Create Custom Watchlist

Create `config/my_watchlist.yaml`:
```yaml
watchlists:
  my_stocks:
    name: "My Portfolio"
    tickers: [AAPL, GOOGL, MSFT, NVDA]
```

Use it:
```bash
uv run equity-signal scan --watchlist config/my_watchlist.yaml
```

#### 3. Adjust Signal Thresholds

Edit `config/signals.yaml`:
```yaml
ml:
  buy_probability_threshold: 0.70  # More conservative
  sell_probability_threshold: 0.30
```

#### 4. Change Default Markets

Edit `.env`:
```bash
MARKETS=us,cn  # Only US and China
```

Or use command line:
```bash
uv run equity-pipeline --markets us
```

---

## 🚀 Advanced Usage

### Custom Ticker Selection

**Combining Filters:**
```bash
# Technology stocks with high priority
uv run equity-daily --sectors Technology --min-priority 9

# Blue-chip dividend stocks
uv run equity-daily --tags blue-chip,dividend --match-all-tags

# FAANG + NVDA
uv run equity-daily --groups faang --tickers NVDA --markets us
```

**Explicit Ticker List:**
```bash
# Override config completely
uv run equity-daily --tickers AAPL,GOOGL,MSFT,NVDA,TSLA --markets us
```

### Stage Skipping Strategies

```bash
# Scenario 1: Data already ingested, just run features + ML
uv run equity-pipeline --skip-ingestion

# Scenario 2: Features computed, just run ML
uv run equity-pipeline --skip-ingestion --skip-features

# Scenario 3: Only ingest data (no features, no ML)
uv run equity-daily  # Use equity-daily instead
```

### Parallel Execution Patterns

```bash
# Parallel market fetching (3x speedup)
uv run equity-daily --parallel

# Custom worker count
uv run equity-daily --parallel --max-workers 4

# Parallel backfill
uv run equity-backfill --days-back 30 --parallel
```

### Gap Detection and Backfill

```bash
# Detect gaps
uv run equity-daily --detect-gaps --days-back 90

# Check coverage
uv run equity-daily --coverage-stats --days-back 90

# Backfill missing dates
uv run equity-backfill --days-back 30 --parallel

# Verify backfill
uv run equity-daily --detect-gaps --days-back 30
```

### Custom Config Files

```bash
# Custom ticker config
uv run equity-daily --config /path/to/custom_tickers.yaml

# Custom signal config
uv run equity-signal scan --config /path/to/custom_signals.yaml

# Custom watchlist
uv run equity-signal scan --watchlist /path/to/custom_watchlist.yaml
```

### Dry Run Testing

```bash
# Test pipeline without writing
uv run equity-pipeline --dry-run --verbose

# Test ingestion
uv run equity-daily --dry-run --verbose

# Test signal scan
uv run equity-signal scan --dry-run --verbose
```

### Result Export

```bash
# Export signals to JSON
uv run equity-signal scan --format json --output signals.json

# Export query results to CSV
uv run equity-query --query gainers_losers --output results.csv

# Save pipeline results
uv run equity-pipeline --save-results
# Results: logs/pipeline_results_YYYY-MM-DD.json
```

---

## ⏰ Automation & Scheduling

### Cron Job Examples

#### Daily Pipeline (Weekdays at 7 PM ET)

```bash
# Edit crontab
crontab -e

# Add this line:
0 19 * * 1-5 cd /path/to/equity-lake && uv run equity-pipeline >> logs/cron.log 2>&1
```

#### Daily EOD Ingestion (6 PM ET)

```bash
# Fetch yesterday's data
0 18 * * 1-5 cd /path/to/equity-lake && uv run equity-daily >> logs/ingestion.log 2>&1
```

#### Health Checks (Every 6 Hours)

```bash
# Monitor pipeline health
0 */6 * * * cd /path/to/equity-lake && uv run equity-monitor >> logs/health.log 2>&1
```

#### Weekly Model Retraining (Sunday 2 AM)

```bash
# Retrain ML models weekly
0 2 * * 0 cd /path/to/equity-lake && uv run python -m equity_lake.price_forecaster --mode backtest --ticker AAPL >> logs/retrain.log 2>&1
```

#### Hourly Feature Updates (Market Hours)

```bash
# Intraday feature updates (9 AM - 4 PM ET)
0 9-16 * * 1-5 cd /path/to/equity-lake && uv run python -m equity_lake.features.engineering --date $(date +\%Y-\%m-\%d) --tickers AAPL,GOOGL,MSFT >> logs/intraday.log 2>&1
```

### Systemd Timer Configuration

#### Service File: `/etc/systemd/system/equity-pipeline.service`

```ini
[Unit]
Description=Equity Data Pipeline
After=network.target

[Service]
Type=oneshot
User=your-user
WorkingDirectory=/path/to/equity-lake
ExecStart=/usr/local/bin/uv run equity-pipeline
StandardOutput=append:/path/to/equity-lake/logs/pipeline.log
StandardError=append:/path/to/equity-lake/logs/pipeline.error
```

#### Timer File: `/etc/systemd/system/equity-pipeline.timer`

```ini
[Unit]
Description=Run Equity Pipeline Daily
Requires=equity-pipeline.service

[Timer]
OnCalendar=Mon-Fri 19:00
Persistent=true

[Install]
WantedBy=timers.target
```

#### Enable and Start:

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable timer
sudo systemctl enable equity-pipeline.timer

# Start timer
sudo systemctl start equity-pipeline.timer

# Check status
sudo systemctl status equity-pipeline.timer
```

### Docker Automation with docker-compose

#### docker-compose.yml

```yaml
version: '3.8'

services:
  pipeline:
    build: .
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./config:/app/config
    environment:
      - LOG_LEVEL=INFO
      - MARKETS=us,cn,hk_sg
    command: uv run equity-pipeline

  scheduler:
    build: .
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - SCHEDULE="0 19 * * 1-5"
    command: uv run equity-pipeline
    restart: always
```

#### Run:

```bash
# Start services
docker compose up -d

# View logs
docker compose logs -f

# Stop services
docker compose down
```

---

## 🩺 Monitoring & Troubleshooting

### Log File Locations

```bash
# Main logs directory
logs/

# Log files
logs/run_pipeline.log         # Main pipeline orchestrator
logs/ingest_daily.log         # Daily ingestion
logs/feature_engineering.log  # Feature engineering
logs/ml_inference.log         # ML inference
logs/monitor_pipeline.log     # Health monitoring
logs/cron.log                 # Scheduled tasks
```

### Viewing Logs

```bash
# Recent logs (last 100 lines)
tail -100 logs/run_pipeline.log

# Follow logs in real-time
tail -f logs/run_pipeline.log

# All log files today
ls -lt logs/*.log | head -10

# Grep for errors
grep -i "error" logs/*.log

# Grep for specific ticker
grep "AAPL" logs/*.log
```

### Health Check Commands

```bash
# Basic health check
uv run equity-monitor

# Verbose health check
uv run equity-monitor --verbose

# Custom thresholds
uv run equity-monitor --max-age-days 1 --null-threshold-pct 3.0

# Save health report
uv run equity-monitor --output-json health_report.json

# Check specific market
ls -la data/lake/us_equity/date=$(date +%Y-%m-%d)/

# Verify data integrity
python -c "import pandas as pd; df = pd.read_parquet('data/lake/us_equity/date=2024-12-01/2024-12-01.parquet'); print(df.head())"
```

### Common Issues and Solutions

#### Issue: "No data found"

**Symptoms:**
```
Catalog Error: No files found matching pattern
```

**Diagnosis:**
```bash
# Check if data directories exist
ls -la data/lake/

# Check if Parquet files are present
find data/lake/ -name "*.parquet" | head -10

# Verify partition structure
ls -la data/lake/us_equity/ | grep "date="
```

**Solutions:**
1. Run initial sync: `make sync` or `uv run equity-sync`
2. Generate test data: `make generate-test-data`
3. Run ingestion: `uv run equity-daily --date 2024-12-01`
4. Verify date format: `date +%Y-%m-%d`

#### Issue: API Rate Limiting

**Symptoms:**
```
Too Many Requests for URL
```

**Solutions:**
```bash
# 1. Add rate limiting (already implemented in fetchers)
# Time delays are built-in: 500ms between requests

# 2. Use batch downloads (already implemented for US market)
# yfinance downloads multiple tickers in parallel

# 3. Reduce number of tickers
uv run equity-daily --tickers AAPL,GOOGL,MSFT

# 4. Retry with exponential backoff (already implemented)
# Set in .env: API_RETRY_ATTEMPTS=3, API_RETRY_DELAY=1.0
```

#### Issue: Feature Engineering Failures

**Symptoms:**
```
Feature engineering failed: Insufficient historical data
```

**Diagnosis:**
```bash
# Check data availability
uv run equity-query --query latest_summary

# Check date range
find data/lake/us_equity/ -name "*.parquet" | wc -l

# Verify minimum data requirement (60 days per ticker)
```

**Solutions:**
```bash
# 1. Backfill more history
uv run equity-backfill --days-back 90 --parallel

# 2. Reduce lookback period (edit config)
# In config/signals.yaml, reduce lookback_days

# 3. Check for gaps
uv run equity-daily --detect-gaps --days-back 90
```

#### Issue: ML Inference Failures

**Symptoms:**
```
ML inference failed: No features found for ticker
```

**Diagnosis:**
```bash
# Check if features exist
ls -la data/lake/features/date=*/

# Check if models exist
ls -la data/models/

# Verify feature schema
python -c "import pandas as pd; df = pd.read_parquet('data/lake/features/date=2024-12-01/*.parquet'); print(df.columns.tolist())"
```

**Solutions:**
```bash
# 1. Run feature engineering first
uv run equity-pipeline --skip-ingestion

# 2. Train models if missing
uv run python -m equity_lake.price_forecaster --mode train --ticker AAPL

# 3. Check feature availability
uv run equity-query --query latest_summary
```

#### Issue: Connection Errors (China Market)

**Symptoms:**
```
requests.exceptions.ConnectionError: HTTPSConnectionPool
```

**Diagnosis:**
```bash
# Test akshare connectivity
python -c "import akshare as ak; print(ak.stock_info_a_code_name())"

# Test efinance connectivity
python -c "import efinance as ef; print(df = ef.stock.get_base_info())"
```

**Solutions:**
```bash
# 1. Check network connectivity (may need VPN for China)
ping api.akshare.xyz

# 2. Verify akshare version
uv pip list | grep akshare

# 3. Update akshare
uv pip install --upgrade akshare

# 4. Use hybrid fetcher (akshare + efinance)
# Already implemented in CNHybridFetcher
```

#### Issue: S3 Sync Failures

**Symptoms:**
```
An error occurred (403) when calling the HeadObject operation
```

**Diagnosis:**
```bash
# Test AWS credentials
aws configure list

# Test bucket access
aws s3 ls s3://your-bucket/

# For public buckets, test with --no-sign-request
aws s3 ls s3://public-bucket/ --no-sign-request
```

**Solutions:**
```bash
# 1. Configure AWS credentials
aws configure

# 2. Use IAM role for EC2 instances
# Add IAM role with S3 read permissions

# 3. For public buckets, use --no-sign-request
uv run equity-sync --no-sign-request

# 4. Check bucket policy permissions
# Ensure bucket allows read access
```

### Performance Issues

**Symptom:** Pipeline is slow

**Diagnosis:**
```bash
# Check if parallel fetching is enabled
uv run equity-daily --verbose | grep "parallel"

# Check for rate limiting
tail -f logs/ingest_daily.log | grep -i "rate limit"

# Check ticker count
uv run equity-daily --list-stats
```

**Solutions:**
```bash
# 1. Enable parallel fetching (3x speedup)
uv run equity-daily --parallel

# 2. Reduce number of tickers
uv run equity-daily --tags blue-chip

# 3. Use fewer markets
uv run equity-pipeline --markets us

# 4. Check network speed
speedtest-cli

# 5. Increase worker count
uv run equity-daily --parallel --max-workers 8
```

---

## ⚡ Performance Optimization

### Parallel Fetching Tips

```bash
# Enable parallel mode (3x speedup)
uv run equity-daily --parallel

# Custom worker count
uv run equity-daily --parallel --max-workers 4

# Parallel backfill
uv run equity-backfill --days-back 30 --parallel
```

### Ticker Count Reduction

```bash
# Use high-priority tickers only
uv run equity-daily --min-priority 9

# Use specific sectors
uv run equity-daily --sectors Technology Healthcare

# Use predefined groups
uv run equity-daily --groups faang
```

### Market Selection Strategies

```bash
# US market only (fastest)
uv run equity-pipeline --markets us

# US + China (medium speed)
uv run equity-pipeline --markets us,cn

# All markets (slowest)
uv run equity-pipeline --markets us,cn,hk_sg
```

### Performance Benchmark Table

| Configuration | Ingestion | Features | ML Inference | Total |
|--------------|-----------|----------|--------------|-------|
| 10 tickers, US only | 1-2 min | 30-60s | 20-30s | **2-4 min** |
| 10 tickers, all markets | 3-5 min | 30-60s | 20-30s | **4-7 min** |
| 50 tickers, US only | 2-3 min | 2-3 min | 1-2 min | **5-8 min** |
| 50 tickers, all markets | 5-8 min | 2-3 min | 1-2 min | **8-13 min** |
| 100 tickers, US only | 3-5 min | 4-6 min | 2-3 min | **9-14 min** |
| 100 tickers, all markets | 10-15 min | 4-6 min | 2-3 min | **16-24 min** |

**Bottlenecks:**
- **Ingestion:** API rate limits (yfinance, akshare)
- **Features:** Rolling window calculations
- **ML:** Model loading (one-time cost per ticker)

**Optimization Tips:**
1. Use parallel fetching (`--parallel`)
2. Reduce ticker count with filters
3. Use fewer markets
4. Skip unnecessary stages (`--skip-ml`, `--skip-features`)
5. Enable caching in ML models

---

## 📚 Best Practices

### Daily Workflow Schedule

**Recommended Daily Routine:**

```bash
# 6:00 PM ET - Check health
uv run equity-monitor

# 6:30 PM ET - Run daily ingestion
uv run equity-daily

# 7:00 PM ET - Run full pipeline
uv run equity-pipeline

# 7:30 PM ET - Scan for signals
uv run equity-signal scan

# 8:00 PM ET - Query results
uv run equity-query --query latest_summary
```

### Configuration Management

1. **Version Control Your Configs**
   ```bash
   # Track configuration changes
   git add config/tickers.yaml
   git commit -m "Update ticker list"
   ```

2. **Use Separate Configs for Different Environments**
   ```bash
   # Development
   config/tickers_dev.yaml

   # Production
   config/tickers_prod.yaml

   # Testing
   config/tickers_test.yaml
   ```

3. **Document Custom Changes**
   ```yaml
   # config/tickers.yaml
   metadata:
     description: "Production ticker configuration"
     last_updated: "2025-01-24"
     maintainer: "your-email@example.com"
     changelog:
       - "2025-01-24: Added NVDA to high-priority list"
       - "2025-01-20: Removed inactive tickers"
   ```

### Error Handling Strategies

1. **Use Continue-on-Error for Production**
   ```bash
   uv run equity-pipeline --continue-on-error
   ```

2. **Implement Retry Logic**
   ```bash
   # Already built-in with exponential backoff
   # Configure in .env:
   API_RETRY_ATTEMPTS=3
   API_RETRY_DELAY=1.0
   ```

3. **Monitor Logs Regularly**
   ```bash
   # Daily log check
   tail -100 logs/run_pipeline.log | grep -i "error"

   # Weekly log review
   grep -c "ERROR" logs/*.log
   ```

### Data Validation Routines

1. **Check Data Freshness Daily**
   ```bash
   uv run equity-monitor --max-age-days 1
   ```

2. **Validate Schema After Ingestion**
   ```bash
   # Built-in schema validation in ingestion
   # Runs automatically during write_to_partitioned_parquet()
   ```

3. **Run Quality Checks Weekly**
   ```bash
   uv run equity-monitor --null-threshold-pct 1.0 --verbose
   ```

### Backup Strategies

1. **Backup Configuration Files**
   ```bash
   # Daily backup
   cp -r config/ config_backup_$(date +%Y%m%d)/
   ```

2. **Backup ML Models**
   ```bash
   # Weekly backup
   tar -czf models_backup_$(date +%Y%m%d).tar.gz data/models/
   ```

3. **Backup Signal History**
   ```bash
   # Monthly backup
   tar -czf signals_backup_$(date +%Y%m%d).tar.gz data/lake/signals/
   ```

### Security Practices

1. **Never Commit Credentials**
   ```bash
   # Add .env to .gitignore
   echo ".env" >> .gitignore
   ```

2. **Use Environment Variables for Secrets**
   ```bash
   # .env file (git-ignored)
   FINNHUB_API_KEY=your_secret_key

   # Load in Python
   from dotenv import load_dotenv
   load_dotenv()
   ```

3. **Rotate API Keys Regularly**
   ```bash
   # Update keys every 90 days
   # Document key rotation in CHANGELOG.md
   ```

4. **Use Read-Only Accounts When Possible**
   ```bash
   # For S3 sync, use read-only IAM credentials
   # Only write permissions needed for data/lake/
   ```

---

## 📎 Appendix

### Complete CLI Reference Summary Table

| Command | Purpose | Key Options |
|---------|---------|-------------|
| `equity-pipeline` | Full pipeline | `--date`, `--markets`, `--tickers`, `--skip-*`, `--dry-run` |
| `equity-daily` | EOD ingestion | `--date`, `--markets`, `--tags`, `--sectors`, `--groups`, `--parallel` |
| `equity-signal scan` | Signal scanning | `--format`, `--date`, `--watchlist`, `--output`, `--dry-run` |
| `equity-query` | DuckDB queries | `--query`, `--days`, `--ticker`, `--output` |
| `equity-monitor` | Health checks | `--max-age-days`, `--null-threshold-pct`, `--output-json` |
| `equity-backtest` | Backtesting | `--strategy`, `--tickers`, `--start-date`, `--end-date`, `--walk-forward` |
| `equity-sync` | S3 sync | `--bucket`, `--tool`, `--workers`, `--dry-run` |
| `equity-macro` | Macro indicators | `--date`, `--indicators` |
| `equity-news` | News fetching | `--date`, `--tickers`, `--dry-run` |
| `equity-sentiment` | Social sentiment | `--date`, `--tickers`, `--dry-run` |
| `equity-backfill` | Data backfilling | `--days-back`, `--parallel`, `--markets` |
| `equity-generate-test-data` | Test data | `--days`, `--tickers` |

### Configuration File Locations

```
equity-lake/
├── .env                          # Environment variables
├── config/
│   ├── tickers.yaml              # Ticker definitions
│   ├── signals.yaml              # Signal configuration
│   └── watchlist.yaml            # Custom watchlists
└── pyproject.toml                # Project dependencies
```

### Data Directory Structure

```
data/
├── lake/
│   ├── us_equity/               # US market data
│   ├── cn_ashare/               # China A-shares
│   ├── hk_sg_equity/            # HK/SG markets
│   ├── features/                # Feature store
│   ├── signals/                 # Signal history
│   └── macro_indicators/        # Macro data
├── models/                      # ML models
└── predictions/                 # ML predictions
```

### Log File Locations

```
logs/
├── run_pipeline.log             # Main pipeline
├── ingest_daily.log             # Daily ingestion
├── feature_engineering.log      # Feature engineering
├── ml_inference.log             # ML inference
├── monitor_pipeline.log         # Health monitoring
├── cron.log                     # Scheduled tasks
└── pipeline_results_*.json      # Execution results
```

### Related Documentation Links

- [Quick Start Guide](../getting-started/quickstart.md)
- [Operations Guide](operations.md)
- [Signal Scanner Guide](signals.md)
- [Development Guide](../development/development.md)
- [Architecture Overview](../architecture/architecture.md)

### Support and Contribution

- **Issues:** Report bugs and feature requests at [GitHub Issues](https://github.com/your-org/equity-lake/issues)
- **Documentation:** Contributions welcome via Pull Requests
- **Discussions:** Join our [GitHub Discussions](https://github.com/your-org/equity-lake/discussions)

---

**Last Updated:** 2025-03-04
**Pipeline Version:** 1.0.0
**Document Maintainer:** Equity Data Pipeline Team
