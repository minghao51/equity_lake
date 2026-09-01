# Handoff 08 — P3: ML & backtest integrity (leakage, strategies, metrics)

Priority: P3 (correctness of research outputs — arguably P2 for anything decision-making
consumes). Depends on: 01. Independent of 02–07 except handoff 07's Sharpe-helper item
(owns the semantics here). Suggested dispatch: **2 parallel `worker`s** (A: `ml/` +
`features/`, B: `backtesting/` + `signals/`), then `reviewer` with quant-leakage focus.
Feature-output changes require catalog regen + feature tests.

## Worker A — ml/ + features/

### A1. `PriceForecaster.backtest()` crash ✅ verified
`ml/forecasting.py:464-467`: `int(test_row[target_column])` — the target
(`next_day_return > 0`, produced by DAG `shift(-1)`) is **null on the final features
row**, and the loop always reaches the last index → `TypeError` on every
`equity intelligence forecast --mode backtest` run whose `end` is today.
Fix: drop null-target rows from the backtest frame (mirror `_prepare_training_frame`'s
filter). Test: frame ending on a null-target row backtests without error.

### A2. Purged-CV fallback reintroduces leakage ✅ verified
`ml/forecasting.py:324`: `cv = splitter if splitter.get_n_splits(X_train) > 0 else 2`
silently switches hyperparameter tuning to plain `KFold(2)` when history is short —
exactly when leakage hurts most. Fix: raise a clear error advising a longer window (or
shrink `train_window` deterministically with a warning); never fall back to unpurged CV.

### A3. Backtest/live parity ✅ verified
`ml/forecasting.py:450-459, ~469-471`: `backtest()` fits without `scale_pos_weight` and
thresholds at fixed 0.5, while live `predict()` uses the saved `optimized_threshold` →
`--mode backtest` metrics misrepresent live behavior. Fix: reuse the training param
builder (handoff 07 worker 2's single param dict) and the stored threshold.

### A4. Strict prediction bounds reject valid float32 output ✅ verified
`ml/__init__.py:25-26`: `validate_predictions` uses `gt(0.0)`/`lt(1.0)`; XGBoost
`predict_proba` in float32 can return exactly 0.0/1.0 → one row rejects the whole batch
and predictions silently vanish (`:90-93`). Fix: inclusive `ge`/`le` (consistent with the
rest of the codebase) + log when clipping is applied.

### A5. Enrichment error paths change the feature schema ✅ verified structure
`features/dag/enrichments_04.py:255,334`: on DuckDB/Polars error,
`_merge_news_sentiment`/`_merge_social_sentiment` return frames **without** the default
zero-columns, while sibling merges add `_add_empty_*_columns` on the same paths →
transient errors silently change the feature column set downstream (train/inference
skew). Fix: return the `_add_empty_*` frames there too; add a test asserting constant
column sets across success/error paths.

### A6. `zscore_cross_sectional` bias ✅ verified
`features/engineering.py:233-236`: nulls are imputed **before** computing cross-sectional
mean/std → biased stats for tickers with missing enrichments; docstring (`:176-178`)
claims nulls are "skipped". Fix: compute stats on non-null values, keep imputation only
for the final z expression, fix the docstring. Feature-output change → feature test.

### A7. Ablation alignment
`ml/ablation.py:243-253`: arms aligned by `min(height)` + `.head()` — per-arm null
filters can drop different rows → silently misaligned OOS folds. Fix: align on the `date`
column (assert equal date sets; intersect). 🔎 verify row-alignment behavior first with a
2-arm test.

### A8. Small
- `sentiment/analyzer.py:70,128` — empty-frame guard (`if frame.is_empty(): return frame`)
  before `analyze_batch([])` produces a column-less frame and `KeyError`s.
- `ml/feature_loader.py:52` — `rglob` full list just for existence (see handoff 06; the
  deletion covers it — skip here if 06 landed).
- `features/pipeline.py:100-101` — `duckdb_conn`/dates typed `Any` → real types.
- `features/dag/raw_01.py:43-60` — validator metadata duplicated in `@tag` and
  `@check_output`; keep one source (low risk, do last).

## Worker B — backtesting/ + signals/

### B1. Degenerate one-day holds ✅ verified
- `strategy/trend_following.py:55`: `when(golden_cross_now).then(1.0).otherwise(0.0)` —
  weight nonzero only on the cross day → enters and exits next bar. A "trend following"
  strategy holding one day per cross is wrong, and arena FindingCards for it (and BB
  mean-reversion) rest on these accidental 1-day trades (9 of the default runs).
- `strategy/mean_reversion.py:49`: same pattern for band entries.

Fix semantics: **hold until the opposite signal** (SMA: cross-under; BB: re-entry above
lower band or mid-band touch — pick and document), i.e. forward-fill state between signal
events the way `momentum.py:80-86` already does for rebalances. **Add weight-shape unit
tests**: constructed series with a known cross → assert contiguous nonzero weight blocks,
entry on the bar after the signal (engine executes next close — verified no same-bar
lookahead).

### B2. Sharpe convention unification ✅ verified
`report.py:70-84` computes Sharpe with rf=0; engine metrics use polars-backtest
`daily_sharpe` (rf=0.02, verified empirically). The strat-vs-benchmark verdict threshold
is ±0.1 (`report.py:93-96`) — same order as the rf bias. Fix: one shared metrics helper
with explicit `rf` (default = the engine's convention) used by both; state the rf in card
metadata. (Coordinate with 07 worker 3 — this handoff owns it if both are live.)

### B3. Engine/loader
- `engine.py:84` — fix install message `--extra` → `--group` ✅.
- `engine.py:70` — `BacktestDataLoader()` constructed even when `preloaded_data` given →
  9 arena engines open 9 DuckDB connections and build unused views. Lazy-create.
- `engine.py:119` — `str` stored into `metrics: dict[str, float]` (type lie); use a
  proper warnings list on `BacktestResult`.
- `engine.py:294` — structlog %-style args won't interpolate; use key-value style.
- `data_loader.py:210-214` — `fill_method="bfill"` option introduces lookahead (future
  prices into past rows). Remove the option (keep ffill), or guard it behind an explicit
  research-only flag with a warning. Also `data_loader.py:165` — pandas import for the
  ticker filter; `conn.register` accepts polars directly.

### B4. Signals layer
- `signals/models.py:12-26` + `history.py:31-45` — no Pydantic model at the signals
  write boundary (convention requires one for `data/<name>/` artifacts; `FindingCard` is
  the model citizen). Add a closed `SignalRecord` model (`extra="forbid"`) between
  `Signal` and `save_history`; flatten metadata via an explicit whitelist (today a dict
  (`barrier_settings`) becomes a Delta struct column, and `ml.py` metadata's `confidence`
  key collides with the base column → schema drift + the (post-03) merge path).
- `signals/scanner.py:86,109` — `print()` → structlog (module imports no logger today).
- Generators silently return `None` on any exception (`ml.py:35,58`, `meta_label.py:30,40`,
  `sentiment.py:36,53`) — log a warning with the exception; zero signals should never be
  indistinguishable from a broken generator.
- `signals/config.py:9-10` — CWD-relative default config paths; anchor to project root
  like `core/paths.py`. `cli/commands/intelligence.py:126-130` — catch missing watchlist
  config → clean `typer.Exit(1)`.
- `generators/backtest.py:99-141` — configured strategy names are decorative (every
  strategy runs the same SMA-deviation rule; `min_win_rate` unused). Either wire names to
  `STRATEGY_REGISTRY` or rename the config keys to describe the actual rule (choose with
  owner; update `config/signals.yaml`).

### B5. Arena honesty (document, don't over-engineer)
- `arena.py:26-30` — "realistic" cost regime uses Taiwan-style 0.3% sell tax for all
  markets (wrong for US); make fees/tax per-market defaults (US: 0 tax; CN: stamp duty) —
  config-level change only.
- `arena.py:69-87` — benchmark runs with zero lag/costs vs strategies' one-bar delay;
  note both in card metadata (full mechanics parity is out of scope).
- `cli/commands/arena.py:25` — default mega-cap universe = survivorship bias; append a
  selection-bias caveat to card `scope`.
- `report.py:144,172,206` — card `evidence_refs` point to directories
  `write_arena_artifacts` never writes; align paths.

## Acceptance criteria

- `--mode backtest` completes on current data and matches live threshold/weights semantics.
- No unpurged-CV fallback path exists (test asserts the error).
- Strategy weight-shape tests prove multi-day holds; arena cards regenerate unchanged in
  structure with corrected metrics + caveats.
- Signals history writes through a closed Pydantic model; duplicate-key merges stay
  duplicate-free (relies on handoff 03's merge fix).
- Any feature-output change: catalog regenerated + feature tests updated.

## Validation

```bash
uv run pytest tests/unit tests/integration -k "ml or forecast or backtest or strategy or signal or arena" -q
uv run equity catalog-generate   # if DAG outputs changed
uv run pytest -n auto && uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Outcome (closed 2026-08-31)

- **Landed:** `a46f0fc`.
- Worker A: backtest() drops null-target rows, fits with train parity
  (`scale_pos_weight` + purged-tail optimized threshold, not fixed 0.5);
  KFold(2) unpurged fallback removed (raises); inclusive prediction bounds +
  clip-with-warning; enrichment error paths schema-stable; momentum duplicate
  expression deduped schema-stable (catalog unchanged); z-score stats on
  non-null values; ablation aligned on the shared date set; empty-frame guard;
  real types in features.pipeline; raw_01 single-source validator spec.
- Worker B: trend/mean-reversion hold-until-opposite-signal (+ weight-shape
  tests, next-bar execution asserted); one shared Sharpe helper
  (`backtesting/metrics.py`, rf=0.02) surfaced on cards; engine lazy loader,
  typed warnings, `--group` hint, structlog kv; `bfill` removed; pandas dropped;
  closed `SignalRecord` write boundary (whitelisted metadata, no struct
  columns); scanner structlog; generator failures log; arena per-market cost
  defaults (KRX sell-tax corrected in review), asymmetry + survivorship
  caveats, evidence_refs point at real artifacts.
- FindingCard values will differ from pre-fix runs by design (multi-day holds,
  US 0-tax, rf-consistent benchmark).
