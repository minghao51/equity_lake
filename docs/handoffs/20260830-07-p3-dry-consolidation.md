# Handoff 07 — P3: DRY / consolidation

Priority: P3. Depends on: 05 (market registry), 06 (dead code gone first — several items
shrink). Suggested dispatch: **3 parallel `worker`s** on disjoint module sets, then
`reviewer`. Guiding rule: smallest architecture, no new abstractions — each item below
picks an existing home rather than creating one.

## Worker 1 — ingestion/sources + storage (data-plane dedup)

- [ ] **Adopt `core/polars_utils.ensure_columns`** in the ~9 fetchers that hand-roll the
      "for col in X_COLUMNS: pl.lit(None)" fill block: `news.py:131-140`,
      `sentiment.py:97-99`, `rss.py:106-108`, `reddit.py:99-101`, `stocktwits.py:80-82`,
      `transcripts.py:100-102`, `sec_fulltext.py:112-114`, `sec_financials.py:68-70`
      (`analyst_ratings.py:79` already does it right — copy that).
- [ ] **Delta-extension bootstrap helper**: `INSTALL delta; LOAD delta` appears at 9
      sites (`storage/duckdb.py:65`, `ingestion/orchestrator.py:66,125`,
      `ingestion/bronze_silver.py:57,172`, `ingestion/gap_detection.py:35`,
      `backtesting/data_loader.py:48`, `ml/feature_loader.py:23`,
      `features/engineering.py:58`) and is **missing** where it's needed
      (`monitoring/health.py:107` — see handoff 09). Add
      `ensure_delta_extension(con)` (and a shared `create_market_views(con)`) next to
      `duckdb_scan_for` in `storage/lake_reader.py`; replace all sites.
- [ ] **DuckDB view setup dedup**: `storage/duckdb.py:_setup_views` vs
      `backtesting/data_loader.py:_setup_views` duplicate per-market view creation over
      the same `core.paths` constants; route both through the new shared helper.
- [ ] **`orchestrator.py` market taxonomy**: the 6-market explicit-tickers tuple is
      duplicated in parallel (`:192-199`) and serial (`:256-263`) branches → module
      constant; `pipeline.py:111-112` `unstructured_markets`/`sec_markets` restates the
      same taxonomy → move beside `REQUIRED_PRICE_MARKETS`/`OPTIONAL_ENRICHMENT_MARKETS`
      in `ingestion/types.py`.
- [ ] **Router**: extract `_require_finnhub_key()` / `_default_us_tickers()` used by the
      four Finnhub factories (`ingestion/router.py:150-253`); replace `globals()[factory_name]`
      dispatch at `:414` with a `MARKET_REGISTRY: dict[str, Callable]` (stringly dispatch
      fails silently on typos).

## Worker 2 — ml/features (model-plane dedup)

- [ ] **One default XGBoost param dict** — currently four copies:
      `ml/forecasting.py:203-214` (`default_params`), `ml/forecasting.py:450-458`
      (`backtest_params`), `ml/validation.py:75-83` (`model_kwargs`), `ml/_metrics.py:26-37`
      (`DEFAULT_FIT_PARAMS`). Home: `ml/backends.py` (the declared backend seam); callers
      override explicitly.
- [ ] **One `scale_pos_weight` implementation** — `ml/trainer.py:14-27`,
      `ml/_metrics.py:52-58`, inline `ml/validation.py:82`. Keep `_metrics`'.
- [ ] **One non-feature-column registry** — `features/engineering.py:32-43`
      (`NON_NUMERIC_FOR_ZSCORE`), `ml/forecasting.py:47-62` (`NON_FEATURE_COLUMNS`),
      `ml/_metrics.py:19-26` (`EXCLUDE_COLUMNS`) must stay in sync by hand today. Define
      once (suggest `features/dag/schemas.py` next to the feature schema) and derive.
- [ ] **Single `FEATURE_SCHEMA_VERSION`** — restated in `features/pipeline.py:19`,
      `dag/schemas.py:51`, `ml/__init__.py:72`. One constant, others import it.
- [ ] **Duplicated `diff(5)` momentum feature** (bug-adjacent, verified):
      `features/dag/enrichments_04.py:313` (`social_sentiment_momentum`) and `:564`
      (`social_sentiment_momentum_5d`) are **identical expressions** → perfectly
      correlated duplicate features fed to models. Compute once; if two names are needed
      for catalog stability, keep one as an alias of the other and note it. **This one
      changes feature output — regenerate the catalog and add a feature test.**
- [ ] `features/__init__.py:21-32` vs `:90-95` — `_load_feature_engineer()` and
      `__getattr__("FeatureEngineer")` duplicate the same guarded import; one delegates.
- [ ] `ml/comparison.py:18-19` — stale docstring claims the validation runner is
      XGBoost-locked and returns no per-fold rows; fix the docstring.

## Worker 3 — cli/dashboard/backtesting/signals (surface dedup)

- [ ] **One synthetic-data seeding module**: three OHLCV generators, three business-day
      generators, three curated ticker lists — `cli/bootstrap.py:95-168`,
      `devtools/seed_demo.py:31-121`, `devtools/test_data.py:51-320`. Consolidate into a
      polars-based `devtools/seeding.py`; `bootstrap sample` and `demo seed` share it.
      (After handoff 04 has redirected their outputs; delete `test_data.py` if it is now
      fully subsumed — coordinate with its owner decision in 04 item 2.)
- [ ] **Shared backtest command helper**: `cli/commands/analysis.py:15-44` vs
      `cli/commands/arena.py:89-131` duplicate strategy-registry validation + engine
      construction → a small factory in `backtesting/`.
- [ ] **Dashboard data loading**: `dashboard/streamlit_app.py:27-42` (pandas, head(50))
      vs `dashboard/exporter.py:101-127` (polars, head(20)) duplicate update-history
      loading with different engines/limits → one polars implementation in
      `dashboard/_common.py` (the file exists for exactly this).
- [ ] **One Sharpe/return metrics helper** (correctness-adjacent): `engine._compute_metrics`
      (`engine.py:126-146`, polars-backtest `daily_sharpe`, rf=0.02) vs
      `report._series_metrics` (`report.py:70-84`, rf=0, √252 annualization) — the rf
      mismatch biases FindingCard verdicts beyond the ±0.1 threshold. Promote a single
      equity-curve metrics helper with explicit `rf` + annualization parameters; both
      callers use it. **Detailed spec lives in handoff 08 item 4 — implement it there,
      not here, if 08 hasn't landed yet** (avoid duplicate work; 08 owns the semantics).
- [ ] `cli/commands/ml.py:73-83,121-131` — `ml compare`/`ml ablate` duplicate a 7-option
      signature + preamble → shared `Annotated` type aliases + helper.
- [ ] `signals/generators/ml.py:79-112` — BUY/SELL `Signal` constructions are
      copy-paste; one construction with a branched reasoning string.
- [ ] `signals/formatters/markdown.py:23-32` vs `terminal.py:33-46` — duplicated
      group-by-action summary → shared `summarize(signals)`.
- [ ] `signals/scanner.py:55-58` — enablement checked twice (`__init__` gate +
      per-`generate()` `is_enabled()`); keep one.

## Cross-worker rules

- No behavior change except the momentum-feature dedup (catalog regen + test required).
- Each worker runs the full suite; the reviewer checks no item was implemented twice
  (especially the Sharpe helper overlap with 08).
- Change matrix: DAG/feature change → Hamilton tags + catalog regen + feature tests;
  CLI change → help text + CLI test + user guide.

## Validation

```bash
uv run equity catalog-generate   # only if feature DAG changed (worker 2)
uv run pytest -n auto && uv run ruff check . && uv run ruff format --check . && uv run mypy
```
