# Ingestion

These examples describe operational commands that can contact live providers
and write lake data. They are not run as part of documentation/review work;
source fetches, paid processing, and production writes require separate owner
authorization and explicit scope.

## Supported sources

The accepted identifiers are `us`, `cn`, `hk_sg`, `jpx`, `krx`, `macro`,
`us_news`, `us_social_sentiment`, `rss_news`, `reddit_posts`,
`stocktwits_messages`, `us_earnings_transcripts`, `us_analyst_ratings`,
`sec_filings_fulltext`, and `us_sec_financials`. The router and destination
map are the executable source of truth.

Use explicit tickers for targeted runs:

```bash
dotenvx run -- uv run equity ingest --markets us_equity --tickers AAPL,MSFT
dotenvx run -- uv run equity pipeline --markets us_equity --tickers AAPL,MSFT
```

Credentials are read from the environment. Use `dotenvx run --` for commands
that rely on `.env`; provider-specific credentials are described in
`docs/20260406-api-keys.md`.

## Destinations and operation

Ingestion writes date-partitioned Delta tables with Parquet data files beneath
`data/lake/01_bronze/` for raw sources and `data/lake/02_silver/` for validated
enrichments. `data/lake/03_gold/features/` and
`data/lake/04_platinum/predictions/` contain derived outputs.

### A second root for real-source rows (ADR-0030)

A real-source slice may live under **`data/lake_real/`** with the same numbered medallion
layout instead of sharing tables with the synthetic demo lake. The root is **only ever
selected explicitly** and never defaulted, discovered or mixed:

```bash
uv run equity corporate-actions --lake-dir data/lake_real --market us_equity --tickers AAPL,MSFT
uv run equity backfill --lake-dir data/lake_real --start 2024-01-02 --end 2024-12-31 --markets us_equity --tickers AAPL,MSFT
uv run equity ingest --lake-dir data/lake_real --date 2026-07-10 --markets us_equity
uv run equity monitor --lake-dir data/lake_real
```

- **Corporate actions come first.** A real-root price write reconstructs raw prices from
  that root's own Silver split actions (ADR-0029 Part I), because the vendor basis is
  split-back-adjusted while the adjusted read assumes unadjusted prices. If the action
  store is absent, a ticker has no stored action, a stored ratio cannot be inverted, or
  the store's newest `ex_date` predates the price rows, the write **refuses** with
  `action_store_missing` / `action_scope_incomplete` / `action_invalid` /
  `action_window_uncovered` and exits non-zero — nothing is written and no root is created.
  The transform is invisible in the schema: `adj_close` stays as fetched, the origin stays
  `unknown`, and no `price_adjustment_version` is stamped.
- `--tickers` scopes a backfill to an explicit list (the landing vehicle for a window); it
  is refused when empty rather than broadened to the configured universe.
- `--lake-dir` must resolve inside `data/`, must not be `data/` itself, must not sit inside
  the canonical lake and must not alias it (a symlink or `..` path that lands on `data/lake/`);
  anything else exits non-zero before any fetch or write.
- A real root carries the **`us_equity` slice only**, so `--markets` must name `us_equity`
  alone (or be omitted on `corporate-actions`, whose default is already `us_equity`). Any
  other price market — and every enrichment dataset `--markets` also accepts (`macro`,
  `us_news`, …) — is refused with `--lake-dir carries the us_equity slice only` and a
  non-zero exit. `ingest` otherwise defaults to the configured market list, which includes
  those enrichments, so name `us_equity` explicitly.
- Omitting it keeps the canonical `data/lake/` — no command reads or writes both roots, and
  there is no fallback when a requested root is absent.
- The real root is **not cataloged** (`data/catalog.jsonl` describes `data/lake/` only) and
  is never merged, copied or promoted into the demo root automatically. Health and
  readiness are reported per root, never summed.

```bash
dotenvx run -- uv run equity ingest --date 2026-07-10 --markets us_equity,cn_ashare
dotenvx run -- uv run equity ingest --dry-run --markets us_equity --tickers AAPL
dotenvx run -- uv run equity backfill --days-back 30 --markets us_equity
```

Dry-run performs no persistence. Fetchers use configured retries and ingestion
may run in parallel; backfill deliberately runs one market/date at a time. A
direct `equity ingest --dry-run` reports each market as `dry_run` — never
`written`. An empty fetch on a scheduled session is reported as `no_data`
(distinct from `skipped_existing`, which means a valid partition already
exists), a market whose exchange is closed on the requested date is reported as
`not_scheduled` (a planned skip, never a failure), and a source failure is
reported as `failed` with its cause rather than being hidden as a skip.

Feature history recovery is separate from ordinary ingestion and must be
authorized:

```bash
dotenvx run -- uv run equity pipeline --markets us_equity --tickers AAPL \
  --allow-history-backfill
```

Without that flag, missing warm-up history is a failed feature stage and no
backfill is started. When recovery runs, the number of backfilled dates is
recorded in the feature stage (`history_backfilled_dates`). Optional enrichment
failures are reported as partial success (`optional_failures` plus
`partial: true`) and do not block core features; required price-source failures
and missing required ticker coverage return a non-zero CLI status. Omitting
`--tickers` processes every configured active ticker in the requested markets,
and an explicitly empty `--tickers` value is rejected rather than broadened.

### Widening history with one range fetch (ADR-0033)

`equity backfill` re-fetches **one trading day per request**, which is right for gap
recovery and wrong for decades: forty years is roughly ten thousand requests. `equity
widen` fetches each ticker's whole window once, reconstructs raw prices once and writes
through the same validation boundary:

```bash
uv run equity widen --lake-dir data/lake_real --markets us_equity \
  --tickers AAPL,MSFT,GOOGL,NVDA,ADBE,BABA,QQQ,SPY,GLD --start 1980-01-01 --end 2026-11-18
```

Each ticker is **clamped to its own evidence** in the target root — the coverage table's
`coverage_start`..`coverage_end` (ADR-0032), or the stored actions' first/last `ex_date`.
A request wider than that is not silently honoured: the per-ticker window shrinks, and a
ticker with no evidence is reported (`no_action_coverage`, `empty_window`,
`actions_not_ingested`). Real windows are therefore data, not dates you choose: ADBE's
last action is 2005, so its price window ends there under ADR-0029 Part I.

An instrument with **no corporate actions at all** (for example `GLD`, a gold trust) is
landable only through the proven-empty path (ADR-0033): `widen` performs a vendor action
fetch that must **succeed**, and only a successful empty list is recorded as a weak
`vendor_events_no_clock` proof over the price window. A failed fetch never proves
emptiness and never writes a proof. The split factor for such an instrument is 1, so its
prices are written unchanged, and the weak basis makes it unusable for point-in-time
consumers exactly like any other vendor-stamped row.

## Ingesting News and Structured Content

Beyond price data, the CLI ingests the news, transcript, ratings, SEC, and
macro sources that feature enrichments join against. All of these are flat
commands on the unified `equity` CLI.

Finnhub-backed commands require `FINNHUB_API_KEY`; run them through `dotenvx`:

```bash
dotenvx run -- uv run equity news --tickers AAPL,MSFT
dotenvx run -- uv run equity sentiment --tickers AAPL,MSFT
dotenvx run -- uv run equity transcripts --tickers AAPL
dotenvx run -- uv run equity ratings --tickers AAPL,MSFT
```

| Command | Writes to | Notes |
|---|---|---|
| `equity news` | `02_silver/news_sentiment` | Finnhub company news scored locally. `--sentiment-method` `vader` (default) or `finbert`; `--max-articles` per ticker (default `50`); `--min-relevance` 0.0–1.0 (default `0.0`); `--max-workers` (default `1`). |
| `equity sentiment` | `02_silver/social_sentiment` | Finnhub social sentiment scores per ticker/date. `--max-workers` (default `1`). |
| `equity transcripts` | `01_bronze/raw_articles` | Earnings-call transcripts (`source_type="transcript"`) for the bronze→silver LLM pipeline. |
| `equity ratings` | `02_silver/analyst_ratings` | Structured analyst consensus and price targets — no LLM involved. |
| `equity sec` | `01_bronze/raw_articles` | 10-K/10-Q full text from EDGAR with `--lookback` days (default `120`). `--process` currently exits nonzero before fetching; no bounded provider actual-cost adapter is available. Omit it to fetch filings without LLM processing. |
| `equity financials` | `02_silver/sec_financials` | Structured XBRL financials (balance sheet, income statement, ratios) — no LLM. `--lookback` default `120`. |
| `equity corporate-actions` | `01_bronze/corporate_actions/<market>`, `02_silver/corporate_actions/<market>`, and the separate `02_silver/corporate_action_coverage` proof table | Dividends and splits (yfinance, ADR-0011). Event-driven and incremental with **per-ticker watermarks** (W03/F14), upserted on `(ticker, ex_date, action)`. The distinct coverage table uses proof schema version 2 and binds each assertion to `(market, MIC, symbol)`, source, coverage interval/status, timezone-aware `source_available_at`, and source-issued `source_valid_until`. `ingested_at` is not a source clock and cannot establish expiry. The native yfinance path currently emits no verified publication/coverage/expiry proof, so adjusted consumers remain unavailable. Corrections at or below a ticker's watermark are **not** re-detected (exclusive bound); the per-ticker window edges are logged and returned in the ingest result payload. |
| `equity macro` | `01_bronze/macro` | FRED and yfinance macro indicators (VIX, treasury yields, DXY, …); `--indicators` filters by name. Fails with a non-zero exit if nothing is fetched. |

Flags vary by command: the daily and date-scoped fetchers accept applicable
subsets of `--date`, `--tickers`/`-t`, `--dry-run`, and `--verbose`; the
corporate-action fetch uses `--market` and has no `--date` option. Only
Finnhub-backed commands read `FINNHUB_API_KEY` from the environment (there is no
`--api-key` option — keys never belong on the command line).
Key requirements:

- `FINNHUB_API_KEY` — `news`, `sentiment`, `transcripts`, `ratings`
- `SEC_USER_AGENT` — `sec` and `financials` (EDGAR fair-access policy)
- `FRED_API_KEY` — FRED-sourced macro indicators (yfinance indicators work
  without it; the fetcher logs a warning and skips FRED)

These tables feed the gold feature stage's optional enrichments — see
[Feature Generation and Enrichment](20260829-features-and-enrichments.md).
Optional enrichment absence is represented as unavailable/null, not as a
measured neutral value. Non-US source-family enrichment remains unsupported
where explicit identity and source-availability clocks are not available.

### Corporate-action proof and adjusted reads

The proof table's schema version 2 requires a source-issued publication clock
(`source_available_at`), complete coverage assertion with status and event-date
bounds, and source-issued expiry (`source_valid_until`) for the same
`(market, MIC, symbol)`. The current native yfinance corporate-action fetch
supplies action rows without a verified source publication timestamp and does
not issue a complete source-scoped coverage/expiry proof. The feature and
adjustment contracts require both; they do not synthesize proof from ingestion
time or assume that an empty action table means no actions. Consequently,
adjusted price consumers remain unavailable until verified source proof is
supplied. The transformation is split-only, excludes cash dividends, and never
rewrites raw Bronze prices. This is a current proof limitation, not a statement
about native provider availability.

### Bronze-to-Silver state and failure handling

An absent table or a truly empty directory is no-work: empty Bronze is reported
as `no_bronze_data`, and empty Silver means there are no processed article IDs.
Recognized Parquet tables and initialized Delta tables are read normally; a
nonempty directory with unrecognized contents, or an empty/malformed Delta log,
fails closed instead of being treated as empty. An unreadable Silver table
fails with `silver_read_failed` **before** the processor is invoked. This
prevents a Silver read failure from resubmitting the Bronze corpus to the
processor, including paid LLM processing. Bronze read failures are reported as
`bronze_read_failed`; processor and write failures remain distinct as
`processing_failed` and `write_failed`.

Bronze-to-Silver replay now requires explicit source/date/market/instrument
scope and is limited to 100 selected items per invocation in batches of at most
25. Its checkpoint and spend ledger are auxiliary state, separate from
cataloged lake tables; the processor rereads Silver as the authoritative
idempotency/durability source before advancing a checkpoint. Historical replay
does not notify. Paid work defaults to zero authorization and requires a
separate finite cumulative estimate, actual-spend cap, per-batch estimate, and
provider-reported actual cost. Unbounded LLM helpers intentionally fail closed;
there is no current provider-cost adapter, so these paths are not
paid-runtime-ready and `equity sec --process` fails before fetching. Pipeline
dry-run skips ingestion and processing entirely and persists nothing.

## Source Operational Notes

Limits and degradation behavior verified in `src/equity_lake/sources/`:

| Source | Operational behavior |
|---|---|
| StockTwits | Disabled by default — the fetcher returns an empty frame unless `STOCKTWITS_ENABLED=true` is set. |
| Reddit | Public `.json` endpoint, ~10 req/min per IP. The fetcher sleeps ~7 s between requests and inspects `X-Ratelimit-Remaining`, pausing for the reset window when the budget runs low. One run covers 6 subreddits (6 requests), which fits the budget. |
| Finnhub transcripts | The transcript endpoint may require a premium API tier. On HTTP 403 the fetcher logs the hint and degrades gracefully to an empty frame — the run succeeds, it just yields no transcripts. |
| SEC EDGAR | Hard rate limit of 10 req/s; the fetcher throttles inter-document requests. `SEC_USER_AGENT` must be set to `CompanyName AdminEmail@example.com` or the fetcher refuses to run. |
| `sec_filings_fulltext` vs `us_sec_financials` | Two distinct SEC sources: `equity sec` fetches filing **text** into Bronze (`01_bronze/raw_articles`); its `--process` option currently fails closed before any fetch because no bounded actual-cost adapter exists. `equity financials` fetches **structured XBRL** straight to `02_silver/sec_financials` with no LLM step. |

## Client-side rate limiting

By default no client-side throttling is applied beyond the per-source sleeps noted
above. Optional sliding-window throttling (requests per minute, per provider) can
be enabled via `config/settings.yaml` or env vars — off unless configured:

```yaml
sources:
  default_rpm: 60            # fallback for every fetcher
  rpm_overrides:
    finnhub: 50              # shared by the news and social-sentiment fetchers
    sec: 8
    reddit: 30
```

```bash
EQUITY_SOURCES__DEFAULT_RPM=60
EQUITY_SOURCES__RPM_OVERRIDES__FINNHUB=50
```

Throttling happens before every fetch attempt (retries included) and is keyed by
provider — `finnhub`, `yahoo`, `akshare`, `efinance`, `sec`, `reddit`, `rss`,
`stocktwits`, `transcripts`, `krx`, `cn_hybrid` — so fetchers sharing a provider
quota share one limiter. When a source would exceed its window, the fetch blocks
until capacity frees up (logged at debug level as `rate_limit_wait`).

## Troubleshooting

Start with `equity pipeline --help`, inspect `--save-results` JSON, and run
`equity monitor`. Verify the identifier in the supported list before debugging
provider credentials. Do not broaden a targeted run into a full-universe
backfill without explicitly changing `--markets` and `--tickers`.

## Related

- [Feature Generation and Enrichment](20260829-features-and-enrichments.md) — how these tables are joined into gold features
- [RAG Corpus Seeding](20260813-rag-corpus-seeding.md) — bronze/silver article tables as a corpus source
- [Pipeline](pipeline.md)
