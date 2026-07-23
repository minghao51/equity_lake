## 1. Workflow
- **Analyze First:** Read relevant files before proposing solutions. Never hallucinate.
- **Approve Changes:** Present a plan for approval before modifying code.
- **Minimal Scope:** Change as little code as possible. No new abstractions.

## 2. Output Style
- High-level summaries only.
- No speculation about code you haven't read.

## 3. Technical Stack
- **Python:**
  - Package manager: `uv`.
  - Execution: Always `uv run <command>`. Never `python`.
  - Sync: `uv sync`.
  - Version: 3.12+ (Docker uses `python:3.12-slim`).
- **Docs:** Update `ARCHITECTURE.md` if structure changes.
- **Files:** Markdown files must follow `YYYYMMDD-filename.md` format.
- **Secrets:** Managed via dotenvx. Run commands with `dotenvx run --`.
- **Docker:** Multi-stage build using `uv sync --frozen`. No requirements.txt files.

## 4. Project Layout

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
config/                   # YAML configs (tickers.yaml, settings.yaml, signals.yaml, watchlist.yaml)
data/lake/                # Partitioned Parquet storage (market/date= partitions)
notebooks/                # Interactive Jupyter notebooks (01–10, standalone runnable)
```

No `domain/` tree — top-level modules are canonical. Import boundary tests in `tests/unit/test_import_boundaries.py` enforce that `core/` does not depend on `cli/`, `dashboard/`, or `sources/`.

## 5. Key Patterns

- **CLI:** Unified `equity` command via Typer. All commands are native Typer — no legacy passthrough.
- **Config:** Single `Settings(BaseSettings)` class with `YamlConfigSettingsSource`. Env prefix `EQUITY_`, nested delimiter `__`. Priority: init > env vars > .env > YAML.
- **Storage:** Numbered medallion Delta tables with Parquet data files. DuckDB for analytical queries. S3 sync via Cloudflare R2.
- **Logging:** structlog with JSON output and correlation IDs. Use `structlog.get_logger()`. Call `setup_structured_logging()` in CLI entry points.
- **Markets:** us_equity, cn_ashare, hk_sg_equity, jpx_equity, krx_equity. Directory constants in `core/paths.py`, mapped via `MARKET_DIR_MAP` in `ingestion/types.py`.
- **Retry:** All source fetchers use `tenacity` for retry/backoff (exponential, max 3 attempts). Do not hand-roll retry loops.
- **Validation:** pointblank schemas enforced at ingestion write boundaries via `validation/pipeline.py`.
- **Backtesting:** `VectorBacktestEngine` (polars-backtest) is default. Requires `uv sync --extra backtesting`.

## Operational guardrails

The numbered medallion layout is canonical: `data/lake/01_bronze/`,
`02_silver/`, `03_gold/`, and `04_platinum/`. Runtime ingestion writes
date-partitioned Delta tables with Parquet data files. `data/catalog.jsonl` is a
generated artifact; edit catalog definitions and run `uv run equity
catalog-generate`, never edit the JSONL directly.

Dry-run means no persistence, backfill, LLM processing, feature output, or ML
inference. Network tests must be explicitly marked and are not part of the
default fast suite. Missing feature history requires
`--allow-history-backfill`, with scoped markets and tickers.

### Change matrix

| Change type | Required accompanying work |
|---|---|
| New source | Router, type/map, schema/validation, config, tests, source docs, catalog |
| Schema change | Schema constants, validators, catalog, reader compatibility, migration note |
| DAG feature change | Hamilton tags, catalog regeneration, feature tests |
| Storage change | Writer, reader, health checks, idempotency tests, architecture docs |
| CLI change | Help text, CLI test, user guide |
| Pipeline-stage change | Failure contract, orchestration test, data-flow update |

Canonical architecture pages and MkDocs navigation are intentional exceptions
to the date-prefixed Markdown rule. New plans, audits, and handoffs remain
`YYYYMMDD-*.md`.

## 6. Commands

```bash
dotenvx run -- uv run equity ingest      # Daily EOD ingestion
dotenvx run -- uv run equity pipeline    # Full pipeline (ingest -> features -> ML)
dotenvx run -- uv run equity sync        # S3 sync
dotenvx run -- uv run equity query       # DuckDB query examples
dotenvx run -- uv run equity news        # Fetch news with sentiment
dotenvx run -- uv run equity signal scan # Generate signals
uv run pytest                            # Tests (markers: slow, integration, unit)
uv run pytest -n auto                    # Parallel tests (except serial-marked)
uv run ruff check .                      # Lint
uv run ruff format .                     # Format
```

## 7. Tooling

- **Ruff:** line-length=150, target=py312, rules=[E,F,UP,B,SIM,I]
- **mypy:** strict mode, py312
- **pytest:** minversion=8, strict-markers, pythonpath=["src"], xdist available
- **pre-commit:** ruff + mypy + standard hooks (see `.pre-commit-config.yaml`)
- **MkDocs:** Material theme + mkdocstrings (see `mkdocs.yml`)
- **Docker:** `.dockerignore` present. Multi-stage build with `uv sync --frozen`.

## 8. Dependencies

New core dependencies (added for resilience/performance):
- `tenacity` — retry/backoff for all API fetchers
- `httpx` — async HTTP for concurrent API ingestion
- `polars` — primary dataframe engine across the ingestion, validation, feature, and ML pipeline. Pandas only at external-library boundaries (yfinance, akshare, efinance).
- `exchange-calendars` — trading-day calendar validation
- `pytest-xdist` — parallel test execution
- `pointblank` — Polars-native data validation and profiling (replaces whylogs)
