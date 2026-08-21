# 20260821-phase2-handoff.md

**Title:** Phase 2 — Consistency, resilience, and architecture cleanup
**Status:** Planned (not started). Phase 1 (`b0fe5a5`) is committed and green.
**Author:** Audit synthesized from parallel `explore` subagents over `src/`.
**Depends on:** Phase 1 commit. Unrelated to the in-tree Phase 2C RAG-agent work (`src/equity_lake/agent/`, `pyproject.toml` `agent` extra) — keep those separate.

---

## Context

Phase 1 fixed the highest-impact *correctness/resilience* bugs (ML label leakage, dead
auto-backfill, non-canonical health report, 429/408 retry, empty-enrichment false failures)
and removed ~the lowest-risk dead code. The remaining findings are **M-effort consistency and
architecture items** that reduce drift risk and make the codebase uniform. None are silent
data-corruption bugs, but several are latent correctness risks (Delta-vs-Parquet reads) or
maintenance hazards (duplicated routing/filter logic, three validation paradigms).

Goal of Phase 2: make the lake read/written **consistently via Delta**, unify the duplicated
market/path maps, and remove the remaining divergent patterns. This sets up Phase 3 (the L-effort
cross-cutting consolidation: centralized validation contracts, `intelligence.py` split, httpx-only).

---

## Prioritized backlog (effort / impact)

| ID | Finding | Location | Effort | Impact | Notes |
|----|---------|----------|--------|--------|-------|
| A3 | Dashboards/monitoring/signals glob `read_parquet('.../**/*.parquet')` instead of Delta — risk silent empty reads on the real lake | `monitoring/health.py` (140,200,335,404), `signals/generators/backtest.py:32`, `signals/generators/sentiment.py:31`, `dashboard/streamlit_app.py` (60,193), `dashboard/exporter.py:128` | M | **High** | Mirror `backtesting/data_loader.py:_setup_views` (Delta-aware `delta_scan` with parquet fallback). Extract a shared `lake_reader` helper in `storage/` and reuse. |
| B5 | `delta.write_delta`/`merge_delta` swallow exceptions → return `False` (root cause buried) | `storage/delta.py:69-71,109-114` | M | Med | Let exceptions propagate OR attach cause so monitoring can distinguish schema vs I/O. |
| B6 | S3 sync has no retry/backoff; `_detect_tool` calls `sys.exit(1)`; uses emojis | `storage/s3_sync.py` | M | Med | tenacity retry on `_test_s3_access` + sync; raise `RuntimeError`; drop emojis/exit. |
| C1 | Duplicate/divergent `backtest` commands + strategy name mismatch (`sma_crossover` vs `trend_following`) | `cli/commands/analysis.py:13-50` vs `cli/commands/arena.py:73-116`; `backtesting/arena.py:41-45` | S/M | Med | Reuse single `STRATEGY_REGISTRY`; top-level `backtest` delegates to / removed in favor of `report backtest` (AGENTS.md: backtest is flat top-level, sub-commands under `report`). |
| C3 | Three divergent market→dir maps (`paths.py` constants vs `ingestion/types.py:MARKET_DIR_MAP` vs `auto_backfill._DELTA_MAP`) | `core/paths.py`, `ingestion/types.py`, `ingestion/auto_backfill.py` | M | Med | Derive `MARKET_DIR_MAP` from `paths.py` (or vice-versa) so there is one source of truth; fixes prior drift (`SILVER_SEC_FINANCIALS_DIR` etc.). |
| C4 | `validate_tickers` hand-rolls checks that duplicate `core/config.py` pydantic models; only non-pydantic validator | `config/validators.py:14-95` | M | Med-High | Drive from `MarketConfig`/`TickersConfig`; catch `pydantic.ValidationError`. |
| C5 | `intelligence.py` overloaded (416 lines: ingestion + forecast) | `cli/commands/intelligence.py` | M | Med | Split ingestion commands into `commands/ingest.py`; keep `forecast` + `signal scan`. |
| C6 | `ensure_dirs()` never called at startup; docstring/behavior mismatch | `core/paths.py:92-98` | S | Med | Wire into `cli/_app.py` startup (alongside `setup_structured_logging`) or remove. |
| C7 | `storage/duckdb.py`, `examples.py` use stdlib `logging` + f-strings vs structlog | — | M | Med | Migrate to `structlog.get_logger()` with key=value args (correlation IDs won't propagate otherwise). |
| C8 | Mixed `requests`/`httpx` in sources; `news`/`sentiment` still `requests` | `sources/base.py`, `news.py`, `sentiment.py` | M | Med-Low | Migrate to `httpx`; then drop `requests` handling from `_retry_on_failure` (Phase 1 already retries both stacks, so this is safe cleanup). |
| C9 | Duplicated ticker-filter logic HK/SG (~70 lines) | `sources/base.py:127-187` vs `hk_sg.py:74-145` | M | Med | Extract `resolve_tickers(config, market, filters, fallback)`; HK/SG calls it twice. |
| A5 | `pipeline.py` branches on `str(exc) == "No features generated"` | `pipeline.py:180` | M | Med | Raise dedicated `NoFeatureHistoryError` from feature job; `except` that type. (Deferred from Phase 1.) |
| C2 | Three divergent validation paradigms (pointblank / Pydantic sampling / inline) | `features/dag/*`, `validation/`, `ml/__init__` | L | Med | Centralize all boundary contracts in `validation/`. **Phase 3** (cross-cutting). |

### Explicitly out of Phase 2 scope
- `compute_concurrency_matrix` (has its own test — remove only with its test), `features/dag`
  unenforced `validated_ohlcv`/`validated_features`, `PredictionModel` — these are part of **C2**
  and belong in Phase 3.
- `krx.py` in-loop `import FinanceDataReader` — intentional lazy import for an optional dep; leave.

---

## Recommended execution order

1. **A3** first — highest leverage (uniform Delta reads unblocks monitoring, signals, dashboards
   and removes a silent-correctness risk). Extract `storage/lake_reader.py` and wire the 5 call sites.
2. **C6** (S) — wire `ensure_dirs` at startup while touching the CLI bootstrap anyway.
3. **C1 + A5** — CLI/contract hygiene (small, high clarity).
4. **C3 + C4 + C9** — the "single source of truth" cluster (paths, validators, ticker filters).
   Do C3 before C9 so the unified map can back both.
5. **B5 + B6** — storage resilience (only if a sync/ingestion run is observed failing).
6. **C7 + C8** — observability/HTTP-stack uniformity (can be parallelized; lower urgency).
7. **C5** — `intelligence.py` split (pure refactor, do last so other CLI touches land first).

---

## Definition of done / verification

- `uv run ruff check .` clean; `uv run ruff format --check .` clean.
- `uv run pytest tests/unit -q` green (add a test that `monitoring`/`signals` reads resolve via
  `delta_scan` when a Delta table is present — guards A3 regression).
- `uv run equity backtest --help` and `uv run equity report backtest --help` show the **same**
  strategy registry (guards C1).
- `uv run equity catalog-generate` still produces identical `data/catalog.jsonl` (guards C3/C4
  don't change catalogs).
- New `storage/lake_reader` helper has a unit test covering Delta-present and parquet-fallback.

## Handoff notes for the next agent
- Phase 1 commit `b0fe5a5` is the baseline; branch from it.
- The `agent/` RAG work in the working tree is **unrelated** — do not fold it into Phase 2 commits.
- Keep commits focused per ID above (e.g. `fix(storage): A3 shared Delta lake reader`).
- AGENTS.md rules still bind: Typer-native CLI, structlog, polars primary, tenacity retries,
  pointblank at ingestion boundaries, `YYYYMMDD-*.md` for handoffs.
