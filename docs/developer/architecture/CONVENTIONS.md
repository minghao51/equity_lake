# Conventions

**Last Updated**: 2026-08-29
**Project**: Equity EOD Data Pipeline

This page records the conventions the code actually follows. When guidance
conflicts, the order in `AGENTS.md` applies: enforced contracts first, then
accepted ADRs in `docs/decisions/`, then these architecture pages.

## Toolchain

- **uv** is the only runner: always `uv run <command>`, never bare `python`.
  Python is `>=3.12,<3.14` (CI tests 3.12 and 3.13).
- **Secrets** go through dotenvx (`dotenvx run -- …`), never committed.
  `.env.example` is the human contract for what exists.
- **Docker** is multi-stage with `uv sync --frozen`; there are no
  `requirements.txt` files. Optional dependencies are `[dependency-groups]`
  entries installed with `uv sync --group <name>`.

### Ruff

Configured in `pyproject.toml`: `line-length = 150`, `target-version =
"py312"`, rule selection `E, F, UP, B, SIM, I`, with only `B026` ignored.
`ruff format` is the formatter (double quotes, 4-space indent, magic trailing
commas). isort settings declare `equity_lake`, `tests`, and `data` as
first-party.

```bash
uv run ruff check .
uv run ruff check --fix .
uv run ruff format --check .   # or: uv run ruff format .
```

### Mypy

`python_version = "3.12"` with strictness expressed as individual flags
(`disallow_untyped_defs`, `disallow_incomplete_defs`, `check_untyped_defs`,
`disallow_untyped_decorators`, `no_implicit_optional`, `warn_return_any`,
`warn_unused_configs`, `warn_redundant_casts`, `warn_unused_ignores`,
`warn_no_return`, `warn_unreachable`, `strict_equality`). `tests/` is
excluded. Third-party modules without stubs get `ignore_missing_imports`
overrides in `pyproject.toml`.

```bash
uv run mypy
```

### Pre-commit

`.pre-commit-config.yaml` runs: `uv-lock`, `ruff` (with `--fix`),
`ruff-format`, a local `mypy src` hook, a local hook that regenerates
`data/catalog.jsonl` when `features/dag` or `catalog/` change, `actionlint`,
and standard hygiene hooks (trailing whitespace, EOF, YAML/JSON/TOML checks,
large-file and merge-conflict checks, private-key detection).

## Typing and Schemas

- **Modern syntax** (enforced by the `UP` rules): `str | None`, `list[str]`,
  `dict[str, Any]` — never `Optional[...]`, `List[...]`, or `Dict[...]`.
- All functions carry type hints; mypy flags above make gaps errors.
- **Column contracts** live in `src/equity_lake/core/schemas.py` as constant
  lists (`STANDARD_COLUMNS`, `NEWS_COLUMNS`, `MACRO_COLUMNS`, …). Import
  these; do not re-declare column lists at call sites.
- **Pydantic models at write boundaries**: auxiliary, non-lake artifacts
  (signals, findings, model outputs) are typed with a Pydantic model where
  they are written instead of catalog/pointblank machinery (ADR-0006).

## Configuration

One `Settings(BaseSettings)` in `core/settings.py` with
`YamlConfigSettingsSource`, `env_prefix="EQUITY_"`,
`env_nested_delimiter="__"`, and `extra="forbid"` (ADR-0004). Priority:
init > env > `.env` > YAML. Because of `extra="forbid"`, any new
`EQUITY_<GROUP>__*` env var needs a matching nested `BaseModel` field and a
`.env.example` entry **in the same change**. SDK/API keys
(`FRED_API_KEY`, `FINNHUB_API_KEY`, `DEEPSEEK_API_KEY`, `WANDB_API_KEY`, …)
stay raw/unprefixed and are read with `os.getenv` at the client seam — never
declared in `Settings`.

## Logging

structlog everywhere, configured once per process:

- Modules call `structlog.get_logger()` (optionally `get_logger(__name__)`)
  at module level — no per-call setup.
- `setup_structured_logging()` from `core/logging.py` runs in CLI entry
  points (`cli/_app.py::_init_logging`, plus commands that need a custom
  level). JSON rendering is the default; `json_output=False` switches to a
  colored console renderer.
- Every log entry gets a `correlation_id` via the `add_correlation_id`
  processor — all output, not just parallel ingestion. Use
  `correlation_context("id")` to scope one explicitly.
- Use the `timer("operation_name", **context)` context manager for duration
  logging instead of hand-rolled timing.
- Event names are snake_case strings; context is passed as keyword
  arguments (`logger.info("fetch_started", market="us", ticker_count=500)`).

## Retries

All source fetch retries use **tenacity** through the single factory in
`core/retry.py`:

```python
from equity_lake.core.retry import build_retry_decorator

retry = build_retry_decorator(
    attempts=3, wait_multiplier=1.0, wait_min=1.0,
    retry_on=TransientError, log=logger,
)
```

The factory standardizes the project shape: exponential backoff capped at
`wait_max=30.0`, a WARNING-level `before_sleep_log`, and `reraise=True` so
the final attempt's exception propagates unchanged. `MarketDataFetcher`
constructs its decorator from `Settings.ingestion` (`retry_attempts=3`,
`retry_delay=1.0`) and wraps calls via `_retry_on_failure`, translating
network/timeout/5xx/408/429 failures into `TransientError` so permanent 4xx
errors are not retried. **Never hand-roll retry loops with `time.sleep`.**

## Data Access

- **Polars-first** (ADR-0003): polars is the primary dataframe engine across
  ingestion, validation, features, and ML. Pandas appears only at
  external-library boundaries (yfinance, akshare, efinance) and is converted
  at the seam via `core/polars_utils.ensure_polars()`.
- **Canonical writer**: `ingestion/writers.upsert_dataset(df, market,
  trading_date, dry_run=…, validate_quality=…)`. It converts to polars,
  checks required columns, optionally runs pointblank quality validation,
  then merges into the date-partitioned Delta table with per-dataset dedupe
  keys (`storage/delta.merge_delta`). Do not write Parquet/Delta directly
  from feature or CLI code.
- **Canonical reader**: `storage/lake_reader.duckdb_scan_for(market_path)`
  returns a DuckDB table expression using `delta_scan(...)` when the path is
  a Delta table, falling back to `read_parquet('.../**/*.parquet',
  hive_partitioning=1)`.

## Paths and Storage Layout

`core/paths.py` is constants-only — there are no `get_*()` helper functions.
Import constants; never build lake paths from strings:

- `PROJECT_ROOT`, `CONFIG_DIR`, `DATA_DIR`, `LAKE_DIR`, `LOGS_DIR`,
  `MODELS_DIR`.
- Medallion roots: `BRONZE_DIR` … `PLATINUM_DIR`
  (`data/lake/01_bronze/` … `04_platinum/`), with per-dataset constants such
  as `US_EQUITY_DIR` (`01_bronze/market_data/us_equity`).
- Market directories are fixed: `us_equity`, `cn_ashare`, `hk_sg_equity`,
  `jpx_equity`, `krx_equity`; source identifiers map to destinations via
  `MARKET_DIR_MAP` in `ingestion/types.py`.
- Auxiliary, non-cataloged artifacts live under `DATA_DIR / "<name>/"`
  (e.g. `SIGNALS_DIR`, `FINDINGS_DIR`) per ADR-0006.
- `ensure_dirs()` creates runtime directories; the CLI app callback calls it
  before any command runs. No filesystem I/O happens at import time.

## Source Fetchers

- Fetchers subclass `MarketDataFetcher` (`sources/base.py`), implementing
  `fetch(trading_date)` and (when the API allows a range request)
  `fetch_range(start, end)`. yfinance-backed fetchers extend
  `YFinanceBaseFetcher`, which provides batching, MultiIndex handling, and
  `standardize_columns(...)` onto `STANDARD_COLUMNS`.
- Routing is declarative: `ingestion/router.MARKET_REGISTRY` maps market
  identifiers to fetcher factories; entry points are
  `fetch_market_data(...)` / `fetch_market_data_with_config(...)`, which
  resolve retry defaults from `Settings.ingestion` and validate the returned
  frame before handing it on.
- New sources require the change-matrix row: router entry, type/map, schema,
  validation, config, tests, source docs, catalog.

## Validation

- pointblank schemas live in `validation/schemas.py` as
  `SCHEMA_REGISTRY` (`price`, `macro`, `news`) and are enforced at ingestion
  write boundaries through `validation/pipeline.ValidationPipeline` when
  `upsert_dataset(..., validate_quality=True)` (ADR-0007). A write that
  fails its contract does not land.
- The `validation/` package also provides ad-hoc quality tooling —
  profiling and drift detection — exposed as `equity validate check |
  profile | drift`.

## CLI

Per ADR-0005:

- Native Typer, no passthrough. Sub-apps are declared in `cli/_app.py` and
  wired with `app.add_typer(<x>_app, name="…")` in `cli/__main__.py`
  **before** importing the command module that decorates commands onto
  them — the import order is load-bearing.
- Every command: docstring help text,
  `Annotated[..., typer.Option("--flag", help="…")]`, and
  `raise typer.Exit(1)` on required failure.
- Every command gets a help-scan test in `tests/unit/test_cli_unified.py`.
- `backtest` is a flat top-level command — never `backtest <sub>`; use a
  dedicated sub-app (e.g. `report`).

## Testing

See [TESTING.md](TESTING.md). In brief: pytest with `--strict-markers` and
seven registered markers (`slow`, `unit`, `integration`, `e2e`, `serial`,
`network`, `smoke`); `tests/conftest.py` auto-marks by directory; run the
fast suite with `uv run pytest`, parallel with `uv run pytest -n auto`;
network tests are explicitly marked and excluded from the fast suite.

## Documentation

- Dated pages (plans, audits, guides) use `YYYYMMDD-filename.md`.
- Intentional exceptions: canonical architecture pages (this directory),
  MkDocs navigation, and ADRs named `NNNN-slug.md` in `docs/decisions/`.
- Superseded and deprecated material lives only in `docs/archive/`
  (ADR-0009); never delete it silently into the active tree.
- Update `ARCHITECTURE.md` when structure changes; regenerate
  `data/catalog.jsonl` via `uv run equity catalog-generate` for pipeline or
  feature changes (never edit the JSONL directly).
