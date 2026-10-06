# Handoff 20260910-03 — ml↔signals Cycle Break, Tier 2 (ADR-0013)

Priority: **Medium** — small diff, high leverage: removes the repo's only
cross-package cycle and its silent config swallow, and pins the direction in CI.
Spec: `docs/decisions/0013-ml-signals-dependency-direction.md` (**Proposed** —
do not dispatch until the owner accepts it).
Prerequisites: handoffs `20260910-01` (Tier 0) and `20260910-02` (Tier 1,
specifically Wave E's boundary extension) closed.
Suggested dispatch: one wave (W1), optional W2; worker → reviewer → full gate → commit.

## Verified current state (recon 2026-09-10; re-verify before editing)

- The cycle: `signals/generators/ml.py:9` imports `PriceForecaster` at module
  level and the generator calls `forecaster.predict(ticker=..., date=...)`
  **on demand** in `generate()` (per ticker per scan — freshness does not depend
  on the pipeline's ML stage). Reverse edge: `ml/forecasting.py:119-129`
  `_load_candidate_strategies` imports `equity_lake.signals.config.load_signal_config`
  inside `contextlib.suppress(Exception)` — any YAML/parse/import failure silently
  yields `[DEFAULT_BACKTEST_STRATEGY]` with no log.
- Resolution order in `_load_candidate_strategies` today: (1)
  `ml_config["candidate_strategies"]` if present; (2) signals.yaml
  `backtest.strategies` via the silent import; (3) `DEFAULT_BACKTEST_STRATEGY`.
- `PriceForecaster` constructors that reach tier-2 code paths:
  `ml/inference.py::run_prediction_job` (post-Tier-1 home; `PriceForecaster(model_dir=...)`,
  empty `ml_config`), `signals/generators/ml.py` (config comes from the scan's
  signal config), and the CLI train path (`cli/commands/intelligence.py`
  forecast-train / `cli/commands/ml.py` train).
- Enforcement: post-Tier-1 `LAYER_BOUNDARIES` has
  `ml: {"cli", "dashboard"}` — this handoff adds `"signals"`.

## Wave W1 — One-way dependency + explicit injection

**Files owned exclusively:** `src/equity_lake/ml/forecasting.py`,
`src/equity_lake/eod_pipeline.py` (post-Tier-1 name), `src/equity_lake/ml/inference.py`
(post-Tier-1 home of `run_prediction_job`), `src/equity_lake/cli/commands/intelligence.py`,
`src/equity_lake/cli/commands/ml.py`, `tests/unit/test_import_boundaries.py`,
ml/signals tests (`rg -l "candidate_strategies|load_signal_config|_load_candidate" tests/`).

1. `PriceForecaster.__init__` gains `candidate_strategies: list[dict[str, Any]] | None = None`.
   `_load_candidate_strategies` becomes import-free, resolution order:
   (a) explicit constructor arg; (b) `ml_config["candidate_strategies"]`;
   (c) `DEFAULT_BACKTEST_STRATEGY` — and when neither (a) nor (b) supplied, log
   once at info: `candidate_strategies_defaulted` (no more silent path).
   Delete the `signals.config` import and its `contextlib.suppress`.
2. Callers that relied on the implicit signals.yaml pickup now pass it
   explicitly — **behavior preservation requirement**. Note the layering rule:
   `ml` may not import `signals`, so every signals.yaml read happens at the
   caller layer, which then threads the result into `ml`:
   - `eod_pipeline._run_ml_stage` (root pipeline may import signals freely):
     load `load_signal_config().backtest.get("strategies")` with an explicit
     try/except (warning log, empty list) and pass it through
     `run_prediction_job(..., candidate_strategies=...)`. `run_prediction_job`
     (`ml/inference.py`) gains a `candidate_strategies: list[dict] | None = None`
     parameter and forwards it to the forecaster; it performs no signals import
     itself.
   - CLI train path (`intelligence.py` forecast-train, `ml.py` train): same
     pattern via a tiny shared helper in `cli/` (e.g.
     `_load_candidate_strategies()` in `cli/_app.py` next to the other shared
     parse helpers).
3. `signals/generators/ml.py`: **unchanged** (module-level import stays; on-demand
   predict semantics stay). Its `ml_config` already flows from signal config.
4. Enforcement: `LAYER_BOUNDARIES["ml"]` → `{"cli", "dashboard", "signals"}`.
   Verify with the boundary test suite (AST check now fails any regression).
5. Tests:
   - Pin behavior preservation: forecaster with strategies.yaml present (passed
     explicitly) trains on the same candidates as the old implicit path.
   - Pin the new default path: no arg + no ml_config → info log + `DEFAULT_BACKTEST_STRATEGY`.
   - Boundary test asserts `ml` cannot import `equity_lake.signals` (new case).
   - Existing signals generator tests must stay green untouched.
6. Docs: `docs/developer/architecture/ARCHITECTURE.md` — dependency note:
   "`signals` → `ml` (one-way, enforced); `ml` receives candidate strategies via
   explicit injection from callers."

## Wave W2 (optional, behavior-visible) — "momentum" disambiguation

Only if the owner opts in; it renames a strategy *label*, affecting FindingCard
ids/comparison output. Files: `ml/candidates.py`, `config/signals.yaml`,
`ml/{comparison,ablation}.py` card ids, `signals/generators/backtest.py` config
key docs, affected tests.

1. `DEFAULT_BACKTEST_STRATEGY["name"]`: `"momentum"` → `"sma_deviation_20d"`
   (matching the signals.yaml key). The cross-sectional arena strategy keeps
   `"momentum"` (`backtesting/strategy/momentum.py` registry key — that is the
   one true momentum).
2. Update comparison/ablation expected card ids + any docs mentioning the v2
   default strategy name.

## Parked (explicit triggers, do not do speculatively)

| Item | Trigger |
|---|---|
| `sources/` sub-packages (`markets/`, `enrichment/`) | 6th price market or 4th provider arrives |
| `forecasting.py` split (`training`/`predict`/`model_store`) | File actively hurts: >1000 lines or 3 parallel changes to distinct concerns |
| `monitoring/health.py` → `checks.py` | Next feature touching health checks |
| Platinum-decoupled signal source (`PredictionSource` protocol) | Scan cost/freshness requirements change; requires its own ADR superseding the injection seam |
| `catalog/builder.py` driver reuse | Next change to `FeaturePipeline._build_driver` config |

## Definition of done

- [ ] `rg "signals" src/equity_lake/ml/` returns nothing (no import, no lazy import)
- [ ] Boundary test enforces `ml` ⊬ `signals`; suite green
- [ ] Behavior-preservation tests green (explicit == old implicit resolution)
- [ ] Silent fallback gone: defaulting is logged
- [ ] ARCHITECTURE.md dependency note updated
- [ ] Full gates green; commit; handoff closed with an Outcome section

## Validation

```bash
uv sync
uv run pytest
uv run pytest -n auto
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Outcome (open)
