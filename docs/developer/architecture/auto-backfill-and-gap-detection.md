# Auto-Backfill and Gap Detection

Bronze market data can drift — a failed fetch, a holiday misclassification,
or a missed run leaves a missing `ticker,date` partition. This page describes
how gaps are detected, the three fill paths (`equity auto-backfill`,
`equity backfill`, and the feature-history recovery behind
`equity pipeline --allow-history-backfill`), and the idempotency rules they
share.

## GapDetector

`ingestion/gap_detection.py` provides `GapDetector`, an in-memory DuckDB
connection (`INSTALL delta; LOAD delta;`) that scans existing Delta data with
`delta_scan('<lake>/<market_dir>')`. Detection compares what exists against
what should exist:

1. An "ideal" date range is generated with DuckDB `generate_series` over the
   requested window.
2. Existing `(ticker, date)` pairs are read from the Delta table via
   `delta_scan`.
3. The ideal range is `LEFT JOIN`-ed against existing data; rows with no match
   are gaps.

With `business_days_only=True` (the default), the ideal range is filtered to
actual trading sessions from `core/calendar.py`
(`trading_days_between`), so weekends and exchange holidays are never
reported as gaps. The calendar key is derived from the market directory
(`01_bronze/market_data/us_equity` → `us_equity` → XNYS sessions), and each
of the five equity markets maps to its home exchange(s).

`find_missing_dates()` returns a `ticker -> [missing dates]` dict for a
single ticker or the full table. The detector also offers `get_latest_date()`
and `get_coverage_stats()` (expected vs actual trading days and coverage
percentage per ticker). The class is a context manager; scans fail loudly on
DuckDB errors rather than reporting a silent empty set.

## `find_and_fill_gaps`

`ingestion/auto_backfill.py` wires `GapDetector` output into the normal
ingestion path:

| Parameter | Default | Meaning |
|---|---|---|
| `end_date` | today | End of the scan window. |
| `days_back` | 90 | Start of the scan window (`end_date - days_back`). |
| `markets` | all valid markets | Market scoping; the five equity price markets are the intended targets — `macro`, `us_news`, and `us_social_sentiment` are skipped explicitly. |
| `max_gap_days` | 30 | Safety valve for suspiciously sparse gaps (below). |
| `dry_run` | false | Report gaps without filling. |

Per market, it detects missing dates, then applies a span guard: if the
distance between the first and last missing date exceeds
`max_gap_days * <number of missing dates>`, the market is skipped with an
`auto_backfill_gap_too_large` warning pointing at manual `equity backfill`.
This avoids fetching a handful of dates spread across a huge, probably
wrong, range.

Filling delegates to `run_daily_ingestion(trading_date=gap_date, ...)` one
missing date at a time, serially, with `skip_existing=False` — the gap scan
already proved the dates are missing, so re-checking existence would be
redundant. Failures on individual dates are logged
(`auto_backfill_date_failed` / `auto_backfill_date_error`) without aborting
the remaining dates. The return value maps each market to the number of dates
actually filled.

## The three fill paths

| | `equity auto-backfill` | `equity backfill` | `equity pipeline --allow-history-backfill` |
|---|---|---|---|
| Trigger | Gap scan decides what is missing | Operator supplies the range | Feature stage raises `NoFeatureHistoryError` |
| Scope | Detected gaps per market | Explicit `--start`/`--end` (or `--days-back`) × `--markets` | 120-day window (`HISTORY_BACKFILL_WINDOW_DAYS`), price markets only |
| Authorization | Direct invocation | Direct invocation | Explicit `--allow-history-backfill` flag — never implicit |
| Purpose | Repair small gaps near the present | Bulk (re)load or large-gap recovery | Recover feature warm-up history so the feature stage can run |
| Re-checks existing dates | No (`skip_existing=False`) | Yes (`skip_existing=True`) | Yes (`skip_existing=True`) |

- `equity auto-backfill` (`cli/commands/data.py`) exposes `--days-back`
  (90), `--markets`, `--max-gap-days` (30), `--dry-run`, and `--verbose`.
  `--dry-run` prints per-market "would fill" counts.
- `equity backfill` requires `--days-back` or `--start` (exit code 1
  otherwise), defaults `--end` to yesterday and `--markets` to
  `us,cn,hk_sg`, and drives `backfill_date_range()`
  (`ingestion/backfill.py`) — one `run_daily_ingestion` call per day in the
  range, per market, serially, with `skip_existing=True`.
- The feature-history recovery in `pipeline.py` is feature-scoped, not a
  general repair tool: when the feature stage reports missing warm-up
  history and the flag is present, it logs the resolved 120-day range,
  markets, ticker count, and explicit tickers, backfills only the requested
  price markets (`REQUIRED_PRICE_MARKETS`), and re-runs the feature job.
  Without the flag the feature stage fails with
  `history_backfill_not_authorized`. This is the guardrail recorded in
  `AGENTS.md` and the pipeline contracts page.

## Idempotency

All fill paths converge on `run_daily_ingestion` in
`ingestion/orchestrator.py`. When `skip_existing=True` (the default for
`ingest` and `backfill`), each market/date is checked before fetching:

- `_market_has_date` — a cheap row-count existence check over
  `delta_scan` filtered to the trading date.
- `_partition_is_valid` — confirms the partition is usable, not merely
  present: non-empty, schema columns present and not all-null, reusing the
  same write-boundary validator that gated the original write (`LIMIT 100`
  keeps the read-back cheap).

A market that passes both is recorded as `SKIPPED_EXISTING` — surfaced as
success so downstream feature/ML stages are not blocked — and skipped for
fetching. Writes themselves are Delta merge/upserts keyed on the natural key
(`ticker,date` for market data), so re-running a fill path is repeatable.
`auto-backfill` passes `skip_existing=False` only because its gap scan has
already established which dates are missing.

## Related

- [Data Flow](data-flow.md) — medallion destinations and source routing.
- [Pipeline Contracts](pipeline-contracts.md) — the backfill and dry-run
  contracts for `equity pipeline`.
- [Pipeline User Guide](../../user-guide/pipeline.md) — operator usage of
  `pipeline`, `backfill`, and `auto-backfill`.
