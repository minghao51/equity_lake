# Phase 2 Handoff — ML Rigor + RAG Agent (grounded, post-Phase-1)

**Date:** 2026-08-05 · **Phase:** 2 of 3 · **Duration:** ~3–4 weeks
**Supersedes:** [`20260804-portfolio-phase2-handoff.md`](./20260804-portfolio-phase2-handoff.md)
(speculative — written before Phase 1 was built; this doc reflects verified reality).
**Roadmap:** [`20260804-portfolio-roadmap.md`](./20260804-portfolio-roadmap.md) ·
**Map:** [`20260804-portfolio-implementation-map.md`](./20260804-portfolio-implementation-map.md) ·
**Recon:** [`20260804-integration-recon.md`](./20260804-integration-recon.md) §B.

> **You are a cold-start agent.** Read §1 (orientation) and §4 (ground-truth API
> map) before writing any code. Every signature below was verified against the
> source on 2026-08-05 with line numbers. Do not re-litigate the decisions in §3 —
> they are locked.

---

## 1. Read these first (orientation)

1. `AGENTS.md` — workflow, output style, stack, layout, patterns, change matrix.
   Non-negotiable: `uv run`, `YYYYMMDD-*.md` docs, `extra="forbid"` Settings,
   sub-app CLI wiring, import boundaries, minimal scope.
2. [`20260804-integration-recon.md`](./20260804-integration-recon.md) §B — the
   **B-corrections** (B4–B11). Phase 2 lives or dies by these.
3. The Phase 1 contracts you must reuse unchanged (§2 below + the source files).
4. This doc's §4 (API map) — the verified reuse points.
5. `tests/unit/test_import_boundaries.py` — the layer graph you must respect.

## 2. Entry state (verified — Phase 1 shipped on 2026-08-05)

Three commits on `main`: `fix(ingestion)` silver-path+drift · `feat(backtest)`
Strategy Lab · `docs` notebook+README. **Working tree clean.** Full fast suite
green; `ruff`/`mypy` clean; `uv.lock` unchanged.

**Frozen contracts (do not change — Phase 3 depends on these):**

| Contract | Source | Surface you reuse |
|---|---|---|
| `FindingCard` | `findings/models.py` | `id, axis, claim, verdict, conclusion, metrics(dict[str,float]), evidence_refs(list[str]), run_date(date), scope(dict)`; `extra="forbid"`; written to `data/findings/<id>.json`. **`axis` already includes `labeling`, `model`, `ablation`** → your 3 new cards need **no schema change**. Also already has `risk` (Phase 3). |
| `FindingCard` I/O | `findings/writer.py` | `write_finding_card(card, *, base=None)`, `load_finding_cards(base=None)->list[FindingCard]`, `evidence_dir(card_id, *, base=None)`. |
| `BacktestResult` | `backtesting/result.py` | `strategy_name, tickers, start/end_date, initial/final_cash, equity_curve(pl.Series), trades(list[dict]), metrics(dict)`; props `total_return, sharpe_ratio, max_drawdown`; `to_dict()` omits equity_curve/trades. |
| Arena | `backtesting/arena.py` | `run_arena(tickers, start_date, end_date, *, markets=("us",), initial_cash=100_000.0, strategies=None, cost_regimes=None, preloaded_data=None) -> ArenaOutcome`. |
| `ArenaOutcome` | `backtesting/arena.py` | fields **`runs, data, benchmark, initial_cash`** (there is NO `.strategies`/`.cost_regimes` — derive from `runs`). |
| Report | `backtesting/report.py` | `write_arena_artifacts(outcome, *, base=None, run_date=None, scope=None)->list[FindingCard]`; `drawdown_series(equity: pl.Series)->pl.Series`; `build_finding_cards(...)`. |
| Demo lake | `devtools/seed_demo.py` | `seed_demo(*, years=5, tickers=None, real=False, seed=42, verbose=False, lake_dir=None)->dict`; CLI `equity demo seed [--real]`. The `demo` config group = **50 liquid US tickers** (`config/tickers.yaml`). |

The lake is currently populated with **synthetic** data (`make demo`). Real data:
`equity demo seed --real`. ML needs feature history — that requires the feature
pipeline; see §6 (2A) for the backfill guardrail.

## 3. Locked decisions (do not re-debate)

From the roadmap (D2/D3) + recon §B, **resolved** in prior planning:

| ID | Decision | Status |
|---|---|---|
| D2 | LightGBM included alongside XGBoost | ✅ locked |
| D3 | Tracking = **W&B** (hosted free tier, public project + Reports) | ✅ locked |
| B1 | Reports under a `report` sub-app; `backtest` stays flat | ✅ **done in P1** |
| B7 | `silver/`→`02_silver/` write-path bug | ✅ **fixed in P1** |
| B4 | `ml/backends.py` `build_estimator(backend, params, scale_pos_weight)` factory replaces **4** XGBoost sites + filename `{backend}` token + back-compat alias | ✅ locked |
| B5 | `comparison.py` reuses `PurgedEmbargoedWalkForwardSplitter.split()` directly (aggregate fn is XGBoost-locked) | ✅ locked |
| B6 | `ablation.py` calls `FeatureEngineer.generate_features(..., include_macro=False)` directly | ✅ locked |
| B8 | New `build_chat_client()`/`build_embedding_client()` factory in `ingestion/llm_base.py`; RAG does **not** subclass `BaseLLMBatchProcessor` | ✅ locked |
| B9 | API routers = thin getters in `api/deps.py` atop `duckdb_scan_for`/`read_delta` | ✅ locked |
| B10 | `fastapi`/`uvicorn` are **core** deps; new `fly.toml` + `.github/workflows/deploy-fly.yml` | ✅ locked |
| B11 | `portfolio/` top-level; risk/portfolio outputs = **JSON report** (no Platinum table) | ✅ locked (Phase 3) |
| B3 | W&B keys stay **raw** (`WANDB_API_KEY/ENTITY/PROJECT`, no `EQUITY_` prefix) | ✅ locked |

## 4. Ground-truth API map (verified 2026-08-05, with line refs)

### ML — `src/equity_lake/ml/forecasting.py`
- **`PriceForecaster`** (class @ L73): construct with `model_mode` (`"v1_direction"` / `"v2_meta_label"`) and `model_dir`. Docstring: "XGBoost-based forecaster for v1 direction and v2 meta-label models."
- **`train_model(ticker, start_date, end_date, params=None, tune_hyperparams=False, validate=False, max_model_age_days=7, validation_mode="purged_walk_forward", train_window=252, test_window=21, embargo_window=1, label_horizon_days=None) -> xgb.XGBClassifier`** (L118).
- **`predict(ticker, date, model=None) -> dict[str, Any]`** (L344). Output is backend-agnostic; `validate_predictions` (in `ml/__init__.py`, **not** `validation/`) only checks `probability∈(0,1)`, `direction∈{up,down}`, non-null keys → **no Platinum schema change for the model swap.**
- **`backtest(ticker, start_date, end_date, train_window=500, retrain_interval=63) -> pl.DataFrame`** (L410).
- **The 4 estimator swap sites (B4):**
  1. `train_model` default fit — **L243** `model = xgb.XGBClassifier(**default_params)` (uses `scale_pos_weight` from class_counts @ L229).
  2. `_tune_hyperparameters` — **L333** `estimator=xgb.XGBClassifier(**estimator_kwargs)` inside `GridSearchCV` (L292 `-> xgb.XGBClassifier`).
  3. `backtest()` retrain — **L439** `xgb.XGBClassifier(max_depth=5, learning_rate=0.05, n_estimators=200, objective="binary:logistic", eval_metric="logloss", random_state=42, …)` — **hardcoded params.**
  4. `validation.run_purged_walk_forward_validation` — **`ml/validation.py:92`** `xgb.XGBClassifier(**model_kwargs)` — **hardcoded params.**
- **Filename/parser to generalize (B4):** `_build_model_filename` (L532, literal `_xgboost_`); `_parse_model_path` (L535, partitions on `_xgboost_`); glob at L547 `{ticker}_xgboost_*.pkl`; `_load_model` (L564, reads `model._xgb_version`). → make `_xgboost_` a `{backend}` token; keep a back-compat alias in the parser so existing model files still load.
- **Metadata the registry consumes:** `_save_training_metadata` (L579) writes `<model>.training_metadata.json`; `*.training_audit.parquet` also emitted. **Feature skew:** `_check_feature_skew` (L710) — **warn-only**; reuse it for LightGBM too.

### ML — `src/equity_lake/ml/validation.py`
- **`PurgedEmbargoedWalkForwardSplitter`** (L17): `.split(X, y=None, groups=None) -> Iterator[tuple[np.ndarray, np.ndarray]]` (L25) — **numpy index pairs, backend-agnostic.** This is what `comparison.py` (B5) calls directly.
- **`run_purged_walk_forward_validation(...)`** (L50) — aggregate, **XGBoost-locked** (constructs its own estimator @ L92), returns no per-fold rows. **Do not reuse for comparison.**

### Features — `src/equity_lake/features/`
- **`run_feature_job(*, tickers, output_start_date, output_end_date, compute_target=True, include_sentiment=False, include_social_sentiment=False, include_enriched_sentiment=False, include_analyst_ratings=False, include_sec_features=False) -> pl.DataFrame`** (`__init__.py:24`). **Does NOT expose `include_macro`** (macro defaults ON internally).
- **`FeatureEngineer.generate_features(...)`** (`engineering.py:89`) **has `include_macro: bool = True`** (L97). For the technical-only ablation arm (B6): `engineer = _load_feature_engineer()` (`features/__init__.py`, the lazy Hamilton loader `run_feature_job` already uses) then `engineer.generate_features(..., include_macro=False)`. Remember `engineer.close()`.
- Expect `_check_feature_skew` **warnings** when ablation arms are cross-scored — warn-only, document it.

### LLM seam — `src/equity_lake/ingestion/llm_base.py`
- **`BaseLLMBatchProcessor[BatchT, ItemT]`** (L38, generic, ABC): `__init__` (L52) builds `AsyncOpenAI(api_key=os.getenv("DEEPSEEK_API_KEY"), base_url="https://api.deepseek.com")`; uses `client.chat.completions.create(model=self.model, …)` (L83). **Chat + JSON-mode only, no embeddings, DeepSeek-locked.**
- **B8:** add `build_chat_client()` / `build_embedding_client()` factory here (or `core/`); reuse for both the batch processor and `agent/rag.py`. **Do not subclass the batch processor.**

### Read layer — `storage/`
- **`duckdb_scan_for(market_path: Path) -> str`** (`lake_reader.py:10`) — returns a **scan string** (`delta_scan('…')` or `read_parquet('…/**/*.parquet', hive_partitioning=1)`), not data. Compose into `EquityDataDB.query(sql)`.
- **`EquityDataDB`** (`storage/duckdb.py:39`): `query(sql)->pl.DataFrame` (L115), `query_arrow(sql)` (L124), `execute(sql)` (L135). Auto-views only the 5 price tables — **silver tables are not auto-viewed** (scan them explicitly).
- **`read_delta(market, version=None, lake_dir=None) -> pl.DataFrame`** (`storage/delta.py:117`).
- **Silver path constants** (`core/paths.py:57–62`): `SILVER_PROCESSED_ARTICLES_DIR`, `SILVER_SEC_EXTRACTIONS_DIR`, `SILVER_NEWS_SENTIMENT_DIR`, `SILVER_SEC_FINANCIALS_DIR`, `SILVER_ANALYST_RATINGS_DIR`, `SILVER_SOCIAL_SENTIMENT_DIR`; aliases `US_NEWS_DIR` (L85), `SEC_EXTRACTIONS_DIR` (L88). RAG + API read SEC/news via these.

## 5. Workstreams (three parallel tracks)

> **Critical path:** **2A** (backends → comparison) is the spine. **2B** (API) and
> **2C** (RAG) are independent and parallelizable. Start 2A's `ml/backends.py`
> first — it unblocks the comparison harness.

### 2A — ML rigor + W&B registry

| Path | | What | Reuses (verified) |
|---|---|---|---|
| `src/equity_lake/ml/backends.py` | ➕ | `ModelBackend` Protocol + XGBoost + LightGBM impls; `build_estimator(backend, params, scale_pos_weight)` factory + param-name normalization (`colsample_bytree`↔`feature_fraction`; objective strings) | the 4 sites in §4 |
| `src/equity_lake/ml/forecasting.py` | ✏️ | replace 4 XGB sites with `build_estimator`; generalize `_xgboost_`→`{backend}` token (+ back-compat alias in `_parse_model_path`) | §4 |
| `src/equity_lake/ml/registry.py` | ➕ | **W&B** adapter; log metrics/config/SHAP-as-artifact; local `*.training_metadata.json`/`*.training_audit.parquet` stay source of truth | `forecasting._save_training_metadata` (L579), `ml/trainer.py` (SHAP) |
| `src/equity_lake/ml/comparison.py` | ➕ | v1-direction vs v2-meta-label OOS **and** XGB vs LGBM, per backend per fold → table → FindingCards | `PurgedEmbargoedWalkForwardSplitter.split()` (validation.py:25); `findings/` |
| `src/equity_lake/ml/ablation.py` | ➕ | enriched vs technical-only (`include_macro=False`) sweep → FindingCard | `FeatureEngineer.generate_features` (engineering.py:89, L97) |
| `pyproject.toml`, `.env.example` | ✏️ | `ml` group += `wandb`, `lightgbm`; raw `WANDB_API_KEY/ENTITY/PROJECT` | — |

**CLI:** `equity ml compare --universe demo` and `equity ml ablate --universe demo` under a new `ml_app` (declare in `cli/_app.py`, wire in `__main__.py` — see P1 pattern). Per the change matrix: help docstring + `test_cli_unified.py` test + user-guide line.

**Backfill guardrail:** `run_feature_job` over the demo window needs feature
history. Missing history requires `--allow-history-backfill` with scoped markets/tickers (AGENTS.md operational guardrails). Run features for the `demo` universe first; confirm `03_gold/features` is populated before training.

### 2B — FastAPI read API

| Path | | What | Reuses (verified) |
|---|---|---|---|
| `src/equity_lake/api/{__init__,main,deps}.py` | ➕ | FastAPI app; `deps.py` thin getters: `get_predictions(ticker,as_of)`, `get_signals(ticker,date)`, `get_sec_extractions(ticker)`, `get_news(ticker,date)`, `get_backtests(strategy)`, `get_findings()` | `duckdb_scan_for`, `read_delta`, `EquityDataDB.query`, silver path constants, `findings/writer.load_finding_cards` |
| `src/equity_lake/api/routers/{signals,predictions,backtests,models,findings}.py` | ➕ | read-only endpoints over snapshots + `data/findings/` | `deps.py` getters |
| `src/equity_lake/dashboard/model_explorer.py` | ➕ | Streamlit: calibration, SHAP beeswarm, drift vs last model | `dashboard/streamlit_app.py` pattern |
| `Dockerfile` | ✏️ | add `api` stage (multi-stage already in place) | existing Dockerfile |

`backtests` persistence is created by Phase 1 `report.py` (`write_arena_artifacts`) — the API only reads it.

### 2C — RAG agent over the lake

| Path | | What | Reuses (verified) |
|---|---|---|---|
| `src/equity_lake/agent/{__init__,rag,tools,index,eval}.py` | ➕ | embed+index silver SEC/news; DuckDB tool; tool-using OpenAI agent; 20-Q eval set; refuses-with-citation when no evidence | `build_chat_client`/`build_embedding_client` (B8), `duckdb_scan_for`, `SILVER_SEC_EXTRACTIONS_DIR`/`SILVER_PROCESSED_ARTICLES_DIR` |
| `pyproject.toml` | ✏️ | `agent` group: `openai` (embeddings), **`sqlite-vec`** (recommended — matches local-first ethos) | — |

**Gate:** the RAG eval (≥ target accuracy **and** citation rate; agent refuses-with-citation when no evidence) is **non-negotiable** before exposing it in the UI.

## 6. FindingCards to produce (3 new — axis already in schema)

| id | axis | question |
|---|---|---|
| `meta-label-vs-direction` | labeling | does v2 meta-labeling beat v1 raw direction on precision / OOS P&L? |
| `xgb-vs-lgbm` | model | XGBoost vs LightGBM — accuracy, calibration, feature-importance agreement |
| `enrichment-ablation` | ablation | do enriched features (sentiment/SEC/analyst) beat technical-only? |

Plus a **public W&B Report** per comparison, linked from the README. Negative
results are first-class — record the honest verdict.

## 7. Cross-cutting obligations (apply to every change)

- **Import boundaries (B2):** extend `LAYER_BOUNDARIES` in
  `tests/unit/test_import_boundaries.py`. Today only `{core, storage, features,
  ingestion}` are enforced. Add: `findings` (may use `core` only), `api` (may use
  `core`/`storage`/`findings`, **not** `cli`/`pipeline`), `agent` (may use
  `core`/`storage`/`ingestion`/`findings`, **not** `api`/`cli`/`dashboard`),
  `portfolio` (decide; recommend may use `core`/`storage`/`ml`). Lazy-import heavy
  deps (`openai`, `wandb`, `lightgbm`, `fastapi`) so base `uv sync` stays fast.
- **Config (B3):** `Settings(extra="forbid")` — any new `EQUITY_<GROUP>__*` env var
  needs a matching nested `BaseModel` field **or it raises at load**. Add the field
  + `.env.example` entry in the same change. W&B keys stay **raw** (`WANDB_*`).
- **Change matrix:** new CLI command → help + CLI test + user-guide line; new
  optional dep → `[dependency-groups]` + lazy import + mypy `ignore_missing_imports`;
  new medallion table → full `change-equity-schema` (Phase 2 adds **none** — all
  outputs are FindingCards/snapshots, not lake tables).
- **Conventional commits** (`feat`/`fix`/`refactor`/`docs`/`test`), repo identity
  `minghao <howt51@gmail.com>` (set repo-local: `git config user.name/email`).

## 8. Phase-1 scars (avoid these)

1. **`uv sync --group X` REMOVES other groups' packages** — it synced base+X and
   uninstalled `polars-backtest` mid-session, silently breaking the engine. Always
   combine the groups you need: `uv sync --group backtesting --group ml --group viz`.
2. **`--extra backtesting` is WRONG** — backtesting is a `[dependency-groups]`
   entry → use `--group backtesting`. (AGENTS.md already corrected.)
3. **`ArenaOutcome`** has `runs/data/benchmark/initial_cash` — there is no
   `.strategies`/`.cost_regimes`; derive from `runs`.
4. **Notebook plotting:** wrap `import matplotlib` in `try/except ImportError` so
   base-deps runs still execute; **don't** put `%matplotlib inline` (it eagerly
   imports matplotlib and breaks the run without the `viz` group). Silence
   structlog in memo cells via `setup_structured_logging(level="ERROR")`.
5. **DuckDB** rejects bare aliases `first`/`last` — use `AS first_day`.

## 9. Exit criteria + verification

```bash
# features must exist before ML (backfill guardrail)
dotenvx run -- uv run equity pipeline --markets us --tickers <demo subset> --allow-history-backfill

# ML rigor (2A)
uv run equity ml compare  --universe demo    # v1/v2 × XGB/LGBM, logs to W&B, 2 FindingCards
uv run equity ml ablate   --universe demo    # enrichment-ablation FindingCard
ls data/findings/                            # 6 cards total (3 from P1 + 3 here)
# open the public W&B project URL: runs + a Report per comparison exist

# API (2B)
uv run uvicorn equity_lake.api.main:app --reload
curl localhost:8000/findings                  # lists all FindingCards
curl localhost:8000/backtests/<strategy>      # equity curve + metrics

# RAG (2C)
uv run python -m equity_lake.agent.eval       # 20-Q eval: accuracy + citation rate

# gates
uv run pytest -q                              # incl. new api/agent/import-boundary tests
uv run ruff check . && uv run mypy src        # clean
```

## 10. Handoff to Phase 3

Phase 3 can rely on:
- A **frozen FastAPI contract** (endpoint paths + `FindingCard` JSON shape unchanged);
  Phase 3 only *adds* `risk`/`portfolio`/`chat` routers.
- `data/findings/` with **6 cards** spanning `labeling/model/ablation/strategy/
  cost/benchmark` → the React Findings page renders them generically.
- A public **W&B project** with Reports, linkable from the README.
- A working **RAG agent** + eval baseline; Phase 3 wires it into the Chat page.
- `portfolio/` top-level already decided (B11); risk/portfolio outputs are JSON
  reports + a `risk`-axis FindingCard (no Platinum table).
