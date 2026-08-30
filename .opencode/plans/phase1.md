# Phase 1 Implementation Plan — Critical Bugs + S-effort Dead Code

Status: APPROVED by user. Blocked only by active plan mode (edits denied). Execute immediately once plan mode is exited.

## A1 — ML label/index leakage (HIGH)
File: `src/equity_lake/ml/forecasting.py`
- `NON_FEATURE_COLUMNS` (lines 37–55) omits the label and barrier bookkeeping columns, so
  `PriceForecaster._get_feature_columns` (line ~538) feeds them back as features → silent model
  corruption. `ml/_metrics.py` already excludes them via `EXCLUDE_COLUMNS` but `forecasting.py`
  uses its own list.
- Fix: add three entries to the set before the closing `}` at line 55:
  ```python
      "target",
      "barrier_start_idx",
      "barrier_end_idx",
  ```

## A2 — Auto-backfill is a no-op for price markets (HIGH)
Files: `src/equity_lake/ingestion/gap_detection.py`
- `find_missing_dates` (and `get_coverage_stats`) pass `market` (the **full Delta path**, e.g.
  `01_bronze/market_data/us_equity`) to `trading_days_between(...)`. The calendar map
  (`core/calendar.py:_MARKET_TO_EXCHANGE`) is keyed by the short name `us_equity`, so the lookup
  returns `[]` and the gap query short-circuits to `[]`. (Tests pass the short key `"us_equity"`,
  which is why the bug was invisible in CI.)
- Fix: derive the calendar key from the path suffix in the two call sites. The scan path stays
  unchanged (still uses full `market`).
  - In `_query_missing_single` (~line 116):
    ```python
    calendar_key = market.rsplit("/", 1)[-1]
    trading_dates = trading_days_between(calendar_key, start_date, end_date)
    ```
  - In `_query_missing_all` (~line 155): identical two-line change.
  - In `get_coverage_stats` (~line 213): `_count_trading_days(calendar_key, ...)`.
- Also remove dead code (see D-section): `_MARKET_DIR_MAP` / `_market_calendar_key` (lines 23–33)
  are unused.

## A4 — Health-report has no canonical location (HIGH)
Files: `src/equity_lake/cli/commands/analysis.py` (`monitor` command), `dashboard/streamlit_app.py`
(~91–97), `dashboard/exporter.py` (~162–165)
- `monitor` writes to a user-supplied `--output-json`; both dashboards look in different empty paths.
- Fix: default `monitor`'s output to `LOGS_DIR / "health-report.json"` (import `LOGS_DIR` from
  `core.paths`), and have both dashboards read from that single path.

## B1 — Retry ignores HTTP 429/408 (MED-HIGH)
File: `src/equity_lake/sources/base.py` (~lines 228–239, `_retry_on_failure`)
- Only `status >= 500` + connection/timeout map to `TransientError`. Rate-limited providers
  (Finnhub, Reddit, SEC) return 429/408 which then fail hard with no backoff.
- Fix: add `429` and `408` (and `503`) to the retryable-status set that raises `TransientError`.

## B2 — `news.py` never calls `raise_for_status` (MED)
File: `src/equity_lake/sources/news.py` (~line 189–200, `_fetch_news_for_ticker`)
- A 5xx body may not be JSON → `response.json()` raises `JSONDecodeError` (not `TransientError`) →
  no retry, ticker silently yields `[]`.
- Fix: add `response.raise_for_status()` before `response.json()` (mirrors `sentiment.py`).
- Also drop the dead guard `if hasattr(response, "json")` (line ~200).

## B3 — `sec_financials.py` skips tenacity (MED)
File: `src/equity_lake/sources/sec_financials.py` (~lines 97–131, `_fetch_ticker`)
- edgar network calls are not routed through `self._retry_on_failure`; transient errors are only
  caught by the per-ticker try/except and silently skipped.
- Fix: wrap the `Company(...).get_filings(...)` / `xbrl()` calls in `self._retry_on_failure(...)`.

## B4 — Disabled/empty enrichments reported FAILED (MED)
Files: `src/equity_lake/ingestion/router.py` (~409–416), `src/equity_lake/ingestion/orchestrator.py`
(~224–225, ~264–267)
- A deliberately disabled `StockTwitsFetcher` (returns empty frame) or any empty enrichment is
  marked `SourceStatus.FAILED`, polluting run status.
- Fix: when `validate_schema` fails on an empty frame (or the fetcher signals "disabled/empty"),
  return `SourceStatus.SKIPPED_EXISTING` (or a new clean-empty outcome) instead of `FAILED`. Minimal
  approach: in `router.fetch_market_data`, distinguish "empty-but-intentional" (return SKIPPED)
  from genuine schema/validation failure (FAILED).

## D — S-effort dead-code / cleanup batch
Mechanical removals (verify no callers with grep first):
1. `core/logging.py`: remove `set_correlation_id` (37–39), `timed` decorator (178–240) — keep
   `timer`; remove `setup_logging` alias (281); replace hand-rolled `add_timestamp` (69–74) with
   structlog `TimeStamper`.
2. `core/paths.py`: remove unused constants `SILVER_SEC_FINANCIALS_DIR` (62), `SEC_FINANCIALS_DIR`
   alias (89), `PLATINUM_PREDICTIONS_DIR` (72), `UPDATE_HISTORY_DIR` (78), `BASE_DIR` (26),
   `ANALYST_RATINGS_DIR` alias (87). (Confirm zero callers.)
3. `ml/forecasting.py`: remove `compute_concurrency_matrix` (sample_weights.py:20–34) if unused;
   remove redundant inner `import numpy as np` in `trainer.py` (46, 59).
4. `backtesting/data_loader.py`: remove dead cache methods `load_cached`, `get_available_tickers`,
   `get_date_range`, `clear_cache` (~225–311) and the `joblib.Memory` machinery if only used there.
5. `dashboard/__init__.py`: remove the unused `DashboardExporter` proxy class (9–24).
6. `ingestion/writers.py`: remove unused `validate_news_data_quality` (140–190) or wire it into
   `upsert_dataset`.
7. `sources/__init__.py`: fix inconsistent re-exports (export `MacroFetcher`, drop unused
   `MacroIndicatorFetcher` re-export from `base.py`).
8. `sources/krx.py`: hoist `import FinanceDataReader as fdr` out of the per-ticker loop (line 77).
9. `pipeline.py`: replace hardcoded `"us"`/`[:10]` default-ticker fallback (line 79) to derive from
   `markets`; replace string-exception match (see A5 note below — defer A5 to Phase 2 if desired).
   Add `HISTORY_BACKFILL_WINDOW_DAYS = 120` constant; replace `elif not skip_ingestion` with `else`.
10. Stale docstrings: `features/engineering.py` (deprecated merge methods), `ml/comparison.py`
    (XGBoost-locked), `pipeline.py` (`PipelineOrchestrator` no longer exists).

## Verification
- `uv run pytest tests/unit/test_gap_detection.py` (A2 regression must stay green).
- `uv run pytest tests/unit/test_forecasting.py` (add a test asserting `_get_feature_columns`
  excludes `target`/`barrier_start_idx`/`barrier_end_idx` — this would have caught A1).
- `uv run ruff check . && uv run ruff format --check .`
- `uv run pytest tests/unit -k "backtest or news or sec or sentiment"` (B1–B4 smoke).
