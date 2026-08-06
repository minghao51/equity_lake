# Phase 2A Execution Handoff — ML Backend Rigor (stepped, post-review)

**Date:** 2026-08-06 · **Workstream:** 2A (of Phase 2) · **Duration:** ~1.5–2 weeks
**Parent:** [`20260805-phase2-handoff.md`](./20260805-phase2-handoff.md)
(§3 B4/B5/B6, §4 ground-truth API map, §5 2A, §6 FindingCards, §7 obligations,
§8 scars, §9 exit criteria). **Do not re-litigate the parent's locked decisions.**

> **You are a cold-start agent.** Read the parent handoff §1 (orientation) and
> §4 (verified API map with line refs) first, then this doc. This doc refines
> workstream 2A into four sequenced steps and folds in the findings of a
> three-way reviewer pass on Step 1 (contract/correctness, test adequacy,
> cross-cutting discipline). Every issue below is mapped to the step where it is
> resolved — nothing is left implicit.

---

## 0. Decisions locked by this handoff

These resolve the reviewer-identified issues. They supersede any looser wording
in the parent §5 2A row for the `ml/backends.py` surface.

| ID | Decision | Resolved at |
|---|---|---|
| **D1** | **Step 1 seam = the parent's locked surface only:** `ModelBackend` Protocol + XGBoost/LightGBM impls + `build_estimator(backend, params, *, scale_pos_weight=)` + `normalize_params` + `validate_backend` + `backend_of`. **Defer `build_fit_kwargs`/`fit_estimator` (+ their 4 tests) to Step 2** where the real `.fit()` flow proves their shape (the LightGBM 4.7 `eval_set` deprecation, D9, may reshape them). Rationale: AGENTS.md "minimal scope / no new abstractions"; shipping an unvalidated public fit-kwargs API before the call sites exist is YAGNI. | Step 1 closeout |
| **D2** | **`subsample` LightGBM no-op fix:** in `_normalize_lightgbm`, when `subsample < 1.0` is present, inject `subsample_freq=1` (sklearn alias for native `bagging_freq`). Without it LightGBM silently ignores `subsample` (reviewer-verified: bit-identical predictions across `subsample∈{0.1,0.5,1.0}`), which would corrupt the `xgb-vs-lgbm` FindingCard and waste the Step-4 grid. | Step 1 closeout |
| **D3** | **Centralize `scale_pos_weight` on one rule:** `_normalize_*` **strips** any `scale_pos_weight` from `params`; `build_estimator` owns it via the kwarg and injects only when `!= 1.0`. Removes the two-path inconsistency (params-path kept `1.0`, kwarg-path dropped it). | Step 1 closeout |
| **D4** | **`_LGBM_OBJECTIVE_MAP` warns on miss:** unknown objective passes through but emits a `structlog` debug warning (add `logger = structlog.get_logger(__name__)` to the module). Only `binary:logistic→binary` is mapped; all 4 sites use it. | Step 1 closeout |
| **D5** | **Reject the `colsample_bytree`+`feature_fraction` collision** with `ValueError` in `_normalize_*` (today it is silent last-write-wins). | Step 1 closeout |
| **D6** | **Step-1 test hardening:** assert LGBM `scale_pos_weight=1.5` injection; decouple the XGB-default test from the XGBoost version (assert vs a fresh `xgb.XGBClassifier().get_params()["scale_pos_weight"]`); add objective pass-through + collision-rejection + `subsample_freq` injection + `backend_of` positive tests; add a `joblib` round-trip test of `build_estimator` output (`dump→load→backend_of==original`). Parametrize build/`normalize_params` over backends. | Step 1 closeout |
| **D7** | **Commit hygiene:** land the incidental `uv.lock` reformat (uv-0.12 `upload-time` metadata on every package — ~3700 lines, zero version changes) as a separate `chore(deps)` commit **before** `feat(ml)`. Procedure: revert the two `pyproject.toml` lines → `uv lock` → commit `chore(deps)`; re-apply → `uv lock` → commit `feat(ml)` with a small delta. | Step 1 closeout |
| **D8** | **Widen `compute_shap_importance`** (`ml/trainer.py:31`) annotation from `xgb.XGBClassifier` to `ModelBackend` and harden the SHAP list-output branch (LightGBM emits list-of-ndarray; the current `ndim != 2` guard would silently `return None`, dropping SHAP from every LGBM run). | Step 2 |
| **D9** | **Resolve the LightGBM 4.7 `eval_set` deprecation** inside `build_fit_kwargs` (translate `eval_set=[(Xv,yv)]` → `eval_X=Xv, eval_y=yv` for the LGBM branch) — this is why `build_fit_kwargs`/`fit_estimator` are reintroduced in Step 2. | Step 2 |
| **D10** | **Guard `_load_model`** (`forecasting.py:564`) — it reads `model._xgb_version` (XGBoost-internal). Branch on `backend_of(model)`; LGBM models have no `_xgb_version`. | Step 2 |
| **D11** | **Filename `{backend}` token + back-compat alias** in `_build_model_filename`/`_parse_model_path`/`_resolve_model_path` (`forecasting.py:532/535/547`). `DEFAULT_BACKEND == "xgboost"` keeps existing `_xgboost_*.pkl` files loadable. | Step 2 |
| **D12** | **`_check_feature_skew` stays warn-only** (`forecasting.py:710`) — reused unchanged for LightGBM; cross-scored ablation arms will emit skew warnings (expected, document in the ablation FindingCard). | Step 4 (document) |
| **D13** | **`predict()` output is backend-agnostic** — `validate_predictions` (`ml/__init__.py`) only checks `probability∈(0,1)` / `direction∈{up,down}` / non-null → **no Platinum schema change** for the model swap (parent §4). Confirm `LGBMClassifier.predict_proba` returns `(N,2)`. | Step 2 (verify) |

---

## 1. Current state (verified 2026-08-06)

Step 1 is **landed but uncommitted**. Working tree:

```
 M pyproject.toml          # ml group += lightgbm>=4.3.0; mypy override += "lightgbm.*"
 M uv.lock                 # +lightgbm 4.7.0 (plus incidental uv-0.12 reformat — see D7)
?? src/equity_lake/ml/backends.py
?? tests/unit/test_ml_backends.py
```

Green: `ruff check` / `ruff format --check` / `mypy src` (strict) clean;
`pytest tests/unit/test_ml_backends.py` → 14 passed + 1 skipped in base env,
15 passed under `--group ml`; `test_import_boundaries.py` → 19 passed. Env
intact (Phase-1 scar #1 avoided): `lightgbm 4.7.0`, `polars_backtest`, `shap`
all present.

> Step 1 is **not "done" until the closeout checklist (§2) lands.** The current
> tree is the pre-closeout state.

---

## 2. Step 1 — Backend seam (closeout)

**Scope:** `src/equity_lake/ml/backends.py`, `tests/unit/test_ml_backends.py`,
`pyproject.toml`, `uv.lock`.

**Keep (parent §4 B4 locked surface):** `ModelBackend` Protocol,
`validate_backend`, `SUPPORTED_BACKENDS`/`DEFAULT_BACKEND="xgboost"`,
`normalize_params` (+ `_normalize_xgboost`/`_normalize_lightgbm`),
`build_estimator(backend, params=None, *, scale_pos_weight=None)`,
`backend_of`. Lazy `import lightgbm` inside `build_estimator`; `import xgboost`
stays at module top (core dep).

### Step-1 closeout checklist (apply before committing)

1. **D1 — Trim to the locked surface.** Remove `build_fit_kwargs`, `fit_estimator`
   (and `backend_of` stays). Remove their 4 tests. They return in Step 2 (D9).
2. **D2 — subsample.** In `_normalize_lightgbm`, after building the dict, if
   `subsample` is present and `< 1.0`, `normalized.setdefault("subsample_freq", 1)`.
3. **D3 — scale_pos_weight centralization.** `_normalize_xgboost` and
   `_normalize_lightgbm` drop `scale_pos_weight` from `params` unconditionally;
   `build_estimator` is the sole injection point (kwarg, `!= 1.0`).
4. **D4 — objective warn.** Add `import structlog` + module logger; warn on
   unmapped objective in the LGBM path (pass-through preserved).
5. **D5 — collision rejection.** If both `colsample_bytree` and `feature_fraction`
   are keys in `params`, `raise ValueError` in `_normalize_*`.
6. **D6 — tests.** Add/fix per the table; parametrize the build +
   `normalize_params` assertions over `("xgboost","lightgbm")` (LightGBM cases
   behind `pytest.importorskip`); add the `joblib` round-trip of a built
   estimator.
7. **D7 — commit split.** `chore(deps)` (lock reformat) then `feat(ml)`.

**Verification (must stay green):**
```bash
uv run ruff check src/equity_lake/ml/backends.py tests/unit/test_ml_backends.py
uv run ruff format --check src/equity_lake/ml/backends.py tests/unit/test_ml_backends.py
uv run mypy src/equity_lake/ml/backends.py
uv run pytest tests/unit/test_ml_backends.py          # base env: LGBM cases skip
uv run --group ml pytest tests/unit/test_ml_backends.py   # exercises LightGBM
uv run pytest tests/unit/test_import_boundaries.py -q    # ml/ layer not enforced; confirm clean
```

**Out of scope for Step 1:** any change to `forecasting.py`, `validation.py`,
`trainer.py`, or the filename token. No CLI, no `.env.example`, no `wandb`.

---

## 3. Step 2 — Wire the seam into the 4 sites + filename token

**Unblocks:** `comparison.py` (Step 4). **Reuses:** parent §4 (verified line
refs) + Step-1 `build_estimator`/`backend_of`.

**Scope:** `src/equity_lake/ml/forecasting.py`, `src/equity_lake/ml/validation.py`,
`src/equity_lake/ml/trainer.py`; reintroduce `build_fit_kwargs`/`fit_estimator`
in `ml/backends.py` (+ their tests).

### 2.1 Replace the 4 XGBoost construction sites (parent §4 B4)

Add `backend: str = DEFAULT_BACKEND` to `PriceForecaster.__init__` (validate via
`validate_backend`); thread it to all four sites.

1. **`train_model` default fit** (`forecasting.py:243`) →
   `build_estimator(self.backend, default_params, scale_pos_weight=class_counts["scale_pos_weight"])`
   then `fit_estimator(model, X_train, y_train_np, sample_weight=…, eval_set=[(X_val,y_val)] if X_val.height>0 else None, eval_sample_weight=[w] if X_val.height>0 else None, verbose=False)`.
2. **`_tune_hyperparameters` GridSearchCV** (`forecasting.py:333`) →
   `estimator=build_estimator(self.backend, estimator_kwargs, scale_pos_weight=…)`.
   GridSearchCV clones the estimator — no eval kwargs here; the grid keys
   (`max_depth, learning_rate, n_estimators, subsample`) are backend-neutral.
3. **`backtest()` retrain** (`forecasting.py:439`, currently hardcoded) → fold
   the hardcoded params into the canonical dict and call `build_estimator`;
   `fit_estimator(model, X_tr, y_tr_np, verbose=False)`.
4. **`validation.run_purged_walk_forward_validation`** (`validation.py:92`,
   hardcoded) → accept a `backend` arg (default `DEFAULT_BACKEND`), call
   `build_estimator(self.backend, model_kwargs, scale_pos_weight=…)`. Note
   `comparison.py` (Step 4) calls `PurgedEmbargoedWalkForwardSplitter.split()`
   **directly**, not this aggregate (parent §4 B5) — so this site is for the
   existing `_validate_model` path; keep it backend-parametric for consistency.

> **Zero-regression guarantee (reviewer-verified):** the XGBoost path must be
> byte-identical to today. `_normalize_xgboost` is a strict passthrough
> (remaps only `feature_fraction→colsample_bytree`, which no site ships), and
> `build_fit_kwargs` reproduces all four sites' fit-arg patterns exactly.

### 2.2 Filename token + back-compat (D11)

Generalize the literal `_xgboost_` to `{backend}` in `_build_model_filename`,
`_parse_model_path`, the glob in `_resolve_model_path`. **Add a back-compat
alias in the parser** so pre-Phase-2 `_xgboost_*.pkl` files still load.
`DEFAULT_BACKEND == "xgboost"` already preserves the token.

### 2.3 SHAP + load guards (D8, D10, D13)

- **D8:** widen `compute_shap_importance`'s `model` annotation to `ModelBackend`;
  harden the SHAP list-output branch so LightGBM's list-of-ndarray is reduced to
  a 2-D array (class-1 slice) instead of silently returning `None`.
- **D10:** in `_load_model`, guard the `model._xgb_version` read on
  `backend_of(model) == "xgboost"` (LGBM models have no such attr); emit a
  version-mismatch warning only for XGBoost.
- **D13:** verify `LGBMClassifier.predict_proba` returns `(N,2)` so the Platinum
  validator's shape assumption holds (the Step-4 test already asserts `(5,2)`).

### 2.4 Reintroduce `build_fit_kwargs`/`fit_estimator` (D1, D9)

Move them back into `ml/backends.py` now that the call sites prove the shape.
**D9:** inside `build_fit_kwargs`, for the LightGBM branch translate
`eval_set=[(Xv,yv)]` → `eval_X=Xv, eval_y=yv` to clear the 4.7 deprecation
(XGBoost keeps `eval_set`). Re-add the 4 fit-kwargs tests.

**Verification:**
```bash
uv run pytest tests/unit/ml -q            # forecasting/validation tests still green
uv run pytest tests/unit/test_ml_backends.py
uv run mypy src && uv run ruff check .
# smoke: train both backends on one demo ticker, confirm model files load back
dotenvx run -- uv run equity ml train --ticker <demo> --backend xgboost
dotenvx run -- uv run equity ml train --ticker <demo> --backend lightgbm
```

---

## 4. Step 3 — W&B registry (`ml/registry.py`)

**Unblocks:** public W&B Reports linked from the README. **Reuses:** parent §5
2A row + `forecasting._save_training_metadata` (`forecasting.py:579`) and
`compute_shap_importance` (`trainer.py`) for SHAP-as-artifact.

**Scope:** `src/equity_lake/ml/registry.py`, `pyproject.toml` (`ml` group +=
`wandb`; mypy override += `wandb.*`), `.env.example` (raw `WANDB_API_KEY`/
`WANDB_ENTITY`/`WANDB_PROJECT` — **no `EQUITY_` prefix**, parent B3).

**Locked rules (parent §3 B3, §5 2A):**
- Local `*.training_metadata.json` / `*.training_audit.parquet` **stay source of
  truth**; the registry is an *adapter* that logs metrics/config/SHAP-as-artifact
  to W&B. Never make W&B a hard runtime dependency of training.
- Lazy `import wandb`; raw `WANDB_*` keys read via `os.getenv` at the client
  seam, **never** declared in `Settings` (parent §5 Config).
- Missing `WANDB_API_KEY` → log + no-op (training must not fail in CI/local
  without W&B configured).
- Public W&B project + one Report per comparison (parent §6).

**Verification:**
```bash
uv run --group ml pytest tests/unit/ml -q
# with WANDB_API_KEY set: confirm a run + a Report land in the public project
```

---

## 5. Step 4 — `comparison.py` + `ablation.py` + `ml_app` CLI

**Produces:** the 3 new FindingCards (parent §6). **Reuses:** parent §4 B5/B6.

### 5.1 `comparison.py` (B5) → FindingCards `meta-label-vs-direction` + `xgb-vs-lgbm`

Call `PurgedEmbargoedWalkForwardSplitter.split()` (`validation.py:25`) **directly**
to get per-fold OOS rows × `{v1_direction, v2_meta_label}` × `{xgboost, lightgbm}`.
**Do not** reuse the aggregate `run_purged_walk_forward_validation` — it is
XGBoost-locked and returns no per-fold rows (parent §4 B5). Build a per-backend
per-fold table → FindingCards via `findings/`.

### 5.2 `ablation.py` (B6) → FindingCard `enrichment-ablation`

Call `FeatureEngineer.generate_features(..., include_macro=False)` directly
(`engineering.py:89`, L97). Load the engineer via the lazy Hamilton loader
`_load_feature_engineer()` (`features/__init__.py`); **remember `engineer.close()`**.
Expect `_check_feature_skew` warnings when arms are cross-scored (D12 — warn-only,
document in the card).

### 5.3 CLI (parent §5 + change matrix)

New `ml_app` sub-app (declare in `cli/_app.py`, wire in `cli/__main__.py`
**before** importing the command module — parent §5 CLI pattern). Commands:
`equity ml compare --universe demo`, `equity ml ablate --universe demo`. Each:
docstring help, `Annotated[..., typer.Option("--flag", help="…")]`,
`raise typer.Exit(1)` on required failure, and a help-scan test in
`tests/unit/test_cli_unified.py` + a user-guide line.

### 5.4 Backfill guardrail (parent §5, §9 — non-negotiable)

`run_feature_job` needs feature history. Run features for the `demo` universe
first with `--allow-history-backfill` (scoped markets/tickers), confirm
`03_gold/features` is populated **before** any training in Steps 2/4.

**Verification (parent §9):**
```bash
dotenvx run -- uv run equity pipeline --markets us --tickers <demo subset> --allow-history-backfill
uv run equity ml compare  --universe demo   # 2 FindingCards + W&B Report
uv run equity ml ablate   --universe demo   # 1 FindingCard
ls data/findings/                            # 6 cards total (3 from P1 + 3 here)
uv run pytest -q && uv run ruff check . && uv run mypy src
```

---

## 6. Cross-cutting obligations (apply to every step — parent §7)

- **Import boundaries:** `ml/` is **not** in `LAYER_BOUNDARIES` and Step 1 adds
  no reason to add it (no `equity_lake.*` imports in `backends.py`). When you
  extend boundaries later (parent §7 lists `findings/api/agent/portfolio`), leave
  `ml` un-enforced. `forecasting.py → ml.backends` is intra-package.
- **Config:** `Settings(extra="forbid")` — `WANDB_*` stay raw (Step 3). No new
  `EQUITY_` nested fields are introduced by 2A.
- **Change matrix:** new optional dep (`lightgbm` done; `wandb` in Step 3) →
  dep-group + lazy import + mypy override. New CLI (Step 4) → help + CLI test +
  user-guide line. **2A adds no medallion tables** — all outputs are
  FindingCards/snapshots (parent §7).
- **Commits:** conventional (`feat`/`fix`/`refactor`/`docs`/`test`); repo
  identity `minghao <howt51@gmail.com>` set repo-local (parent §7). Observe D7.
- **Phase-1 scars (parent §8):** combine groups when syncing
  (`uv sync --group backtesting --group ml --group viz`); `--group` not
  `--extra`; `ArenaOutcome` has no `.strategies`/`.cost_regimes`; notebook
  plotting wraps `matplotlib` in `try/except ImportError`, no `%matplotlib inline`;
  DuckDB rejects bare `first`/`last` aliases.

---

## 7. Exit criteria (2A done) — parent §9

- `data/findings/` holds **6 cards** (3 from P1 + `meta-label-vs-direction`,
  `xgb-vs-lgbm`, `enrichment-ablation`); negative results recorded honestly.
- A **public W&B project** with runs + one Report per comparison, linked from
  the README.
- `uv run pytest -q` green (incl. new `ml` + boundary tests); `ruff`/`mypy` clean;
  `uv.lock` consistent.
- Both backends (`xgboost`, `lightgbm`) train, persist, and reload end-to-end on
  at least one demo ticker, with pre-Phase-2 `_xgboost_*.pkl` files still loading.

---

## 8. Sequencing summary

| Step | Files | Unblocks | FindingCards |
|---|---|---|---|
| **1 — Seam (closeout)** | `ml/backends.py`, `tests`, `pyproject.toml`, `uv.lock` | Step 2 | — |
| **2 — Wire + token** | `forecasting.py`, `validation.py`, `trainer.py`, `ml/backends.py` | Step 4 | — |
| **3 — W&B registry** | `ml/registry.py`, `pyproject.toml`, `.env.example` | README link, Step 4 logging | — |
| **4 — comparison + ablation + CLI** | `ml/comparison.py`, `ml/ablation.py`, `cli/` | Phase 3 (frozen API + 6 cards) | 3 new |

**Critical path:** 1 → 2 → 4 (3 parallels 2/4 for the logging layer).
