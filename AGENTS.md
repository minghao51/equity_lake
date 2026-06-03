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
- **Frontend:**
  - Verify: Run `npm run check` and `npm test` after changes.
- **Docs:** Update `ARCHITECTURE.md` if structure changes.
- **Files:** Markdown files must follow `YYYYMMDD-filename.md` format.

## 4. Project Layout

```
src/equity_lake/          # Source
├── backtesting/          # Backtesting framework (engine.py deprecated, use vector_engine.py)
├── cli/                  # Typer-based CLI (`equity` command)
├── config/               # Pydantic settings, YAML loader
├── core/                 # runtime.py (dirs, constants), logging.py (structlog)
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

- **CLI:** Unified `equity` command via Typer. Legacy entrypoints (`equity-daily` etc.) deprecated but functional.
- **Config:** Pydantic Settings with env overrides. Ticker config in `config/tickers.yaml` per-market (us, cn, hk_sg, jpx, krx).
- **Storage:** Hive-partitioned Parquet files. DuckDB for analytical queries. S3 sync via Cloudflare R2.
- **Logging:** structlog with JSON output and correlation IDs. Use `structlog.get_logger()`.
- **Markets:** us_equity, cn_ashare, hk_sg_equity, jpx_equity, krx_equity. Directory constants in `core/runtime.py`, mapped via `MARKET_DIR_MAP` in `ingestion/types.py`.

## 6. Commands

```bash
uv run equity ingest --daily    # Daily EOD ingestion
uv run equity pipeline          # Full pipeline (ingest → features → ML)
uv run equity sync              # S3 sync
uv run equity query             # DuckDB query examples
uv run pytest                   # Tests (markers: slow, integration, unit)
uv run ruff check .             # Lint
uv run ruff format .            # Format
```

## 7. Tooling

- **Ruff:** line-length=150, target=py312, rules=[E,F,UP,B,SIM,I]
- **mypy:** strict mode, py312
- **pytest:** minversion=8, strict-markers, pythonpath=["src"]
