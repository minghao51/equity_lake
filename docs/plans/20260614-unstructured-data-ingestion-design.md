# Unstructured Data Ingestion Layer Design

**Date:** 2026-06-14
**Status:** Approved (brainstormed)
**Scope:** Phased enhancement of unstructured data acquisition across financial news, social media, earnings transcripts, analyst reports, and SEC filings.

---

## 1. Problem Statement

The current ingestion pipeline supports structured market data (OHLCV across US, CN, HK/SG, JPX, KRX markets) and limited unstructured data (Finnhub company news with VADER sentiment, Finnhub social sentiment counts, SEC filing metadata). The system lacks:

- Raw text acquisition from diverse media sources (RSS feeds, social platforms)
- LLM-based sentiment and entity extraction (VADER only, no financial-domain NLP)
- Earnings call transcripts and analyst rating data
- SEC full-text body extraction (metadata only currently)
- A Medallion data architecture for raw vs processed unstructured data

This design defines a comprehensive unstructured data layer, delivered in phases.

---

## 2. Architecture Overview

### Design Decision: EOD-Coupled

Unstructured ingestion runs **within** the existing `run_daily_ingestion()` orchestrator and `execute_eod_pipeline()`. No separate scheduler or decoupled track. All new sources route through the existing `ingestion/router.py` dispatch pattern.

```
equity pipeline → execute_eod_pipeline()
  ├── Stage 1: run_daily_ingestion()
  │     ├── [existing] OHLCV + macro + Finnhub news/sentiment
  │     └── [NEW] Unstructured ingestion track:
  │           ├── Fetch (RSS + Reddit + StockTwits)
  │           ├── Bronze layer → raw text storage
  │           ├── LLM batch processing (DeepSeek v4-flash + instructor)
  │           └── Silver layer → exploded article-ticker pairs
  ├── Stage 2: run_feature_job() → Hamilton DAG
  │     └── [NEW] merge_enriched_sentiment_features() reads silver layer
  └── Stage 3: run_prediction_job()
```

### Medallion Storage

| Layer | Location | Purpose | Content |
|-------|----------|---------|---------|
| **Bronze** | `data/lake/bronze/raw_articles/` | Raw fidelity | Full article/post text, metadata, no transformation |
| **Silver** | `data/lake/silver/processed_articles/` | Feature-ready | LLM-enriched, exploded to article-ticker pairs |

The feature pipeline (Stage 2) only reads silver. Bronze is searchable for debugging and re-processing.

---

## 3. Phasing Plan

### Phase 1 — Financial News RSS + Reddit + StockTwits

**Categories:** A (RSS) + B (Social)

Free, reliable APIs. Highest daily signal volume. RSS feeds are HTTP GET + XML parse. Reddit has a public JSON API. StockTwits has a free developer API.

| Module | Source | Data |
|--------|--------|------|
| `sources/rss.py` | RSS/Atom feeds (Reuters, MarketWatch, Seeking Alpha, CNBC, Yahoo Finance, Investopedia, Barron's, Motley Fool) | Article title, body, URL, published_at, author |
| `sources/reddit.py` | Reddit JSON API (r/wallstreetbets, r/stocks, r/investing, r/stockmarket, r/ValueInvesting, r/algotrading) | Post title, body, score, upvote_ratio, top comments |
| `sources/stocktwits.py` | StockTwits developer API (symbol streams) | Message body, built-in bullish/bearish sentiment, user, created_at |
| `ingestion/llm_processor.py` | DeepSeek v4-flash via `instructor` | Batch structured extraction (sentiment, tickers, events, summary) |
| `ingestion/bronze_silver.py` | Internal | Bronze write + silver explode + ticker validation |
| `features/enriched_sentiment.py` | Silver Delta table | Feature aggregation and Hamilton DAG merge |

**Config files:**
- `config/rss_feeds.yaml` — Feed URLs, categories, full_body flags
- `config/social_sources.yaml` — Subreddits, post/comment limits, StockTwits symbols

### Phase 2 — Earnings Transcripts + Analyst Ratings

**Categories:** C (Transcripts) + D (Analyst)

Higher structure, requires specific providers. Transcripts are quarterly cadence (lower volume, higher value).

| Source | Provider | Data |
|--------|----------|------|
| Earnings transcripts | Motley Fool API (free tier) or Seeking Alpha scraping | Full transcript text, quarter, fiscal year, participants |
| Analyst ratings | MarketBeat RSS, TipRanks API, Finnhub analyst recommendation (existing API key) | Rating (buy/hold/sell), price target, analyst firm, date |

### Phase 3 — SEC EDGAR Full-Text

**Category:** E (Regulatory)

Most complex parsing. Extends existing `SECFilingsLoader` to extract and chunk 10-K/10-Q body text (risk factors, MD&A, financials). Requires section segmentation and large-document handling.

---

## 4. Source Architecture (Phase 1)

### Class Hierarchy

All new fetchers extend the existing `MarketDataFetcher` base class (`sources/base.py`) with tenacity retry, config-driven ticker lists, and the standard `fetch()` contract.

```
MarketDataFetcher  (tenacity retry, config loading)
├── [existing] YFinanceBaseFetcher, CNAshareFetcher, etc.
├── [NEW] RSSNewsFetcher
├── [NEW] RedditFetcher
└── [NEW] StockTwitsFetcher
```

### RSS Feed Fetcher (`sources/rss.py`)

```python
class RSSNewsFetcher(MarketDataFetcher):
    """Fetches financial news from RSS/Atom feeds."""
    # Uses feedparser for XML parsing
    # Extracts: title, body/summary, url, published_at, source, author
    # Full body extraction via readability (config-driven per feed)
    # Ticker extraction deferred to LLM (bronze stores raw)
```

**Feed configuration** (`config/rss_feeds.yaml`):

```yaml
feeds:
  - name: reuters_business
    url: "https://feeds.reuters.com/reuters/businessNews"
    category: [business, macro]
    full_body: false
  - name: seeking_alpha_market_current
    url: "https://seekingalpha.com/market_currents.xml"
    category: [earnings, analyst, stock]
    full_body: true
  - name: marketwatch_topstories
    url: "https://feeds.content.dowjones.io/public/rss/mw_topstories"
    category: [stock, macro]
    full_body: false
  - name: cnbc_top_news
    url: "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"
    category: [business, stock]
    full_body: false
  # ... Yahoo Finance, Investopedia, Barron's, Motley Fool
```

### Reddit Fetcher (`sources/reddit.py`)

```python
class RedditFetcher(MarketDataFetcher):
    """Fetches posts + top comments from financial subreddits."""
    # Reddit JSON API: https://www.reddit.com/r/{sub}/hot.json
    # No auth needed for public data (rate-limited, tenacity handles 429)
    # Fetches: post title, body, score, upvote_ratio, num_comments
    # Also fetches top N comments per post for richer context
```

**Subreddit configuration** (`config/social_sources.yaml`):

```yaml
reddit:
  subreddits:
    - name: wallstreetbets
      post_limit: 50
      comment_limit: 5
    - name: stocks
      post_limit: 30
      comment_limit: 5
    - name: investing
      post_limit: 30
      comment_limit: 3
    - name: stockmarket
      post_limit: 30
      comment_limit: 3
    - name: ValueInvesting
      post_limit: 20
      comment_limit: 3
    - name: algotrading
      post_limit: 20
      comment_limit: 3

stocktwits:
  # Iterates over watchlist tickers from config/watchlist.yaml
  messages_per_symbol: 30
```

### StockTwits Fetcher (`sources/stocktwits.py`)

```python
class StockTwitsFetcher(MarketDataFetcher):
    """Fetches symbol-specific streams from StockTwits API."""
    # GET https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json
    # Free tier: 200 req/hour, 40 messages per call
    # Extracts: body, sentiment (built-in bull/bear), user, created_at
    # Returns per-ticker messages (already ticker-tagged by nature)
```

StockTwits already tags messages with sentiment (bullish/bearish) — free structured data that supplements the LLM extraction.

### Router Integration

`ingestion/router.py` gains three new market type dispatches:

| Market key | Fetcher | ALWAYS_REFETCH |
|------------|---------|----------------|
| `rss_news` | `RSSNewsFetcher` | Yes |
| `reddit_posts` | `RedditFetcher` | Yes |
| `stocktwits_messages` | `StockTwitsFetcher` | Yes |

Added to `ALWAYS_REFETCH` set in `orchestrator.py:65-67` (alongside existing `macro`, `us_news`, `us_social_sentiment`).

---

## 5. LLM Processing Pipeline (`ingestion/llm_processor.py`)

### Technology Stack

| Component | Choice | Rationale |
|-----------|--------|-----------|
| LLM provider | **DeepSeek v4-flash** | `deepseek-chat` deprecated 2026/07/24. v4-flash is fast, high-throughput, ~$0.14/M input tokens |
| Client | **`AsyncOpenAI`** with DeepSeek base_url | OpenAI-compatible SDK, native async |
| Structured output | **`instructor`** library | Pydantic-validated responses, auto-retry on validation failure, `Mode.MD_JSON` for DeepSeek |
| Rate limiting | **`asyncio.Semaphore(10)`** | DeepSeek uses concurrency limits (2,500 for v4-flash), not RPM — semaphore is sufficient |
| Fallback | **VADER** (existing `sentiment/analyzer.py`) | Graceful degradation when API fails |

### Pydantic Extraction Models

```python
class SentimentResult(BaseModel):
    score: float = Field(ge=-1.0, le=1.0, description="Sentiment score from -1.0 (bearish) to 1.0 (bullish)")
    label: str = Field(description="bullish | bearish | neutral")
    confidence: float = Field(ge=0.0, le=1.0)


class ArticleExtraction(BaseModel):
    id: str = Field(description="Source article UUID for mapping back")
    mentioned_tickers: list[str] = Field(default_factory=list)
    sentiment: SentimentResult
    event_type: str = Field(description="earnings | m&a | product | regulatory | analyst | macro | general")
    key_entities: list[str] = Field(default_factory=list)
    summary: str = Field(description="1-2 sentence concise summary")
    impact_horizon: str = Field(description="short (days) | medium (weeks) | long (months)")
    market_relevance: float = Field(ge=0.0, le=1.0)


class BatchExtraction(BaseModel):
    items: list[ArticleExtraction]
```

### Async Batch Processing

```python
import instructor
from openai import AsyncOpenAI

deepseek_client = instructor.from_provider(
    "deepseek/deepseek-v4-flash",
    base_url="https://api.deepseek.com",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    mode=instructor.Mode.MD_JSON,
    async_client=True,
    max_retries=3,
)


class DeepSeekBatchProcessor:
    def __init__(self, batch_size: int = 15, max_concurrency: int = 10):
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrency)

    async def process_batch(self, batch: list[RawArticle]) -> BatchExtraction:
        async with self.semaphore:
            result = await deepseek_client.create(
                response_model=BatchExtraction,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_WITH_SCHEMA},
                    {"role": "user", "content": self._format_batch(batch)},
                ],
                max_tokens=8192,
            )
            return result

    async def process_all(self, items: list[RawArticle]) -> list[ArticleExtraction]:
        batches = self._chunk(items, self.batch_size)
        tasks = [self.process_batch(b) for b in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        extracted = []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("Batch failed, falling back to VADER", error=str(r))
                extracted.extend(self._vader_fallback(r.batch))
            else:
                extracted.extend(r.items)
        return extracted
```

### Batch Grouping Strategy

Articles batched by **source type** for coherent context:

| Batch | Source | Grouping | Items/Batch |
|-------|--------|----------|-------------|
| 1-N | RSS articles | By source_type | 15 |
| N+1-M | Reddit posts | By source_type | 15 |
| M+1-P | StockTwits messages | By source_type | 20 (shorter text) |

For ~300 items/day, this yields ~15-20 API calls total.

### Token Budget & Cost Controls

| Control | Value | Rationale |
|---------|-------|-----------|
| Article body max | ~2,000 tokens | Full article context for quality extraction |
| Batch size | 15 articles per call | ~30K input tokens/batch |
| Max output tokens | 8,192 per batch | Full structured extraction for 15 items |
| Max concurrency | 10 parallel batches | Well under 2,500 concurrent limit |
| Est. daily cost | ~$0.50-1.00/day | ~20 calls x ~30K input tokens + ~8K output tokens each |
| Rate limiting | `asyncio.Semaphore(10)` | DeepSeek is concurrency-limited, not RPM-limited |
| Failure handling | VADER fallback per failed batch | Graceful degradation |
| Dedup | URL hash pre-filter | Skip already-processed articles |

### Key Advantages of `instructor` over Raw JSON

1. **Pydantic validation** — response auto-validated against schema. Invalid fields trigger auto-retry (up to 3 attempts).
2. **Type-safe** — IDE autocomplete, mypy strict compliance.
3. **Auto-retry on validation failure** — no manual JSON parse/retry logic.
4. **`Mode.MD_JSON`** for DeepSeek — wraps JSON mode with markdown code fences for reliability.

---

## 6. Storage Schema (Bronze/Silver)

### Bronze Layer — Raw Articles (`data/lake/bronze/raw_articles/`)

Written during ingestion, before LLM processing. Stores everything fetched, untransformed.

| Column | Type | Description |
|--------|------|-------------|
| `article_id` | str | UUID, primary key |
| `source_type` | str | rss \| reddit \| stocktwits |
| `source_name` | str | reuters, r/wallstreetbets, stocktwits |
| `source_url` | str | Original URL (dedup key) |
| `title` | str | Headline or post title |
| `body` | str | Full text (RSS: extracted body; Reddit: post+comments; StockTwits: message) |
| `author` | str | Article author / Reddit user / StockTwits user |
| `published_at` | datetime | Article publish timestamp |
| `fetched_at` | datetime | Ingestion timestamp |
| `source_metadata` | str | JSON blob (upvote_ratio, num_comments, score, tags, etc.) |
| `date` | date | Partition key (= published_at.date()) |

**Dedup key:** `source_url`

### Silver Layer — Processed Article-Ticker Pairs (`data/lake/silver/processed_articles/`)

Exploded from bronze after LLM processing. One row per article x ticker pair.

| Column | Type | Description |
|--------|------|-------------|
| `article_id` | str | FK to bronze |
| `ticker` | str | Extracted ticker (one row per mentioned ticker; NULL for macro/general) |
| `source_type` | str | rss \| reddit \| stocktwits |
| `source_name` | str | |
| `published_at` | datetime | |
| `date` | date | Partition key |
| `sentiment_score` | float | -1.0 to 1.0 |
| `sentiment_label` | str | bullish \| bearish \| neutral |
| `confidence` | float | 0.0 to 1.0 |
| `event_type` | str | earnings \| m&a \| product \| regulatory \| analyst \| macro \| general |
| `summary` | str | LLM-generated 1-2 sentence summary |
| `impact_horizon` | str | short \| medium \| long |
| `market_relevance` | float | 0.0 to 1.0 |
| `key_entities` | str | JSON array serialized |
| `source_metadata` | str | Passthrough from bronze |

**Dedup key:** `(article_id, ticker)`

### Bronze to Silver Transform (`ingestion/bronze_silver.py`)

```
bronze articles
  |
  +-- LLM batch processing (DeepSeek -> BatchExtraction)
  |     \__ each article -> ArticleExtraction with mentioned_tickers[]
  |
  +-- Explode: mentioned_tickers[] -> one row per ticker
  |     \__ Articles with no tickers -> ticker=NULL (kept for macro/general news)
  |
  +-- Ticker validation: filter against config/tickers.yaml watchlist
  |     \__ Unknown tickers dropped (reduces noise)
  |
  +-- Write to silver Delta table (merge_delta with article_id+ticker dedup)
```

### Table Registration

New entries in `writers.py`:

```python
BRONZE_DEDUPE_KEYS = {"bronze_raw_articles": ["source_url"]}
SILVER_DEDUPE_KEYS = {"silver_processed_articles": ["article_id", "ticker"]}
```

New entries in `orchestrator.py` `ALWAYS_REFETCH`: `bronze_raw_articles`, `silver_processed_articles`.

---

## 7. Feature Engineering Integration

### New Module: `features/enriched_sentiment.py`

```python
def merge_enriched_sentiment_features(
    price_df: pl.DataFrame,
    silver_table_path: str,
    aggregation: str = "daily",
) -> pl.DataFrame:
```

Reads the silver Delta table via DuckDB, aggregates per-ticker daily features, and left-joins into the price DataFrame. Follows the existing `merge_sentiment_features()` pattern at `features/engineering.py:181`.

### Aggregated Features Per Ticker Per Day

| Feature | Description |
|---------|-------------|
| `enriched_article_count` | Number of articles mentioning ticker that day |
| `enriched_sentiment_mean` | Average sentiment score (-1.0 to 1.0) |
| `enriched_sentiment_ewma_3d` | 3-day exponentially weighted sentiment |
| `enriched_sentiment_ewma_7d` | 7-day EWMA sentiment |
| `enriched_confidence_mean` | Average LLM confidence |
| `enriched_relevance_mean` | Average market relevance score |
| `bullish_ratio` | Fraction of articles bullish (vs bearish+neutral) |
| `event_type_top` | Most frequent event type (categorical for one-hot encoding) |
| `social_volume` | Reddit + StockTwits post count (source_type filter) |
| `social_sentiment_mean` | Sentiment from social sources only |
| `breaking_news_flag` | 1 if any article has market_relevance > 0.8 and impact_horizon == "short" |

### Integration into `run_feature_job()`

Following the existing opt-in pattern (`features/__init__.py:30-31`):

```python
run_feature_job(
    tickers=["AAPL", "MSFT"],
    include_sentiment=True,              # existing Finnhub
    include_social_sentiment=True,       # existing Finnhub social
    include_enriched_sentiment=True,     # NEW - silver layer
)
```

`FeatureEngineer` gets a new DuckDB view:

```sql
CREATE OR REPLACE VIEW enriched_sentiment AS
SELECT ticker, date, sentiment_score, sentiment_label, confidence,
       event_type, market_relevance, source_type, impact_horizon
FROM delta_scan('silver/processed_articles')
WHERE ticker IS NOT NULL
```

### Hamilton DAG Impact

New features become input nodes in the Hamilton DAG (`features/pipeline.py`). Computed in `FeatureEngineer` before the Hamilton `.compute()` call, same as current sentiment features:

```
FeatureEngineer pipeline:
  1. Load OHLCV (DuckDB view)
  2. [existing] merge_sentiment_features()          -> Finnhub news
  3. [existing] merge_social_sentiment_features()    -> Finnhub social
  4. [existing] merge_macro_features()               -> macro indicators
  5. [NEW] merge_enriched_sentiment_features()       -> silver layer
  6. Pass enriched DataFrame -> Hamilton DAG -> compute features
```

ML model in Stage 3 automatically picks up new features since it reads from the feature DataFrame.

### Relationship with Existing Finnhub News

The existing `us_news` and `us_social_sentiment` tables stay as-is. The enriched sentiment layer is **additive**, not a replacement. Over time Finnhub can be deprecated if the enriched layer provides better coverage, but Phase 1 keeps both running in parallel.

---

## 8. New Modules Summary

| Module | Purpose |
|--------|---------|
| `sources/rss.py` | RSS/Atom feed fetcher (feedparser + tenacity) |
| `sources/reddit.py` | Reddit JSON API fetcher (subreddit posts + comments) |
| `sources/stocktwits.py` | StockTwits developer API fetcher |
| `ingestion/llm_processor.py` | DeepSeek batch processing via instructor (structured extraction) |
| `ingestion/bronze_silver.py` | Bronze write + silver explode logic |
| `features/enriched_sentiment.py` | Feature merge for silver-layer data into Hamilton DAG |

### New Config Files

| File | Purpose |
|------|---------|
| `config/rss_feeds.yaml` | RSS feed URLs, categories, full_body flags |
| `config/social_sources.yaml` | Subreddits, post/comment limits, StockTwits settings |

### New Dependencies

| Package | Purpose |
|---------|---------|
| `feedparser` | RSS/Atom XML parsing |
| `instructor` | Structured LLM output via Pydantic validation |
| `readability-lxml` | Full article body extraction from HTML (for RSS feeds with full_body: true) |

### New Environment Variables

| Variable | Purpose |
|----------|---------|
| `EQUITY__DEEPSEEK_API_KEY` | DeepSeek API key (via dotenvx) |

---

## 9. Data Flow Summary

```
config/rss_feeds.yaml ----+
                          |
config/social_sources.yaml--+-- RSSNewsFetcher ---+
config/watchlist.yaml ----+   RedditFetcher     +-- Bronze Layer
                              StockTwitsFetcher--+   (data/lake/bronze/raw_articles/)
                                                          |
                                                          +-- DeepSeekBatchProcessor
                                                          |   (instructor + AsyncOpenAI)
                                                          |
                                                          +-- Bronze to Silver Explode
                                                          |   (ticker validation, explode)
                                                          |
                                                          v
                                                    Silver Layer
                                                    (data/lake/silver/processed_articles/)
                                                          |
                                                          +-- merge_enriched_sentiment_features()
                                                          |   (features/enriched_sentiment.py)
                                                          |
                                                          v
                                                    Feature DataFrame
                                                    +-- Hamilton DAG (.compute())
                                                    |
                                                    v
                                              ML Prediction (Stage 3)
```
