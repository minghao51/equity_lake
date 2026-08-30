# Project guidance

## Purpose and decision order

Build a local-first, multi-market equity EOD data lake — ingestion, medallion
storage, feature engineering, ML inference, and signals — operated through the
unified `equity` CLI. When guidance conflicts, follow this order:

1. Enforced storage and pipeline contracts: schemas, the numbered medallion
   layout, validation boundaries, and import boundary tests.
2. Accepted decision records in `docs/decisions/`.
3. The canonical architecture pages in `docs/developer/architecture/` and the
   documentation map in `docs/README.md`.

## Architectural boundaries

- Top-level modules under `src/equity_lake/` are canonical — no `domain/` tree.
  Import boundary tests (`tests/unit/test_import_boundaries.py`) keep `core/`
  independent of `cli/`, `dashboard/`, and `sources/`.
- The numbered medallion layout is canonical: `data/lake/01_bronze/` …
  `04_platinum/`, date-partitioned Delta tables with Parquet data files.
- `data/catalog.jsonl` is generated from `catalog/datasets.py` via
  `uv run equity catalog-generate`; never edit the JSONL directly.
- Cataloged tables live under `data/lake/` only. Auxiliary artifacts — signals,
  update history, model outputs, findings, backtest/risk reports — live under
  `data/<name>/` (`DATA_DIR / "<name>"` in `core/paths.py`); they are not
  cataloged or pointblank-validated. Define a Pydantic model at their write
  boundary instead.
- Dry-run means no persistence, backfill, LLM processing, feature output, or ML
  inference. Network tests are explicitly marked and excluded from the default
  fast suite. Missing feature history requires `--allow-history-backfill`,
  with scoped markets and tickers.
- Markets are fixed: us_equity, cn_ashare, hk_sg_equity, jpx_equity, krx_equity.
  Directory constants in `core/paths.py`, mapped via `MARKET_DIR_MAP` in
  `ingestion/types.py`.
- Prefer the smallest architecture that supports the measured need. Record any
  change to these boundaries as an ADR in `docs/decisions/` before
  implementing it.

## Engineering conventions

- Start from the documentation map (`docs/README.md`); superseded and
  deprecated material lives only in `docs/archive/`.
- Read relevant files before proposing solutions; never speculate about code
  you haven't read. Present a plan for approval before modifying code. Change
  as little as possible; no new abstractions; high-level summaries only.
- Python 3.12+ with `uv`: always `uv run <command>`, never bare `python`.
  Secrets via dotenvx (`dotenvx run -- …`), never committed. Docker is
  multi-stage with `uv sync --frozen`; no requirements.txt files.
- Polars is the primary dataframe engine; pandas only at external-library
  boundaries (yfinance, akshare, efinance).
- Config: single `Settings(BaseSettings)` with `YamlConfigSettingsSource`,
  `env_prefix="EQUITY_"`, `env_nested_delimiter="__"`, `extra="forbid"`.
  Any `EQUITY_<GROUP>__*` env var requires a matching nested `BaseModel` field
  or Settings raises at load — add the model + `.env.example` entry in the same
  change. SDK/API keys stay raw/unprefixed (`FRED_API_KEY`, `FINNHUB_API_KEY`,
  `DEEPSEEK_API_KEY`, `WANDB_API_KEY`, …), read via `os.getenv` at the client
  seam, never declared in `Settings`.
- CLI: unified `equity` command via native Typer (no passthrough). Sub-apps are
  declared in `cli/_app.py` and wired with `app.add_typer(<x>_app, name="…")`
  in `cli/__main__.py` **before** importing the command module that decorates
  commands onto them. Every command: docstring help text,
  `Annotated[..., typer.Option("--flag", help="…")]`,
  `raise typer.Exit(1)` on required failure, and a help-scan test in
  `tests/unit/test_cli_unified.py`. `backtest` is a flat top-level command —
  never add `backtest <sub>`; use a dedicated sub-app (e.g. `report`).
- Retry: `tenacity` (exponential backoff, max 3 attempts) for all source
  fetchers; never hand-roll retry loops.
- Logging: structlog with JSON output and correlation IDs
  (`structlog.get_logger()`; `setup_structured_logging()` in CLI entry points).
- Validation: pointblank schemas enforced at ingestion write boundaries via
  `validation/pipeline.py`.
- Backtesting: `VectorBacktestEngine` (polars-backtest) is default; requires
  `uv sync --group backtesting` (a `[dependency-groups]` entry — `--group`,
  not `--extra`).
- Markdown files follow `YYYYMMDD-filename.md`. Canonical architecture pages,
  MkDocs navigation, and ADRs under `docs/decisions/` are intentional
  exceptions. Update `ARCHITECTURE.md` when structure changes (canonical copy:
  `docs/developer/architecture/ARCHITECTURE.md`; the root file is a pointer).
- Tooling: ruff (line-length=150, py312, rules E,F,UP,B,SIM,I), mypy strict,
  pytest (minversion=8, strict-markers; markers slow/integration/unit; xdist),
  pre-commit, MkDocs Material. Layout reference:
  `docs/developer-guide/project-structure.md`.

### Change matrix

| Change type | Required accompanying work |
|---|---|
| New source | Router, type/map, schema/validation, config, tests, source docs, catalog |
| Schema change | Schema constants, validators, catalog, reader compatibility, migration note |
| DAG feature change | Hamilton tags, catalog regeneration, feature tests |
| Storage change | Writer, reader, health checks, idempotency tests, architecture docs |
| CLI change | Help text, CLI test, user guide |
| Pipeline-stage change | Failure contract, orchestration test, data-flow update |
| New top-level package | Extend `LAYER_BOUNDARIES` in `tests/unit/test_import_boundaries.py`; lazy-import heavy deps |
| New optional dependency | Add a `[dependency-groups]` extra; import lazily (`try/except ImportError`); mypy `ignore_missing_imports` override |
| Boundary change | ADR in `docs/decisions/` before implementation |

## Validation

Run before handing off a change:

```bash
uv sync
uv run pytest
uv run pytest -n auto            # parallel; serial-marked tests excluded
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

A change is complete when relevant tests pass, the change matrix is satisfied,
public behavior is documented (user guide for CLI changes, `ARCHITECTURE.md`
for structure), the catalog is regenerated for pipeline or feature changes,
and any boundary change has an accepted ADR.
