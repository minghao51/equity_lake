# Integration Recon — Findings & Plan Corrections

**Date:** 2026-08-04
**Source:** 5 parallel `scout` subagents (contracts, Phase 1, Phase 2 ML, Phase
2 API/RAG, cross-cutting). All findings below are grounded in actual files read.
**Purpose:** feed corrections into `20260804-portfolio-roadmap.md`,
`20260804-portfolio-implementation-map.md`, `20260804-portfolio-phase{1,2,3}-handoff.md`,
and `AGENTS.md`.

---

## A. What the plan got right (confirmed)

- **Hatch packaging needs no change.** `packages = ["src/equity_lake"]` (single glob)
  auto-ships `findings/`, `api/`, `agent/`, `portfolio/`. Do NOT add `src/api` etc.
- **`data/findings/` is auxiliary** (like `data/signals/`, `data/update_history/`) —
  NOT a medallion table, NOT in `catalog.jsonl`, NOT in `ensure_dirs()`.
- **LightGBM does NOT change the Platinum predictions schema.** `predict()` returns
  a backend-agnostic dict; `validate_predictions` (in `ml/__init__.py`, not
  `validation/`) checks only `probability∈(0,1)`, `direction∈{up,down}`, non-null
  keys. → **No `change-equity-schema` trigger for the model swap.**
- **`BacktestResult` and `FindingCard` are frozen contracts** that Phase 2/3 serve
  unchanged.
- **`seed_demo` is standalone**, not a pipeline stage (no dry-run/backfill contract).

## B. Corrections the plan must absorb

### B1. CLI command collision — `equity backtest report` is not addable as-is ⚠️ DECISION
`backtest` is currently a **flat top-level** command (`@app.command("backtest")` in
`cli/commands/analysis.py`). You cannot add `backtest report` without one of:
- **(a)** Convert `backtest` → `backtest_app` sub-app (`equity backtest run ...`).
  *Breaking* — moves existing args; must update `test_cli_unified.py` + user guide.
- **(b)** Rename to flat `equity backtest-report` (or `equity report backtest`).
- **(c)** Put reports under a new `report` sub-app: `equity report backtest`.

Also: **every new sub-app** (`demo_app`, `arena_app`, `ml_app`, `risk_app`,
`api_app`, `report_app`) must be declared in `cli/_app.py` and wired with
`app.add_typer(<x>_app, name="…")` in `cli/__main__.py` **before** importing the
command module. Recommended default: **(c)** a `report` sub-app for all report
commands, and **(b)** keep `backtest` flat — least churn.

### B2. Import-boundary enforcement gap ⚠️
`tests/unit/test_import_boundaries.py` only enforces `LAYER_BOUNDARIES` =
{core, storage, features, ingestion}. New packages `findings/`, `api/`, `agent/`,
`portfolio/` are **skipped (unconstrained)** today. Plan must extend:
```python
LAYER_BOUNDARIES = {
    "core": {"cli","dashboard","sources","api","agent","findings"},
    "storage": {"cli","dashboard","api","agent"},
    "features": {"cli","dashboard","api","agent"},
    "ingestion": {"cli","dashboard","api","agent"},
    "agent": {"api","cli","dashboard"},   # agent must not import api/cli
    # "api": set(),  "findings": {"cli"},  "portfolio": {"cli"}  (decide)
}
```

### B3. `Settings(extra="forbid")` trap + raw vs prefixed env vars
- Any new `EQUITY_<GROUP>__*` env var without a matching nested `BaseModel` field
  **raises at load**. Add the model field + `.env.example` entry in the same change.
- **W&B keys stay RAW** (`WANDB_API_KEY`, `WANDB_ENTITY`, `WANDB_PROJECT`) — the
  `wandb` SDK reads them natively; do NOT prefix with `EQUITY_`. Same precedent as
  `FRED_API_KEY`/`DEEPSEEK_API_KEY`/`FINNHUB_API_KEY`.
- **Pre-existing bug:** `.env.example` has a dead `EQUITY_STORAGE__*` block (no
  `StorageSettings` model exists) — would raise if set. Remove or implement.

### B4. LightGBM swap touches **4** estimator sites (not 1)
`xgb.XGBClassifier(...)` is constructed in: (1) `train_model` default fit, (2)
`_tune_hyperparameters` (inside `GridSearchCV`), (3) `backtest()` walk-forward
retrain, (4) `validation.run_purged_walk_forward_validation` per-fold. Sites #3/#4
have **hardcoded params**. → `ml/backends.py` needs a `build_estimator(backend,
params, scale_pos_weight)` factory + param-name normalization
(`colsample_bytree`↔`feature_fraction`; objective strings). Also generalize
`_build_model_filename`/`_parse_model_path` from literal `_xgboost_` to a
`{backend}` token (keep back-compat alias in the parser), and generalize
`_load_model`'s `model._xgb_version` read.

### B5. `run_purged_walk_forward_validation` is XGBoost-locked & aggregate-only
`ml/comparison.py` must **reuse the splitter directly**
(`PurgedEmbargoedWalkForwardSplitter.split()` yields numpy index pairs, backend-
agnostic) and fit each backend itself to get per-fold rows. Do not reuse the
aggregate function.

### B6. Enrichment ablation: `include_macro` is not exposed ⚠️ DECISION
`run_feature_job` exposes `include_sentiment/social/enriched/analyst/sec` but **not**
`include_macro` (macro defaults ON inside `FeatureEngineer`). For a true
technical-only frame, `ml/ablation.py` must either call `FeatureEngineer.generate_features`
directly with `include_macro=False`, or extend `run_feature_job` (a "CLI change" per
the matrix). Feature-skew checks (`_check_feature_skew`) are **warn-only**, so
ablation arms will log skew warnings when cross-scored — acceptable, document it.

### B7. ⚠️ PRE-EXISTING BUG — bronze→silver path divergence
`ingestion/bronze_silver.py` and `ingestion/sec_processor.py` write with non-numbered
`market=` strings (`"silver/processed_articles"`, `"silver/sec_extractions"`) → land
in `data/lake/silver/`, while catalog/paths/constants/health all use `02_silver/`.
→ `agent/index.py` must read via **path constants** (`SILVER_PROCESSED_ARTICLES_DIR`
etc.), and we should fix the two `market=` strings (`"02_silver/..."`) as a small
separate PR so writes and reads agree. **Blocks correct RAG indexing if unfixed.**

### B8. LLM seam for RAG ≠ batch processor
`BaseLLMBatchProcessor` is DeepSeek-locked, chat+JSON-mode only, **no embeddings**.
RAG needs a separate embedding client. → add a tiny `build_chat_client()` /
`build_embedding_client()` factory in `ingestion/llm_base.py` (or `core/`), reuse
for both the batch processor and `agent/rag.py`. Do **not** subclass the batch
processor.

### B9. Read layer needs thin helpers (no engine, but ~5-line getters)
`lake_reader.duckdb_scan_for()` is a 1-line scan-string helper (not a façade);
`EquityDataDB` auto-views only the 5 price tables. Routers need small getters
(`api/deps.py` or `storage/queries.py`): `get_predictions(ticker,as_of)`,
`get_signals(ticker,date)`, `get_sec_extractions(ticker)`, `get_news(ticker,date)`
atop `duckdb_scan_for`/`read_delta`. **Backtests are not persisted today** — Phase 1
`report.py` is what creates that persistence.

### B10. Deploy: production stage runs `uv sync --frozen --no-dev`
So `fastapi`/`uvicorn` must be **core** deps, OR the production stage must run
`uv sync --group api`. No `fly.toml` exists; need new `fly.toml` +
`.github/workflows/deploy-fly.yml` (separate from `pages.yml`: different secret
`FLY_API_TOKEN` + permissions). `sync_schedule.py` only checks `pages.yml`'s first
cron — a new `snapshot.yml` cron won't be auto-validated (reuse `schedule.cron`
literally, or extend the tool with `--workflow`).

### B11. `portfolio/` location + persist-to-lake fork ⚠️ DECISION
- Is it `ml/portfolio/` (sub-package) or top-level `portfolio/`? (hatch auto-covers
  either; boundaries differ)
- Risk/portfolio outputs: **persisted as a Platinum table** (→ full
  `change-equity-schema`: schema constants, catalog entry, pointblank validator,
  `SCHEMA_REGISTRY`) **or JSON report** (no schema chain)? Recommend **JSON report
  + FindingCard** for the portfolio (keeps scope sane); persist only if a daily
  portfolio output is genuinely needed.

### B12. Doc drift to fix alongside
`STACK.md`/`CONVENTIONS.md` stale (Python 3.11 vs 3.12; ruff line-length 88 vs 150;
nonexistent `core/constants.py`, `core/runtime.py`, `storage/parquet.py`).
`technical_roadmap.md` references Click + "v0.4.0".

## C. Contracts checklist (apply to every new feature)

| Change | Required work (from AGENTS.md matrix + recon) |
|---|---|
| New top-level package (`api/`,`agent/`,`findings/`,`portfolio/`) | hatch: none. **Extend `LAYER_BOUNDARIES`.** Lazy-import heavy deps. |
| New CLI command / sub-app | Declare sub-app in `cli/_app.py`; `add_typer` in `__main__.py`; help docstring; `Annotated[..., typer.Option("--x", help=…)]`; `raise typer.Exit(1)` on failure; CLI test in `test_cli_unified.py`; user-guide page + `mkdocs.yml` nav. |
| New optional dependency | Add to `[dependency-groups]` extra; import **lazily** (`try/except ImportError`); add to mypy `ignore_missing_imports`. |
| New env var | RAW SDK keys (no prefix) → `.env.example` only. App knobs → nested `BaseModel` on `Settings` + `.env.example` (because `extra="forbid"`). |
| New medallion table | Full `change-equity-schema`: `core/schemas.py` constants, `catalog/datasets.py` entry + `_DTYPE_MAP`, `validation/schemas.py` + `SCHEMA_REGISTRY`, write-boundary validator, `catalog-generate`, migration note. |
| Non-lake artifact (`data/findings/`, backtest report) | `DATA_DIR/<name>` path constant; Pydantic model at write boundary (no pointblank); **no** catalog entry. |
| Pipeline-stage change | dry-run skip, failure contract, orchestration test, deterministic exit status. |

## D. Open decisions to confirm (block doc finalization)

1. **B1** — backtest command shape: `report` sub-app (rec) / flat `backtest-report` / break to `backtest_app`?
2. **B6** — ablation: call `FeatureEngineer` directly (rec) vs extend `run_feature_job`?
3. **B11** — `portfolio/` top-level (rec) vs `ml/portfolio/`; and JSON report (rec) vs Platinum table?
4. **B10** — fastapi/uvicorn as **core** deps (rec, simplest for prod stage) vs `--group api` stage?
5. **B7** — fix the `silver/` → `02_silver/` path bug now (small PR) before RAG work? (rec yes)
