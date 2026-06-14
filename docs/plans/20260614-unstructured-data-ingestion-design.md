# Unstructured Data Ingestion Layer Design

**Date:** 2026-06-14
**Status:** Phase 1 implemented; Phases 2-3 planned
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
  │           ├── LLM batch processing (DeepSeek v4-flash + AsyncOpenAI)
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
**Status:** IMPLEMENTED

Free, reliable APIs. Highest daily signal volume. RSS feeds are HTTP GET + XML parse. Reddit has a public JSON API. StockTwits has a free developer API.

| Module | Source | Data |
|--------|--------|------|
| `sources/rss.py` | RSS/Atom feeds (Reuters, MarketWatch, Seeking Alpha, CNBC, Yahoo Finance, Investopedia, Barron's, Motley Fool) | Article title, body, URL, published_at, author |
| `sources/reddit.py` | Reddit JSON API (r/wallstreetbets, r/stocks, r/investing, r/stockmarket, r/ValueInvesting, r/algotrading) | Post title, body, score, upvote_ratio, top comments |
| `sources/stocktwits.py` | StockTwits developer API (symbol streams) | Message body, built-in bullish/bearish sentiment, user, created_at |
| `ingestion/llm_processor.py` | DeepSeek v4-flash via `AsyncOpenAI` + Pydantic + tenacity | Batch structured extraction (sentiment, tickers, events, summary) |
| `ingestion/bronze_silver.py` | Internal | Bronze write + silver explode + ticker validation |
| `features/enriched_sentiment.py` | Silver Delta table | Feature aggregation and Hamilton DAG merge |

**Config files:**
- `config/rss_feeds.yaml` — Feed URLs, categories, full_body flags
- `config/social_sources.yaml` — Subreddits, post/comment limits, StockTwits symbols

**Pipeline integration:**
- New markets `rss_news`, `reddit_posts`, `stocktwits_messages` added to `ingestion/types.py`, routed via `ingestion/router.py`
- `execute_eod_pipeline()` calls `process_bronze_to_silver()` after ingestion, passes `include_enriched_sentiment=True` to `run_feature_job()`
- 10 new enriched sentiment features merged into Hamilton DAG via DuckDB aggregation

### Phase 2 — Earnings Transcripts + Analyst Ratings

**Categories:** C (Transcripts) + D (Analyst)
**Status:** PLANNED

Higher structure, requires specific providers. Transcripts are quarterly cadence (lower volume, higher value). Analyst ratings are daily.

| Source | Provider | Data | Bronze `source_type` |
|--------|----------|------|---------------------|
| Earnings transcripts | Motley Fool API (free tier) or Seeking Alpha scraping | Full transcript text, quarter, fiscal year, participants, Q&A sections | `transcript` |
| Analyst ratings | MarketBeat RSS, TipRanks API, Finnhub analyst recommendation (existing API key) | Rating (buy/hold/sell), price target, analyst firm, date, action (upgrade/downgrade/initiate) | `analyst` |

**New modules:**
- `sources/transcripts.py` — `EarningsTranscriptFetcher` extends `MarketDataFetcher`
  - Motley Fool API: `GET https://www.fool.com/services/v1/articles/...` (requires API key)
  - Fallback: scrape Seeking Alpha transcript pages (requires `readability-lxml` for full text)
  - Output schema: extends bronze with `quarter`, `fiscal_year`, `participants`, `qa_sections`
  - Cadence: fetch after each earnings season (quarterly), not daily
- `sources/analyst_ratings.py` — `AnalystRatingFetcher` extends `MarketDataFetcher`
  - Finnhub API: `GET /stock/recommendation` (uses existing `FINNHUB_API_KEY`)
  - TipRanks API: `GET https://api.tipranks.com/api/Data/GetAnalystRatings` (free, no auth)
  - MarketBeat RSS: `https://www.marketbeat.com/rss/analyst_ratings.xml`
  - Output: structured ratings data, no LLM processing needed (already structured)
  - Could bypass bronze and write directly to a `data/lake/analyst_ratings/` Delta table

**LLM processing change:**
- Transcripts: LLM processes the full transcript for management tone analysis, forward guidance extraction, and section-level sentiment
- Analyst ratings: No LLM needed — data is already structured. Write directly to its own Delta table.

**Feature engineering additions:**
- `analyst_consensus_score` — Weighted average of recent ratings (upgrade = +1, initiate buy = +0.5, hold = 0, downgrade = -1)
- `price_target_mean` / `price_target_upside` — Average analyst price target vs current close
- `rating_change_count` — Number of rating changes in past 7/30 days
- `transcript_sentiment_score` — LLM-derived sentiment from latest earnings call
- `guidance_sentiment` — Forward-looking sentiment extracted from transcript guidance section

**Router integration:**
- New markets: `transcripts`, `analyst_ratings`
- `transcripts` added to `ALWAYS_REFETCH` (quarterly check, not daily)
- `analyst_ratings` added to `ALWAYS_REFETCH` (daily, free structured data)

**Estimated effort:** 2-3 days
- Transcript fetcher with provider fallback: 1 day
- Analyst rating fetcher (3 providers): 0.5 day
- Feature engineering integration: 0.5-1 day

### Phase 3 — SEC EDGAR Full-Text

**Category:** E (Regulatory)
**Status:** PLANNED

Most complex parsing. Extends existing `SECFilingsLoader` (`loaders/sec_loader.py:16`) to extract and chunk 10-K/10-Q body text (risk factors, MD&A, financials). Requires section segmentation and large-document handling.

**Current state:**
- `SECFilingsLoader` fetches filing metadata (type, date, URL) and Form 4 insider transactions
- No full-text body extraction — only metadata is stored

**New modules:**
- `sources/sec_fulltext.py` — `SECFullTextExtractor`
  - Fetches filing HTML/XBRL from SEC EDGAR direct URLs
  - Parses 10-K/10-Q into structured sections:
    - Item 1A (Risk Factors) — key for LLM risk assessment
    - Item 7 (MD&A) — management discussion, forward guidance
    - Item 8 (Financial Statements) — structured financial data
  - Uses `beautifulsoup4` + `lxml` for HTML parsing, `python-edgar` for XBRL
  - Chunks sections into LLM-processable segments (~2000 tokens each)
- `ingestion/sec_processor.py` — LLM processing for filing sections
  - Risk factor extraction: identify top risks, sentiment per risk, change vs prior filing
  - MD&A analysis: management tone, forward guidance, business outlook
  - Batch processing via existing `DeepSeekBatchProcessor` (same pattern)

**LLM extraction schema for SEC sections:**
```python
class SECSectionExtraction(BaseModel):
    filing_id: str
    section_type: str  # risk_factors | mda | financial_statements
    risk_sentiment: float  # -1.0 (confident) to 1.0 (highly concerned)
    key_risks: list[str]  # extracted risk factors
    guidance_direction: str  # positive | negative | neutral | none
    forward_statements: list[str]  # forward-looking statements
    management_tone: float  # -1.0 to 1.0
    new_vs_repeated: str  # new | repeated | modified (vs prior filing)
```

**Feature engineering additions:**
- `sec_risk_sentiment` — Sentiment of risk factors section (quarterly, forward-filled)
- `sec_guidance_positive` — Binary: 1 if guidance direction is positive
- `sec_management_tone` — Tone score from MD&A
- `sec_risk_change_flag` — 1 if new/modified risks vs prior filing

**Storage:**
- Bronze: `data/lake/bronze/raw_filings/` — Full filing text, sectioned
- Silver: `data/lake/silver/processed_filings/` — LLM-extracted insights per section

**Router integration:**
- New market: `sec_filings_fulltext`
- Cadence: triggered after SEC filing dates (not daily) — check EDGAR RSS feed for new filings

**Estimated effort:** 3-5 days
- Full-text extractor with section parsing: 2 days
- SEC processor with LLM batch: 1 day
- Feature engineering + testing: 1-2 days

**New dependencies:**
- `beautifulsoup4` + `lxml` — HTML parsing (may already be available via readability)
- `python-edgar` — XBRL parsing for structured financials
- `readability-lxml` — Clean text extraction from filing HTML

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
| Client | **`AsyncOpenAI`** with DeepSeek base_url | OpenAI-compatible SDK, native async. No extra dependency (openai already used by Finnhub) |
| Structured output | **`response_format={'type': 'json_object'}`** + Pydantic validation | DeepSeek native JSON mode. Pydantic `model_validate_json()` for type-safe parsing. No `instructor` dependency. |
| Retry | **tenacity** (existing) | Consistent with all other fetchers. Exponential backoff, max 3 attempts. Retries on empty content (DeepSeek known bug) and JSON parse errors. |
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
import json
from openai import AsyncOpenAI
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

deepseek_client = AsyncOpenAI(
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
)

SYSTEM_PROMPT = """You are a financial news analyst. Analyze the provided articles and return
results as JSON matching this schema: {"items": [{"id": str, "mentioned_tickers": [str],
"sentiment": {"score": float, "label": str, "confidence": float}, "event_type": str,
"key_entities": [str], "summary": str, "impact_horizon": str, "market_relevance": float}]}"""


class RetryableError(Exception):
    """Raised when DeepSeek returns empty content or invalid JSON (triggers tenacity retry)."""


class DeepSeekBatchProcessor:
    def __init__(self, batch_size: int = 15, max_concurrency: int = 10):
        self.batch_size = batch_size
        self.semaphore = asyncio.Semaphore(max_concurrency)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type(RetryableError),
        reraise=True,
    )
    async def process_batch(self, batch: list[RawArticle]) -> BatchExtraction:
        async with self.semaphore:
            response = await deepseek_client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": self._format_batch(batch)},
                ],
                response_format={"type": "json_object"},
                max_tokens=8192,
            )

            content = response.choices[0].message.content
            if not content:
                raise RetryableError("DeepSeek returned empty content")

            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as e:
                raise RetryableError(f"Invalid JSON: {e}") from e

            try:
                return BatchExtraction.model_validate(parsed)
            except ValidationError as e:
                raise RetryableError(f"Validation failed: {e}") from e

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

### Robustness Without `instructor`

The raw approach handles all edge cases that `instructor` would abstract away:

1. **Empty content** (DeepSeek known bug): `RetryableError` triggers tenacity retry
2. **Invalid JSON**: `json.JSONDecodeError` caught and retried
3. **Schema validation failure**: Pydantic `ValidationError` caught and retried
4. **Persistent failure**: After 3 retries, batch falls back to VADER sentiment

This keeps the dependency surface minimal while matching `instructor`'s robustness for this low-volume use case (~15-20 calls/day).

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
| `ingestion/llm_processor.py` | DeepSeek batch processing via AsyncOpenAI + Pydantic + tenacity (structured extraction) |
| `ingestion/bronze_silver.py` | Bronze write + silver explode logic |
| `features/enriched_sentiment.py` | Feature merge for silver-layer data into Hamilton DAG |

### New Config Files

| File | Purpose |
|------|---------|
| `config/rss_feeds.yaml` | RSS feed URLs, categories, full_body flags |
| `config/social_sources.yaml` | Subreddits, post/comment limits, StockTwits settings |

### New Dependencies (Phase 1 — Implemented)

| Package | Purpose |
|---------|---------|
| `feedparser` | RSS/Atom XML parsing |
| `openai` | `AsyncOpenAI` client for DeepSeek API |

### Phase 2 Dependencies (Planned)

| Package | Purpose |
|---------|---------|
| `readability-lxml` | Full article body extraction from HTML (for transcript scraping) |

### Phase 3 Dependencies (Planned)

| Package | Purpose |
|---------|---------|
| `beautifulsoup4` + `lxml` | HTML parsing for SEC filing sections |
| `python-edgar` | XBRL parsing for structured financials |
| `readability-lxml` | Clean text extraction from filing HTML |

### New Environment Variables

| Variable | Purpose |
|----------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek API key (via dotenvx) |
| `STOCKTWITS_CLIENT_ID` | StockTwits API client ID (optional — free tier works without auth) |

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
                                                           |   (AsyncOpenAI + Pydantic + tenacity)
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

---

## 10. Implementation Status

### Phase 1 — COMPLETE

| Component | File | Status |
|-----------|------|--------|
| RSS fetcher | `sources/rss.py` | Done |
| Reddit fetcher | `sources/reddit.py` | Done |
| StockTwits fetcher | `sources/stocktwits.py` | Done |
| LLM processor | `ingestion/llm_processor.py` | Done |
| Bronze→silver transform | `ingestion/bronze_silver.py` | Done |
| Feature engineering | `features/enriched_sentiment.py` | Done |
| Router integration | `ingestion/router.py` | Done |
| Orchestrator | `ingestion/orchestrator.py` | Done |
| Writers (dedup) | `ingestion/writers.py` | Done |
| Types/markets | `ingestion/types.py` | Done |
| Schema constants | `core/schemas.py` | Done |
| Pipeline integration | `pipeline.py` | Done |
| Config files | `config/rss_feeds.yaml`, `config/social_sources.yaml` | Done |
| Dependencies | `feedparser`, `openai` in `pyproject.toml` | Done |

**Verification:** ruff clean, mypy clean (15 files), 282 unit tests pass.

**Remaining Phase 1 tasks:**
- Set `DEEPSEEK_API_KEY` in dotenvx
- Add new markets to `settings.yaml` default markets list (or invoke via CLI with explicit markets)
- Integration test with live RSS feeds + DeepSeek API

### Phase 2 — PLANNED (not started)

See Section 3, Phase 2 for detailed design. Estimated 2-3 days.

### Phase 3 — PLANNED (not started)

See Section 3, Phase 3 for detailed design. Estimated 3-5 days.
