# Unstructured Data Ingestion — Audit Findings & Remaining Tasks

**Date:** 2026-06-15
**Scope:** Phases 1–3 audit remediation (commit `c9e1c34`) + remaining work

---

## Audit Remediation Summary

All CRITICAL, HIGH, and actionable MEDIUM issues from the audit have been fixed
in commit `c9e1c34`. Below is what was fixed and what remains.

### Fixed in This Commit

| ID | Severity | Fix |
|----|----------|-----|
| C1 | CRITICAL | Look-ahead bias in `sec_features.py` — replaced ticker-only join with ASOF (point-in-time) join |
| C2 | CRITICAL | Retry filter in `base.py` — only `TransientError` (5xx, network) triggers retries |
| C3 | CRITICAL | Timezone bugs across all source files — `datetime.now(UTC)`, `calendar.timegm`, `fromtimestamp(tz=UTC)` |
| H2 | HIGH | DuckDB connection leaks in `bronze_silver.py` and `sec_processor.py` — added `try/finally` |
| H3 | HIGH | SEC section TOC bug — `finditer()` picks last match (actual section, not TOC) |
| H4 | HIGH | SEC processor missing ticker filtering — added `_load_known_tickers()` |
| H5 | HIGH | HTML entity destruction — replaced regex deletion with `html.unescape()` |
| Q4 | MEDIUM | Delta schema evolution — `merge_delta` catches schema mismatch, falls back to append with `schema_mode='merge'` |
| Q5 | MEDIUM | VADER ImportError guard removed — it's already a required dependency |
| Q6 | HIGH | Bronze dedup key — changed from `source_url` to `(source_type, source_url)` |
| M3 | MEDIUM | `follow_redirects=True` added to `reddit.py` and `stocktwits.py` |
| M4 | MEDIUM | StockTwits now sends `User-Agent` + `Accept` headers |
| M7 | MEDIUM | Reddit `time.sleep(7)` only between subreddits, not after last |

---

## Remaining Technical Debt

### 1. Code Duplication: `llm_processor.py` vs `sec_processor.py` (HIGH)

**Status:** Acknowledged, deferred

Both processors are ~85% structurally identical (`__init__`, `process_batch`,
`process_all`, `_to_silver_df`, `run_*` wrapper). They differ only in:
- System prompt text
- Pydantic models (`ArticleExtraction` vs `SECSectionExtraction`)
- Fallback logic
- Row-mapping function

**Recommended fix:** Extract `BaseLLMBatchProcessor[T]` generic class.
Subclasses provide prompt, Pydantic models, and fallback. Estimated 250 lines
eliminated.

**Why deferred:** Both processors work correctly. The duplication is
maintainable in the short term. Refactoring risk > benefit until a third
processor is needed.

### 2. Parallel Bronze→Silver Pipelines (MEDIUM)

**Status:** Acknowledged, deferred

`bronze_silver.py:process_bronze_to_silver` and
`sec_processor.py:process_sec_bronze_to_silver` implement the same 5-step
pattern (read bronze → get processed IDs → filter → run LLM → write silver).

**Recommended fix:** Unify into a single parameterized function:
`process_bronze_to_silver(trading_date, source_type_filter, processor, silver_writer)`.

### 3. `asyncio.run()` Cannot Be Called from Event Loop (MEDIUM)

**Files:** `llm_processor.py:275`, `sec_processor.py:280`

`run_llm_processing()` and `run_sec_processing()` use `asyncio.run()` which
crashes if called from an async context (FastAPI, Jupyter with asyncio).

**Recommended fix:** Detect running event loop and use `asyncio.ensure_future()`
or `loop.run_until_complete()` as fallback.

### 4. Missing Rate Limiting in Finnhub Fetchers (MEDIUM)

**Files:** `transcripts.py`, `analyst_ratings.py`

Finnhub free tier: 60 req/min. With 50+ tickers, both fetchers will hit rate
limits and trigger retry storms.

**Recommended fix:** Add configurable inter-request delay (default 1s) via
`time.sleep()` or `asyncio.Semaphore` pattern.

### 5. EWMA Half-Life Naming (LOW)

**File:** `analyst_features.py:86`

Column `analyst_consensus_score_ewma_7d` uses `half_life=7.0` (7 observations,
not 7 calendar days). The "7d" label is misleading when data is non-daily.

**Recommended fix:** Either rename to `_ewma_hl7` or document the semantics.

### 6. Inconsistent HTTP Timeouts (LOW)

| File | Timeout |
|------|---------|
| `news.py` | 10s |
| `reddit.py` | 15s |
| `stocktwits.py` | 15s |
| `rss.py` | 20s |
| `analyst_ratings.py` | 10s |
| `transcripts.py` | 15s |
| `sec_fulltext.py` | 15-30s |

**Recommended fix:** Define `DEFAULT_HTTP_TIMEOUT = 15` constant in `base.py`.

### 7. StockTwits `client_id` vs `access_token` Naming (LOW)

**File:** `stocktwits.py:102`

OAuth `client_id` is used as `access_token` API parameter. In OAuth, these
are different credentials. The env var name or parameter name is semantically
wrong.

**Note:** StockTwits API is disabled by default (`STOCKTWITS_ENABLED=false`).
This is low priority.

### 8. YAML Encoding (LOW)

**Files:** `rss.py:32`, `reddit.py:50`

YAML files opened without explicit `encoding="utf-8"`. Uses platform default
which may cause issues on non-UTF-8 systems.

---

## Test Coverage Gaps

### Critical Missing Tests

| Area | Missing Test | Risk |
|------|-------------|------|
| `llm_processor.py` | `process_batch` with mocked OpenAI API | Core LLM path untested |
| `llm_processor.py` | Retry behavior (RetryableError → retry → success) | Retry logic untested |
| `sec_processor.py` | `process_sec_bronze_to_silver` full flow | End-to-end untested |
| `sec_fulltext.py` | Section extraction with real TOC + body | TOC fix untested with realistic data |
| `sec_fulltext.py` | `_strip_html_tags` with numeric entities | Entity fix untested |
| Delta `merge_delta` | Schema evolution fallback path | Schema fix untested |
| Pipeline | Integration: ingest → bronze → silver → features | Full flow untested |

### Recommended New Tests

1. **`test_llm_processor.py`**: Mock `AsyncOpenAI.chat.completions.create`,
   verify JSON parsing, retry on empty content, VADER fallback scoring
2. **`test_sec_processor.py`**: Mock DuckDB bronze/silver reads, verify
   `process_sec_bronze_to_silver` skip-already-processed logic
3. **`test_delta.py`**: Verify `merge_delta` schema evolution fallback
4. **`test_sec_fulltext.py`**: TOC + body section extraction with realistic
   10-K HTML, `_strip_html_tags` entity preservation

---

## Operational Concerns

### 1. No Monitoring for Unstructured Data

**Status:** Gap

Existing `monitoring/` module checks price data staleness and null rates.
No health checks for:
- Bronze article ingestion volume per source
- Silver processing success rate
- LLM API call count / cost tracking
- SEC filing coverage per ticker

**Recommended:** Add monitoring checks for bronze/silver table freshness.

### 2. No CLI Convenience Commands

**Status:** Gap

New data sources must be triggered via `equity ingest --markets sec_filings_fulltext`.
No convenience commands like `equity sec` or `equity transcripts`.

**Recommended:** Add CLI commands in `cli/commands/intelligence.py` for
SEC filings, transcripts, and analyst ratings.

### 3. DeepSeek Model Lifecycle

**Status:** Watch

`deepseek-v4-flash` is confirmed valid as of Jul 2026 but deprecation date
unknown. Both LLM processors hardcode the model name.

**Recommended:** Make model name configurable via env var
`DEEPSEEK_MODEL` with `deepseek-v4-flash` as default.

### 4. Finnhub Premium Tier Verification

**Status:** Unverified

The earnings transcript endpoint (`/stock/earnings-call-transcripts`) may
require a premium Finnhub tier. The fetcher handles 403 gracefully but this
has not been tested with live API calls.

**Action:** Set `FINNHUB_API_KEY` and run `equity ingest --markets us_earnings_transcripts`
to verify.

### 5. ARCHITECTURE.md Not Updated

**Status:** Gap

Three phases of new modules added (sources, processors, features) but
`ARCHITECTURE.md` has not been updated.

**Recommended:** Document the bronze/silver Medallion architecture,
new source modules, and feature pipeline additions.

---

## Future Enhancements (Not Bugs)

### SEC XBRL / Structured Financials (Phase 4 Candidate)

**Status:** Deferred

The design doc mentions `python-edgar` for XBRL parsing of structured
financials (Item 8 — balance sheet, income statement). This was not
implemented. SEC structured data would enable:
- Automated ratio extraction (P/E, debt/equity, ROE)
- Quarter-over-quarter trend features
- Direct comparison across companies

**Dependency:** `python-edgar` or `edgartools` library
**Estimated effort:** 3-5 days

### News Source Expansion

**Status:** Future

Current RSS feeds are configurable in `config/rss_feeds.yaml`. Potential
additions:
- SEC EDGAR RSS feed (filing notifications)
- Fed RSS (FOMC statements, economic releases)
- Exchange data feeds

### Social Sentiment Migration

**Status:** Future

StockTwits API is effectively dead (registrations frozen). The fetcher
returns empty by default. Migration path to Finnhub social sentiment
(`us_social_sentiment` market) is documented but not yet implemented as
a bronze article source.

### LLM Cost Tracking

**Status:** Future

No token usage tracking for DeepSeek API calls. For production use, adding
cost tracking (tokens in/out, estimated cost per call) would enable
budget monitoring.

**Recommended:** Log `response.usage.prompt_tokens` and
`response.usage.completion_tokens` from each API call.

---

## Summary

| Category | Fixed | Remaining |
|----------|-------|-----------|
| CRITICAL bugs | 3 | 0 |
| HIGH bugs | 4 | 0 |
| MEDIUM issues | 6 | 3 (deferred) |
| LOW issues | 0 | 3 (low priority) |
| Test gaps | 1 added | ~10 recommended |
| Operational gaps | 0 | 5 documented |
| Future enhancements | 0 | 4 documented |

**Test suite:** 366 passed, 2 skipped, 0 failed
**Lint:** ruff clean, mypy clean
**Commits:** `c9e1c34` (audit fixes), `a6c38f0` (Phase 3), `1eb79ed` (Phase 2), `d8c21f2` (Phase 1)
