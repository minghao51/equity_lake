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

## Outcome (closed 2026-10-28)

All four waves implemented and committed on local `main`; the tree was clean at
handoff close. Full serial and parallel suites were run sequentially and agree.

| Commit | Scope |
| --- | --- |
| `ee48e1d` | `core/paths.py` operator-artifact path constants (Wave D prerequisite, additive only) |
| `913d004` | Wave A — typed `QueryError` query contract |
| `a012e1c` | Wave B — loud writer fallthrough + market parity guard |
| `74a25a9` | Wave C — config shim exports, stale docstrings, `_metrics` cast |
| `41091fd` | Wave D — shared window helper, card printer, artifact paths, ARCHITECTURE note |

The path constants were split into their own additive commit so every commit is
self-consistent (Wave A's `analysis.py` and Wave D's consumers both need them).
`tests/unit/test_cli_unified.py` carries both Wave A (`TestQueryErrorContract`)
and Wave D (`TestDefaultWindows`) tests, so it landed with Wave D.

### Definition of done

- [x] Bad SQL / unknown query name fail loudly (`QueryError`, CLI exit 1)
- [x] Unknown market key logs a warning (no silent OHLCV validation)
- [x] Parity test green: `MARKET_REGISTRY == VALID_MARKETS == MARKET_DIR_MAP`
      (minus features/predictions)
- [x] Shim `__all__` complete; stale docstrings fixed; **akshare lazy — not applied, see deviations**
- [x] `engineering.py` views derived from the registry; suppressions log
      (both already true on HEAD — see stale recon)
- [x] One `_default_window` helper; per-command defaults unchanged and now pinned
- [x] One FindingCard printer; artifact paths from `core/paths.py` constants
- [x] Full gates green; commits per wave; handoff closed with this Outcome

### Gate evidence

- Serial: **3 failed, 1571 passed, 22 skipped, 95 deselected** — `/tmp/tier0-serial4.{log,xml}`
- Parallel (`-n auto`): **3 failed, 1571 passed, 22 skipped** — `/tmp/tier0-parallel4.{log,xml}`
- The 3 failures are the pre-existing `tests/unit/test_arena.py` set (F28/W07,
  already red at the W03 baseline). Verified identical on clean HEAD by stashing
  this work's `src/` and `tests/` changes, so they are not caused by this batch.
- Ruff clean, `ruff format --check` clean (308 files), `mypy src/equity_lake`
  clean (171 files).
- Focused: DuckDB integration **27 passed**; CLI + writers + market-vocabulary +
  monitor + fetchers **239 passed**; pipeline/dashboard **19 passed**.

### Deviations from the handoff as written

1. **`sources/cn.py` lazy akshare — not applied.** akshare is a declared hard
dependency (`akshare>=1.15.0`), so deferring the import buys nothing at install
or runtime; `tests/unit/test_fetchers.py` patches the module global `cn.ak`, which
an `AK = None` pattern breaks; and akshare ships typed stubs, so a `None`
fallback becomes a use-site type error that only escapes `mypy` via an ignore.
This item remains open only in the narrow sense that the import stays eager.
2. **Wave C items 6–7 were already done on HEAD** (2026-09-10 recon was stale):
`features/engineering.py::_setup_views` already derives from `PRICE_MARKETS`, and
`signals/generators/{backtest,sentiment}.py` already log instead of swallowing.
No change was made; nothing was re-implemented.
3. **`ml/_metrics.py` cast (not in the handoff).** Needed to clear a Pyright
sklearn-stub mismatch: sklearn 1.8 accepts int `0` and rejects `"0"`; the stubs
type the parameter `str`-only. Runtime behaviour is unchanged.

### Independent review findings (read-only reviewer, GLM)

Verdict **READY WITH WARNINGS**, no must-fix. Dispositions:

- **F1 (Medium) — fixed.** `_COMPARISON_LOOKBACK_DAYS` was consumed by `ml_train`
  while compare/ablate took 730d. Verified against HEAD that the *handoff prose*
  was inverted (HEAD: compare/ablate 730d, train 365d). Renamed to
  `_COMPARE_LOOKBACK_DAYS` / `_TRAIN_LOOKBACK_DAYS`, renamed the ml-local helper
  to `_compare_default_window`, corrected the test docstring, and added a wiring
  test that binds each constant to its consuming command.
- **F3 (Low) — fixed.** Two tests now pin `unknown_market_key_ohlcv_default` (and
  its silence for canonical routes) via an isolated recording logger. An initial
  `structlog.testing.capture_logs` version passed alone but failed in the full
  suite: `test_settings_redaction.py` calls `setup_structured_logging()`, whose
  process-wide config defeats `capture_logs`. Verified green in both orders.
- **F5 (Info) — addressed by comment.** `HEALTH_REPORT_PATH` stays exported for
  fixed-path callers while consumers compose `LOGS_DIR / HEALTH_REPORT_FILENAME`
  at call time to preserve the `analysis.LOGS_DIR` test seam.
- **F6 (Info) — fixed.** `QueryError` added to `storage/duckdb.py.__all__`.
  `storage/__init__.py` re-exports were left unchanged (explicit imports work).
- **F8 (Info) — fixed.** `ARCHITECTURE.md` now states the raising contract and
  the empty-lake view behaviour.
- **F4 (Info) — kept, documented.** The `# mypy: disable-error-code` header on
  `tests/integration/test_duckdb_queries.py` is inert for configured mypy
  (`exclude = ["tests/"]`) but still serves editor/LSP type checks; the comment
  now says so rather than implying it gates anything.
- **F2 (Low) — accepted, not fixed.** `run_all_queries()` swallows per-query
exceptions, so `equity query` with no `--query` still exits 0/yellow even if every
  query fails. This asymmetry predates the handoff; changing it would alter the
  document run-all contract. Recorded below as a loose end.
- **F7 (Info) — accepted.** Integration tests set `db.available_views` /
  `db._views_initialized` directly to seed a fixed fixture; acceptable coupling.

### Loose ends (recorded, not scheduled)

- `run_all_queries` still converts per-query failures into empty frames (F2).
  Making the document mode fail loudly is a separate contract decision.
- `cli/bootstrap.py` `MARKET_DIRS` remains a private copy of market paths
  (ADR-0010 acknowledged it) — derive when bootstrap is next touched.
- `catalog/builder.py` builds its own Hamilton driver instead of reusing
  `FeaturePipeline._build_driver` — structural reuse deferred.
- `backtesting/factory.py:59-62` attribute-lookup indirection for test
  monkeypatching — documented; leave.
- The pre-existing `tests/unit/test_arena.py` failures belong to F28/W07; they
  were not touched here and still need their own fix.
