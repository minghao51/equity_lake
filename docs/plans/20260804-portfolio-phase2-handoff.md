# Phase 2 Handoff — ML Rigor + RAG Agent

> ⚠️ **SUPERSEDED** by [`20260805-phase2-handoff.md`](./20260805-phase2-handoff.md)
> (grounded against the actual source after Phase 1 shipped). This speculative
> draft is retained for history only.

**Date:** 2026-08-04 · **Phase:** 2 of 3 · **Duration:** ~3–4 weeks
**Roadmap:** [`20260804-portfolio-roadmap.md`](./20260804-portfolio-roadmap.md) ·
**Map:** [`20260804-portfolio-implementation-map.md`](./20260804-portfolio-implementation-map.md)
**Depends on:** Phase 1 (`FindingCard` schema, populated lake, demo universe)

## Goal

Prove ML engineering judgment and ship a modern AI feature. Produce three more
evidence-backed findings (labeling, model family, enrichment ablation), stand up
the read API the React app will consume, and ship a citation-grounded RAG agent
over SEC filings + news.

## Entry assumptions (from Phase 1 handoff)

- Lake populated (US + macro); `FindingCard` schema frozen; demo universe fixed.
- `PriceForecaster` (v1-direction + v2-meta-label), `PurgedEmbargoedWalkForwardSplitter`,
  triple-barrier labeling, SHAP, and `training_metadata.json`/`training_audit.parquet`
  all already emitted by `ml/forecasting.py`.

## Deliverables (file-level)

Three **parallel** workstreams (independent; can be split across people/PRs).

### 2A — ML rigor + registry (W&B)

| # | Path | | What |
|---|---|---|---|
| 1 | `src/equity_lake/ml/registry.py` | ➕ | **W&B** adapter; log metrics/config/SHAP-as-artifact; local JSON stays source of truth |
| 2 | `src/equity_lake/ml/backends.py` | ➕ | `ModelBackend` protocol + XGBoost + **LightGBM** (D2) behind a config flag |
| 3 | `src/equity_lake/ml/comparison.py` | ➕ | v1-direction vs v2-meta-label OOS + XGB vs LGBM table → FindingCards |
| 4 | `src/equity_lake/ml/ablation.py` | ➕ | enriched vs technical-only sweep (toggle DAG enrichment flags) → FindingCard |
| 5 | `.env.example`, `pyproject.toml` | ✏️ | `WANDB_API_KEY/ENTITY/PROJECT`; `wandb`,`lightgbm` in `ml` group |

### 2B — FastAPI read API

| # | Path | | What |
|---|---|---|---|
| 6 | `src/equity_lake/api/{__init__,main,deps}.py` | ➕ | FastAPI app, snapshot-path deps |
| 7 | `src/equity_lake/api/routers/{signals,predictions,backtests,models,findings}.py` | ➕ | read endpoints over snapshots + `data/findings/` |
| 8 | `src/equity_lake/dashboard/model_explorer.py` | ➕ | Streamlit: calibration, SHAP beeswarm, drift vs last model |
| 9 | `tests/unit/test_import_boundaries.py` | ✏️ | assert `api/` may use `core/`,`storage/` but not `cli/` |
| 10 | `Dockerfile` | ✏️ | add `api` stage |

### 2C — RAG agent over the lake

| # | Path | | What |
|---|---|---|---|
| 11 | `src/equity_lake/agent/{__init__,rag,tools,index,eval}.py` | ➕ | embed+index silver SEC/news; DuckDB tool; tool-using OpenAI agent; 20-Q eval set |
| 12 | `pyproject.toml` | ✏️ | `agent` group: openai embeddings, vector store (`sqlite-vec` recommended) |

## FindingCards produced

| id | axis | question |
|---|---|---|
| `meta-label-vs-direction` | labeling | does v2 meta-labeling beat v1 raw direction on precision / OOS P&L? |
| `xgb-vs-lgbm` | model | XGBoost vs LightGBM — accuracy, calibration, feature-importance agreement |
| `enrichment-ablation` | ablation | do enriched features (sentiment/SEC/analyst) beat technical-only? |

Plus a **public W&B Report** per comparison, linked from the README.

## Recon-driven corrections

See [`20260804-integration-recon.md`](./20260804-integration-recon.md) §B. Phase-2-specific:

- **B4** — `ml/backends.py` exposes `build_estimator(backend, params,
  scale_pos_weight)` and replaces **4** XGBoost sites: `train_model` default fit,
  `_tune_hyperparameters` (in `GridSearchCV`), `forecasting.backtest()`, and
  `validation.run_purged_walk_forward_validation` (last two have hardcoded
  params). Generalize `_build_model_filename`/`_parse_model_path` from literal
  `_xgboost_` to a `{backend}` token (keep a back-compat alias) and generalize
  `_load_model`'s `model._xgb_version` read. **No Platinum schema change**
  (`predict()` output + `validate_predictions` are backend-agnostic).
- **B5** — `ml/comparison.py` reuses `PurgedEmbargoedWalkForwardSplitter.split()`
  directly and fits each backend per fold (the aggregate `run_purged_walk_forward_
  validation` is XGBoost-locked and returns no per-fold rows).
- **B6** — `ml/ablation.py` calls `FeatureEngineer.generate_features` directly
  with `include_macro=False` for the technical-only arm (`run_feature_job` does
  not expose `include_macro`). Expect `_check_feature_skew` warnings when arms
  are cross-scored — warn-only, document it.
- **B8** — add `build_chat_client()`/`build_embedding_client()` factories in
  `ingestion/llm_base.py`; `agent/rag.py` uses them. Do **not** subclass
  `BaseLLMBatchProcessor` (DeepSeek-locked, chat-only, no embeddings).
- **B9** — API routers use thin getters in `api/deps.py` atop
  `duckdb_scan_for`/`read_delta`; `EquityDataDB` only auto-views the 5 price
  tables. Read SEC/news via **path constants** (`SILVER_SEC_EXTRACTIONS_DIR`…).
- **B10** — `fastapi`/`uvicorn` are **core** deps. W&B keys stay **raw**
  (`WANDB_*`, no `EQUITY_` prefix); add an `MlSettings`/`RegistrySettings`
  group for app-level knobs only.
- **B7 dependency** — RAG indexing requires the `silver/`→`02_silver/` fix
  ([`20260804-pre-phase1-hygiene.md`](./20260804-pre-phase1-hygiene.md)); else
  reads via path constants still work but writes diverge.

## Exit criteria + verification

```bash
# ML rigor
uv run equity ml compare --universe demo      # trains v1/v2 x XGB/LGBM, logs to W&B, emits 3 FindingCards
uv run equity ml ablate --universe demo       # enrichment ablation FindingCard
ls data/findings/                             # 6 cards total (3 from P1 + 3 here)
# verify W&B: open the public project URL; runs + a Report exist per comparison

# API
uv run uvicorn equity_lake.api.main:app --reload
curl localhost:8000/findings                  # lists all FindingCards
curl localhost:8000/backtests/<strategy>      # equity curve + metrics
uv run pytest tests/unit -q                   # incl. new api + import-boundary tests

# RAG
uv run python -m equity_lake.agent.eval       # 20-Q eval, prints accuracy + citation rate
```

- RAG gate: ≥ target accuracy **and** citation rate on the 20-Q eval; agent
  refuses-with-citation when no evidence (no ungrounded claims).
- LightGBM uses the **same feature-skew logging** already in `forecasting.py`
  (`_check_feature_skew`) — no new leakage guard needed.

## Risks / gotchas

- **W&B secret** — managed via `dotenvx run --` like existing keys (FRED/OPENAI);
  never commit `WANDB_API_KEY`.
- **RAG hallucination** — force tool-grounded answers; the eval gate is
  non-negotiable before exposing the agent in the UI.
- **Feature skew across model families** — reuse `_check_feature_skew`; the
  ablation must train and score on identical feature frames.
- **FastAPI import boundary** — `api/` is a read surface; keep it out of `cli/`
  and `pipeline.py` to satisfy `test_import_boundaries.py`.

## Handoff to Phase 3

Phase 3 can rely on:
- A **frozen FastAPI contract**: endpoint paths + the `FindingCard` JSON shape do
  not change. Phase 3 only *adds* `risk`/`portfolio`/`chat` routers.
- `data/findings/` containing 6 cards spanning all 6 axes → the React Findings
  page renders them generically.
- A public **W&B project** with Reports, linkable from the portfolio README.
- A working **RAG agent** + eval baseline; Phase 3 wires it into the Chat page.
