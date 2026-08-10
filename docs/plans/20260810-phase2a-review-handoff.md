# Phase 2A Review Handoff — close-out fixes, ops run, and 2B/2C on-ramp

**Date:** 2026-08-10 · **Workstream:** 2A (of Phase 2) · **Status:** code-complete,
pending P1 fixes + operational close-out
**Companion (read first):** [`20260806-phase2a-execution-handoff.md`](./20260806-phase2a-execution-handoff.md)
(the executed spec) and [`20260805-phase2-handoff.md`](./20260805-phase2-handoff.md)
§4 (API map) / §6 (FindingCards) / §7 (change matrix).

> **You are a cold-start agent.** Read `AGENTS.md`, then the two handoffs above,
> then this doc. This doc records (a) what shipped in 2A Steps 1–4, (b) the
> issues a 2026-08-10 review found (file:line + concrete fixes), (c) the ordered
> operational run that materializes the 6 FindingCards + the public W&B project,
> and (d) the on-ramp to Phase 2B/2C. Steps 1–4 code is **landed on `main`**;
> this doc's fixes are **not yet implemented**.

---

## 1. Entry state (verified 2026-08-10, working tree clean)

Six commits on `main` (newest first):

| Hash | Step | Subject |
|---|---|---|
| `f5e6ff2` | 4 | `feat(ml): comparison + ablation harness + ml_app CLI` |
| `a1efb91` | 3 | `feat(ml): W&B registry adapter` |
| `c57e982` | 2 | `feat(ml): wire backend seam into the 4 fit sites + filename token` |
| `5ea2baf` | 1 | `feat(ml): pluggable XGBoost/LightGBM backend seam` |
| `3e7a90b` | — | `chore(deps): reformat uv.lock for uv-0.12 upload-time metadata` |
| `1b41b0c` | — | `docs(plans): Phase 2A execution handoff` |

**Verified green:** `uv run ruff check .` (226 files) · `uv run ruff format --check .`
· `uv run mypy src` (strict, 139 files) · `uv run pytest tests/unit` (EXIT 0; only
the pre-existing EOD-data skip). `.env` now holds real `DEEPSEEK_API_KEY`,
`WANDB_API_KEY`/`WANDB_ENTITY`/`WANDB_PROJECT`, and `FINNHUB_API_KEY`.

### What the spec asked for, and what landed

- **Step 1 (seam):** `ModelBackend` Protocol, `validate_backend`, `normalize_params`
  (+ D2 `subsample_freq`, D3 `scale_pos_weight` centralization, D4 objective warn,
  D5 colsample collision), `build_estimator`, `backend_of` — all in
  `src/equity_lake/ml/backends.py`.
- **Step 2 (wire):** the 4 XGBoost construction sites replaced by
  `build_estimator`+`fit_estimator`; filename `{backend}` token + back-compat
  `_xgboost_` parser; `_load_model` D10 guard; `compute_shap_importance` D8
  widening; D9 LightGBM 4.7 `eval_set`→`eval_X`/`eval_y`.
- **Step 3 (registry):** `ml/registry.py` — `log_training_run` + `log_comparison`,
  lazy `wandb`, raw `WANDB_*` via `os.getenv`, never-raise, no-op without key.
- **Step 4 (harness + CLI):** `ml/comparison.py` (`run_comparison` →
  `meta-label-vs-direction` + `xgb-vs-lgbm`), `ml/ablation.py` (`run_ablation` →
  `enrichment-ablation`), `cli/commands/ml.py` (`equity ml compare/ablate/train`),
  `ml_app` wired in `cli/_app.py` + `cli/__main__.py` (add_typer **before** the
  command-module import), help-scan tests.

`ml/` is intentionally **not** in `LAYER_BOUNDARIES`
(`tests/unit/test_import_boundaries.py:120`); `WANDB_*` are raw (zero hits in
`config/`); XGBoost path is byte-identical (zero-regression holds).

---

## 2. Review findings — implement before declaring 2A done

Every item is grounded in `file:line`. **P1 are blockers for the 2A exit
criteria; P2 are improvements that may be deferred.**

### P1 — FindingCard `scope` omits the ticker (reproducibility)

`comparison.py` and `ablation.py` build cards whose `scope` carries
`backends/modes/windows` but **no ticker** — yet the harness is per-ticker and
`FindingCard.scope` is defined as reproducibility metadata "tickers, window,
costs, seed" (`findings/models.py:39`).

- `src/equity_lake/ml/comparison.py` — `_build_meta_label_card` scope (~L250) and
  `_build_model_card` scope (~L340): add `"tickers": [<the ticker>]`.
- `src/equity_lake/ml/ablation.py` — `_build_ablation_card` scope (~L160): same.
- Thread the ticker through: `run_comparison`/`run_ablation` currently take only
  `features`/`enriched_features` frames. Add a `ticker: str` param (or derive
  from `features["ticker"][0]`) and pass it into both card builders. Update the
  CLI call sites in `cli/commands/ml.py` (`ml_compare` ~L88, `ml_ablate` ~L140)
  to pass `selected`.

### P1 — D8 SHAP LightGBM list-path is untested

`compute_shap_importance` was widened to reduce LightGBM's list-of-ndarray SHAP
output to the class-1 slice (`src/equity_lake/ml/trainer.py:23`), but **no test
exercises it with a LightGBM model** — `test_shap_feature_importance_recorded_when_shap_available`
trains XGBoost only. The silent-failure D8 was meant to prevent is itself
unverified.

- Add a test in `tests/unit/test_ml_backends.py` (or a new
  `tests/unit/test_ml_shap.py`) that builds a LightGBM estimator via
  `build_estimator("lightgbm", …)`, fits it on a small synthetic frame, calls
  `compute_shap_importance(model, X, feature_cols)`, and asserts it returns a
  **non-None** dict (gated behind `pytest.importorskip("lightgbm")` and
  `importorskip("shap")`). This proves the list branch reduces to 2-D instead of
  returning `None`.

### P1 — Change-matrix gap: no user-guide line for `equity ml`

The new CLI has help docstrings ✓ and help-scan tests ✓
(`tests/unit/test_cli_unified.py:100` `TestMlSubcommands`), but AGENTS.md's
change matrix requires a **user-guide line** too.

- `mkdocs.yml` `nav:` "User Guide" has no ML entry (it lists CLI Reference,
  Ingestion, Pipeline, Backtesting, Signals, Dashboard Hosting). Add
  `- ML Rigor: user-guide/ml-rigor.md` (date-prefixed per AGENTS.md if standalone:
  `user-guide/YYYYMMDD-ml-rigor.md`).
- `docs/user-guide/20260406-cli-reference.md` needs an `equity ml` section
  (`compare`/`ablate`/`train`, the `--backend` flag, the backfill prerequisite).
- Author the guide: what each command does, the backfill guardrail, the
  FindingCard outputs, the W&B link, and that `--universe demo` runs the first
  ticker (override with `--ticker`).

### P2 — Redundant double-write in the CLI

`cli/commands/ml.py` `ml_compare` (~L111) and `ml_ablate` (~L160) call
`write_finding_card(card, base=base)` **again** after `run_comparison`/
`run_ablation` already wrote the card internally — done only to obtain the path
for display. It is idempotent but wasteful. Refactor: compute the destination
path via `findings/writer` helpers (or have `run_comparison`/`run_ablation`
return the paths alongside the cards) instead of re-writing.

### P2 — ablation imports comparison's private helpers

`src/equity_lake/ml/ablation.py:18` imports `_DEFAULT_FIT_PARAMS`,
`_aggregate_oos`, `_feature_columns`, `_scale_pos_weight` from `comparison.py`
(underscore-prefixed = private). Intra-package, but it couples the modules via a
private API. Promote these to a shared `src/equity_lake/ml/_metrics.py` (or
`_fold_scoring.py`) and import from both.

### P2 — LightGBM `subsample` silently ignored during `--tune`

In `forecasting.py` `_tune_hyperparameters`, `GridSearchCV` sets `subsample` on
each clone via `set_params`, which **bypasses** `normalize_params`' `subsample_freq`
injection — so LightGBM bagging stays off (`bagging_freq=0`) during tuning.
**Narrow blast radius:** affects only `equity ml train --tune --backend lightgbm`;
the comparison cards are unaffected (they use `build_estimator` directly, which
does inject `subsample_freq`). Fix: when `self.backend == "lightgbm"`, add
`"subsample_freq": 1` to `estimator_kwargs` (XGBoost has no such param, so branch
on backend). Or, at minimum, document the caveat in the ML user guide.

### P2 — Two training entrypoints

`equity ml train` (new) overlaps `equity intelligence forecast --mode train`
(`cli/commands/intelligence.py:68`). Pick one canonical path (recommend `equity ml
train` since it surfaces `--backend`) and either deprecate the `intelligence
forecast --mode train` path or cross-link the two in help text + the user guide.

---

## 3. Operational close-out of 2A (the `.env` keys unblock this)

`data/findings/` currently holds **3** Phase-1 cards (`cost-regime`,
`strategy-comparison`, `vs-benchmark`); the 3 ML cards are produced on demand
but `03_gold/features` is **empty**, so they cannot materialize yet. Run, in
order:

```bash
# 0. keep the full env intact (Phase-1 scar #1: --group X alone removes others)
uv sync --group backtesting --group ml --group viz

# 1. backfill feature history for a scoped demo subset (AGENTS.md guardrail)
dotenvx run -- uv run equity pipeline --markets us \
    --tickers AAPL,MSFT,GOOGL,AMZN,META,NVDA --allow-history-backfill
#    confirm 03_gold/features is populated before proceeding

# 2. materialize the 3 ML FindingCards (W&B run + Report land live via WANDB_API_KEY)
dotenvx run -- uv run equity ml compare  --universe demo      # -> meta-label-vs-direction, xgb-vs-lgbm
dotenvx run -- uv run equity ml ablate   --universe demo      # -> enrichment-ablation
ls data/findings/   # expect 6 cards total

# 3. publish the W&B project link in the README (public project + one Report per comparison)
```

**Caveats to expect:**
- `equity ml compare --universe demo` runs the **first** ticker of the group
  (`AAPL`); override with `--ticker`. For statistically defensible verdicts, run
  across more of the universe and merge — or explicitly scope each card as
  per-ticker (the P1 `tickers`-in-scope fix makes this honest).
- Record **honest** verdicts. Single-ticker OOS on synthetic/short windows can
  flip a verdict; the card's `scope` (with tickers + windows) is the contract.
- If W&B logging fails, `ml/registry.py` returns `None` and the FindingCards are
  still written locally — W&B is best-effort, never a hard dep.

---

## 4. Exit criteria for 2A (parent §7 / §9)

- [ ] P1 fixes from §2 landed (`scope.tickes`, LGBM SHAP test, ML user guide).
- [ ] `data/findings/` holds **6** cards; negative results recorded honestly.
- [ ] A public W&B project with runs + one Report per comparison, linked from README.
- [ ] `uv run pytest -q` green (incl. the new SHAP test); `ruff`/`mypy` clean;
      `uv.lock` consistent.
- [ ] Both backends train, persist, reload end-to-end on ≥1 demo ticker;
      pre-Phase-2 `_xgboost_*.pkl` files still load (already verified in Step 2).

---

## 5. On-ramp to Phase 2B/2C (parent `20260805` §5)

Once 2A is closed, Phase 2 continues on two **independent, parallelizable**
tracks (2A's spine is done):

- **2B — FastAPI read API:** `src/equity_lake/api/{__init__,main,deps}.py` +
  `routers/{signals,predictions,backtests,models,findings}.py`; `deps.py` thin
  getters over `duckdb_scan_for` / `read_delta` / `EquityDataDB.query` /
  `load_finding_cards`. `fastapi`/`uvicorn` are **core** deps (B10); add a
  `Dockerfile` `api` stage. Change matrix: new top-level package → extend
  `LAYER_BOUNDARIES` (`api` may use core/storage/findings, **not** cli/pipeline);
  lazy-import `fastapi`.
- **2C — RAG agent over the lake:** `src/equity_lake/agent/{rag,tools,index,eval}.py`;
  reuse `build_chat_client`/`build_embedding_client` (B8 — to be added to
  `ingestion/llm_base.py`), `duckdb_scan_for`, `SILVER_SEC_EXTRACTIONS_DIR`/
  `SILVER_PROCESSED_ARTICLES_DIR`. `agent` group: `openai` + `sqlite-vec`. The
  RAG eval (≥ target accuracy **and** citation rate; refuses-with-citation when
  no evidence) is **non-negotiable** before UI exposure.

Both add new top-level packages → extend `LAYER_BOUNDARIES`
(`tests/unit/test_import_boundaries.py:120`) and add mypy
`ignore_missing_imports` overrides for `fastapi`/`openai`/`sqlite_vec`.

---

## 6. Phase-1 scars to keep avoiding (parent §8)

1. `uv sync --group X` **removes** other groups' packages — always combine:
   `uv sync --group backtesting --group ml --group viz`.
2. Backtesting is a `[dependency-groups]` entry → `--group backtesting`, never
   `--extra backtesting`.
3. `ArenaOutcome` has `runs/data/benchmark/initial_cash` — no `.strategies`/
   `.cost_regimes`.
4. Notebook plotting wraps `matplotlib` in `try/except ImportError`; no
   `%matplotlib inline`; silence structlog in memo cells.
5. DuckDB rejects bare `first`/`last` aliases — use `AS first_day`.

---

## 7. Suggested sequencing for the next thread

1. **§2 P1 fixes** (small, ~half a day): scope-tickers, LGBM SHAP test, ML user
   guide + mkdocs nav + CLI-reference section. Re-run the full gate.
2. **§3 ops run** (backfill → compare/ablate → README link). Iterate on
   `--ticker`/universe breadth until the 6 cards are defensible.
3. **§4 exit-criteria checklist** sign-off.
4. Branch into **2B** and/or **2C** (§5).
