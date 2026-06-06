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
- **Docs:** Update `ARCHITECTURE.md` if structure changes.
- **Files:** Markdown files must follow `YYYYMMDD-filename.md` format.
- **Secrets:** Managed via dotenvx. Run commands with `dotenvx run --`.

## 4. Project Layout

```
src/equity_lake/          # Source
├── backtesting/          # Backtesting framework (engine.py deprecated, use vector_engine.py)
├── cli/                  # Typer-based CLI (`equity` command, native Typer — no passthrough)
│   └── __main__.py       # Single file with all Typer commands
├── config/               # Pydantic Settings with YamlConfigSettingsSource
│   ├── settings.py       # Settings(BaseSettings) with EQUITY_ prefix, __ nested delimiter
│   ├── models.py         # Pydantic models for ticker config
│   ├── loader.py         # TickerConfig class (YAML loader)
│   └── selectors.py      # Query helpers for ticker config
├── core/                 # paths.py (dirs), logging.py (structlog), schemas.py (columns)
├── dashboard/            # Dashboard/export components
├── features/             # Feature engineering (Hamilton-based)
├── ingestion/            # Data ingestion pipeline (orchestrator, writers, sources)
├── ml/                   # ML inference
├── sentiment/            # Sentiment analysis
├── signals/              # Signal generators
├── storage/              # DuckDB (EquityDataDB), S3 sync
├── updates/              # Data update engine
└── validation/           # Pandera-based data validation
config/                   # YAML configs (tickers.yaml, settings.yaml, signals.yaml, watchlist.yaml)
data/lake/                # Partitioned Parquet storage (market/date= partitions)
```

## 5. Key Patterns

- **CLI:** Unified `equity` command via Typer. All commands are native Typer — no legacy passthrough.
- **Config:** Single `Settings(BaseSettings)` class with `YamlConfigSettingsSource`. Env prefix `EQUITY_`, nested delimiter `__`. Priority: init > env vars > .env > YAML.
- **Storage:** Hive-partitioned Parquet files. DuckDB for analytical queries. S3 sync via Cloudflare R2.
- **Logging:** structlog with JSON output and correlation IDs. Use `structlog.get_logger()`. Call `setup_structured_logging()` in CLI entry points.
- **Markets:** us_equity, cn_ashare, hk_sg_equity, jpx_equity, krx_equity. Directory constants in `core/paths.py`, mapped via `MARKET_DIR_MAP` in `ingestion/types.py`.

## 6. Commands

```bash
dotenvx run -- uv run equity ingest      # Daily EOD ingestion
dotenvx run -- uv run equity pipeline    # Full pipeline (ingest → features → ML)
dotenvx run -- uv run equity sync        # S3 sync
dotenvx run -- uv run equity query       # DuckDB query examples
dotenvx run -- uv run equity news        # Fetch news with sentiment
dotenvx run -- uv run equity signal scan # Generate signals
uv run pytest                            # Tests (markers: slow, integration, unit)
uv run ruff check .                      # Lint
uv run ruff format .                     # Format
```

## 7. Tooling

- **Ruff:** line-length=150, target=py312, rules=[E,F,UP,B,SIM,I]
- **mypy:** strict mode, py312
- **pytest:** minversion=8, strict-markers, pythonpath=["src"]
- **pre-commit:** ruff + mypy + standard hooks (see `.pre-commit-config.yaml`)
- **MkDocs:** Material theme + mkdocstrings (see `mkdocs.yml`)
