# CLI Reference

Every command lives on the unified `equity` CLI. This page maps the full
surface by workflow area; the live help (`uv run equity --help` and
`uv run equity <command> --help`) is always authoritative.

Key-dependent commands — `sync`, `macro`, `news`, `sentiment`, `transcripts`,
`ratings`, `sec`, `financials`, and the ingestion/pipeline commands that read
`.env` — must run through `dotenvx run --` (see
[API keys](../20260406-api-keys.md)). Local-only commands run fine with plain
`uv run`.

Unless noted otherwise, every command accepts `--verbose` / `-v` for debug
logging, and mutating commands accept `--dry-run` to simulate without writes.

## Command overview

| Area | Commands |
|---|---|
| Ingestion & data | `ingest`, `backfill`, `auto-backfill`, `sync`, `macro`, `pipeline` |
| Unstructured & news | `news`, `sentiment`, `transcripts`, `ratings`, `sec`, `financials` |
| Intelligence & ML | `forecast`, `ml compare`, `ml ablate`, `ml train` |
| Analysis | `query`, `signal scan`, `backtest`, `arena run`, `report backtest` |
| Maintenance & ops | `monitor`, `delta-vacuum`, `delta-compact`, `delta-migrate`, `catalog-generate`, `dashboard build`, `dashboard serve` |
| Config & validation | `config show`, `config get`, `config validate`, `config export`, `validate check`, `validate profile`, `validate drift` |
| Utilities | `bootstrap sample`, `demo seed`, `api serve` |

## Ingestion & data

### `equity ingest`

Ingest daily equity market data.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date `YYYY-MM-DD` |
| `--markets`, `-m` | str | from settings | Comma-separated markets |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--dry-run` | flag | off | Simulate without writes |

```bash
dotenvx run -- uv run equity ingest --date 2026-07-10 --markets us,cn
dotenvx run -- uv run equity ingest --dry-run --markets us --tickers AAPL
```

### `equity backfill`

Backfill historical data. One of `--start` or `--days-back` is required, else
the command exits non-zero. Backfill deliberately runs one market/date at a
time.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--start` | str | — | Start date `YYYY-MM-DD` |
| `--end` | str | yesterday | End date `YYYY-MM-DD` |
| `--days-back` | int | — | Calendar days back from `--end` |
| `--markets`, `-m` | str | `us,cn,hk_sg` | Comma-separated markets |
| `--dry-run` | flag | off | No writes |

```bash
dotenvx run -- uv run equity backfill --days-back 30 --markets us
```

### `equity auto-backfill`

Auto-detect and fill data gaps across markets.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--days-back` | int | `90` | Days to scan for gaps |
| `--markets`, `-m` | str | all | Comma-separated markets |
| `--max-gap-days` | int | `30` | Skip gaps larger than this |
| `--dry-run` | flag | off | Show gaps without filling |

```bash
dotenvx run -- uv run equity auto-backfill --days-back 90 --dry-run
```

### `equity sync`

Sync the data lake from S3 (bootstrap). The remote tree must mirror the local
numbered medallion layout without a `data/lake/` prefix — each market is pulled
from `<bucket>/01_bronze/market_data/<market_dir>`.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--bucket`, `-b` | str | `S3_BUCKET` env | S3 bucket root URL, e.g. `s3://my-bucket` |
| `--workers`, `-w` | int | `16` | Download workers |
| `--dry-run` | flag | off | Simulate |

```bash
dotenvx run -- uv run equity sync --bucket s3://your-bucket
```

### `equity macro`

Fetch macro indicators (FRED/yfinance; FRED series require `FRED_API_KEY`,
yfinance indicators work without it) and upsert them to `01_bronze/macro`.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--indicators` | str | all configured | Comma-separated indicators |
| `--dry-run` | flag | off | Simulate |

```bash
dotenvx run -- uv run equity macro --date 2026-07-10
```

### `equity pipeline`

Run the full EOD pipeline (ingest → features → ML).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date `YYYY-MM-DD` |
| `--days-back` | int | `1` | Days back |
| `--markets`, `-m` | str | all configured | Comma-separated markets |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--skip-ingestion` | flag | off | Skip Stage 1 |
| `--skip-features` | flag | off | Skip Stage 2 |
| `--skip-ml` | flag | off | Skip Stage 3 |
| `--allow-history-backfill` | flag | off | Authorize a 120-day feature-history recovery |
| `--save-results` | flag | off | Save JSON results to `pipeline_results_<date>.json` |
| `--dry-run` | flag | off | Simulate |

```bash
dotenvx run -- uv run equity pipeline --markets us --tickers AAPL,MSFT
dotenvx run -- uv run equity pipeline --markets us --tickers AAPL --allow-history-backfill
```

Without `--allow-history-backfill`, missing warm-up history fails the feature
stage instead of starting a backfill.

## Unstructured & news

These commands need credentials from `.env`, so run them via
`dotenvx run --`. The Finnhub commands (`news`, `sentiment`, `transcripts`,
`ratings`) read `FINNHUB_API_KEY` (or take `--api-key`) and exit non-zero when
no key is found.

### `equity news`

Fetch market news with sentiment analysis.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--max-articles` | int | `50` | Max articles per ticker |
| `--sentiment-method` | str | `vader` | `vader` or `finbert` |
| `--min-relevance` | float | `0.0` | Min relevance 0.0–1.0 |
| `--max-workers` | int | `1` | Parallel workers |
| `--api-key` | str | `FINNHUB_API_KEY` | Finnhub API key |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity news --tickers AAPL,MSFT --sentiment-method finbert
```

### `equity sentiment`

Analyze market sentiment (Finnhub social sentiment).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--max-workers` | int | `1` | Parallel workers |
| `--api-key` | str | `FINNHUB_API_KEY` | Finnhub API key |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity sentiment --tickers AAPL
```

### `equity transcripts`

Fetch earnings call transcripts from Finnhub.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--api-key` | str | `FINNHUB_API_KEY` | Finnhub API key |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity transcripts --tickers AAPL
```

### `equity ratings`

Fetch analyst ratings from Finnhub (structured data, no LLM needed).

Same flags as `transcripts` (`--date`, `--tickers`, `--api-key`, `--dry-run`).

```bash
dotenvx run -- uv run equity ratings --tickers AAPL,MSFT
```

### `equity sec`

Fetch SEC 10-K/10-Q filings from EDGAR full-text search and optionally process
them to silver.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--lookback` | int | `120` | Filing lookback days |
| `--process` | flag | off | Process bronze to silver via LLM |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity sec --tickers AAPL --lookback 90
dotenvx run -- uv run equity sec --tickers AAPL --process
```

### `equity financials`

Fetch SEC XBRL structured financials (balance sheet, income statement,
ratios).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--lookback` | int | `120` | Filing lookback days |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity financials --tickers AAPL,MSFT
```

## Intelligence & ML

### `equity forecast`

Price forecasting (train/predict/backtest). For model training prefer
`equity ml train`, which surfaces `--backend`.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--mode` | str | `predict` | `train`, `predict`, or `backtest` |
| `--ticker` | str | `AAPL` | Ticker symbol |
| `--start` / `--end` | str | past year → today | Train/backtest window `YYYY-MM-DD` |
| `--date` | str | today | Single prediction date (predict mode) |
| `--model-dir` | str | default location | Model directory |
| `--model-mode` | str | `v1_direction` | `v1_direction` or `v2_meta_label` |
| `--tune` | flag | off | Hyperparameter tuning |

```bash
uv run equity forecast --mode predict --ticker AAPL
uv run equity forecast --mode backtest --ticker MSFT --start 2025-01-01
```

### `equity ml`

The ML rigor harness — comparisons and ablations that write FindingCards to
`data/findings/`. See the [ML Rigor guide](20260810-ml-rigor.md) for the full
workflow.

| Command | Purpose |
|---|---|
| `ml compare` | Compare v1 vs v2 labeling and XGBoost vs LightGBM, emit 2 FindingCards |
| `ml ablate` | Ablate enriched vs technical-only features, emit the enrichment-ablation card |
| `ml train` | Train one backend classifier (canonical training entrypoint) |

`ml compare` and `ml ablate` share these flags:

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--universe` | str | `demo` | Config ticker group in `config/tickers.yaml` |
| `--ticker`, `-t` | str | first of universe | Run on a specific ticker |
| `--start` / `--end` | str | ~2y ago → today | Window `YYYY-MM-DD` |
| `--output-dir`, `-o` | str | `data/findings` | Findings dir |
| `--train-window` | int | `252` | Walk-forward train window (rows) |
| `--test-window` | int | `21` | Walk-forward test window (rows) |
| `--embargo-window` | int | `1` | Post-test embargo (rows) |

`ml train` flags:

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--ticker`, `-t` | str | `AAPL` | Ticker symbol |
| `--backend` | str | `xgboost` | `xgboost` or `lightgbm` |
| `--start` / `--end` | str | ~1y ago → today | Window `YYYY-MM-DD` |
| `--model-mode` | str | `v1_direction` | `v1_direction` or `v2_meta_label` |
| `--tune` | flag | off | Hyperparameter tuning |

```bash
dotenvx run -- uv run equity ml compare  --universe demo
dotenvx run -- uv run equity ml ablate   --universe demo
dotenvx run -- uv run equity ml train --ticker AAPL --backend lightgbm --model-mode v2_meta_label
```

`ml compare` and `ml ablate` require feature history under `03_gold/features`;
when it is missing they exit non-zero and point at
`equity pipeline --markets us --tickers <t> --allow-history-backfill` rather
than auto-backfilling.

## Analysis

### `equity query`

Query the data lake via DuckDB.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--query`, `-q` | str | run all | Named query |
| `--db` | str | `equity_data.duckdb` | DuckDB path |

Named queries: `latest_summary`, `top_volume`, `gainers_losers`,
`cross_market`, `moving_avg`, `volatility`, `market_stats`, `price_range`.

```bash
uv run equity query --query top_volume
```

### `equity signal scan`

Scan watchlist and generate signals.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--format`, `-f` | str | `table` | `json`, `md`, or `table` |
| `--date`, `-d` | str | resolved trading date | Target date `YYYY-MM-DD` |
| `--watchlist`, `-w` | str | `config/watchlist.yaml` | Watchlist config path |
| `--config`, `-c` | str | `config/signals.yaml` | Signal config path |
| `--output`, `-o` | str | stdout | Save output to file |
| `--dry-run` | flag | off | Don't save history |

```bash
uv run equity signal scan
uv run equity signal scan --format json --output signals.json
```

### `equity backtest`

Backtest trading strategies (shares the strategy registry with
`equity report backtest`). This is a flat top-level command — there is no
`backtest` sub-app.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--strategy`, `-s` | str | `momentum` | `momentum`, `mean_reversion`, or `trend_following` |
| `--tickers`, `-t` | str | `AAPL,MSFT` | Comma-separated tickers |
| `--start-date` | str | **required** | `YYYY-MM-DD` |
| `--end-date` | str | **required** | `YYYY-MM-DD` |
| `--initial-cash` | float | `100000` | Initial capital |
| `--output`, `-o` | str | stdout only | Output JSON |

```bash
uv run equity backtest --strategy momentum --tickers AAPL,MSFT \
  --start-date 2025-01-01 --end-date 2025-12-31
```

### `equity arena run`

Run the strategy arena (strategies × cost regimes) and emit FindingCards plus
per-run artifacts.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--tickers`, `-t` | str | `AAPL,MSFT,GOOGL,AMZN,NVDA` | Comma-separated tickers |
| `--start-date` | str | **required** | `YYYY-MM-DD` |
| `--end-date` | str | **required** | `YYYY-MM-DD` |
| `--markets` | str | `us` | Comma-separated market codes |
| `--initial-cash` | float | `100000` | Initial capital |
| `--strategies` | str | all | Comma-separated strategy names |
| `--cost-regimes` | str | all | Comma-separated regimes (`zero`, `realistic`, `high`) |
| `--output-dir`, `-o` | str | `data/findings` | Findings dir |

```bash
uv run equity arena run --start-date 2025-01-01 --end-date 2025-12-31 \
  --strategies momentum,mean_reversion
```

### `equity report backtest`

Run a single backtest under one cost regime and write its report artifacts
(equity/drawdown/metrics/trades) to `data/findings/<strategy>__<regime>/`. No
FindingCard.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--strategy`, `-s` | str | `momentum` | Strategy name |
| `--tickers`, `-t` | str | `AAPL,MSFT,GOOGL,AMZN,NVDA` | Comma-separated tickers |
| `--start-date` | str | **required** | `YYYY-MM-DD` |
| `--end-date` | str | **required** | `YYYY-MM-DD` |
| `--markets` | str | `us` | Comma-separated market codes |
| `--cost-regime` | str | `realistic` | `zero`, `realistic`, or `high` |
| `--initial-cash` | float | `100000` | Initial capital |
| `--output-dir`, `-o` | str | `data/findings` | Findings dir |

```bash
uv run equity report backtest --strategy mean_reversion --cost-regime high \
  --start-date 2025-01-01 --end-date 2025-12-31
```

## Maintenance & ops

### `equity monitor`

Monitor pipeline health and data quality. Always writes a report (default
`logs/health-report.json`) so dashboards can render it.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--max-age-days` | int | from settings | Max data age |
| `--null-threshold` | float | from settings | Null % threshold |
| `--output-json` | str | `logs/health-report.json` | Save full report |

```bash
dotenvx run -- uv run equity monitor --output-json site/health-report.json
```

### `equity delta-vacuum`

Remove stale files from Delta Lake tables. Accepts short names (`us`), long
table names (`us_equity`), or full medallion paths
(`01_bronze/market_data/us_equity`).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--markets`, `-m` | str | `us_equity,cn_ashare,hk_sg_equity` | Comma-separated datasets |
| `--retention-hours` | int | `168` | Retention window in hours |
| `--dry-run` | flag | **on** | Preview only; pass `--dry-run=false` to execute |

```bash
uv run equity delta-vacuum --markets us_equity --dry-run=false
```

### `equity delta-compact`

Compact small files in Delta Lake tables.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--markets`, `-m` | str | `us_equity,cn_ashare,hk_sg_equity` | Comma-separated datasets |

```bash
uv run equity delta-compact --markets us_equity,cn_ashare
```

### `equity delta-migrate`

Migrate Hive-partitioned Parquet to Delta Lake format.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--markets`, `-m` | str | all five markets | Comma-separated datasets |
| `--dry-run` | flag | off | Preview only |

```bash
uv run equity delta-migrate --dry-run
```

### `equity catalog-generate`

Generate `data/catalog.jsonl` from the Hamilton DAG topology (one JSON line per
dataset, node, and edge). Never edit the JSONL directly.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--output`, `-o` | str | `data/catalog.jsonl` | Output JSONL path |

```bash
uv run equity catalog-generate
```

### `equity dashboard build`

Build the static HTML dashboard (`index.html`, `datasets.html`,
`health.html`, `updates.html`, `config.html`, plus `dashboard-data.json`).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--output-dir`, `-o` | str | settings default | Output directory |

```bash
dotenvx run -- uv run equity dashboard build --output-dir site
```

### `equity dashboard serve`

Serve the Streamlit dashboard locally.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--port`, `-p` | int | `8501` | Port |

```bash
uv run equity dashboard serve --port 8501
```

## Config & validation

### `equity config`

Inspect and validate the unified application settings.

| Command | Purpose |
|---|---|
| `config show` | Show full configuration (JSON) |
| `config get <path>` | Get a specific value by dotted path (e.g. `storage.data_dir`) |
| `config validate` | Validate YAML configuration files |
| `config export [file]` | Export configuration to a file (stdout if omitted) |

`config validate` flags:

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--tickers`, `-t` | str | `config/tickers.yaml` | Path to tickers.yaml |
| `--watchlist`, `-w` | str | `config/watchlist.yaml` | Path to watchlist.yaml |
| `--signals`, `-s` | str | `config/signals.yaml` | Path to signals.yaml |
| `--all` | flag | off | Validate all config files |

```bash
uv run equity config show
uv run equity config get schedule.cron
uv run equity config validate --all
```

### `equity validate`

Pointblank-backed data quality checks over Parquet files or directories.

| Command | Purpose |
|---|---|
| `validate check <path>` | Validate data against schema and produce quality metrics |
| `validate profile <path> --name <n>` | Profile a dataset and display quality metrics |
| `validate drift <current> <baseline>` | Compare two datasets for drift detection |

Flags: `validate check` takes `--type` (`price`, `macro`, or `news`; default
`price`) and `--strict` (fail on warnings); `validate drift` takes
`--threshold` (float, default `0.1`).

```bash
uv run equity validate check data/lake/01_bronze/market_data/us_equity --type price
uv run equity validate drift current.parquet baseline.parquet --threshold 0.1
```

## Utilities

### `equity bootstrap sample`

Generate mock Parquet data for testing.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--days`, `-d` | int | `30` | Number of trading days |
| `--tickers`, `-t` | str | default set | Comma-separated tickers |
| `--output-dir`, `-o` | str | default location | Output directory |
| `--seed`, `-s` | int | `42` | Random seed |

```bash
uv run equity bootstrap sample --days 30 --seed 42
```

### `equity demo seed`

Seed the lake with a demo US universe (synthetic by default, offline-safe) for
the Strategy Lab showcase.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--years` | float | `5.0` | Years of history to seed |
| `--tickers`, `-t` | str | demo group | Comma-separated tickers |
| `--real` | flag | off | Attempt live yfinance fetch (falls back to synthetic) |
| `--seed` | int | `42` | Synthetic RNG seed |

```bash
uv run equity demo seed
uv run equity demo seed --real
```

### `equity api serve`

Serve the read-only FastAPI API over the equity data lake. OpenAPI docs at
`/docs`, health at `/health`. See the [REST API guide](20260811-read-api.md).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--host` | str | `127.0.0.1` | Bind host |
| `--port`, `-p` | int | `8000` | Bind port |
| `--reload` | flag | off | Auto-reload on code changes (dev) |

```bash
uv run equity api serve --port 8000
```

## Related guides

- [Ingestion](ingestion.md)
- [Pipeline](pipeline.md)
- [Signals](signals.md)
- [Backtesting](backtesting.md)
- [ML Rigor](20260810-ml-rigor.md)
- [REST API](20260811-read-api.md)
- [API keys](../20260406-api-keys.md)
