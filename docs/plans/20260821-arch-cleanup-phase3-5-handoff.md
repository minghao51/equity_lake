# 20260821-arch-cleanup-phase3-5-handoff.md

**Title:** Architecture Cleanup — Phases 3–5 (structural refactoring, convention alignment, dependency hygiene)
**Status:** Planned. Phases 1–2 committed on `main` (`b0fe5a5`, `203e9e4`).
**Author:** Derived from full-repo audit via `explore` subagents.
**Depends on:** Phases 1–2 (dead code removed, shared utilities extracted, dashboard consolidated).

---

## 1. Orientation

Phases 1–2 removed ~430 lines of dead code, fixed anti-patterns (`extra="ignore"`, `__import__` hack, eager heavy imports), extracted shared utilities (`ensure_columns`, `resolve_ticker`, `log_to_wandb`), and consolidated dashboard duplication. The codebase is cleaner but still has three structural debt clusters:

1. **God-modules** — `core/config.py` (593 lines), `cli/commands/ingest.py` (287 lines, 6 duplicated commands), `pipeline.py` (255-line function).
2. **Convention drift** — f-string logging instead of structured logging, missing docstrings/annotations, inconsistent `__future__` imports.
3. **Dependency bloat** — 8+ packages in core `dependencies` that are only used by single optional modules.

Phases 3–5 address these. Each phase is independently shippable.

---

## 2. Entry state (post-Phase 2)

**Commits on `main`:** `b0fe5a5` (Phase 1) → `203e9e4` (Phase 2). **Working tree clean** (except uncommitted Phase 1–2 changes in working copy).

**Key verified file sizes (post-Phase 1–2):**

| File | Lines | Role |
|---|---|---|
| `core/config.py` | 593 | Ticker config models + app settings + loaders |
| `cli/commands/ingest.py` | 287 | 6 near-identical Finnhub CLI commands |
| `pipeline.py` | 255 | `execute_eod_pipeline()` = 195-line function |
| `monitoring/health.py` | 502 | Health checks with f-string logging |
| `features/dag/enrichments_04.py` | 677 | Hamilton DAG enrichments |

**Frozen contracts (do not change):**

| Contract | Source | Notes |
|---|---|---|
| `TickerConfigRoot` / `TickerConfig` | `core/config.py` | 16 callers across codebase use `TickerConfig()`. Any split must preserve this import path. |
| `Settings(BaseSettings)` | `core/config.py:469` | `extra="forbid"`, `env_prefix="EQUITY_"`. Already fixed in Phase 1. |
| `execute_eod_pipeline()` | `pipeline.py:61` | Called from CLI `pipeline` command. Signature must stay stable. |
| `upsert_dataset()` | `ingestion/writers.py` | Canonical write path. Phase 1 removed `write_to_partitioned_parquet` alias. |

---

## 3. Phase 3 — Structural Refactoring (god-module splits + CLI dedup)

### 3A. Split `core/config.py` (593 lines → 3 files)

**Rationale:** This file contains 5 ticker-config Pydantic models, 5 app-settings models, `Settings(BaseSettings)`, a backwards-compat `TickerConfig` subclass, and 5 loader functions. Single-responsibility violation.

**Plan:**

| New file | Contents (line refs from current `core/config.py`) |
|---|---|
| `core/config_models.py` | `TickerMetadata` (L25–59), `MarketConfig` (L62–134), `GroupConfig` (L137–140), `ValidationConfig` (L143–148), `TickerConfigRoot` (L151–427) |
| `core/settings.py` | `ProjectSettings` (L430–433), `IngestionSettings` (L436–441), `ScheduleSettings` (L443–453), `DashboardSettings` (L456–462), `MonitoringSettings` (L464–466), `Settings` (L469–498), `get_settings()` (L504–506), `load_settings()` (L509–537) |
| `core/config.py` (slimmed) | `DEFAULT_TICKERS_PATH`, `TickerConfig` (L554–576), `clear_settings_cache()` (L540–542), `get_ticker_config()` (L545–548), `logger`, `__all__`. Re-exports from `config_models` and `settings` for backwards compatibility. |

**Critical:** `core/config.py` must re-export all public names so existing `from equity_lake.core.config import TickerConfig, Settings, get_settings` still works. Use `from equity_lake.core.config_models import *` and `from equity_lake.core.settings import *` at the bottom of the slimmed `config.py`.

**Also:** Remove `PROJECT_ROOT` / `CONFIG_DIR` duplication — `core/config.py:20-22` defines them independently from `core/paths.py:25-26`. Import from `core/paths.py` instead. The third copy in `ml/forecasting.py:33` should also import from `core/paths.py`.

**Effort:** M (1–2 hours). **Impact:** High (navigability, reduced merge conflicts).

### 3B. Refactor `cli/commands/ingest.py` (287 lines, 6 duplicated commands)

**Rationale:** The `news`, `sentiment`, `sec`, `transcripts`, `ratings`, and `financials` commands all follow the same skeleton: `_init_logging(verbose)` → API key check → `resolve_trading_date()` → `_parse_comma_list()` → create fetcher → fetch → validate → write. The API key check pattern is copy-pasted 4 times.

**Plan:**

Extract a generic helper:

```python
def _finnhub_ingest_command(
    name: str,
    date_str: str | None,
    tickers: str | None,
    api_key: str | None,
    dry_run: bool,
    verbose: bool,
    fetcher_factory: Callable[..., MarketDataFetcher],
    dataset_path: Path,
    schema_market: str,
    fetch_kwargs: dict[str, Any] | None = None,
) -> None:
    """Shared ingestion command body for all Finnhub-backed sources."""
    _init_logging(verbose)
    _require_finnhub_api_key(api_key)
    trading_date = resolve_trading_date(date_str)
    ticker_list = _parse_comma_list(tickers)
    dataset_path.mkdir(parents=True, exist_ok=True)
    fetcher = fetcher_factory(api_key=api_key, tickers=ticker_list, **(fetch_kwargs or {}))
    df = fetcher.fetch(trading_date)
    if df.is_empty():
        typer.secho(f"No {name} data for {trading_date}", fg=typer.colors.YELLOW)
        raise typer.Exit(0)
    validate_schema(df, schema_market)
    upsert_dataset(df, schema_market, trading_date, dry_run=dry_run)
```

Each command becomes 10–15 lines calling the helper. Also extract `_require_finnhub_api_key()` to eliminate the 4x duplication of the API key check pattern.

**Effort:** M (1–2 hours). **Impact:** High (eliminates ~150 lines of duplication).

### 3C. Refactor `pipeline.py` `execute_eod_pipeline()` (195-line function)

**Rationale:** The function has 3 levels of nested try/except, inline backfill logic, and complex stage-result composition. The feature stage alone (L162–232) has nested `try/except/else` with backfill retry.

**Plan:** Extract three private helpers:

| Helper | Responsibility |
|---|---|
| `_run_ingestion_stage(trading_date, markets, ...)` | Lines 85–144. Returns `(results_dict, market_results)`. |
| `_run_feature_stage(tickers, trading_date, ..., allow_history_backfill)` | Lines 146–232. Handles the backfill retry logic. Returns `stage_result`. |
| `_run_ml_stage(tickers, trading_date, ...)` | Lines 234–255. Simple delegation to `run_prediction_job`. |

The main function becomes a ~40-line orchestrator calling these three helpers.

**Effort:** M (1–2 hours). **Impact:** High (testability, readability).

### 3D. Clean up `ingestion/gap_detection.py` (post-Phase 1)

After Phase 1 removed dead methods (`get_missing_date_ranges`, `print_gap_report`, `print_coverage_stats`, `_count_business_days`), the file should be ~180 lines. Verify no further cleanup needed.

**Effort:** S (15 min). **Impact:** Low (already mostly done).

---

## 4. Phase 4 — Convention Alignment

### 4A. Structured Logging Migration

**Problem:** Multiple modules use f-string logging (`logger.info(f"...")`) instead of structlog's structured pattern (`logger.info("event_name", key=value)`). This defeats structlog's JSON output and correlation IDs.

**Files to fix (verified locations):**

| File | Approximate f-string log count | Lines |
|---|---|---|
| `monitoring/health.py` | ~15 | L170, L187, L236, L255, L279, L294, L359, L423, L502 + others |
| `features/engineering.py` | ~6 | L114, L135, L138, L150, L192, L193 |
| `sources/macro.py` | ~15 | L62, L84, L92, L105, L133, L148, L174, L197, L210, L213, L215, L219, L229, L232, L237, L244 |
| `ingestion/orchestrator.py` | ~7 | L220, L225, L231, L246, L273, L284 |
| `signals/scanner.py` | 2 | L86, L109 (uses `print()` instead of logger) |

**Pattern:**
```python
# Before (f-string):
logger.info(f"{status} {market}: Latest data = {latest_date} ({age_days} days old)")

# After (structured):
logger.info("data_freshness_check", market=market, latest_date=str(latest_date), age_days=age_days, status=status)
```

**Effort:** M (2–3 hours). **Impact:** Medium (observability, structured logs).

### 4B. Missing Module Docstrings

Add module docstrings to (currently missing):
- `cli/commands/ingest.py` — longest command file (287 lines), no docstring
- `cli/commands/data.py` — 295 lines, no docstring
- `cli/commands/pipeline.py` — no docstring
- `core/ticker_utils.py` — no docstring, no `__all__`

**Effort:** S (15 min). **Impact:** Low.

### 4C. `from __future__ import annotations` Consistency

Add to files missing it:
- `storage/s3_sync.py`
- `ml/validation.py`
- `ml/trainer.py`

**Effort:** S (5 min). **Impact:** Low (consistency only; works without it on Python 3.12+).

### 4D. Logger Pattern Standardization

Standardize on `structlog.get_logger(__name__)` everywhere. Currently mixed:
- `structlog.get_logger()` (no name): `engineering.py`, `features/dag/clean_02.py`, `features/dag/features_03.py`
- `structlog.get_logger(__name__)`: `ml/backends.py`, `ml/comparison.py`, `ml/forecasting.py`, `ingestion/gap_detection.py`

**Effort:** S (15 min). **Impact:** Low.

---

## 5. Phase 5 — Dependency Cleanup

### 5A. Move Optional Dependencies to `[dependency-groups]`

**Current state:** `pyproject.toml` has 30+ core `dependencies` including packages only used by single modules.

**Plan:**

| Package | Current | Target group | Used by |
|---|---|---|---|
| `fredapi>=0.5.2` | core deps | `[dependency-groups] macro` | `sources/macro.py` only |
| `finance-datareader>=0.9.96` | core deps | `[dependency-groups] macro` | `sources/macro.py` only |
| `readability-lxml>=0.8.1` | core deps | `[dependency-groups] sec` | `sources/sec_fulltext.py` only |
| `edgartools>=5.36.0` | core deps | `[dependency-groups] sec` | `sources/sec_fulltext.py` only |
| `fastapi>=0.115.0` | core deps | `[dependency-groups] api` | `api/` only |
| `uvicorn>=0.30.0` | core deps | `[dependency-groups] api` | `api/` only |
| `openai>=1.50.0` | core deps | `[dependency-groups] llm` | `ingestion/llm_processor.py` only |
| `feedparser>=6.0.11` | core deps | `[dependency-groups] sources` | `sources/rss.py` only |

**For each moved package:**
1. Add `try/except ImportError` lazy-import guard at the call site.
2. Add mypy `ignore_missing_imports` override in `pyproject.toml` if not already present.
3. Update `.env.example` / docs if the package needs env vars.

**Effort:** M (1–2 hours). **Impact:** Medium (smaller default install, faster `uv sync`).

### 5B. Fix Duplicate Dependency Definitions

| Package | Appears in | Fix |
|---|---|---|
| `pointblank>=0.8` | core deps + `[dependency-groups] validation` | Remove from core deps; keep in `validation` group |
| `croniter` | core deps + `[dependency-groups] schedule` | Remove from core deps; keep in `schedule` group |
| `vaderSentiment` | `[dependency-groups] dev` + `[dependency-groups] sentiment` | Keep only in `sentiment` group |

### 5C. Fix Version Inconsistency

| Package | Group A | Group B | Fix |
|---|---|---|---|
| `seaborn` | `ml` group: `>=0.13.2` | `viz` group: `>=0.13.0` | Use single pin `>=0.13.2` in both |

**Effort:** S (15 min). **Impact:** Low.

---

## 6. Execution order and dependencies

```
Phase 3A (config split) ──┐
                           ├──→ Phase 3C (pipeline refactor) ──→ Phase 4 (conventions)
Phase 3B (ingest dedup) ──┘
                                                        ──→ Phase 5 (deps)
```

- **Phase 3A** and **3B** are independent — can be done in parallel.
- **Phase 3C** benefits from 3A being done first (cleaner imports).
- **Phase 4** is independent of Phase 3 — can be done anytime.
- **Phase 5** is independent of Phases 3–4 — can be done anytime.
- **All phases** should run `uv run ruff check .` and `uv run mypy src/` after each change.

---

## 7. Verification checklist

After each phase:

```bash
# Lint + typecheck
uv run ruff check src/equity_lake/
uv run mypy src/equity_lake/

# Unit tests (full suite)
uv run pytest tests/unit/ -x -q

# Specific regression checks
uv run pytest tests/unit/test_config_contract.py -v      # config split (3A)
uv run pytest tests/unit/test_cli_unified.py -v           # CLI changes (3B)
uv run pytest tests/unit/test_pipeline_orchestrator.py -v # pipeline refactor (3C)
```

After Phase 5:

```bash
# Verify base install still works (no missing imports)
uv sync
uv run python -c "from equity_lake.core.config import Settings; print('OK')"

# Verify optional groups install correctly
uv sync --group macro
uv run python -c "from equity_lake.sources.macro import MacroIndicatorFetcher; print('OK')"
```

---

## 8. Risk notes

1. **`core/config.py` split (3A):** Highest risk. 16 files import from `equity_lake.core.config`. The re-export shim in the slimmed `config.py` is critical — test with `uv run pytest tests/unit/test_config_contract.py` before and after.
2. **`pipeline.py` refactor (3C):** The backfill retry logic (L178–232) is subtle. Extract carefully; add a test for the `NoFeatureHistoryError` path if one doesn't exist.
3. **`cli/commands/ingest.py` refactor (3B):** The `sec` command has a different fetcher (`SECFilingFetcher`) and schema path. Verify it still works after extracting the shared helper.
4. **Phase 5 deps:** Moving `pointblank` out of core deps will break `ingestion/writers.py:validate_schema` if called without the `validation` group. Add a lazy-import guard.

---

## 9. Handoff notes for the implementing agent

- Baseline: working tree with Phases 1–2 applied (commit `203e9e4` or later).
- **Commit per ID:** `refactor(config): 3A split core/config.py into config_models + settings`, etc.
- AGENTS.md still binds: `uv run` (never bare `python`), structlog, polars primary, `extra="forbid"`, `YYYYMMDD-*.md` docs, import boundaries.
- Do **not** touch `features/dag/enrichments_04.py` (677 lines) — it's large but each enrichment function is self-contained; splitting would break the Hamilton DAG wiring.
- Do **not** add comments unless asked (AGENTS.md code style).
- Run `uv run ruff format .` after each phase to auto-format.
