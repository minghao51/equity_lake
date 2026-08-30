# Handoff 02 — P0: Ingestion data correctness (sources/ + ingestion/)

Priority: P0. Depends on: 01.
Suggested dispatch: **2 parallel `worker`s** (A: `sources/`, B: `ingestion/`), then one
`reviewer` over the combined diff. Files are disjoint between workers.

## Worker A — `sources/` (retry honesty + data duplication)

### A1. Retry coverage is httpx-only ✅ verified
`sources/base.py:215-240` (`_retry_on_failure`) converts only
`httpx.{ConnectError,ReadTimeout,WriteTimeout,PoolTimeout,RemoteProtocolError}` to
`TransientError`; the tenacity decorator (`base.py:152-156`) retries only
`TransientError`. yfinance/akshare/efinance/FinanceDataReader are requests/urllib-based →
**none of the five price markets ever retry**. Contract: tenacity, max 3, for ALL fetchers.

Fix: extend the conversion tuple with
`requests.exceptions.{ConnectionError,Timeout,ChunkedEncodingError}` and
`urllib.error.{URLError,HTTPError}` (5xx/429 only for HTTPError). Check what the libs
actually raise (yfinance wraps requests). Add unit tests: a fetcher whose fetch raises
`requests.ConnectionError` is retried 3× then fails; a 404 is not retried.

### A2. Flat-frame fallback duplicates prices across the batch ✅ verified
`sources/base.py:300-308`: when `yf.download` returns a non-MultiIndex frame for a
multi-ticker batch, the code copies the same OHLCV frame onto **every** ticker in the
batch (up to `DEFAULT_BATCH_SIZE = 500`) and returns them as real data. Also `:304` is a
no-op ternary (`ticker_batch if len(ticker_batch) > 1 else [ticker_batch[0]]`).

Fix: a flat frame from a multi-ticker batch is a failed download → log + return `[]`.
Keep the flat-frame handling only for single-ticker batches. Add a regression test with a
2-ticker batch returning a flat frame → expect empty result, not 2 copies.

### A3. `sources/cn.py:167` broad `except Exception` ✅ verified
`_fetch()` wraps the whole body in `try/except Exception` and returns `_empty_frame()` on
any failure → (a) tenacity can never see an exception → no retry; (b) provider failures
are misclassified as `no_data_fetched` by the orchestrator. `sources/cn_efinance.py`
(~:193-199) does it right (lets exceptions escape). Align `cn.py` with `cn_efinance.py`;
per-stock failures already degrade to `failure_count` inside the loop — keep that, only
stop swallowing the outer error.

### A4. Small hygiene fixes (each tiny, do in same PR)
- `sources/stocktwits.py:102` — `client_id` sent as `access_token` query param ✅.
  Rename env/param consistently (`STOCKTWITS_ACCESS_TOKEN` + matching `.env.example`
  entry) and prefer header if the API allows.
- `sources/rss.py:126-127` — fallback `feedparser.parse(feed_url)` performs its own HTTP
  with **no timeout** ✅. Fetch only via the `httpx` client (`timeout=20` already set);
  on fetch error return empty.
- `sources/macro.py:168-170` — `load_dotenv()` inside library code ✅ violates the
  dotenvx-only convention. Delete; env loading belongs to the dotenvx CLI seam.
- `sources/news.py:262` — `article.get("datetime", 0)` defaults missing timestamps to
  epoch-0 → 1970-01-01 partitions. Drop the article instead (code already drops
  unparseable ones).
- `sources/sec_financials.py` — narrow `except Exception: pass` in `_get_metric`/
  `_get_shares` (log at debug with the concept list); fix mypy strict errors flagged by
  `uv run mypy` in this file; delete no-op `except Exception: raise`.
- `sources/cn.py:124-137` — remove the `executor._max_workers` mutation ("adaptive
  throttle" has no effect on queued futures) and the unreachable
  `future.result(timeout=30)` after `as_completed`.

## Worker B — `ingestion/` (gap detection + orchestration honesty)

### B1. auto_backfill crash / silent no-op ✅ verified structure
- `ingestion/auto_backfill.py:47` hard-codes skip list
  `("macro", "us_news", "us_social_sentiment")` which diverges from
  `OPTIONAL_ENRICHMENT_MARKETS` (`ingestion/types.py`). Markets like `rss_news` share
  `01_bronze/raw_articles` (no `ticker` column) → `gap_detection.py`'s
  `SELECT DISTINCT ticker` raises `duckdb.BinderException`, and `find_missing_dates`
  re-raises → the whole `find_and_fill_gaps` run aborts.
- For enrichment tables that do have `ticker` (`us_analyst_ratings`), the calendar key
  derived by `market.rsplit("/",1)[-1]` ("analyst_ratings") is not in
  `core/calendar._MARKET_TO_EXCHANGE` → zero trading days → silent no-op.

Fix: iterate only `REQUIRED_PRICE_MARKETS` for gap-filling (or per-market try/except +
skip non-ticker tables with a logged reason); derive exchange from the market map, not a
substring; add a test for a ticker-less bronze table.

### B2. Parallel-duration bug
`ingestion/parallel.py:53-55`: `start = time.monotonic()` is taken **after**
`as_completed` yields the future → `duration_seconds ≈ 0` in summaries. Record submit
time per future. Also `parallel.py:85` re-imports `time` inside the loop.

### B3. HK/SG calendar asymmetry (⚠️ verify first, then fix)
`core/calendar.py:47-51`: `is_trading_day` unions XHKG+XSES but
`trading_days_between` uses `exchanges[0]` (XHKG) only → `.SI` holidays show as gaps.
Fix: iterate all exchanges and intersect (a date is a trading day only if the ticker's
exchange trades) or accept the union explicitly and document.

## Acceptance criteria (both workers)

- Retry tests prove requests-based fetchers retry transient errors 3× and do not retry 4xx.
- Multi-ticker flat-frame batch produces zero rows (test).
- `cn.py` propagates fetcher errors; orchestrator logs `fetch_failed`-class outcomes, not
  `no_data_fetched`.
- Gap detection completes over all markets without exception; ticker-less tables are
  skipped with a warning.
- No behavior change to dedupe keys or schemas.

## Validation

```bash
uv run pytest tests/unit -k "source or cn or gap or backfill or parallel" -q
uv run pytest -n auto && uv run ruff check . && uv run mypy
```

## Out of scope

Writer-boundary validation policy (handoff 03), market vocabulary (05), router factory
dedup (07).
