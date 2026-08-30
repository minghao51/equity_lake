# Ingestion

## Supported sources

The accepted identifiers are `us`, `cn`, `hk_sg`, `jpx`, `krx`, `macro`,
`us_news`, `us_social_sentiment`, `rss_news`, `reddit_posts`,
`stocktwits_messages`, `us_earnings_transcripts`, `us_analyst_ratings`,
`sec_filings_fulltext`, and `us_sec_financials`. The router and destination
map are the executable source of truth.

Use explicit tickers for targeted runs:

```bash
dotenvx run -- uv run equity ingest --markets us --tickers AAPL,MSFT
dotenvx run -- uv run equity pipeline --markets us --tickers AAPL,MSFT
```

Credentials are read from the environment. Use `dotenvx run --` for commands
that rely on `.env`; provider-specific credentials are described in
`docs/20260406-api-keys.md`.

## Destinations and operation

Ingestion writes date-partitioned Delta tables with Parquet data files beneath
`data/lake/01_bronze/` for raw sources and `data/lake/02_silver/` for validated
enrichments. `data/lake/03_gold/features/` and
`data/lake/04_platinum/predictions/` contain derived outputs.

```bash
dotenvx run -- uv run equity ingest --date 2026-07-10 --markets us,cn
dotenvx run -- uv run equity ingest --dry-run --markets us --tickers AAPL
dotenvx run -- uv run equity backfill --days-back 30 --markets us
```

Dry-run performs no persistence. Fetchers use configured retries and ingestion
may run in parallel; backfill deliberately runs one market/date at a time.

Feature history recovery is separate from ordinary ingestion and must be
authorized:

```bash
dotenvx run -- uv run equity pipeline --markets us --tickers AAPL \
  --allow-history-backfill
```

Without that flag, missing warm-up history is a failed feature stage and no
backfill is started. Optional enrichment failures are reported as partial
success; required price-source failures return a non-zero CLI status.

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
| `equity sec` | `01_bronze/raw_articles` | 10-K/10-Q full text from EDGAR with `--lookback` days (default `120`). Add `--process` to run the bronze→silver LLM extraction to `02_silver/sec_extractions` in the same command. |
| `equity financials` | `02_silver/sec_financials` | Structured XBRL financials (balance sheet, income statement, ratios) — no LLM. `--lookback` default `120`. |
| `equity macro` | `01_bronze/macro` | FRED and yfinance macro indicators (VIX, treasury yields, DXY, …); `--indicators` filters by name. Fails with a non-zero exit if nothing is fetched. |

All of them share `--date`, `--tickers`/`-t`, `--dry-run`, and `--verbose`,
and read the Finnhub key from the `FINNHUB_API_KEY` environment variable
(there is no `--api-key` option — keys never belong on the command line).
Key requirements:

- `FINNHUB_API_KEY` — `news`, `sentiment`, `transcripts`, `ratings`
- `SEC_USER_AGENT` — `sec` and `financials` (EDGAR fair-access policy)
- `FRED_API_KEY` — FRED-sourced macro indicators (yfinance indicators work
  without it; the fetcher logs a warning and skips FRED)

These tables feed the gold feature stage's optional enrichments — see
[Feature Generation and Enrichment](20260829-features-and-enrichments.md).

## Source Operational Notes

Limits and degradation behavior verified in `src/equity_lake/sources/`:

| Source | Operational behavior |
|---|---|
| StockTwits | Disabled by default — the fetcher returns an empty frame unless `STOCKTWITS_ENABLED=true` is set. |
| Reddit | Public `.json` endpoint, ~10 req/min per IP. The fetcher sleeps ~7 s between requests and inspects `X-Ratelimit-Remaining`, pausing for the reset window when the budget runs low. One run covers 6 subreddits (6 requests), which fits the budget. |
| Finnhub transcripts | The transcript endpoint may require a premium API tier. On HTTP 403 the fetcher logs the hint and degrades gracefully to an empty frame — the run succeeds, it just yields no transcripts. |
| SEC EDGAR | Hard rate limit of 10 req/s; the fetcher throttles inter-document requests. `SEC_USER_AGENT` must be set to `CompanyName AdminEmail@example.com` or the fetcher refuses to run. |
| `sec_filings_fulltext` vs `us_sec_financials` | Two distinct SEC sources: `equity sec` fetches filing **text** into bronze (`01_bronze/raw_articles`) and requires LLM processing (`--process`) to reach silver (`02_silver/sec_extractions`); `equity financials` fetches **structured XBRL** straight to `02_silver/sec_financials` with no LLM step. |

## Troubleshooting

Start with `equity pipeline --help`, inspect `--save-results` JSON, and run
`equity monitor`. Verify the identifier in the supported list before debugging
provider credentials. Do not broaden a targeted run into a full-universe
backfill without explicitly changing `--markets` and `--tickers`.

## Related

- [Feature Generation and Enrichment](20260829-features-and-enrichments.md) — how these tables are joined into gold features
- [RAG Corpus Seeding](20260813-rag-corpus-seeding.md) — bronze/silver article tables as a corpus source
- [Pipeline](pipeline.md)
