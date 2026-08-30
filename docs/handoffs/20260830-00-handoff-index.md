# Handoff Index — src/ Audit Remediation (2026-08-30)

Provenance: full-tree audit of `src/equity_lake/` (157 files, ~22.2K lines) performed by 7
parallel review agents (per-module + cross-cutting sweep). Key findings were then
**re-verified by hand** (file reads, greps, and test runs) before writing these handoffs.
Line numbers refer to the working tree as of 2026-08-30 (HEAD `203e9e4`).

## ⚠️ Read first: working-tree state

The tree contains a **large uncommitted refactor** (33 modified files under `src/`,
+548/−1338) on top of the landed `b0fe5a5` / `203e9e4` phases. Three integration tests
currently fail. **Do not start any other workstream before [01-P0](20260830-01-p0-reconcile-working-tree.md)
reconciles the tree.** All handoffs assume HEAD includes 01's fixes.

## Verification ledger

Status: ✅ = re-verified by hand on this date · 🔎 = audit-reported, spot-checked or
plausible, verify before fixing (each handoff says which).

| # | Finding | Status |
|---|---------|--------|
| 1 | `pipeline.py` reads top-level `bronze_to_silver`/`sec_to_silver`; sub-stages live under `results["ingestion"]` → enrichments silently always disabled | ✅ code + failing tests + `*_skipped_or_failed` warnings in test log |
| 2 | History backfill unscoped when called with `tickers` only (`explicit_tickers=None`) | ✅ code (`pipeline.py:27-46,229`) + failing test |
| 3 | 3 integration tests red in working tree | ✅ `uv run pytest tests/integration/test_pipeline_orchestrator.py` |
| 4 | Retry converts only `httpx` exceptions → all five price markets never retried | ✅ `sources/base.py:215-240` |
| 5 | yfinance flat-frame fallback duplicates one ticker's OHLCV across the whole batch | ✅ `sources/base.py:300-308` |
| 6 | `sources/cn.py:167` broad `except Exception` → retry unreachable, failures logged as no-data | ✅ code |
| 7 | pointblank `validate_quality` dormant: default `False`, zero callers pass `True` | ✅ grep |
| 8 | `upsert_dataset` runs profiling **before** the `dry_run` check; profiler writes JSON, CWD-relative | ✅ `writers.py` order, `profiling.py:70-80` |
| 9 | `validation/__init__.py` eagerly imports `pointblank` (optional group, no `default-groups`) → breaks lazy imports under plain `uv sync` | ✅ init + `schemas.py:7` + pyproject |
| 10 | `merge_delta` schema-mismatch fallback → `append` duplicates keyed rows; test pins the behavior | ✅ `storage/delta.py:131-136`, `tests/unit/test_delta_schema.py` |
| 11 | `equity demo seed` overwrites canonical bronze with no guard | ✅ `devtools/seed_demo.py:226` |
| 12 | `devtools/test_data.py` writes hive-partitioned parquet into canonical Delta dirs | ✅ `:32,43,95,139,417,650` |
| 13 | `--dry-run --save-results` persists to CWD; dashboard reads `LOGS_DIR` → feature broken both ways | ✅ `cli/commands/pipeline.py:62-65` vs `dashboard/exporter.py:111` |
| 14 | `PriceForecaster.backtest()` crashes on null target (last row) | ✅ `ml/forecasting.py:467` |
| 15 | Purged CV silently falls back to plain `KFold(2)` on short history | ✅ `ml/forecasting.py:324` |
| 16 | Trend-following & mean-reversion strategies hold exactly one day | ✅ weight expressions `trend_following.py:55`, `mean_reversion.py:49` |
| 17 | Sharpe rf mismatch: report rf=0 vs engine `daily_sharpe` (rf=0.02) — bias exceeds ±0.1 verdict threshold | ✅ `report.py:71-78` vs engine stats |
| 18 | `core/dates.py` infinite loop for unknown market key (`any([])` forever False) | ✅ `dates.py:8-16` + `calendar.py:44` |
| 19 | Engine error says `uv sync --extra backtesting`; contract is `--group` | ✅ `engine.py:84` |
| 20 | Duplicate feature: `social_sentiment_momentum` ≡ `social_sentiment_momentum_5d` (identical expr) | ✅ `enrichments_04.py:313` vs `:564` |
| 21 | `zscore_cross_sectional` imputes nulls before computing cross-sectional stats | ✅ `engineering.py:233-236` |
| 22 | Dead code set (agent/ stub, `write_silver`, `build_embedding_client`, `delta_table_version`, `optimize()`, …) | ✅ zero-reference greps (full list in [06](20260830-06-p2-dead-code-sweep.md)) |
| 23 | Market vocabulary split: short keys (`us`) in settings/pipeline vs long keys (`us_equity`) in calendar/paths | ✅ `settings.py:30` vs `core/paths.py` |
| 24 | StockTwits client_id sent as `access_token` query param | ✅ `stocktwits.py:56,102` |
| 25 | rss.py fallback `feedparser.parse(feed_url)` does its own HTTP with no timeout | ✅ `rss.py:126-127` |
| 26 | `load_dotenv()` inside library code (`sources/macro.py`) | ✅ `macro.py:168-170` |
| 27 | Health checks target log files with no producer | ✅ check exists (`health.py:282-284`); no-producer verified by audit grep |
| 28 | Catalog declares `format="parquet"` for all 15 Delta datasets | ✅ `catalog/datasets.py` (15 hits) |
| 29 | `query_4`/`query_5` require args but `run_all_queries` calls them bare | 🔎 audit-verified only — reproduce first |
| 30 | Monitoring masks failures as healthy; permanent SEC staleness alert; delta extension unbootstrapped in monitor | 🔎 audit-verified — re-check line numbers before fixing |
| 31 | API `read_delta` swallows errors → HTTP 200 with `[]`; full-table scan per request | ✅ swallow (`delta.py:150-153`); scan 🔎 |
| 32 | s3_sync 600s hard cap; destructive migration ordering | 🔎 audit-verified |

## Workstreams

| ID | Handoff | Priority | Depends on | Parallelizable |
|----|---------|----------|------------|----------------|
| 01 | [Reconcile working tree + pipeline orchestrator bugs](20260830-01-p0-reconcile-working-tree.md) | P0 | — | No (serial) |
| 02 | [Ingestion data correctness](20260830-02-p0-ingestion-correctness.md) | P0 | 01 | 2 workers |
| 03 | [Storage & validation correctness](20260830-03-p0-storage-validation-correctness.md) | P0 | 01 | 2 workers |
| 04 | [Safety rails (devtools, CLI, secrets)](20260830-04-p1-safety-rails.md) | P1 | 01 | Yes |
| 05 | [Market vocabulary + registry ADR](20260830-05-p2-market-vocabulary.md) | P2 | 01 (ADR before code) | Planner → worker |
| 06 | [Dead-code sweep](20260830-06-p2-dead-code-sweep.md) | P2 | 01, ideally before 07 | Single worker |
| 07 | [DRY consolidation](20260830-07-p3-dry-consolidation.md) | P3 | 05, 06 | 3 workers (disjoint modules) |
| 08 | [ML & backtest integrity](20260830-08-p3-ml-backtest-integrity.md) | P3 | 01 | 2 workers (ml / backtesting) |
| 09 | [Monitoring, API, catalog](20260830-09-p4-monitoring-api-catalog.md) | P4 | 03 | 2 workers |

## Suggested execution waves (for subagent orchestration)

```
Wave 0 (serial):   01 — one worker, then one reviewer. Gate: pytest green.
Wave 1 (parallel): 02a+02b, 03a+03b, 04  — up to 5 workers; one reviewer per handoff after merge.
Wave 2 (parallel): 05 planner (ADR), 06 worker, 08a+08b workers.
Wave 3 (parallel): 07a/b/c workers (needs 05+06), 09a/b workers.
Final:             full validation suite + catalog regen check + docs map update.
```

Agent-type mapping: `scout` for the 🔎 items' re-verification, `worker` for all
implementation, `reviewer` for post-merge review of each handoff, `planner` for the 05 ADR.

## Ground rules (digest of AGENTS.md — bind into every task brief)

- Always `uv run <cmd>`; never bare `python`. Present a plan before code changes; change
  as little as possible; no new abstractions.
- tenacity (exponential, max 3) via `core/retry.py` for all fetchers — never hand-rolled retry.
- Polars primary; pandas only at yfinance/akshare/efinance boundaries.
- structlog everywhere; no `print()` in library code; no stdlib logging outside `core/logging.py`.
- Dry-run = no persistence, no LLM, no feature output, no ML inference.
- pointblank schemas at ingestion write boundaries; auxiliary artifacts under `data/<name>/`
  with a Pydantic model at the write boundary; cataloged tables under `data/lake/` only.
- Markets fixed: us_equity, cn_ashare, hk_sg_equity, jpx_equity, krx_equity.
- Change matrix: schema/storage/CLI/pipeline/boundary changes carry the listed companions;
  boundary changes need an accepted ADR in `docs/decisions/` first.
- Markdown files: `YYYYMMDD-filename.md`.

## Handoff validation (run before closing any workstream)

```bash
uv sync
uv run pytest
uv run pytest -n auto
uv run ruff check .
uv run ruff format --check .
uv run mypy
# when pipeline/feature structure changed:
uv run equity catalog-generate
```

Per-handoff additions are listed inside each file.
