# Integrations

**Last Updated**: 2026-08-29
**Project**: Equity EOD Data Pipeline

## Overview

All external data enters through the adapter layer in `src/equity_lake/sources/`.
Every adapter implements the `MarketDataFetcher` ABC from
`sources/base.py` (`fetch(trading_date) -> pl.DataFrame`, plus `fetch_range`
and config-driven ticker resolution) and gets its retry behavior from the
shared tenacity factory `core/retry.py::build_retry_decorator` — never from
hand-rolled loops.

Routing is declarative: `ingestion/router.py` maps each market identifier to a
fetcher factory in `MARKET_REGISTRY` (factories lazy-import their fetcher and
propagate configuration errors such as missing API keys to the caller). After a
fetch, `fetch_market_data_with_config` converts to polars and validates the
schema via `ingestion/writers.validate_schema` before the frame is handed to
the Delta writer.

`ingestion/types.py` classifies the 15 market identifiers:

- `REQUIRED_PRICE_MARKETS` = `us`, `cn`, `hk_sg`, `jpx`, `krx` — their failure
  blocks dependent features and ML.
- `OPTIONAL_ENRICHMENT_MARKETS` = everything else — their failure is recorded
  and the core feature path continues.

Destinations below come from `MARKET_DIR_MAP` in `ingestion/types.py`, derived
from the canonical path constants in `core/paths.py`. All tables are
date-partitioned Delta tables under `data/lake/`.

## Price-Market Fetchers

| Market ID | Adapter (`src/equity_lake/sources/`) | Upstream | Lake destination |
|---|---|---|---|
| `us` | `us.py` — `USEquityFetcher` (`YFinanceBaseFetcher`, batch size 500) | yfinance | `01_bronze/market_data/us_equity` |
| `cn` | `cn_hybrid.py` — `CNHybridFetcher` (uses `cn.py` `CNAshareFetcher`, `cn_efinance.py` `CNEfinanceFetcher`) | akshare (primary) → yfinance (fallback when akshare returns empty) → efinance (opt-in via `enable_efinance=True`) | `01_bronze/market_data/cn_ashare` |
| `hk_sg` | `hk_sg.py` — `HKSGEquityFetcher` (splits `.HK` / `.SI` symbols) | yfinance | `01_bronze/market_data/hk_sg_equity` |
| `jpx` | `jpx.py` — `JPXEquityFetcher` (`.T`-suffixed tickers, e.g. `7203.T`) | yfinance | `01_bronze/market_data/jpx_equity` |
| `krx` | `krx.py` — `KRXEquityFetcher` (6-digit codes, e.g. `005930`; default retry delay 2s) | finance-datareader | `01_bronze/market_data/krx_equity` |

All five write the standard OHLCV schema (`core/schemas.py::STANDARD_COLUMNS`).
An optional one-time S3 bootstrap (`storage/s3_sync.py`, `equity sync` in
`cli/commands/data.py`) can seed US history before daily fetches; boto3/s5cmd
live in the optional `s3` dependency group.

## Enrichment & News Adapters

| Market ID | Adapter (`src/equity_lake/sources/`) | Upstream | Destination | Notes |
|---|---|---|---|---|
| `macro` | `macro.py` — `MacroFetcher` / `MacroDataPipeline` (`FredFetcher`, `YFinanceFetcher` per `MACRO_INDICATOR_CONFIG`) | FRED + yfinance (DXY, Treasury 10Y, TIPS, breakeven inflation, VIX, gold, policy uncertainty) | `01_bronze/macro` | `FRED_API_KEY` needed for FRED series; yfinance indicators key-free |
| `us_news` | `news.py` — `FinnhubNewsFetcher` | Finnhub company news | `02_silver/news_sentiment` | VADER sentiment computed at fetch time |
| `us_social_sentiment` | `sentiment.py` — `FinnhubSocialSentimentFetcher` | Finnhub social sentiment (Reddit/Twitter metrics) | `02_silver/social_sentiment` | |
| `reddit_posts` | `reddit.py` — `RedditFetcher` | Reddit public `.json` endpoint (no OAuth) | `01_bronze/raw_articles` | Budget ~10 req/min; ~7s inter-request delay plus `X-Ratelimit-Remaining` inspection; `REDDIT_USER_AGENT` required in `<platform>:<app-id>:<version> (by u/<username>)` format; subreddits from `config/social_sources.yaml` |
| `rss_news` | `rss.py` — `RSSNewsFetcher` | RSS/Atom feeds via feedparser | `01_bronze/raw_articles` | Feeds configured in `config/rss_feeds.yaml` |
| `stocktwits_messages` | `stocktwits.py` — `StockTwitsFetcher` | StockTwits API | `01_bronze/raw_articles` | Disabled by default (`STOCKTWITS_ENABLED=false`); developer registrations are frozen; returns an empty frame with a warning when disabled. Use `us_social_sentiment` instead |
| `us_earnings_transcripts` | `transcripts.py` — `EarningsTranscriptFetcher` | Finnhub earnings-call transcripts | `01_bronze/raw_articles` | Endpoint may require a premium tier; degrades gracefully to empty; content flows through the bronze→silver LLM pipeline |
| `us_analyst_ratings` | `analyst_ratings.py` — `AnalystRatingFetcher` | Finnhub `/stock/recommendation` + `/stock/price-target` | `02_silver/analyst_ratings` | Already structured — no LLM processing |
| `sec_filings_fulltext` | `sec_fulltext.py` — `SECFilingFetcher` | SEC EDGAR submissions + archives (10-K/10-Q) | `01_bronze/raw_articles` | readability-lxml text extraction, sectioned (Item 1A risk factors, Item 7 MD&A); SEC rate limit 10 req/s; `SEC_USER_AGENT` recommended |
| `us_sec_financials` | `sec_financials.py` — `SECFinancialsFetcher` | SEC XBRL via edgartools | `02_silver/sec_financials` | Structured numeric extraction with ratios — no LLM; edgartools throttles EDGAR access internally |

Adapters whose destination is `01_bronze/raw_articles` are unstructured
inputs; the bronze→silver LLM processors (`ingestion/`, DeepSeek via the
OpenAI-compatible client in `ingestion/llm_base.py`) turn them into
`02_silver/processed_articles` and `02_silver/sec_extractions`.

## Keys & Authentication

Keys are raw, unprefixed environment variables read with `os.getenv` at each
client seam — they are deliberately not declared in `Settings` (which is
`extra="forbid"`). Run commands through dotenvx so `.env` is loaded:

```bash
cp .env.example .env
dotenvx run -- uv run equity ingest --markets us
```

| Variable | Required for | Notes |
|---|---|---|
| `FRED_API_KEY` | `macro` (FRED series) | Free key from FRED |
| `FINNHUB_API_KEY` | `us_news`, `us_social_sentiment`, `us_earnings_transcripts`, `us_analyst_ratings` | The router raises when unset for these markets |
| `REDDIT_USER_AGENT` | `reddit_posts` | Format `<platform>:<app-id>:<version> (by u/<username>)`; `REDDIT_CLIENT_ID`/`REDDIT_CLIENT_SECRET` are listed for the praw-based Reddit sentiment loader (`sentiment` group) |
| `SEC_USER_AGENT` | `sec_filings_fulltext`, `us_sec_financials` | Descriptive agent with contact email, per SEC policy |
| `STOCKTWITS_ENABLED`, `STOCKTWITS_CLIENT_ID` | `stocktwits_messages` | Off by default |
| `DEEPSEEK_API_KEY` | Bronze→silver LLM enrichment | OpenAI-compatible client against `api.deepseek.com` |
| `OPENROUTER_API_KEY` | Embeddings (RAG vector index) | |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `S3_BUCKET` | `equity sync` (S3 bootstrap) | Only for private buckets; boto3/s5cmd in the `s3` group |

Full key-by-feature guide: [API Keys and Credentials](../../20260406-api-keys.md).

Core price ingestion (US, CN, HK/SG, JPX, KRX) needs no API key at all.

## Rate Limiting & Retry

- **Retry (all adapters)**: tenacity via `core/retry.py::build_retry_decorator`
  — exponential backoff, default 3 attempts, wait capped at 30s, `reraise=True`.
  Only `TransientError` (network, timeout, HTTP 5xx) is retried; 4xx and
  configuration errors propagate immediately. Attempt counts/delays default
  from `Settings.ingestion`, overridable via `EQUITY_INGESTION__RETRY_ATTEMPTS`
  / `EQUITY_INGESTION__RETRY_DELAY`.
- **yfinance adapters**: batched downloads (500 tickers per batch for US/JPX).
- **CN**: per-stock fetches parallelized via `ThreadPoolExecutor`
  (`max_workers=10`), with adaptive fallback between sources.
- **KRX**: default retry delay 2s.
- **Reddit**: unauthenticated IP-based budget of ~10 requests/minute enforced
  by ~7s spacing and `X-Ratelimit-Remaining` header checks.
- **SEC EDGAR**: 10 requests/second limit; `edgartools` throttles internally
  for the XBRL path; a descriptive `SEC_USER_AGENT` is expected by policy.
- **Graceful degradation**: optional enrichment failures are logged and
  recorded; required price-market failures block dependent stages
  (see [pipeline contracts](pipeline-contracts.md)).

## Storage Destinations

Written by `storage/delta.py` as date-partitioned Delta tables (Parquet data
files) under `data/lake/`:

- **01_bronze**: `market_data/{us_equity, cn_ashare, hk_sg_equity, jpx_equity,
  krx_equity}`, `macro`, `raw_articles` (news, Reddit, RSS, StockTwits,
  transcripts, SEC filings).
- **02_silver**: `news_sentiment`, `social_sentiment`, `analyst_ratings`,
  `sec_financials` (direct-from-source structured outputs), plus
  `processed_articles` / `sec_extractions` (LLM outputs).
- **03_gold / 04_platinum**: derived features and predictions, not written by
  source adapters.

Only tables under `data/lake/` are cataloged and pointblank-validated at the
ingestion write boundary (`validation/pipeline.py`).

## Testing

Fetcher unit tests mock the upstream libraries — no network needed (network
tests are marked and excluded from the default suite):

- `tests/unit/test_fetchers.py` — US, CN (akshare, efinance, hybrid)
- `tests/unit/test_jpx_krx_fetchers.py` — JPX and KRX
- `tests/unit/test_news_fetcher.py` — Finnhub news + `SentimentAnalyzer`
- `tests/unit/test_social_sentiment.py`, `tests/unit/test_sentiment_generator.py`
- `tests/unit/test_reddit_fetcher.py`, `tests/unit/test_rss_fetcher.py`,
  `tests/unit/test_stocktwits_fetcher.py`
- `tests/unit/test_transcripts.py`, `tests/unit/test_analyst_ratings.py`
- `tests/unit/test_sec_fulltext.py`, `tests/unit/test_sec_fulltext_edge.py`,
  `tests/unit/test_sec_processor.py`, `tests/unit/test_sec_financials.py`
- `tests/unit/test_macro_sources.py`
- `tests/unit/test_router.py` — `MARKET_REGISTRY` routing
- `tests/unit/test_ingestion_orchestrator.py` — orchestration incl. HK/SG

## Monitoring Touchpoints

- **structlog** JSON logging with correlation IDs in every adapter.
- Per-market outcomes are structured `SourceOutcome`s (`written`,
  `skipped_existing`, `failed`) serialized into the pipeline results payload.
- `equity monitor` (`cli/commands/analysis.py` → `monitoring/health.py`)
  checks freshness and quality across the medallion tables, including
  JPX/KRX bronze directories; `monitoring/alerting.py` dispatches alerts.
- `equity query` (`cli/commands/analysis.py`) exposes DuckDB analytical reads
  over the lake (`storage/duckdb.py`, `storage/lake_reader.py`).
