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

## Troubleshooting

Start with `equity pipeline --help`, inspect `--save-results` JSON, and run
`equity monitor`. Verify the identifier in the supported list before debugging
provider credentials. Do not broaden a targeted run into a full-universe
backfill without explicitly changing `--markets` and `--tickers`.
