# News & Sentiment Ingestion Module - Implementation Plan

**Project**: Equity Lake Data Pipeline Enhancement
**Date**: 2026-02-28
**Status**: Ready for Implementation
**Estimated Timeline**: 4 weeks (MVP), 2 weeks (accelerated)

---

## Executive Summary

This plan designs and implements a **news and sentiment data collection module** for the equity data pipeline. The module will collect financial news headlines and social sentiment signals, analyze them for sentiment polarity, and integrate them into the existing ML feature pipeline to enhance price prediction accuracy.

### Key Highlights
- **Primary Data Source**: Finnhub API (free tier, 60 calls/min)
- **Sentiment Analysis**: VADER (MVP) → FinBERT (production)
- **Storage**: Hive-partitioned Parquet (follows existing pattern)
- **ML Integration**: Sentiment features merged into XGBoost forecaster
- **Cost**: $0/month (100% free tier APIs)
- **Scope**: US Equities, Top 100 tickers (MVP)

---

## Table of Contents

1. [Research Findings](#1-research-findings)
2. [Architecture Design](#2-architecture-design)
3. [Data Source Selection](#3-data-source-selection)
4. [Implementation Phases](#4-implementation-phases)
5. [Testing Strategy](#5-testing-strategy)
6. [Integration Points](#6-integration-points)
7. [Critical Files](#7-critical-files)
8. [Success Metrics](#8-success-metrics)
9. [Risks & Mitigation](#9-risks--mitigation)

---

## 1. Research Findings

### 1.1 Financial News APIs (2026 Landscape)

| Provider | Free Tier | Pros | Cons | Recommendation |
|----------|-----------|------|------|----------------|
| **Finnhub** | 60 calls/min, unlimited news | Official API, news + sentiment endpoints, US market focus | None | ✅ **PRIMARY** |
| Alpha Vantage | 5 calls/min, 500/day | Global coverage, reliable | Restrictive rate limits | ⚠️ Backup |
| Polygon.io | 5 calls/min | 2 years historical | News requires paid plan | ❌ Not MVP |
| NewsAPI.org | Non-commercial | 150k+ sources | Licensing issues | ❌ Legal risk |
| Yahoo Finance | N/A (unofficial) | Free | No official API, unstable | ❌ Scraping risk |

### 1.2 Social Sentiment Sources

| Provider | Cost | Coverage | MVP Feasibility |
|----------|------|----------|-----------------|
| **Finnhub Social** | Free | Reddit + Twitter metrics | ✅ **PRIMARY** |
| Quiver Quant | Paid | WallStreetBets mentions | ❌ Cost |
| SocialGrep | $9/month | Reddit historical | ❌ Cost |
| EODHD | Paid | Twitter sentiment | ❌ Cost |

### 1.3 Sentiment Analysis Libraries

| Library | Speed | Accuracy | Financial Domain | MVP Use |
|---------|-------|----------|------------------|---------|
| **VADER** | ⚡ Fast (100ms) | Moderate (70%) | No | ✅ **MVP** |
| **FinBERT** | 🐌 Slow (1-2s) | High (90%+) | Yes | 🔄 Phase 2 |
| TextBlob | Fast | Low (60%) | No | ❌ Not recommended |
| pyFin-Sentiment | Medium | High (85%) | Yes | ⚠️ Alternative |

**Key Finding**: Start with VADER for MVP speed, upgrade to FinBERT for production accuracy.

### 1.4 Web Scraping Libraries

| Library | Best For | MVP Use |
|---------|----------|---------|
| **Playwright** | JS-heavy sites, anti-bot | ⚠️ Backup only |
| **newspaper4k** | Article extraction | ⚠️ If needed |
| **feedparser** | RSS monitoring | ❌ Not MVP |
| **requests + BeautifulSoup** | Simple static sites | ❌ Not MVP |

**Decision**: Use official APIs (Finnhub) instead of scraping for MVP reliability.

---

## 2. Architecture Design

### 2.1 Schema Design

#### News Schema (`NEWS_COLUMNS`)
```python
NEWS_COLUMNS = [
    'ticker',          # STRING: Stock symbol (e.g., 'AAPL')
    'date',            # DATE: Published date (partition key)
    'datetime',        # DATETIME: Exact publication timestamp
    'source',          # STRING: News source (e.g., 'Reuters', 'Bloomberg')
    'headline',        # STRING: Article title
    'summary',         # STRING: Article summary/description
    'url',             # STRING: Article URL
    'category',        # STRING: News category (e.g., 'earnings', 'merger')
    'sentiment_score', # FLOAT: VADER/FinBERT score (-1.0 to 1.0)
    'sentiment_label', # STRING: 'positive', 'negative', 'neutral'
    'relevance_score', # FLOAT: API-provided relevance (0.0 to 1.0)
]
```

#### Social Sentiment Schema (`SOCIAL_COLUMNS`)
```python
SOCIAL_COLUMNS = [
    'ticker',              # STRING: Stock symbol
    'date',                # DATE: Date of sentiment measurement (partition key)
    'datetime',            # DATETIME: Exact timestamp
    'source',              # STRING: 'reddit', 'twitter', etc.
    'mention_count',       # INT: Number of mentions
    'positive_score',      # FLOAT: Positive sentiment score
    'negative_score',      # FLOAT: Negative sentiment score
    'score',               # FLOAT: Normalized sentiment score (-1.0 to 1.0)
    'social_metric',       # STRING: Metric type
]
```

### 2.2 Storage Structure

Follow existing Hive partitioning pattern:

```
data/lake/
├── us_news/
│   ├── date=2025-01-02/
│   │   └── 2025-01-02.parquet
│   └── date=2025-01-03/
│       └── 2025-01-03.parquet
├── us_social_sentiment/
│   ├── date=2025-01-02/
│   │   └── 2025-01-02.parquet
│   └── date=2025-01-03/
│       └── 2025-01-03.parquet
└── features/
    └── date=2025-01-02/
        └── features_with_sentiment.parquet  # Merged in feature engineering
```

**Rationale**:
- ✅ Follows existing pattern (one directory per data type)
- ✅ Enables market-specific filtering
- ✅ Leverages existing partition pruning by date
- ✅ Easy to join with price data on `ticker` + `date`

### 2.3 Code Structure

```
src/equity_lake/
├── ingestion/sources/
│   ├── __init__.py              # ADD: Export NewsDataFetcher, SentimentDataFetcher
│   ├── news.py                  # NEW: FinnhubNewsFetcher class
│   ├── sentiment.py             # NEW: FinnhubSentimentFetcher class
│   └── base.py                  # EXISTING: MarketDataFetcher base class
├── sentiment/                   # NEW MODULE
│   ├── __init__.py
│   ├── analyzer.py              # SentimentAnalyzer class (VADER/FinBERT wrapper)
│   └── models.py                # SentimentResult Pydantic models
├── features/
│   ├── engineering.py           # MODIFY: Add merge_sentiment_features()
│   └── jobs.py                  # MODIFY: Add sentiment merging to pipeline
├── core/
│   └── runtime.py               # ADD: NEWS_COLUMNS, SOCIAL_COLUMNS, directory paths
├── ingestion/
│   ├── orchestrator.py          # MODIFY: Add 'us_news', 'us_social_sentiment' markets
│   └── writers.py               # ADD: Schema validation for news/sentiment
└── cli/
    ├── news.py                  # NEW: CLI for news ingestion
    └── sentiment.py             # NEW: CLI for sentiment ingestion
```

### 2.4 Data Flow Architecture

```
┌─────────────────┐
│  Finnhub API    │
│  (News + Social)│
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  FinnhubNewsFetcher / SentimentFetcher  │
│  (ingestion/sources/news.py)            │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  SentimentAnalyzer (VADER)              │
│  (sentiment/analyzer.py)                │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Orchestrator (parallel execution)      │
│  (ingestion/orchestrator.py)            │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Write to Partitioned Parquet           │
│  (data/lake/us_news/date=YYYY-MM-DD/)   │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  Feature Engineering (merge_sentiment)  │
│  (features/engineering.py)              │
└────────┬────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────┐
│  XGBoost Forecaster (ML)                │
│  (ml/forecasting.py)                    │
└─────────────────────────────────────────┘
```

---

## 3. Data Source Selection

### 3.1 Primary Choice: Finnhub API

**Justification**:
- ✅ **Free tier**: 60 calls/minute, unlimited news access
- ✅ **Official API**: Stable, documented, no scraping risks
- ✅ **Dual endpoints**: News + Social sentiment in one platform
- ✅ **US market focus**: Matches current ticker universe (5000+ stocks)
- ✅ **No rate limiting pain**: 60 calls/min is generous for daily batch

**Finnhub Endpoints**:

1. **Company News**: `/api/v1/company-news?symbol=AAPL&from=2025-01-01&to=2025-01-02`
   - Returns: Array of news articles with headline, summary, url, datetime, source
   - Max: ~50 articles per ticker per day

2. **News Sentiment**: `/api/v1/news-sentiment?symbol=AAPL`
   - Returns: Reddit/Twitter metrics (mention count, positive/negative scores)
   - Updated: Every hour

**API Key Setup**:
1. Register at https://finnhub.io/
2. Get free API key (instant, no credit card)
3. Add to `.env`: `FINNHUB_API_KEY=your_key_here`

### 3.2 Backup: Alpha Vantage

**When to use**: If Finnhub fails or exceeds rate limits

**Pros**:
- Free tier: 5 calls/minute, 500/day
- Global coverage (CN, HK, SG markets)
- Reliable uptime

**Cons**:
- More restrictive rate limits
- News endpoint only (no social sentiment)
- Slower for large ticker batches

**Usage Pattern**:
```python
try:
    df = finnhub_fetcher.fetch(trading_date)
except RateLimitError:
    logger.warning("Finnhub rate limited, falling back to Alpha Vantage")
    df = alpha_vantage_fetcher.fetch(trading_date)
```

### 3.3 Sentiment Analysis: Two-Phase Approach

#### Phase 1 (MVP): VADER
- **Library**: `vaderSentiment==3.3.2`
- **Speed**: ~100ms per article
- **Accuracy**: ~70% on financial text
- **Cost**: Free (no GPU required)

**When to use**: MVP validation, real-time daily updates

#### Phase 2 (Production): FinBERT
- **Library**: `transformers>=4.30.0`, `torch>=2.0.0`
- **Speed**: ~1-2 seconds per article (CPU), ~100ms (GPU)
- **Accuracy**: ~90%+ on financial text
- **Cost**: Free (GPU optional)

**When to use**: Batch historical backfills, production accuracy

**Migration Strategy**:
- MVP: Use VADER for all sentiment analysis
- Week 4: Add FinBERT as optional `method="finbert"`
- Production: Use FinBERT for backfills, VADER for daily updates

---

## 4. Implementation Phases

### Phase 1: Foundation (Week 1) - 11 hours

**Goal**: Basic news fetching with VADER sentiment

| Step | Task | Files | Time |
|------|------|-------|------|
| 1.1 | Add Finnhub API key to `.env.example` | `.env.example` | 5 min |
| 1.2 | Install `vaderSentiment` dependency | `pyproject.toml` | 5 min |
| 1.3 | Create `SentimentAnalyzer` wrapper | `src/equity_lake/sentiment/analyzer.py` | 2 hrs |
| 1.4 | Create `FinnhubNewsFetcher` class | `src/equity_lake/ingestion/sources/news.py` | 3 hrs |
| 1.5 | Add schema validation for news | `src/equity_lake/ingestion/writers.py` | 1 hr |
| 1.6 | Add news market to orchestrator | `src/equity_lake/ingestion/orchestrator.py` | 1 hr |
| 1.7 | Create news CLI | `src/equity_lake/cli/news.py` | 2 hrs |
| 1.8 | Test with 5 tickers, single date | Manual testing | 2 hrs |

**Deliverables**:
- Working news fetcher with VADER sentiment
- CLI command: `equity-news --date 2025-01-02 --tickers AAPL,GOOGL`
- Parquet files in `data/lake/us_news/date=*/`

### Phase 2: Productionization (Week 2) - 13.5 hours

**Goal**: Robust error handling, testing, documentation

| Step | Task | Files | Time |
|------|------|-------|------|
| 2.1 | Add unit tests for fetcher | `tests/unit/test_news_fetcher.py` | 3 hrs |
| 2.2 | Add integration test with mock API | `tests/integration/test_news_ingestion.py` | 2 hrs |
| 2.3 | Add rate limiting to FinnhubNewsFetcher | `src/equity_lake/ingestion/sources/news.py` | 1 hr |
| 2.4 | Add parallel fetching support | `src/equity_lake/ingestion/sources/news.py` | 2 hrs |
| 2.5 | Add data quality validation | `src/equity_lake/ingestion/writers.py` | 1 hr |
| 2.6 | Update documentation | `docs/news-ingestion-guide.md` | 2 hrs |
| 2.7 | Add Makefile commands | `Makefile` | 30 min |
| 2.8 | End-to-end testing | Manual | 2 hrs |

**Deliverables**:
- Comprehensive test suite (>80% coverage)
- Documentation for users and developers
- Production-ready error handling

### Phase 3: Sentiment Integration (Week 3) - 10 hours

**Goal**: Merge sentiment into ML features

| Step | Task | Files | Time |
|------|------|-------|------|
| 3.1 | Create `merge_sentiment_features()` | `src/equity_lake/features/engineering.py` | 2 hrs |
| 3.2 | Update feature job CLI | `src/equity_lake/features/jobs.py` | 1 hr |
| 3.3 | Test feature merging | Manual | 1 hr |
| 3.4 | Train XGBoost with sentiment | `src/equity_lake/ml/training.py` | 3 hrs |
| 3.5 | Compare model performance | Jupyter notebook | 2 hrs |
| 3.6 | Document results | `docs/sentiment-ml-impact.md` | 1 hr |

**Deliverables**:
- Sentiment features integrated into feature pipeline
- XGBoost model trained with sentiment
- Performance comparison report (baseline vs sentiment)

### Phase 4: Social Sentiment (Week 4) - 8.5 hours

**Goal**: Add Reddit/Twitter sentiment from Finnhub

| Step | Task | Files | Time |
|------|------|-------|------|
| 4.1 | Create `FinnhubSentimentFetcher` | `src/equity_lake/ingestion/sources/sentiment.py` | 2 hrs |
| 4.2 | Add social sentiment schema | `src/equity_lake/core/runtime.py` | 30 min |
| 4.3 | Add to orchestrator | `src/equity_lake/ingestion/orchestrator.py` | 1 hr |
| 4.4 | Create sentiment CLI | `src/equity_lake/cli/sentiment.py` | 1 hr |
| 4.5 | Merge social sentiment into features | `src/equity_lake/features/engineering.py` | 2 hrs |
| 4.6 | Testing and validation | Manual + unit tests | 2 hrs |

**Deliverables**:
- Social sentiment fetcher (Reddit/Twitter metrics)
- CLI command: `equity-sentiment --date 2025-01-02`
- Social sentiment features in ML pipeline

### Accelerated Timeline (2 weeks)

**If compressed**, combine phases:
- **Week 1**: Phase 1 + Phase 2 (Foundation + Productionization) = 24.5 hrs
- **Week 2**: Phase 3 + Phase 4 (Sentiment Integration + Social) = 18.5 hrs

**Trade-offs**:
- ⚠️ Less time for testing and validation
- ⚠️ Documentation may be minimal
- ⚠️ Higher risk of bugs in production

---

## 5. Testing Strategy

### 5.1 Unit Tests

**File**: `tests/unit/test_news_fetcher.py`

```python
def test_sentiment_analyzer_vader():
    """Test VADER sentiment analyzer."""
    analyzer = SentimentAnalyzer(method="vader")
    result = analyzer.analyze("AAPL stock surges on earnings beat")
    assert result["label"] == "positive"
    assert result["compound"] > 0

@pytest.mark.parametrize("text,expected_label", [
    ("Great earnings report", "positive"),
    ("Terrible revenue miss", "negative"),
    ("Stock price unchanged", "neutral"),
])
def test_sentiment_classification(text, expected_label):
    """Test sentiment classification accuracy."""
    analyzer = SentimentAnalyzer(method="vader")
    result = analyzer.analyze(text)
    assert result["label"] == expected_label
```

### 5.2 Integration Tests

**File**: `tests/integration/test_news_ingestion.py`

```python
@pytest.mark.integration
def test_fetch_news_for_tickers():
    """Test actual Finnhub API call."""
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        pytest.skip("FINNHUB_API_KEY not set")

    fetcher = FinnhubNewsFetcher(
        api_key=api_key,
        tickers=["AAPL"],
        max_articles_per_ticker=5,
    )

    df = fetcher.fetch(date.today())

    assert not df.empty
    assert all(col in df.columns for col in NEWS_COLUMNS)
    assert df["ticker"].unique()[0] == "AAPL"
```

### 5.3 Manual Testing Checklist

- [ ] Fetch news for 5 tickers on a known news date (e.g., earnings day)
- [ ] Verify sentiment scores match manual expectations
- [ ] Test rate limiting (fetch 100 tickers, ensure no 429 errors)
- [ ] Test parallel fetching with 3 workers
- [ ] Verify parquet files are written correctly
- [ ] Test feature merging with sentiment data
- [ ] Run ML training with and without sentiment features

### 5.4 Test Coverage Targets

| Module | Target Coverage | Critical Paths |
|--------|----------------|----------------|
| `sentiment/analyzer.py` | 90% | VADER scoring, label mapping |
| `ingestion/sources/news.py` | 80% | API calls, retry logic, rate limiting |
| `features/engineering.py` | 75% | Sentiment merging, feature creation |
| `cli/news.py` | 70% | Argument parsing, error handling |

---

## 6. Integration Points

### 6.1 Orchestrator Integration

**File**: `src/equity_lake/ingestion/orchestrator.py`

**Add to `fetch_market_data_with_config()`** (around line 747):

```python
elif market == "us_news":
    from equity_lake.ingestion.sources.news import FinnhubNewsFetcher

    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        logger.error("FINNHUB_API_KEY not set")
        return None

    fetcher = FinnhubNewsFetcher(
        api_key=api_key,
        tickers=explicit_tickers or ticker_config.get_tickers_for_market("us")[:100],
        retry_attempts=retry_attempts,
    )
    return fetcher.fetch(trading_date)

elif market == "us_social_sentiment":
    from equity_lake.ingestion.sources.sentiment import FinnhubSentimentFetcher

    api_key = os.getenv("FINNHUB_API_KEY")
    fetcher = FinnhubSentimentFetcher(
        api_key=api_key,
        tickers=explicit_tickers or ticker_config.get_tickers_for_market("us")[:100],
    )
    return fetcher.fetch(trading_date)
```

**Add to market mapping** (around line 637):

```python
market_dir_map = {
    'us': 'us_equity',
    'cn': 'cn_ashare',
    'hk_sg': 'hk_sg_equity',
    'macro': 'macro_indicators',
    'us_news': 'us_news',              # NEW
    'us_social_sentiment': 'us_social_sentiment',  # NEW
}
```

### 6.2 Feature Engineering Integration

**File**: `src/equity_lake/features/engineering.py`

**Add method to `FeatureEngineer` class**:

```python
def merge_sentiment_features(
    self,
    features_df: pd.DataFrame,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Merge aggregated sentiment scores into features."""

    # Load sentiment data
    sentiment_query = f"""
        SELECT
            ticker,
            date,
            AVG(sentiment_score) as avg_daily_sentiment,
            COUNT(*) as news_count,
            SUM(CASE WHEN sentiment_label = 'positive' THEN 1 ELSE 0 END) as positive_count,
            SUM(CASE WHEN sentiment_label = 'negative' THEN 1 ELSE 0 END) as negative_count,
        FROM read_parquet('{LAKE_DIR}/us_news/**/*.parquet', hive_partitioning=1)
        WHERE date BETWEEN '{start_date}' AND '{end_date}'
        GROUP BY ticker, date
    """

    sentiment_df = self.conn.execute(sentiment_query).df()

    # Merge with features
    merged_df = features_df.merge(
        sentiment_df,
        on=['ticker', 'date'],
        how='left',
    )

    # Fill missing sentiment (no news that day)
    merged_df['avg_daily_sentiment'] = merged_df['avg_daily_sentiment'].fillna(0.0)
    merged_df['news_count'] = merged_df['news_count'].fillna(0)

    return merged_df
```

### 6.3 Pipeline Integration

**File**: `src/equity_lake/run_pipeline.py`

**Add to default markets** (around line 432):

```python
parser.add_argument(
    '--markets',
    type=str,
    default='us,cn,hk_sg,us_news',  # Add us_news
    help='Comma-separated markets to ingest'
)
```

### 6.4 CLI Integration

**Register CLI commands in `pyproject.toml`**:

```toml
[project.scripts]
equity-news = "equity_lake.cli.news:main"
equity-sentiment = "equity_lake.cli.sentiment:main"
```

---

## 7. Critical Files

### 7.1 Files to Modify

| File | Changes | Lines Affected |
|------|---------|----------------|
| `src/equity_lake/core/runtime.py` | Add NEWS_COLUMNS, SOCIAL_COLUMNS, directory paths | ~20 lines |
| `src/equity_lake/ingestion/orchestrator.py` | Add 'us_news', 'us_social_sentiment' markets | ~50 lines |
| `src/equity_lake/ingestion/writers.py` | Add schema validation for news/sentiment | ~30 lines |
| `src/equity_lake/features/engineering.py` | Add `merge_sentiment_features()` method | ~40 lines |
| `src/equity_lake/ingestion/sources/__init__.py` | Export NewsDataFetcher, SentimentDataFetcher | ~5 lines |
| `pyproject.toml` | Add dependencies, CLI scripts | ~10 lines |
| `.env.example` | Add FINNHUB_API_KEY | ~1 line |
| `Makefile` | Add commands for news/sentiment | ~10 lines |

### 7.2 Files to Create

| File | Purpose | Estimated Lines |
|------|---------|-----------------|
| `src/equity_lake/sentiment/__init__.py` | Module init | ~10 lines |
| `src/equity_lake/sentiment/analyzer.py` | SentimentAnalyzer wrapper | ~100 lines |
| `src/equity_lake/sentiment/models.py` | Pydantic models | ~30 lines |
| `src/equity_lake/ingestion/sources/news.py` | FinnhubNewsFetcher class | ~200 lines |
| `src/equity_lake/ingestion/sources/sentiment.py` | FinnhubSentimentFetcher class | ~150 lines |
| `src/equity_lake/cli/news.py` | News ingestion CLI | ~100 lines |
| `src/equity_lake/cli/sentiment.py` | Social sentiment CLI | ~100 lines |
| `tests/unit/test_news_fetcher.py` | Unit tests | ~200 lines |
| `tests/integration/test_news_ingestion.py` | Integration tests | ~150 lines |
| `docs/news-ingestion-guide.md` | User documentation | ~300 lines |

**Total New Code**: ~1,440 lines (excluding tests and docs)

---

## 8. Success Metrics

### 8.1 Technical Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **API success rate** | >95% | Log analysis: successful fetches / total attempts |
| **Data freshness** | <24 hours lag | Timestamp comparison: publication vs ingestion |
| **Sentiment accuracy** | >70% (VADER) | Manual validation on 100-article sample |
| **Storage efficiency** | <1GB/month | `du -sh data/lake/us_news/` |
| **Pipeline runtime** | <10 minutes (100 tickers) | Timer in CLI output |
| **Test coverage** | >80% | `pytest --cov` |
| **Test pass rate** | 100% | CI/CD results |

### 8.2 ML Impact Metrics

| Metric | Baseline | With Sentiment | Target | Measurement Method |
|--------|----------|----------------|--------|-------------------|
| **XGBoost RMSE** | TBD | TBD | -5% improvement | Backtest on 2024 data |
| **Feature importance** | N/A | Sentiment rank | Top 10 features | `xgb.plot_importance()` |
| **Direction accuracy** | TBD | TBD | +3% improvement | Compare predicted vs actual |
| **Sharpe ratio** | TBD | TBD | +0.1 improvement | Trading simulation |

### 8.3 Operational Metrics

| Metric | Target | Measurement Method |
|--------|--------|-------------------|
| **API quota usage** | <80% of free tier | Finnhub dashboard |
| **Cost per month** | $0 | Billing report |
| **Pipeline failures** | <2% per month | Error log analysis |
| **Mean time to recovery** | <1 hour | Incident tracking |

---

## 9. Risks & Mitigation

### 9.1 Technical Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Finnhub API downtime** | High (daily pipeline fails) | Low | Add fallback to Alpha Vantage; cache last successful fetch |
| **Rate limiting exceeded** | Medium (incomplete data) | Medium | Implement exponential backoff; throttle requests; use parallel workers |
| **VADER accuracy issues** | Medium (poor sentiment) | High | Validate on sample of 100 articles; plan FinBERT upgrade path |
| **Storage bloat** (news articles) | Medium (disk space) | High | Implement 30-day retention for raw news; keep only aggregated sentiment |
| **GPU unavailability** (FinBERT) | Low (MVP uses VADER) | N/A | Use CPU fallback; cloud GPU for batch processing |

### 9.2 Data Quality Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Garbage headlines** (non-company news) | Medium (noise) | Medium | Add relevance filtering; ticker symbol validation in headline |
| **Duplicate articles** | Low (redundancy) | High | Deduplication by URL + headline hash in writer |
| **Missing sentiment labels** | Low (incomplete data) | Low | Fill with neutral (0.0); add `null_flag` column |
| **API returns wrong date** | Medium (data corruption) | Low | Validate date range in schema validator |

### 9.3 Operational Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **API key leakage** | High (security) | Low | Use `.env` file; add to `.gitignore`; document security best practices |
| **Cost overruns** (API limits) | High (unexpected charges) | Low | Monitor API usage daily; set up alerts; use free tier only |
| **Ticker universe expansion** | Medium (API call explosion) | Medium | Implement pagination; batch processing; prioritize by `priority` field |
| **Legal issues** (news scraping) | High (lawsuits) | Very Low | Use official APIs only; respect robots.txt; terms of service compliance |

### 9.4 ML Model Risks

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| **Sentiment features add noise** | Medium (model degradation) | Medium | A/B test with/without sentiment; monitor feature importance |
| **Overfitting to sentiment** | Medium (poor generalization) | Low | Use cross-validation; regularization; feature selection |
| **Concept drift** (sentiment patterns change) | Medium (model decay) | Medium | Quarterly retraining; rolling window validation |

---

## 10. Dependencies & Configuration

### 10.1 Required Dependencies

**Add to `pyproject.toml`**:

```toml
[project]
dependencies = [
    # ... existing dependencies ...
    "vaderSentiment>=3.3.2",     # Sentiment analysis (VADER)
    "requests>=2.31.0",          # HTTP client for Finnhub API
]

[project.optional-dependencies]
sentiment-ml = [
    "transformers>=4.30.0",      # FinBERT support
    "torch>=2.0.0",              # PyTorch for FinBERT
]
```

### 10.2 Environment Variables

**Add to `.env.example`**:

```bash
# Finnhub API Configuration
FINNHUB_API_KEY=your_finnhub_api_key_here  # Get free at https://finnhub.io/

# Optional: Alpha Vantage Backup
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key_here  # Get free at https://www.alphavantage.co/
```

### 10.3 New Makefile Commands

**Add to `Makefile`**:

```makefile
# News ingestion
news:
	@uv run equity-news --date $$(date -v-1d +%Y-%m-%d)

news-dry:
	@uv run equity-news --date $$(date -v-1d +%Y-%m-%d) --dry-run --verbose

# Social sentiment
sentiment:
	@uv run equity-sentiment --date $$(date -v-1d +%Y-%m-%d)

# Full pipeline with news
pipeline-with-news:
	@uv run equity-pipeline --markets us,cn,us_news

# Test news module
test-news:
	@uv run pytest tests/unit/test_news_fetcher.py -v

test-news-integration:
	@uv run pytest tests/integration/test_news_ingestion.py -v -m integration
```

---

## 11. Documentation Requirements

### 11.1 User Documentation

**Create** `docs/news-ingestion-guide.md`:

- How to get Finnhub API key (step-by-step with screenshots)
- CLI usage examples (`equity-news`, `equity-sentiment`)
- Configuration options (tickers, date ranges, max articles)
- Troubleshooting common issues (API errors, rate limits)
- Performance tuning tips (parallel workers, batch sizes)

**Create** `docs/sentiment-analysis-guide.md`:

- VADER vs FinBERT comparison table
- How to switch sentiment methods (`--sentiment-method finbert`)
- Interpreting sentiment scores (what is "positive"?)
- Feature engineering with sentiment (which features added?)
- Best practices for production use

**Update** `docs/QUICKSTART.md`:

- Add news/sentiment section
- Quick start: "Fetch news for 5 tickers in 5 minutes"

### 11.2 Developer Documentation

**Create** `docs/design/news-sentiment-design.md`:

- Architecture diagram (data flow)
- Schema definitions (NEWS_COLUMNS, SOCIAL_COLUMNS)
- API integration details (Finnhub endpoints, rate limits)
- Known limitations (free tier constraints, VADER accuracy)

**Create** `docs/implementations/news-ingestion-summary.md`:

- Implementation timeline (Week 1-4)
- Key decisions and trade-offs (why Finhub? why VADER?)
- Future enhancement roadmap (FinBERT, multi-market)
- Lessons learned (what went well, what didn't)

---

## 12. Verification Plan

### 12.1 Pre-Implementation Checklist

- [ ] Finnhub API key obtained and tested
- [ ] `.env` file configured with API key
- [ ] `vaderSentiment` dependency added to `pyproject.toml`
- [ ] Test tickers selected (top 5 by priority)
- [ ] Test date selected (known high-news day, e.g., earnings announcement)
- [ ] Backup plan documented (Alpha Vantage fallback)

### 12.2 End-to-End Test Plan

**Step 1**: Fetch news for 5 tickers
```bash
export FINNHUB_API_KEY=your_key
equity-news --date 2025-01-02 --tickers AAPL,GOOGL,MSFT,AMZN,TSLA --max-articles 10
```

**Expected Output**:
- CLI logs: "Fetching news for 5 tickers..."
- Parquet files created: `data/lake/us_news/date=2025-01-02/2025-01-02.parquet`
- Log summary: "Fetched 47 articles, sentiment analysis complete"

**Step 2**: Verify data quality
```bash
python -c "
import pandas as pd
df = pd.read_parquet('data/lake/us_news/date=2025-01-02/2025-01-02.parquet')
print(f'Rows: {len(df)}')
print(f'Columns: {df.columns.tolist()}')
print(f'Sentiment distribution:\n{df[\"sentiment_label\"].value_counts()}')
print(f'Sample headlines:\n{df[[\"ticker\", \"headline\", \"sentiment_score\"]].head()}')
"
```

**Expected Output**:
- Rows: ~40-50 (max 10 per ticker × 5 tickers)
- Columns: All NEWS_COLUMNS present
- Sentiment distribution: Mix of positive, negative, neutral
- Sample headlines: Relevant to tickers, scores make sense

**Step 3**: Test feature merging
```bash
equity-features --tickers AAPL --start-date 2025-01-01 --end-date 2025-01-07
```

**Expected Output**:
- Features DataFrame includes `avg_daily_sentiment`, `news_count`
- Missing sentiment days filled with 0.0
- No errors or warnings in logs

**Step 4**: Train ML model with sentiment
```bash
equity-train --tickers AAPL --start-date 2024-01-01 --end-date 2024-12-31 --with-sentiment
```

**Expected Output**:
- Model trains successfully
- Feature importance shows sentiment features in top 10
- RMSE improved compared to baseline (without sentiment)

### 12.3 Rollback Plan

**If implementation fails**:
1. Revert feature branch: `git checkout main`
2. Delete news data directories: `rm -rf data/lake/us_news/ data/lake/us_social_sentiment/`
3. Remove dependencies: `uv pip remove vaderSentiment`
4. Restore previous pipeline: `equity-pipeline --markets us,cn,hk_sg`

**Data backup strategy**:
- Commit test data to Git: `data/lake/us_news/date=2025-01-02/*.parquet`
- Tag release: `git tag -a v0.2.0 -m "Pre-news-ingestion baseline"`

---

## 13. Cost & Resource Estimate

### 13.1 Development Costs

| Phase | Hours | Rate ($/hr) | Total Cost |
|-------|-------|-------------|------------|
| Phase 1 (Foundation) | 11 hrs | $100 | $1,100 |
| Phase 2 (Productionization) | 13.5 hrs | $100 | $1,350 |
| Phase 3 (Sentiment Integration) | 10 hrs | $100 | $1,000 |
| Phase 4 (Social Sentiment) | 8.5 hrs | $100 | $850 |
| **Total** | **43 hrs** | | **$4,300** |

**Accelerated Timeline** (2 weeks):
- Total hours: ~43 hours (same work, compressed timeline)
- Higher risk of bugs due to rushed testing
- Potential for rework if issues found

### 13.2 Operational Costs

| Item | Monthly Cost | Annual Cost |
|------|--------------|-------------|
| Finnhub API (free tier) | $0 | $0 |
| Alpha Vantage (backup, free tier) | $0 | $0 |
| VADER sentiment | $0 | $0 |
| Additional storage (1GB/month) | $0 | $0 |
| **Total Operational Cost** | **$0** | **$0** |

**Future Phase Costs** (optional):
- Finnhub paid tier: $60/month (if exceeding free limits)
- Cloud GPU (FinBERT): $50-100/month (Google Colab Pro or AWS)
- SocialGrep/Quiver Quant: $9-29/month (optional Reddit data)

### 13.3 Resource Requirements

**Hardware**:
- CPU: Modern multi-core (for parallel fetching)
- RAM: 8GB minimum (16GB recommended for FinBERT)
- Disk: 10GB free space (news data grows ~1GB/year)
- GPU: Optional (for FinBERT, not required for MVP)

**Software**:
- Python 3.11+
- uv package manager
- Git
- Make

---

## 14. Future Enhancements (Out of Scope for MVP)

### 14.1 Phase 5: Advanced Features (Optional)

| Feature | Description | Priority | Estimated Effort |
|---------|-------------|----------|------------------|
| **FinBERT integration** | Replace VADER with FinBERT for accuracy | Medium | 8 hrs |
| **Multi-market support** | Add CN/HK news sources | Low | 16 hrs |
| **Sentiment trends** | Compute 7/30/90-day sentiment moving averages | Medium | 4 hrs |
| **News clustering** | Group similar news articles (NLP) | Low | 12 hrs |
| **Real-time updates** | Stream news via WebSocket | Low | 20 hrs |
| **Sentiment momentum** | Rate of change in sentiment (derivative) | Medium | 4 hrs |
| **Sector sentiment** | Aggregate sentiment by sector | Medium | 6 hrs |
| **News sentiment heatmap** | Visual dashboard of sentiment across tickers | Low | 8 hrs |

### 14.2 Phase 6: Production Hardening

| Feature | Description | Priority | Estimated Effort |
|---------|-------------|----------|------------------|
| **API monitoring** | Alert on API failures, quota usage | High | 4 hrs |
| **Data quality dashboards** | Real-time monitoring of sentiment scores | Medium | 8 hrs |
| **Automated backtesting** | Daily sentiment impact on predictions | Medium | 8 hrs |
| **Sentiment drift detection** | Alert on sudden sentiment changes | Medium | 6 hrs |
| **Cost optimization** | Cache API responses, reduce redundant calls | Low | 4 hrs |

---

## 15. Questions for User Approval

Before implementation begins, please confirm:

1. **API Source**: Should we proceed with **Finnhub** as the primary data source?
   - Alternative: Alpha Vantage (more restrictive rate limits)

2. **Sentiment Approach**: Start with **VADER** (fast, MVP) or **FinBERT** (accurate, slower)?
   - Recommendation: VADER for MVP, upgrade to FinBERT in Phase 5

3. **Ticker Scope**: **Top 100 tickers** (MVP) or **full universe** (5000+ stocks)?
   - Recommendation: Top 100 for MVP, scale up in Phase 5

4. **Timeline**: **4-week standard** or **2-week accelerated** timeline?
   - Recommendation: 4 weeks for thorough testing and documentation

5. **Storage Retention**: **30-day rolling window** for raw news acceptable?
   - Alternative: 90-day or infinite retention (requires more disk space)

6. **ML Integration**: Should sentiment features be **mandatory** or **optional** in ML pipeline?
   - Recommendation: Optional (with `--with-sentiment` flag)

7. **Social Sentiment**: Include **Reddit/Twitter metrics** in MVP or defer to Phase 4?
   - Recommendation: Defer to Phase 4 (focus on news sentiment first)

---

## 16. Approval Sign-Off

**Upon approval**, implementation will proceed as follows:

1. Create feature branch: `git checkout -b feature/news-sentiment-ingestion`
2. Set up Finnhub API account and add key to `.env`
3. Begin Phase 1 implementation (Foundation)
4. Weekly progress updates and demos
5. End-of-phase testing and validation
6. Final documentation and handoff

**Estimated completion**: 4 weeks from approval date
**Target release**: v0.3.0 (with news/sentiment support)

---

**Plan prepared by**: Claude (AI Assistant)
**Date**: 2026-02-28
**Version**: 1.0
**Status**: ✅ Ready for user review and approval
