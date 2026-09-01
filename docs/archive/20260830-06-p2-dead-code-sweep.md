# Handoff 06 — P2: Dead-code sweep (verified zero-reference)

Priority: P2. Depends on: 01 (line numbers shift); run **before** 07 so consolidation
doesn't churn dead code. Suggested dispatch: **one `worker`** (mechanical), then
`reviewer`. Every item below was verified by zero-reference grep across `src/` + `tests/`
on 2026-08-30; re-run the grep before each deletion to confirm nothing landed in between.

## Deletions (src)

- [ ] `src/equity_lake/agent/` — **decision point for the owner**: the package is a
      17-line docstring-only stub describing `agent.index/rag/tools/eval` (none exist).
      Either (a) delete the package + its `LAYER_BOUNDARIES` entry in
      `tests/unit/test_import_boundaries.py` + the `agent` dependency group
      (`sqlite-vec`), or (b) keep if Phase-2C is scheduled — but then add a tracker
      issue. Default recommendation: delete until it exists ("smallest architecture").
- [ ] `ingestion/llm_base.py:40-57` — `OPENROUTER_BASE_URL`, `EMBEDDING_MODEL`,
      `EMBEDDING_DIM`, `build_embedding_client` (+ `__all__` entry). Extracted for a RAG
      feature that doesn't exist (commit `0183256`).
- [ ] `ingestion/gap_detection.py` — `get_latest_date`, `get_coverage_stats`.
- [ ] `ingestion/bronze_silver.py` — `write_silver` (only `tests/unit/test_bronze_silver.py`
      uses it; production path is `_write_silver_generic`). Move the two tests to the
      generic path, then delete.
- [ ] `ingestion/types.py` — unused `Market` Literal alias; legacy unprefixed writer
      alias strings in `writers.py:12-31` (`"macro_indicators"`, `"bronze/raw_articles"`,
      `"silver/processed_articles"`). Note: `MARKET_DIR_MAP` contains `"features"` /
      `"predictions"` keys outside `VALID_MARKETS` — keep (used) but document, or move
      under an explicit `NON_MARKET_TABLE_PATHS` name (align with handoff 05).
- [ ] `ingestion/sources` — `sources/cn_hybrid.py:335-342 get_source_status` (test-only:
      delete accessor + its test assertions).
- [ ] `backtesting/engine.py:238-311` — `optimize()`: no callers, no train/test
      discipline, duplicated kwargs. Delete rather than fix (audit + verification agree).
- [ ] `backtesting/strategy/momentum.py:18` — `volatility_target` param never read.
- [ ] `signals/generators/backtest.py:24` — `min_win_rate` parsed, never used (also
      remove from `config/signals.yaml:11`).
- [ ] `config/signals.yaml:17-18` — `sentiment.sources` ignored by the generator.
- [ ] `config/signals.yaml:39-41` + `signals/models.py:69` — whole `aggregation` block
      (`agreement_boost`, `unanimous_boost`) has no consumer; `SignalScanner` never
      aggregates. Delete config block + model field.
- [ ] `features/engineering.py:81-88` — `FeatureEngineer._date_scalar` (never called;
      distinct from the identically-named `monitoring/health.py` helper).
- [ ] `features/pipeline.py:74` — `DEFAULT_FEATURES` alias.
- [ ] `ml/feature_loader.py:52-60` — unreachable second branch in `_feature_scan`
      (condition contradicts the outer `if`); simplify to
      `any(GOLD_FEATURES_DIR.rglob("*.parquet"))`.
- [ ] `ml/sample_weights.py` — `compute_concurrency_matrix` (O(n²), test-only) and dead
      `if n > 0` at `:56`. Keep the production uniqueness path.
- [ ] `ml/registry.py:87-176` — `log_training_run`: either wire it into
      `_save_training_metadata` (preferred if WANB logging is wanted) or delete; do not
      leave it unwired.
- [ ] `sentiment/analyzer.py` — FinBERT branch (`SentimentMethod.FINBERT`,
      `NotImplementedError` paths, docstring claim). Remove the stub or file an issue to
      implement it; docstring must match reality.
- [ ] `ml/forecasting.py:389` — dead `prediction` variable on the v2 return path.
- [ ] `features/dag/schemas.py:42-52` — `PredictionModel` enforced nowhere (delete, or
      enforce at the platinum write boundary if wanted — owner's call; default delete).
- [ ] `storage/delta.py` — `delta_table_version()`.
- [ ] `storage/duckdb.py` — `query_arrow()`, `execute()`.
- [ ] `storage/examples.py` / `storage/__init__.py` — `benchmark_queries` re-export
      (keep the integration test using it, or move the benchmark into the test).
- [ ] `validation/profiling.py` — `load_profile()`, `ProfileView.read()`; drop the
      `# noqa: F401` profiling re-export in `validation/pipeline.py:20`.
- [ ] `validation/pipeline.py` — `validate_and_fix()` (tests-only; move the test to
      `ValidationPipeline.validate` or delete both).
- [ ] `monitoring/alerting.py` — `WebhookAlerter` unreachable in production (no Settings
      field, no CLI flag): either wire it (`EQUITY_ALERTING__WEBHOOK_URL` nested model +
      `.env.example`) or delete. Owner decision; default wire-it (small).
- [ ] `core/config.py` — unused module `logger`; `get_ticker_config()` (no src callers).
- [ ] `core/config_models.py` — unused selectors: `get_stats`, `list_tickers`,
      `get_market_currency`, `get_all_tickers`, `get_tickers_by_exchange` (root +
      `MarketConfig`), `get_groups`, `get_group_info`; `ValidationConfig`'s
      `required_fields` / `valid_exchanges` / `valid_sectors` / `valid_tags`.
- [ ] `pipeline.py` — unused `tickers` parameter of `_backfill_feature_history`
      (resolved properly in handoff 01; verify gone).
- [ ] `core/paths.py:90-92` — `US_NEWS_DIR` / `US_SOCIAL_SENTIMENT_DIR` /
      `SEC_EXTRACTIONS_DIR` are marked "deprecated" in docstrings yet are the primary
      names at call sites: remove the stale deprecation notes (or finish the rename —
      prefer removing the notes).
- [ ] `dashboard/__init__.py:7-12` — dead `build_dashboard` re-export.
- [ ] `devtools/__init__.py:3-5` — eager re-export of `TestDataGenerator` /
      `generate_trading_dates` (also violates lazy-import: numpy+pandas on package
      import). Make the package empty of imports.
- [ ] `catalog/writer.py:38` — no-op `entry["columns"]` overwrite.

## pyproject cleanups

- [ ] `backtesting` group: `jinja2` unused in src.
- [ ] `sentiment` group: `praw` never imported (`sources/reddit.py` uses raw httpx).
- [ ] `viz` group (plotly, matplotlib): zero imports anywhere → delete the group or file
      an issue if planned.
- [ ] mypy override for `vectorbt.*` references a non-dependency → remove.
- [ ] If `agent/` is deleted: remove `agent` group + `sqlite-vec`.

## Guardrails

- Run the full validation suite after the sweep; run `uv run equity --help` (CLI surface
  unchanged).
- Do not fold any behavior change into this PR — deletions only, except where a test
  must move with a deleted symbol.
- Update `docs/` references if any deleted symbol is documented (grep docs/ too).

## Validation

```bash
uv run pytest -n auto && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Outcome (closed 2026-08-31)

- **Landed:** `f41c886`.
- Owner decisions executed: `agent/` package + boundary entry + `sqlite-vec`
  group deleted; `WebhookAlerter` wired via `EQUITY_ALERTING__WEBHOOK_URL`
  (Settings nested model); `log_training_run` deleted (resurrect from git if
  needed); FinBERT stub removed; `PredictionModel` removed.
- Full deletion list per the brief (each re-verified zero-reference at execute
  time); pyproject: `viz` group, `jinja2`, `praw`, stale mypy overrides removed;
  `uv.lock` regenerated; canonical docs aligned.
- Flags for the owner (not actioned): `seaborn` unused in `ml` group;
  `docs/plans/20260614-*` + `technical_roadmap.md` keep historical references
  (allowed); RAG seeding guide references the deleted Phase-2C agent as future
  consumer.
