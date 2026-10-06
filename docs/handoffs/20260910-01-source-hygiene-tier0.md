# Handoff 20260910-01 — Source Hygiene Tier 0 (no ADR required)

Priority: **Medium** — zero-risk hygiene from the 2026-09-10 four-scout/two-reviewer
`src/` audit. Every item is a behavior-preserving fix (or an explicit bugfix where
noted); none changes package boundaries, data paths, or schemas, so no ADR is needed
(ADR-0012/0013 cover the structural Tiers 1–2 in handoffs `20260910-02`/`-03`).
Suggested dispatch: Wave A first, then B ∥ C in parallel (disjoint files), then D
(shares `analysis.py`/`pipeline.py` with A/C). Each wave: worker → reviewer → full
gate → commit.

## Verified current state (recon 2026-09-10; re-verify line numbers before editing)

- `storage/duckdb.py` — `query()` (:107-114) catches all exceptions and returns
  `pl.DataFrame()`, making SQL bugs indistinguishable from "no data" at
  `equity query`. `run_named_query` (:117-128) also returns an empty frame for
  unknown query names. `_create_market_view` (:69) has zero production callers;
  only `tests/integration/test_duckdb_queries.py` calls it (it duplicates
  `lake_reader.create_market_views`). House style for typed storage errors
  exists: `DeltaWriteError`/`DeltaMergeError`/`DeltaReadError` in `storage/delta.py`.
- `ingestion/writers.py` — `_dedupe_key_columns` (:16-49) ends with a silent
  `return ["ticker", "date"]` fallthrough; an unknown market key is validated as
  OHLCV and written under `data/lake/<typo>`. `validate_schema` (:152-180) has the
  same dual-key literal tuples.
- `core/config.py` — shim `__all__` (:67-84) omits `S3SyncSettings`
  (`core/settings.py:119`) and `SourcesSettings` (`core/settings.py:129`).
- Docstring lies: root `pipeline.py:1` claims "Hamilton driver builder" (no
  Hamilton code in file); `ml/registry.py:12` references nonexistent
  `equity_lake.config.Settings`; `sources/cn_hybrid.py` class docstring (:37-41)
  says akshare-primary but `fetch()` tries efinance first when enabled, and the
  threshold docstring says 50% while code uses `fallback_threshold=0.3` (:309).
- `sources/cn.py:8` — eager `import akshare as ak` (vs lazy pattern in
  `cn_efinance.py:20-26`).
- `features/engineering.py:53-63` — `_setup_views` hardcodes 5 market tuples with
  deprecated short aliases + literal paths instead of deriving from `PRICE_MARKETS`.
- Silent `suppress(Exception)`: `ml/forecasting.py:119-129` (signals config load),
  `signals/generators/backtest.py:39-41` and `sentiment.py:29-38` (view creation).
- Interface duplication: FindingCard print ×3 (`cli/commands/ml.py:148,191`,
  `arena.py:83`); default windows defined 3 ways (`ml.py:96-99` = 730d,
  `ml.py:217`, `intelligence.py:73,87` = 365d inline); health-report path
  duplicated (`cli/commands/analysis.py:133`, `dashboard/_common.py:86-88`) and
  pipeline-results glob duplicated (`dashboard/exporter.py:107`,
  `cli/commands/pipeline.py:45`) with only comments holding the contract.

## Wave A — Query error contract (typed `QueryError`)

**Files owned exclusively:** `src/equity_lake/storage/duckdb.py`,
`src/equity_lake/cli/commands/analysis.py`, `tests/integration/test_duckdb_queries.py`,
plus any unit test found by `rg -l "\.query\(|run_named_query" tests/` (extend, don't
rewrite others' files).

1. Add `class QueryError(RuntimeError)` to `storage/duckdb.py` (mirror the
   Delta error classes' docstring style).
2. `query()`: on exception, `raise QueryError(str(e)) from e` — no empty frame.
   Keep the `logger.error` line.
3. `run_named_query()`: unknown name → `raise QueryError(f"Unknown query {name!r}; available: ...")`
   (build the available list from `QUERY_MAP` as today's log does).
4. Delete `_create_market_view` (:69). Update `tests/integration/test_duckdb_queries.py`
   to create views via `lake_reader.create_market_views` instead.
5. `cli/commands/analysis.py` `query` command: wrap both the named-query and
   run-all branches in `try/except QueryError` → `typer.secho(error, fg=RED)` +
   `raise typer.Exit(1)`. "No results" (genuinely empty frame) stays a yellow
   warning, not an error.
6. Tests: error contract (bad SQL raises `QueryError`), unknown-name raises,
   CLI exit code 1 on failure, exit 0 + yellow on empty-but-valid.

## Wave B — Loud writer fallthrough + market parity test

**Files owned exclusively:** `src/equity_lake/ingestion/writers.py`,
`tests/unit/test_market_vocabulary.py` (new).

1. `_dedupe_key_columns`: replace the final `return ["ticker", "date"]` with an
   explicit OHLCV membership branch (the 5 price-market keys + their
   `01_bronze/market_data/<market>` path forms + `"features"` +
   gold-features path) and a `logger.warning("unknown_market_key_ohlcv_default",
   market=market)` for anything else. Zero behavior change for known keys.
2. Same treatment for `validate_schema`'s fallthrough branch.
3. New `tests/unit/test_market_vocabulary.py`:
   `set(MARKET_REGISTRY) == set(VALID_MARKETS)` and
   `set(MARKET_DIR_MAP) - {"features", "predictions"} == set(VALID_MARKETS)`
   (imports: `ingestion/router.py`, `ingestion/types.py`). This is the drift
   guard that makes the Tier-1 derivation (ADR-0012) safe.

## Wave C — Cosmetics batch (disjoint from A/B)

**Files owned exclusively:** `src/equity_lake/core/config.py`,
`src/equity_lake/pipeline.py` (docstring only), `src/equity_lake/ml/registry.py`,
`src/equity_lake/sources/{cn,cn_hybrid}.py`, `src/equity_lake/features/engineering.py`,
`src/equity_lake/ml/forecasting.py`, `src/equity_lake/signals/generators/{backtest,sentiment}.py`.

1. `core/config.py`: add `S3SyncSettings`, `SourcesSettings` to imports + `__all__`.
2. Root `pipeline.py:1` docstring → "EOD pipeline orchestrator (ingestion →
   features → ML inference)."
3. `ml/registry.py:12` docstring → `equity_lake.core.settings.Settings`.
4. CN truth-pass: fix `cn_hybrid.py` class + `fetch()` docstrings to match code
   (efinance opt-in-first when enabled → akshare primary → yfinance fallback;
   threshold 0.3). Do not change code behavior.
5. `sources/cn.py:8`: lazy `import akshare` inside the functions that use it,
   mirroring `cn_efinance.py:20-26` (module-level `AK = None` + try/except).
6. `features/engineering.py` `_setup_views`: derive view tuples from
   `PRICE_MARKETS` + `market_dir()` (long keys as view names); keep the change
   internal to the file — first `rg -n '"us"|equity_all' src/equity_lake/features/`
   to confirm no external consumer references the short-alias view names.
7. Narrow silent suppressions (behavior-preserving, log-don't-swallow):
   `forecasting.py:119-129` → explicit `try/except` with
   `logger.warning("candidate_strategies_config_unavailable", error=...)` before
   the default fallback; `signals/generators/backtest.py:39-41` and
   `sentiment.py:29-38` → log view-creation failures at warning.
   (The forecasting import itself is removed structurally in Tier 2, ADR-0013.)

## Wave D — Interface dedup (after A and C; shares `analysis.py`, `pipeline.py`)

**Files owned exclusively:** `src/equity_lake/cli/_app.py`, `src/equity_lake/cli/_display.py`
(new), `src/equity_lake/cli/commands/{ml,intelligence,arena,pipeline}.py`,
`src/equity_lake/core/paths.py`, `src/equity_lake/dashboard/{_common,exporter}.py`,
`src/equity_lake/cli/commands/analysis.py` (monitor section only — Wave A owns query).

1. `core/paths.py`: add `HEALTH_REPORT_PATH = LOGS_DIR / "health-report.json"` and
   `PIPELINE_RESULTS_GLOB = "pipeline_results_*.json"` (+ `__all__` entries).
2. Replace the literals: `analysis.py:133`, `dashboard/_common.py:86-88` (keep the
   multi-location fallback list, use the constant for the logs location),
   `dashboard/exporter.py:107`, and the writer side in `cli/commands/pipeline.py`
   (`rg -n "pipeline_results_" src/` for the write site).
3. `cli/_app.py`: one helper `_default_window(days: int) -> tuple[date, date]`
   returning `(today - timedelta(days=days), today)`. Per-command explicit
   constants — **preserve current values, do not unify defaults**:
   `ml.py` train keeps 730d (`_default_window` now calls the shared helper),
   `ml.py` compare/ablate and `intelligence.py` forecast keep 365d, each as a
   named constant at the top of its command module.
4. New `cli/_display.py` (~15 lines): `print_finding_cards(cards)` with the exact
   current line `f"  [{card.axis}] {card.id}: {card.verdict} — {card.conclusion}"`.
   Replace the 3 copies (`ml.py:148`, `ml.py:191`, `arena.py:83`). No public API.

## Definition of done (whole handoff)

- [ ] Bad SQL / unknown query name fail loudly (`QueryError`, CLI exit 1)
- [ ] Unknown market key logs a warning (no silent OHLCV validation)
- [ ] Parity test green: `MARKET_REGISTRY == VALID_MARKETS == MARKET_DIR_MAP` (minus features/predictions)
- [ ] Shim `__all__` complete; stale docstrings fixed; akshare lazy
- [ ] `engineering.py` views derived from the registry; suppressions log
- [ ] One `_default_window` helper; per-command defaults unchanged (730/365 pinned by tests)
- [ ] One FindingCard printer; artifact paths from `core/paths.py` constants
- [ ] Full gates green; commits per wave; handoff closed with an Outcome section

## Loose ends (recorded, not scheduled)

- `cli/bootstrap.py` `MARKET_DIRS` still a private copy of market paths (ADR-0010
  acknowledged it) — derive when bootstrap is next touched.
- `catalog/builder.py` builds its own Hamilton driver instead of reusing
  `FeaturePipeline._build_driver` — docstring now says so; structural reuse deferred.
- `backtesting/factory.py:59-62` attribute-lookup indirection for test
  monkeypatching — documented; leave.

## Validation

```bash
uv sync
uv run pytest
uv run pytest -n auto
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Outcome (open)
