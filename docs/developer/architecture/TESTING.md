# Testing

**Last Updated**: 2026-08-29
**Project**: Equity EOD Data Pipeline

## Framework and Configuration

pytest (`>=8.0.0`) with pytest-mock and pytest-xdist from the `dev`
dependency group. There is no pytest-asyncio. The configuration in
`pyproject.toml` is the single source of truth:

```toml
[tool.pytest.ini_options]
minversion = "8.0"
addopts = ["--strict-markers", "--strict-config", "-ra", "-q", "--durations=10", "--import-mode=importlib"]
testpaths = ["tests"]
pythonpath = ["src", "."]
markers = [
    "slow: >1s tests",
    "unit: fast isolated tests",
    "integration: external services / cross-module",
    "e2e: full workflow",
    "serial: cannot run in parallel",
    "network: needs internet or API key",
    "smoke: critical path",
]
```

What the flags buy:

- `--strict-markers` — a typo'd `@pytest.mark.<name>` is a collection error,
  not a silent no-op. Only the seven markers above are registered.
- `--strict-config` — unknown INI keys error out.
- `-ra` / `-q` — quiet output with a failure summary.
- `--durations=10` — the ten slowest tests are listed every run.
- `--import-mode=importlib` — test modules do not need `__init__.py` files
  and can share basenames across directories.
- `pythonpath = ["src", "."]` — imports `equity_lake` straight from `src/`
  and picks up the root `tests/conftest.py`.

## Test Layout

Tests are organized by scope first; `tests/` holds only `conftest.py`, the
two scope directories, and a `README.md`:

| Location | Scope | Contents |
|---|---|---|
| `tests/unit/` | Fast, isolated suite | ~70 flat `test_*.py` modules (fetchers, router, writers, catalog, CLI, signals, ML, validation, …) plus `tests/unit/features/dag/` for the Hamilton feature DAG |
| `tests/integration/` | Cross-module workflows | 5 files: `test_duckdb_queries.py`, `test_news_ingestion.py`, `test_pipeline_orchestrator.py`, `test_dashboard_exporter.py`, `test_signal_integration.py` |

`tests/conftest.py::pytest_collection_modifyitems` auto-marks every test by
location: `unit/` → `@pytest.mark.unit`; `integration/` →
`@pytest.mark.integration` **and** `@pytest.mark.slow`. Manual markers are
only needed to add `network`/`e2e`/`smoke` or to override the defaults.

## Marker Policy

- `network` — the test needs internet or a real API key. It must be applied
  explicitly (conventionally stacked with `slow` + `integration`) and is
  excluded from the default fast suite via `-m "not network"`; CI's
  `-m "not integration"` run also deselects it. Examples:
  `tests/unit/test_ingestion_orchestrator.py`,
  `tests/unit/test_macro_sources.py`.
- `serial` — reserved for tests that cannot run under pytest-xdist workers;
  keep them out of `-n auto` runs.
- `slow` — anything over ~1 second, including everything under
  `integration/` via auto-marking.
- `unit` / `integration` / `e2e` / `smoke` — scope labels for selection;
  `e2e` and `smoke` are registered for future use.

Because `--strict-markers` is on, adding a new marker means registering it
in `pyproject.toml` first.

## Running Tests

```bash
uv run pytest                     # full local suite (quiet, durations)
uv run pytest -n auto             # parallel via pytest-xdist (serial-marked tests excluded)
uv run pytest -m "not network"    # fast suite without network tests
uv run pytest tests/unit -q       # just the fast unit suite
uv run pytest -m integration      # integration suite
uv run pytest -k "router"         # keyword selection
uv run pytest --lf                 # re-run failures
```

Makefile wrappers (see `Makefile`):

```bash
make test              # dotenvx run -- uv run pytest -v --cov=src/equity_lake --cov-report=html --cov-report=term
make test-unit         # -m "unit"
make test-integration  # -m "integration"
make test-slow         # -m "slow"
```

Coverage is measured with pytest-cov on demand (as in `make test`). The
`[tool.coverage.*]` sections in `pyproject.toml` set the source to
`src/equity_lake` and exclude repr/debug/main-guard lines — there is **no**
coverage threshold gate configured.

## Shared Fixtures (`tests/conftest.py`)

| Fixture | Provides |
|---|---|
| `sample_ohlcv_data` | 5-row OHLCV **polars** `pl.DataFrame` (explicit dtypes) for 2024-01-01 |
| `sample_multi_day_data` | 3 tickers × 5 days polars OHLCV |
| `sample_us_tickers` / `sample_cn_tickers` | Small ticker lists |
| `sample_large_ticker_list` | 1200 tickers to exercise batch chunking (batch size 500) |
| `temp_data_dir` | `tmp_path / "lake"` with market subdirectories |
| `temp_partitioned_parquet` | Hive-partitioned `date=YYYY-MM-DD/` Parquet tree from `sample_multi_day_data` |
| `mock_env_vars` | `monkeypatch`-set environment variables |
| `mock_yfinance_download` | Patches `yfinance.download` with a one-day OHLCV pandas frame |
| `mock_akshare_stock_zh_a_hist`, `mock_akshare_stock_info_a_code_name` | Chinese-column akshare frames |
| `mock_efinance_get_quote_history`, `mock_efinance_get_realtime_quotes`, `mock_efinance_module` | efinance seams (including a full module mock) |
| `mock_httpx_client` | `MagicMock` pre-wired as an `httpx.Client` context manager for `patch("<module>.httpx.Client", …)` |
| `capture_logs` | `caplog` set to DEBUG |
| `temp_duckdb_db` | Path string for a throwaway DuckDB database |

Helpers `create_test_parquet_file()` and `count_parquet_files()` live in the
same module. Add new shared fixtures there, not in per-file `conftest.py`
copies.

## Mocking Patterns

- Patch at the seam where the library is *used*, not where it is defined —
  e.g. `@patch("equity_lake.sources.base.yf.download")` as done in
  `tests/unit/test_fetchers.py`.
- External pandas frames from mocks flow through
  `standardize_columns()`/`ensure_polars()` exactly like production data;
  assertions are made on the resulting polars frame.
- HTTP clients are faked with the `mock_httpx_client` fixture; set
  `.get`/`.post` return values and patch the target module's
  `httpx.Client`.
- Anything needing real internet or API keys gets `@pytest.mark.network`
  (stacked with `slow` + `integration`) so the fast suite stays offline.

## Guardrail Tests

Two unit modules encode architecture contracts; keep them green when
touching structure:

- `tests/unit/test_import_boundaries.py` — two mechanisms: a runtime import
  check that every `equity_lake.core` module imports without
  `cli`/`dashboard`/`sources` present, and a static AST pass asserting each
  layer in `LAYER_BOUNDARIES` (`core`, `storage`, `ingestion`, `features`,
  `agent`, `api`) imports no forbidden top-level package. Also asserts no
  `domain/` tree and that legacy module shims stay absent. A new top-level
  package must extend `LAYER_BOUNDARIES` here.
- `tests/unit/test_cli_unified.py` — help-scan coverage for the unified CLI
  (ADR-0005): every command and sub-app is invoked with `--help` via
  Typer's `CliRunner` and must exit 0. Every new command needs an entry.

## Continuous Integration

`.github/workflows/` (there is no `test.yml`):

- **quality.yml** — four jobs on PR and pushes to `main`:
  - `lint-typecheck`: `uv run ruff check .` + `uv run mypy src`, matrixed
    over Python 3.12/3.13.
  - `test`: `uv run pytest -q -m "not integration"` on the same 3.12/3.13
    matrix, plus an advisory `pip-audit` (non-blocking).
  - `docs`: pymarkdown lint on selected pages + `mkdocs build` (twice, for
    internal link validation).
  - `cli-smoke`: verifies the `equity --help` command tree for key
    commands (`dashboard`, `monitor`, `pipeline`, `catalog-generate`).
- **data-validation.yml** — on changes to `config/tickers.yaml`,
  `config/watchlist.yaml`, or `config/signals.yaml`, runs
  `uv run equity config validate --all`.
- **catalog-check.yml / catalog-deploy.yml / pages.yml** — catalog and docs
  site publishing.

Local equivalents of the CI gate: `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy`, `uv run pytest`.

## Test Data

- **Synthetic generator**: `src/equity_lake/devtools/test_data.py`
  (`uv run python -m equity_lake.devtools.test_data`) writes realistic
  partitioned OHLCV Parquet across markets for manual experiments; see
  `--start-date`, `--days`, `--markets`, `--num-tickers`.
  (`make generate-test-data` runs the curated `equity bootstrap sample`
  instead.) See [Developer Tools](../../developer-guide/devtools.md).
- **Curated sample**: `uv run equity bootstrap sample` produces a small
  sample dataset (reusing existing lake data when available) for onboarding
  and dashboard checks; `make demo` seeds the full demo lake via
  `devtools/seed_demo.py`.
- Prefer the polars fixtures from `tests/conftest.py` for unit tests;
  generated data is for manual/visual use.
