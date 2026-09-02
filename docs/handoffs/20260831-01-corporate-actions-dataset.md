# Handoff 20260831-01 — Corporate Actions Dataset (ADR-0011, accepted)

Priority: **High** — correctness bug (phantom split returns in ML labels) +
Month-1 portfolio deliverable (defensible OOS backtest vs SPY total return).
Spec: `docs/decisions/0011-corporate-actions-dataset.md` (**Accepted** 2026-08-31 —
read it first; this handoff operationalizes it into dispatchable waves).
Suggested dispatch: 4 waves — A1 ∥ A2 in parallel (disjoint files), then B1, then C1,
each as worker → reviewer → full gate → commit. Ground rules at the bottom bind every brief.

## Verified current state (recon done 2026-08-31; re-verify line numbers before editing)

- **Prices are raw.** `yf.download(..., auto_adjust=False)` in
  `sources/base.py` (`YFinanceBaseFetcher._download_batch`); `adj_close` is stored
  but projected away by consumers — `backtesting/data_loader.py` selects
  `["ticker","date","open","high","low","close","volume"]`; features/labels
  (`next_day_return`) are computed on raw closes in `features/engineering.py`.
- **Writer seam:** `ingestion/writers.py` — `upsert_dataset(df, market, trading_date, …)`
  is the canonical write path; it dispatches `_dedupe_key_columns(market)`,
  `validate_schema(df, market)` (column-presence), `_quality_data_type(market)`
  → `SCHEMA_REGISTRY` key, then `merge_delta(df, market, key_columns)`.
- **Delta layer:** `storage/delta.py` — `delta_table_path(table) = LAKE_DIR / table`
  (table = lake-relative path like `"01_bronze/macro"`). `merge_delta` creates
  missing tables via `write_delta(partition_by=["date"])` — **hardcoded**; there is
  no `partition_by` passthrough on `merge_delta` yet (needed: `ex_date`).
  `normalize_temporal_columns(df, date_columns=("date",))` only touches a `date`
  column — `ex_date` passes through untouched (fine).
- **Validation:** `validation/schemas.py` — `PointblankSchema` base
  (`_build_validation(df) -> pb.Validate`; `.validate()` raises ValueError listing
  failed steps; empty frames pass) + `SCHEMA_REGISTRY: dict[str, type[PointblankSchema]]`.
- **Paths:** `core/paths.py` — `BRONZE_DIR/SILVER_DIR` + per-dataset constants with
  `__all__` exports; catalog paths are built from these constants via
  `_rel_path()` in `catalog/datasets.py` (catalog must not drift from paths).
- **Catalog:** `catalog/datasets.py` — `DatasetEntry(name, layer, path, description,
  format="delta", columns=_columns_from_list(COLUMNS))`; dtype mapping in
  `_DTYPE_MAP` (`ex_date`→date and `ingested_at`→datetime need adding;
  `value`→float64 and `ticker`/`source`→string already exist). Regenerate with
  `uv run equity catalog-generate`; a CI drift test pins `data/catalog.jsonl`.
- **Readers:** `storage/lake_reader.py` — `read_delta(table)` raises
  `DeltaReadError`; `duckdb_scan_for`; `ensure_delta_extension`/
  `create_market_views` live here. Polars `join_asof` is the intended
  adjustment primitive (ADR-0003: Polars-first).
- **Rate limiting (new):** fetchers carry provider-level `source_name`
  (`YFinanceBaseFetcher` → `"yahoo"`); `throttle()` fires in
  `MarketDataFetcher._wrapped`. Session B's fetcher needs **no** new limiter key.

## Wave A1 — Foundation: schema, storage, validation, writers, catalog

**Worker A1 files (own exclusively):** `core/paths.py`, `core/schemas.py`,
`storage/delta.py`, `validation/schemas.py`, `ingestion/writers.py`,
`catalog/datasets.py`, plus new/extended tests
(`tests/unit/test_corporate_actions_schema.py`, additions to
`tests/unit/test_source_storage_contracts.py` is READ-ONLY — use your own files;
`tests/unit/test_delta_schema.py` and `tests/unit/test_ingestion_writers.py` may be
extended). Do NOT touch `storage/lake_reader.py` (Worker A2 owns it).

1. **Paths** (`core/paths.py`): `BRONZE_CORPORATE_ACTIONS_DIR = BRONZE_DIR / "corporate_actions"`,
   `SILVER_CORPORATE_ACTIONS_DIR = SILVER_DIR / "corporate_actions"` + `__all__`.
   (Markets nest underneath in Session B: `.../corporate_actions/us_equity`.)
2. **Column constants** (`core/schemas.py`):
   `CORPORATE_ACTION_COLUMNS = ["ticker", "ex_date", "action", "value", "source", "ingested_at"]`
   and `CORPORATE_ACTION_TYPES = ("dividend", "split")` + `__all__`.
3. **Storage** (`storage/delta.py`): add `partition_by: list[str] | None = None` to
   `merge_delta`, forwarded to the internal `write_delta` call (default unchanged —
   `["date"]`). This lets the corporate-actions table partition on `ex_date`.
   Add a focused test: create+merge twice with `partition_by=["ex_date"]`,
   `key_columns=["ticker","ex_date","action"]` → idempotent (1 row), correct partition dirs.
4. **Validation** (`validation/schemas.py`): `CorporateActionSchema(PointblankSchema)`
   — not-null on `ticker/ex_date/action/value`; `action` in
   `CORPORATE_ACTION_TYPES`; `value >= 0` overall AND
   `(action != "split") | (value > 0)`; `ex_date <= today`;
   composite uniqueness `~pl.struct("ticker","ex_date","action").is_duplicated()`.
   Register `SCHEMA_REGISTRY["corporate_action"]`. Tests: valid frame passes;
   each violation fails with a message naming the step.
5. **Writers** (`ingestion/writers.py`):
   - `_dedupe_key_columns`: `"01_bronze/corporate_actions"`,
     `"02_silver/corporate_actions"` (and `"corporate_actions"`) →
     `["ticker", "ex_date", "action"]`.
   - `validate_schema`: required-cols branch → `CORPORATE_ACTION_COLUMNS`.
   - `_quality_data_type`: those paths → `"corporate_action"`.
   - `upsert_dataset` gains optional `partition_by: list[str] | None = None`
     passthrough to `merge_delta` (default `None` → delta default; zero change
     for existing callers).
6. **Catalog** (`catalog/datasets.py`): bronze + silver `DatasetEntry` named
   `corporate_actions` at `_rel_path(BRONZE/SILVER_CORPORATE_ACTIONS_DIR)`,
   `columns=_columns_from_list(CORPORATE_ACTION_COLUMNS)`, dtype map additions
   (`ex_date`→date, `ingested_at`→datetime). Then **run
   `uv run equity catalog-generate`** and commit the regenerated
   `data/catalog.jsonl` (drift test must pass).

Acceptance: all new tests pass; `uv run pytest tests/unit/test_delta_schema.py
tests/unit/test_ingestion_writers.py tests/unit/test_corporate_actions_schema.py
-n 4 -q` green; ruff + mypy clean on touched files; drift test green.

## Wave A2 — Adjustment engine (parallel with A1)

**Worker A2 files (own exclusively):** `storage/lake_reader.py` (additive only —
do not modify existing functions) + new `tests/unit/test_price_adjustment.py`.
Pure functions only — no paths/catalog dependencies (the I/O loader lands in B1).

1. `with_price_adjustment(prices: pl.DataFrame, actions: pl.DataFrame, *,
   method: Literal["split_only", "total_return"] = "split_only",
   as_of: date | None = None) -> pl.DataFrame`
   - Validate `method` (ValueError otherwise). Empty `actions` → return `prices`
     unchanged. `as_of` filters actions to `ex_date <= as_of` first.
   - Build a per-ticker **event table** `(ticker, ex_date, factor_step)`:
     splits → `step = value` (ratio, >0); total_return adds dividends →
     `step = 1 - value / prev_close` where `prev_close` is the last close
     **strictly before** `ex_date` (resolve per ticker via `join_asof` backward
     on `ex_date - 1 day` so the ex-date's own close is never used; tickers
     with no prior close get the dividend dropped with a warning).
   - `factor_after` per event = product of `factor_step` of strictly later
     events: sort events asc per ticker → `pl.col("factor_step").reverse().cum_prod().reverse()`
     → `shift(-1).fill(1.0)`.
   - Join prices to events `join_asof(strategy="backward", by="ticker",
     left_on="date", right_on="ex_date")` → each price row takes the last
     event's `factor_after` (default 1.0). **Sort both frames by the as-of key
     within each by-group first** (`sort(["ticker","date"])` /
     `sort(["ticker","ex_date"])`) — join_asof raises otherwise.
   - Multiply `open/high/low/close` (intersection with present columns) by
     `factor_after`. Never touch `volume` or `adj_close`.
2. `factor_snapshot(actions, as_of)` (small helper, optional): factor per ticker
   as of a date — useful for tests and future point-in-time features.
3. **Tests (hand-computed oracles, no network):**
   - 2-for-1 split: closes `100 → 52` across ex-date ⇒ adjusted prior close 50,
     cross-boundary return ≈ +4%, series continuous.
   - Chained splits (2:1 then 3:1): product of ratios applies strictly before
     each ex-date.
   - Dividend `$0.50` with prev close `25.00` ⇒ step `0.98`; rows before ex-date
     scaled; rows after untouched.
   - `as_of` before an event ⇒ event invisible.
   - Unknown method raises; empty actions returns frame equal to input;
     missing OHLC columns tolerated (intersection adjusted).
   - Multi-ticker isolation (factors never leak across tickers).

Acceptance: `uv run pytest tests/unit/test_price_adjustment.py -q` green;
ruff + mypy clean.

## Wave B1 — Ingestion route (after A1 + A2 merge)

**Worker B1 files:** `sources/base.py` (additive: corporate-actions fetch on
`YFinanceBaseFetcher`), `ingestion/router.py` (route entry), `ingestion/types.py`
only if a type/map entry is genuinely needed (check `VALID_MARKETS` semantics —
ADR-0010 long keys), `pipeline.py`/`ingestion/orchestrator.py` (wire the route
into the daily flow where prices are written), plus tests. Coordinate-with note:
`market_data` writers resolve per-market dirs via `core.paths.market_dir`;
corporate actions resolve via the new `*_CORPORATE_ACTIONS_DIR / market` instead —
add a `corporate_actions_dir(market)` helper in `core/paths.py` (small, additive).

1. `YFinanceBaseFetcher.fetch_corporate_actions(tickers, start) -> pl.DataFrame`:
   `Ticker.actions` per ticker (dividends + splits), mapped to
   `CORPORATE_ACTION_COLUMNS` (`source="yahoo"`, `ingested_at=now`), incremental
   from max stored `ex_date` per ticker (read silver table first; empty → full
   history). Reuse `_retry_on_failure` (transient contract applies; the
   provider throttle key `"yahoo"` already covers it).
2. Route: a routable identifier (`corporate_actions`) resolving to
   `corporate_actions_dir(market)` relative path; writes via
   `upsert_dataset(..., partition_by=["ex_date"])`. Session B targets
   `us_equity` only.
3. Bronze→silver: bronze rows land raw; the silver write applies
   `CorporateActionSchema` via the existing quality gate (no LLM step).
4. CLI: extend `equity ingest`/`backfill` market handling **only if** the
   existing dispatch makes it free; otherwise expose
   `equity ingest --datasets corporate_actions` style entry following the
   existing dataset-flag conventions — CLI change ⇒ help text + CLI test +
   user-guide companions (change matrix).
5. Tests: fetch mapping (mock `yf.Ticker`, no network), incremental start-date
   logic, upsert idempotency through `upsert_dataset` with a tmp lake
   (`merge_delta(lake_dir=…)` pattern from `test_delta_schema.py`), route
   resolution.

Acceptance: targeted suites green; a mocked end-to-end
(fetch → upsert → read → adjust) test proves the pipeline.

## Wave C1 — Consumer opt-in (after B1)

**Worker C1 files:** `backtesting/data_loader.py`, `cli/commands/analysis.py`
(backtest command flag), user guide + docs.

1. `--adjust {none, splits, total_return}` (default `none`) on `equity backtest`:
   loads corporate actions (silver) and applies `with_price_adjustment` in the
   loader when enabled. `none` must be byte-identical to today's behavior.
2. Surface the setting in the backtest report (costs/assumptions honesty:
   state the adjustment method used — same spirit as the existing cost disclosure).
3. Feature-side opt-in (ml/feature_loader) is **explicitly deferred** — separate
   decision after the backtest wave proves the data; note it in the PR.
4. Tests: loader parity when `none`; adjusted run differs only around ex-dates;
   CLI help-scan + flag tests.

Acceptance: full unit gate; integration suite; report snapshot shows the
adjustment disclosure.

## Cross-wave rules

- Zero behavior change for existing consumers/datasets except where a wave
  states an opt-in flag (`--adjust`, default off).
- ADR-0010 vocabulary everywhere (long market keys); ticker-config short-key
  coupling stays at the documented pipeline seam.
- New settings would need `EQUITY_*` nested models + `.env.example` same-change
  (none anticipated — do not add config without need).
- Class-attribute insertions near class docstrings: ruff format after every edit
  batch and keep docstrings as the first statement (a prior sweep orphaned 13
  docstrings this way — AST-check if unsure).
- Each wave: worker (targeted tests only, no git) → reviewer (read-only) →
  `uv run pytest tests/unit -n auto` + `ruff check .` + `ruff format --check .` +
  `mypy` → thematic commit. Catalog regen (`uv run equity catalog-generate`)
  accompanies any `catalog/datasets.py` change (Wave A1).
- Waves touch disjoint files; never edit another wave's files — coordinate
  additions through the wave that owns them.

## Definition of done (whole handoff)

- [ ] Corporate actions bronze+silver tables writable/idempotent, pointblank-gated
- [ ] `with_price_adjustment` with hand-computed-oracle tests
- [ ] `us_equity` ingestion route end-to-end (mocked network) + incremental
- [ ] `equity backtest --adjust` opt-in with report disclosure
- [ ] Catalog regenerated, drift test green
- [ ] Docs: ingestion guide section, backtest user-guide flag, STACK row if needed
- [ ] Full gates green; commits per wave; handoff closed with an Outcome section

## Loose ends (recorded, not scheduled)

- CN akshare corporate actions (second source pass, same schema)
- Spin-offs/mergers not modeled by the value schema (documented limitation)
- Feature-side adjustment opt-in (ml/feature_loader) — deferred by design
- `data/update_history` freshness wiring for the new dataset (monitor check)

## Validation

```bash
uv sync --all-groups            # full suite needs schedule/intel groups on fresh clones
uv run equity catalog-generate  # after catalog/datasets.py changes (Wave A1)
uv run pytest tests/unit -n auto && uv run pytest tests/integration -n 4
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Outcome (closed 2026-09-02)

All waves implemented, reviewed inline (worker subagents aborted on rate limits;
edits verified marker-by-marker), full gate green, committed per wave:

| Wave | Commit | Notes |
|---|---|---|
| A1 | `e40dc5b` | Bronze entry renamed `corporate_actions_raw` during review — duplicate cross-layer names broke the name-keyed lineage check |
| A2 | `6fe007f` | Review fixed a real design bug: backward-asof was inverted → forward-asof on `date + 1`; same-day split+dividend events collapsed to a step product |
| B1 | `88684b8` | `equity corporate-actions` (root-app command); writers match per-market table paths by prefix; yfinance split multipliers inverted to lake ratio convention |
| C1 | `8a25153` | `equity backtest --adjust {none,splits,total_return}` with disclosure line; `none` byte-identical to prior behavior; missing tables warn + raw fallback |

Final gate: **984 passed / 1 skipped** (`tests/unit -n auto`), ruff + mypy (162
files) clean. Definition of done: all boxes satisfied except feature-side
adjustment (deferred by design — see Loose ends). Follow-ups unchanged from the
Loose ends list: CN akshare source, spin-offs, feature-loader opt-in, monitor
freshness wiring for the new dataset, and threading `adjust` through
`equity report backtest` if the report surface ever needs it.
