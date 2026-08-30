# Tech Stack

**Last Updated**: 2026-08-29
**Project**: Equity EOD Data Pipeline

This page describes the stack as declared in `pyproject.toml`: 31 production
dependencies plus 11 optional `[dependency-groups]`. Optional groups are
installed with `uv sync --group <name>` and imported lazily (see
[Optional vs core](#optional-vs-core-dependencies)).

## Runtime & Language

- **Python**: `>=3.12,<3.14` (`requires-python`); local pin `.python-version` = 3.12; classifiers cover 3.12 and 3.13.
- **Package manager**: uv. Always `uv run <command>`, never bare `python`. Lock file is `uv.lock` (not `.uv.lock`); Docker builds use `uv sync --frozen`.
- **Secrets**: dotenvx (`dotenvx run -- …`). Keys are never committed; `.env.example` is the template.
- **Build backend**: hatchling; wheel packages `src/equity_lake` (src layout).
- **CLI entry point**: `equity = "equity_lake.cli.__main__:app"` — a native Typer app (no passthrough), with sub-apps wired in `cli/__main__.py`.

## Data Engine

- **polars** (>=1.0.0): primary dataframe engine across ingestion, validation, features, and ML prep — ADR-0003 (`docs/decisions/0003-polars-first-dataframe-engine.md`).
- **pandas** (>=2.2.0) + **numpy**: only at external-library boundaries that require them (yfinance, akshare, efinance); converted in and out at the client seam.
- **DuckDB** (>=1.0.0): embedded SQL engine for analytical queries over the lake (`storage/duckdb.py`, `storage/lake_reader.py`, monitoring health checks).
- **deltalake** (>=0.25.0) + **pyarrow** (>=18.0.0): all cataloged lake tables are date-partitioned Delta tables whose data files are Parquet (`storage/delta.py`).
- **exchange-calendars** (>=4.6): trading-calendar awareness for scheduling and freshness checks (`core/calendar.py`).

## Pipeline & ML

- **sf-hamilton** (>=1.69.0): the feature-engineering DAG (`features/`), tagged for catalog generation (`catalog/datasets.py` → `data/catalog.jsonl`).
- **xgboost** (>=3.1.3) + **scikit-learn** (>=1.8.0): core model stack (`ml/backends.py`, training/inference in `ml/`).
- **lightgbm**: alternative backend behind the same `ml/backends.py` seam; imported lazily and requires `uv sync --group ml`.
- **joblib** (>=1.5.3): model (de)serialization and parallel utilities.

## API & Serving

These ship today — they are not future work.

- **fastapi** (>=0.115.0): REST API in `src/equity_lake/api/` (`main.py`, `routers/`, `deps.py`).
- **uvicorn** (>=0.30.0): ASGI server behind `equity api serve` (`cli/commands/api.py`).
- **Docker**: the `Dockerfile` has a dedicated `api` image stage running `equity api serve --host 0.0.0.0 --port 8000`.
- **streamlit**: local dashboard UI, behind the optional `dashboard` group (`uv sync --group dashboard`).

## Integrations (External Data & Content)

| Package | Use |
|---|---|
| yfinance (>=0.2.50) | US, HK/SG, JPX EOD prices; some macro indicators |
| akshare (>=1.15.0) | China A-share EOD prices (primary CN source) |
| efinance (>=0.5.5.2,<0.5.6) | CN fallback source in the hybrid fetcher |
| finance-datareader (>=0.9.96,<1.0) | KRX (South Korea) EOD prices |
| fredapi (>=0.5.2) | FRED macro indicators (`FRED_API_KEY`) |
| feedparser (>=6.0.11) | RSS/Atom financial news feeds |
| openai (>=1.50.0) | OpenAI-compatible client used for DeepSeek LLM enrichment and OpenRouter embeddings (`ingestion/llm_base.py`; raw `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`) |
| edgartools (>=5.36.0) | SEC XBRL structured financials |
| readability-lxml (>=0.8.1) | Clean-text extraction from SEC filings and articles |

See [INTEGRATIONS.md](INTEGRATIONS.md) for the full adapter map.

## Ops & Quality

- **structlog** (>=24.1.0): structured JSON logging with correlation IDs (`setup_structured_logging()` at CLI entry points).
- **tenacity** (>=9.0.0): retry with exponential backoff (max 3 attempts) for all source fetchers, via the shared factory `core/retry.py::build_retry_decorator`. Never hand-roll retry loops.
- **httpx** (>=0.28.0): HTTP client for REST APIs (Finnhub, Reddit JSON, SEC EDGAR, StockTwits).
- **pydantic** (>=2.5.0) / **pydantic-settings** (>=2.6.1): schemas and the single `Settings(BaseSettings)` with `YamlConfigSettingsSource`, `env_prefix="EQUITY_"`, `env_nested_delimiter="__"`, `extra="forbid"` (ADR-0004).
- **pointblank**: validation schemas at ingestion write boundaries (`validation/pipeline.py`) — behind the optional `validation` group.
- **typer** (>=0.12.0) + **rich** (>=13.7.0): unified `equity` CLI and terminal output.
- **pyyaml** (>=6.0.2): config files (`config/settings.yaml`, RSS/social source lists). **tqdm**: progress bars.
- **Dev tooling** (in the `dev` group): pytest (>=8), pytest-cov, pytest-mock, pytest-xdist, ruff (>=0.8, line-length 150, rules E,F,UP,B,SIM,I), mypy (>=1.11, strict), pre-commit, pymarkdownlnt, pip-audit, jupyter/ipykernel, vaderSentiment.

## Storage Layout Summary

Numbered medallion layout under `data/lake/` (canonical — see `core/paths.py`):

```
data/lake/
├── 01_bronze/            # market_data/{us_equity, cn_ashare, hk_sg_equity, jpx_equity, krx_equity},
│                         # raw_articles/, macro/
├── 02_silver/            # news_sentiment/, social_sentiment/, processed_articles/,
│                         # sec_extractions/, analyst_ratings/, sec_financials/
├── 03_gold/              # features/
└── 04_platinum/          # predictions/
```

All tables are date-partitioned Delta tables with Parquet data files. The five
price markets are fixed: us_equity, cn_ashare, hk_sg_equity, jpx_equity,
krx_equity. Auxiliary artifacts (signals, findings, models, backtest/risk
reports) live under `data/<name>/` and are not cataloged.

## Dependency Groups

Every optional capability is a `[dependency-groups]` entry (installed with
`uv sync --group <name>`, not `--extra`):

| Group | Purpose | Install |
|---|---|---|
| `dev` | pytest suite, ruff, mypy, pre-commit, audit, notebook tooling | `uv sync --group dev` |
| `ml` | Extended ML: lightgbm, shap, wandb, statsmodels, networkx, seaborn | `uv sync --group ml` |
| `viz` | Plotting: matplotlib, seaborn, plotly | `uv sync --group viz` |
| `backtesting` | `VectorBacktestEngine` (polars-backtest) + jinja2 reports — required for `equity backtest` | `uv sync --group backtesting` |
| `agent` | sqlite-vec vector store for RAG | `uv sync --group agent` |
| `dashboard` | streamlit dashboard UI | `uv sync --group dashboard` |
| `validation` | pointblank data validation | `uv sync --group validation` |
| `sentiment` | vaderSentiment + praw | `uv sync --group sentiment` |
| `s3` | boto3 + s5cmd for S3 bootstrap/sync | `uv sync --group s3` |
| `schedule` | croniter schedule parsing | `uv sync --group schedule` |
| `docs` | MkDocs Material, mkdocstrings-python, mike | `uv sync --group docs` |

Note: `boto3` and `s5cmd` are **not** core dependencies — they belong to the
optional `s3` group. Likewise `pointblank` (validation), `streamlit`
(dashboard), and `lightgbm` (ml) are optional.

## Optional vs Core Dependencies

Per `AGENTS.md`, adding an optional dependency requires:

- A `[dependency-groups]` entry (never `[project.optional-dependencies]`).
- Lazy imports at the usage seam (`try/except ImportError`), e.g.
  `ml/backends.py` for lightgbm, `cn_hybrid.py`/`cn_efinance.py` for efinance.
- A mypy `ignore_missing_imports` override for the module in `pyproject.toml`
  (the override list already covers lightgbm, wandb, shap, praw, streamlit,
  pointblank, polars-backtest, sqlite_vec, croniter, edgar, and others).

API/SDK keys (`FRED_API_KEY`, `FINNHUB_API_KEY`, `DEEPSEEK_API_KEY`,
`WANDB_API_KEY`, …) stay raw/unprefixed and are read via `os.getenv` at the
client seam — never declared in `Settings`.

## Development Workflow

```bash
uv sync                     # core + dev
uv run pytest               # fast suite
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

Docker: multi-stage builds install with `uv sync --frozen` (`--no-dev` for the
production image, `--all-groups` for the dev image); the `api` stage serves
FastAPI via uvicorn.
