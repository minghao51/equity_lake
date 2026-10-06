# Handoff 20260910-02 — Config Unification & Module Renames, Tier 1 (ADR-0012)

Priority: **High** (onboarding payoff) / risk **low** (mechanical, import-only).
Spec: `docs/decisions/0012-source-tree-hygiene.md` (**Proposed** — do not dispatch
until the owner accepts it; this handoff operationalizes it into waves).
Prerequisite: handoff `20260910-01` (Tier 0) closed — its shim `__all__` fix and
parity test are inputs here.
Suggested dispatch: Wave A → Wave B → Wave C ∥ Wave D → Wave E. Sequential A/B
because both touch `pipeline.py` and `core`-adjacent import lines; C and D are
disjoint; E pins everything last. Each wave: worker → reviewer → full gate → commit.

## Verified current state (recon 2026-09-10; re-verify before editing)

- **Config collision:** `src/equity_lake/config/` = `validators.py` only, **no
  `__init__.py`**; its importers are exactly `cli/commands/admin.py` (3 refs) and
  `tests/unit/test_cli_unified.py`. `equity_lake.core.config` is referenced in 31
  files (30 external + the shim): 16 in `src/` (all `sources/*`, 6 `ingestion/*`,
  `cli/commands/{admin,data,ml}.py`, `dashboard/exporter.py`, `devtools/seed_demo.py`,
  `core/rate_limit.py`, root `pipeline.py`) and 8 test files
  (`test_rate_limit`, `test_cli_unified`, `test_s3_sync`, `test_config_contract`,
  `test_app_settings`, `test_pipeline_orchestrator`, + others via grep).
- **Shim contents (`core/config.py`, 84 lines):** re-exports from
  `config_models` (`TickerMetadata`, `MarketConfig`, `GroupConfig`,
  `ValidationConfig`, `TickerConfigRoot`) and `settings` (`Settings`,
  `get_settings`, `load_settings`), plus the legacy `TickerConfig` wrapper
  (:44; used by ~10 files: root `pipeline.py`, `ingestion/{router,orchestrator,
  backfill,auto_backfill,bronze_silver}.py`, `sources/{cn,cn_hybrid,jpx}.py`) and
  `clear_settings_cache` (:40; tests patch `equity_lake.core.config.get_settings`,
  `core/rate_limit.py:72` imports it lazily — the indirection is load-bearing
  until migrated).
- **pipeline rename blast radius:** importers of `equity_lake.pipeline` =
  `cli/commands/pipeline.py`, `tests/integration/test_pipeline_orchestrator.py`,
  `tests/unit/test_cli_unified.py`. `LAYER_BOUNDARIES["api"]` contains the token
  `"pipeline"` (`tests/unit/test_import_boundaries.py:122`). Docs referencing the
  module: `docs/developer/architecture/{ARCHITECTURE,CONVENTIONS,catalog-subsystem,INTEGRATIONS}.md`
  (+ archive files — do not edit archive).
- **schemas rename blast radius:** `core/schemas.py` imported by 18 src files
  (all `sources/*`, `ingestion/writers.py`, `catalog/datasets.py`,
  `devtools/seed_transcripts.py`, `validation/schemas.py`) + 11 test files.
  `features/dag/schemas.py` imported only within `features/dag/`.
- **ml seam:** `run_prediction_job` + `validate_predictions` live in
  `ml/__init__.py` (133 lines) with function-level imports of
  `features.pipeline.FEATURE_SCHEMA_VERSION` and `storage.delta.merge_delta`;
  `__all__ = ["PriceForecaster", "run_prediction_job", "validate_predictions"]`
  plus a lazy `__getattr__` for `PriceForecaster`.
  `PriceForecaster._prepare_training_frame` (forecasting.py:515-546) is mirrored
  by `comparison.py:57-76`, which hardcodes `DEFAULT_V2_SETTINGS` where the
  forecaster merges `ml_config` overrides — comparison can score different
  labels than training (accepted behavior change, see Wave C).
- **sources/base.py** (486 lines): `MarketDataFetcher` + `TransientError` +
  `standardize_columns` + ticker filtering (`_apply_market_filters` — imported
  by `hk_sg.py:8` — and `resolve_tickers`) + `YFinanceBaseFetcher` (batching,
  `fetch_corporate_actions` stays here per ADR-0011). `MacroFetcher`
  (`sources/macro.py:240-249`) extends nothing; `MacroIndicatorFetcher`
  `_retry_on_failure` (:55-68) swallows all exceptions → `None` (callers:
  `cli/commands/data.py` macro command, `ingestion/router.py:_make_macro_fetcher`).
  CN column maps duplicated: `cn.py:139` vs `cn_efinance.py:96-102`.
- **Boundary check today:** none of `ml, backtesting, signals, sources,
  monitoring, catalog, validation, findings, sentiment, devtools` imports
  `cli` or `dashboard` (verified 2026-09-10) — Wave E's extension passes on arrival.

## Wave A — Config unification (two commits)

**Files owned exclusively:** `src/equity_lake/config/**` (delete),
`src/equity_lake/core/{config.py,config_models.py,settings.py,rate_limit.py}`,
all 16 src + 8 test importers of `core.config` (list above; re-derive with
`rg -l "equity_lake\.core\.config" src/ tests/`), `cli/commands/admin.py`,
 `src/equity_lake/eod_pipeline.py` (post-Wave-B name; if dispatching before B,
  edit `pipeline.py` and carry the rename).

**Commit 1 — remove the colliding package:**
1. `git mv src/equity_lake/config/validators.py src/equity_lake/core/config_validators.py`
   (delete the directory incl. `__pycache__`). Update its two importers:
   `cli/commands/admin.py`, `tests/unit/test_cli_unified.py`.
2. `core/config_validators.py` keeps importing from `equity_lake.core.config`
   (shim still present at this commit).

**Commit 2 — migrate off the shim and delete it:**
3. Mechanical migration per symbol: `Settings`, `get_settings`, `load_settings`,
   `clear_settings_cache` → `equity_lake.core.settings`; `TickerMetadata`,
   `MarketConfig`, `GroupConfig`, `ValidationConfig`, `TickerConfigRoot`,
   `TickerConfig` → `equity_lake.core.config_models`.
4. Move the `TickerConfig` legacy wrapper (core/config.py:44) into
   `config_models.py` verbatim.
5. Update `core/rate_limit.py:72` lazy import; update every test patch target
   (`equity_lake.core.config.get_settings` → `equity_lake.core.settings.get_settings`).
6. Delete `core/config.py`. Extend `test_legacy_module_shims_are_absent`
   (tests/unit/test_import_boundaries.py:71-80) with `"core.config"`.
7. Full-file grep gate: `rg "equity_lake\.core\.config|equity_lake\.config\b" src/ tests/`
   must return nothing.

## Wave B — Renames (ADR-0012 decisions 3-4)

**Files owned exclusively:** `src/equity_lake/pipeline.py`, `src/equity_lake/core/schemas.py`,
`src/equity_lake/features/dag/schemas.py`, the ~29 + few importers (mechanical),
`tests/unit/test_import_boundaries.py`, `docs/developer/architecture/*.md` (non-archive).

1. `git mv src/equity_lake/pipeline.py src/equity_lake/eod_pipeline.py`; update
   importers (`cli/commands/pipeline.py`, both tests) and
   `LAYER_BOUNDARIES["api"]`: `"pipeline"` → `"eod_pipeline"`.
2. `git mv src/equity_lake/core/schemas.py src/equity_lake/core/contracts.py`;
   sed `from equity_lake.core.schemas import` → `from equity_lake.core.contracts import`
   across the 18 src + 11 test importers. Module docstring: "Column-name lists and
   dtype contracts for lake tables; enforcement lives in `validation/schemas.py`
   (pointblank), DAG row models in `features/dag/row_models.py`."
3. `git mv src/equity_lake/features/dag/schemas.py src/equity_lake/features/dag/row_models.py`;
   update importers within `features/dag/` + any tests
   (`rg -l "dag.schemas|dag import schemas" src/ tests/`).
4. `validation/schemas.py` keeps its name; add a one-line cross-reference to
   `core/contracts.py` in its docstring.
5. Docs: update `equity_lake.pipeline` / `core/schemas` references in
   `docs/developer/architecture/{ARCHITECTURE,CONVENTIONS,catalog-subsystem,INTEGRATIONS}.md`
   only (never `docs/archive/`). ARCHITECTURE.md module table gets the new names.

## Wave C — ml extraction (incl. accepted behavior change D6)

**Files owned exclusively:** `src/equity_lake/ml/__init__.py`,
`src/equity_lake/ml/inference.py` (new), `src/equity_lake/ml/frames.py` (new),
`src/equity_lake/ml/forecasting.py`, `src/equity_lake/ml/comparison.py`,
`src/equity_lake/ml/ablation.py`, ml tests (`rg -l "run_prediction_job|prepare_training|comparison" tests/`).

1. `ml/inference.py`: move `run_prediction_job` + `validate_predictions` out of
   `__init__.py` verbatim. `ml/__init__.py` becomes: lazy `PriceForecaster`
   `__getattr__` (unchanged) + `from equity_lake.ml.inference import
   run_prediction_job, validate_predictions` re-export; `__all__` unchanged.
   Importers of `equity_lake.ml import run_prediction_job` keep working
   (verify with `rg -n "run_prediction_job" src/ tests/`).
2. `ml/frames.py`: extract the shared training-frame orchestration from
   `PriceForecaster._prepare_training_frame` (forecasting.py:515-546) as
   `prepare_training_frame(frame, *, mode, v2_settings, candidate_strategies)`
   using `ml/candidates.build_candidate_frame` + `ml/labeling`.
3. **Behavior change (owner-approved):** `comparison.py` (and `ablation.py` if it
   mirrors) now passes the same config-merged `v2_settings` the forecaster uses
   (read from `ml_config` overrides over `DEFAULT_V2_SETTINGS`) instead of
   hardcoded `DEFAULT_V2_SETTINGS`. Where overrides exist, `ml compare` labels
   change to match training — this is the drift bugfix. Update comparison test
   expectations; add a regression test pinning "comparison labels == training
   labels when overrides present".
4. `forecasting.py:41-43`: move the mid-file `# noqa: E402` `DATA_DIR` import up
   with the other imports.

## Wave D — sources/ base split + MacroFetcher conformance

**Files owned exclusively:** `src/equity_lake/sources/{base,ticker_selection,yfinance_base,macro,__init__}.py`
(`ticker_selection.py`, `yfinance_base.py` new), `sources/hk_sg.py` (one import line),
`sources/{cn,cn_efinance}.py` (column-map constant), `src/equity_lake/core/contracts.py`
(shared CN map constant — coordinate with Wave B ordering: after B),
`ingestion/router.py` (`_make_macro_fetcher` only), `cli/commands/data.py` (macro
command error handling), sources tests (`rg -l "MacroFetcher|resolve_tickers|_apply_market_filters" tests/`).

1. Split `base.py`: `ticker_selection.py` gets `resolve_tickers` +
   `_apply_market_filters` (rename to `apply_market_filters` — it is de-facto
   public via `hk_sg.py:8`); `yfinance_base.py` gets `YFinanceBaseFetcher`
   incl. `fetch_corporate_actions` (ADR-0011 keeps it here); `base.py` keeps
   `MarketDataFetcher`, `TransientError`, `standardize_columns`, `_empty_frame`
   (imported by 8+ modules — make it public `empty_frame` while moving, update
   those imports). Update `sources/__init__.py` re-export paths; keep exported
   names identical.
2. `MacroFetcher` extends `MarketDataFetcher`: retry via the base tenacity
   contract (raise `TransientError` subclasses only on transient causes; other
   exceptions propagate), `fetch` returns an empty frame on exhaustion instead
   of `None`. Update `cli/commands/data.py` macro command + `router._make_macro_fetcher`
   to the raise/empty-frame contract; tests updated accordingly.
3. Dedupe the CN column maps into `core/contracts.py`
   (`CN_OHLCV_COLUMN_MAP` superset incl. `成交额 → amount`; `cn_efinance.py` uses
   it directly, `cn.py` uses the OHLCV subset).

## Wave E — Boundary enforcement extension (ADR-0012 decision 5; last)

**Files owned exclusively:** `tests/unit/test_import_boundaries.py`.

1. Extend `LAYER_BOUNDARIES` with:
   `ml, backtesting, signals, sources, monitoring, catalog, validation, findings,
   sentiment, devtools` → `{"cli", "dashboard"}`. Do **not** add `signals` to
   `ml`'s forbidden set — that is ADR-0013 / handoff `20260910-03`.
2. Consider extending the runtime-eviction test beyond `core` only if cheap;
   AST coverage is the contract.

## Definition of done (whole handoff)

- [ ] `equity_lake.config` and `equity_lake.core.config` unimportable; parity grep clean
- [ ] `eod_pipeline`, `core/contracts`, `dag/row_models` in place; docs updated; ARCHITECTURE.md module table current
- [ ] `ml/__init__` owns no write boundary; `frames.py` shared; comparison labels pinned to training labels
- [ ] `sources/base.py` ≤ ~200 lines; MacroFetcher on the standard contract; CN maps single-sourced
- [ ] `LAYER_BOUNDARIES` covers 15 packages; boundary tests green
- [ ] Catalog regeneration check (no dataset changes expected — CI drift test must stay green)
- [ ] Full gates green; commits per wave; handoff closed with an Outcome section

## Loose ends (recorded, not scheduled)

- `monitoring/health.py` (516 lines, one class) — extract `checks.py` only when
  next feature touches it.
- `signals/generators` per-instance `:memory:` duckdb + missing `close()` —
  lifecycle contract worth adding with the scanner next pass.
- `equity backtest` vs `equity report backtest` near-duplicate command bodies —
  consolidate presentation via `backtesting/factory` next time either changes.

## Validation

```bash
uv sync
uv run pytest
uv run pytest -n auto
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run equity catalog-generate && git diff --exit-code data/catalog.jsonl   # no drift expected
```

## Outcome (open)
