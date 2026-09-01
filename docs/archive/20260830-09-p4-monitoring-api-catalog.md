# Handoff 09 — P4: Monitoring credibility, API, catalog hygiene

Priority: P4. Depends on: 03 (read-error semantics overlap). Suggested dispatch:
**2 parallel `worker`s** (A: monitoring, B: API + catalog + storage odds & ends), then
`reviewer`. Monitoring checks against artifacts nothing produces must be **fixed or
removed**, never left green-by-default.

## Worker A — monitoring/

### A1. False-confidence failure modes 🔎 re-verify line numbers, then fix
- `health.py:384-386` `check_feature_store` returns `True` on **any** exception (a
  corrupt Delta log reads as "features fine"); `check_unstructured_freshness:431-434`
  logs per-table errors at debug and stays `all_fresh=True`. Align with
  `check_data_freshness`/`check_data_quality`: exception → check failed (False) + alert.
- `health.py:107` — `PipelineMonitor` executes `delta_scan(...)` on a raw DuckDB
  connection **without** `INSTALL delta; LOAD delta;` (every other consumer bootstraps —
  9 sites; the shared helper lands in handoff 07). On fresh machines every freshness
  check fails with confusing errors. Use the shared `ensure_delta_extension`.
- `health.py:282-284` — log checks target `monitor_pipeline.log`, `ingest_daily.log`,
  `sync_from_s3.log`; **nothing writes these files** (audit grep; re-verify) → check
  always passes. Either remove the check or point it at real artifacts; also stop reading
  whole files for the last 100 lines and substring-matching "ERROR" against what would be
  structlog JSON.

### A2. Alert fatigue by design
- `health.py:400` — `silver/sec_extractions` freshness uses `max_age_days=2`; SEC
  filings are quarterly → permanent stale alert every run. Per-market/per-table freshness
  expectations (price daily; news daily; SEC quarterly; transcripts monthly) as config
  (`EQUITY_MONITORING__*` nested model + `.env.example`).
- Alerts accumulate per-market per-check with no dedup and are never cleared between
  runs of a long-lived monitor — dedupe by (check, target).
- `CompositeAlerter` always includes `ConsoleAlerter`, and `run_health_check`
  (`health.py:466-500`) *also* prints its own ALERTS banner → every alert prints twice;
  `run_health_check` mixes computation with presentation (violates the "no print in
  library code" spirit). Split: monitor returns data; CLI renders.
- `alerting.py:56-59` — webhook alerts swallow failures with a warning (alert lost; no
  retry). Route through `core/retry.py` (tenacity) and log delivery outcomes; wire
  `build_alerter(webhook_url=...)` to a Settings field or delete (coordinate with
  handoff 06's WebhookAlerter decision — that handoff owns the delete-vs-wire call).

## Worker B — API + catalog + storage odds & ends

### B1. API
- `api/deps.py:48` — `read_delta("04_platinum/predictions")` full-scan per request, then
  sorts in memory and `head(limit)`. Prune by date (predictions are date-partitioned;
  the router already has a date param — pass it through to a partition-filtered read).
- Error mapping: after handoff 03's `read_delta` raises, map storage errors to HTTP 503
  (not 200-`[]`). Add a narrow exception handler in `api/main.py`.
- `api/routers/signals.py:20` — defaults to `date.today()` (machine-local, not
  market-calendar-aware) → empty results on non-trading days; default to the last
  trading date via `core/dates.resolve_trading_date`.
- Document/resolve the 0.0.0.0 exposure: `equity api serve --host 0.0.0.0` publishes
  findings/models/predictions + `/docs` unauthenticated. Minimal fix: warn + confirm when
  host is non-loopback (matches handoff 04's demo-seed guard pattern); no auth machinery.

### B2. Catalog hygiene
- `catalog/datasets.py` — all 15 entries declare `format="parquet"` for **Delta** tables
  (pinned by `tests/unit/test_catalog_datasets.py:67` — update the test). Consumers
  currently survive only because `duckdb_scan_for` auto-detects.
- Build entry paths from `core/paths.py` constants instead of duplicated string literals
  (silent-drift risk on layout changes; coordinates with handoff 05's registry).
- Drift guard: add a unit test that builds the catalog and compares against
  `data/catalog.jsonl` (fails CI when someone hand-edits or DAG changes without
  regenerating). Optionally have `build_catalog` warn when a declared dataset path
  doesn't exist on disk.

### B3. Storage odds & ends (🔎 verify first)
- `storage/duckdb.py:169-171` + `storage/examples.py:123,146` — `run_all_queries` calls
  `query_4_cross_market_comparison(ticker)` / `query_5_moving_averages(ticker, …)` with
  no args → TypeError → caught → "Query failed". Give the demo queries defaults so
  `equity query --all` works end-to-end; while there, make queries 4/5 use `db.query()`
  like their siblings (`:170-179,204-214`).
- `storage/delta.py:54` — `WriteMode` advertises `"ignore"`/`"error"` but only
  append/overwrite are documented/used; trim the Literal or implement.
- `monitoring/health.py` `PipelineMonitor` — no `close()` for its DuckDB connection; add
  context-manager support.

## Acceptance criteria

- A deliberately corrupted Delta table yields unhealthy checks + alerts, not green.
- No check references an artifact nothing produces (grep-provable).
- Fresh-machine simulation (no delta extension installed in a scratch DB) passes
  freshness checks after bootstrap.
- `equity query --all` runs every named query successfully.
- Catalog test pins `format="delta"` and CI fails on catalog drift.
- API returns 503 on storage errors and pruned reads (test with a two-partition table).

## Validation

```bash
uv run pytest tests/unit -k "health or monitor or alert or api or catalog" tests/integration/test_duckdb_queries.py -q
uv run equity monitor --dry-run   # smoke: sane output, no duplicate alerts
uv run pytest -n auto && uv run ruff check . && uv run mypy
```

## Out of scope

Alert routing/retry infrastructure beyond tenacity-on-webhook; auth for the API.

## Outcome (closed 2026-08-31)

- **Landed:** `624b182`.
- Monitoring: failure semantics fixed (corrupt tables now fail + alert);
  monitor bootstraps the delta extension locally (shared-helper consolidation
  remains 07's); phantom log-check removed; per-table/market freshness via
  `EQUITY_MONITORING__*` (SEC quarterly — permanent false alert gone); alerts
  deduped by (check, target); double-print split (monitor returns data, CLI
  renders); webhook delivery retried via tenacity.
- API: predictions partition-pruned by `target_date` (handoff assumed the param
  existed — it was added); signals default to the last trading day;
  non-loopback `serve` guard (warn + confirm, EOF-safe).
- Catalog: all 15 entries `format="delta"`; paths from `core.paths`; CI drift
  test pins `catalog.jsonl` to build output (regenerated); missing-path warning.
- `equity query` run-all fixed (query_4/5 defaults + `db.query`);
  `WriteMode` trimmed; `PipelineMonitor.close()` + context manager; CLI closes
  in `finally`; missing unstructured tables logged as vacuous passes.
