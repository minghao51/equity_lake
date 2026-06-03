# News & Sentiment Ingestion Guide

This guide covers how to fetch financial news with sentiment analysis for US equities.

## Overview

The news ingestion module collects financial news articles from Finnhub API and analyzes sentiment using VADER (Valence Aware Dictionary and sEntiment Reasoner). This enables:

- **Alternative data collection**: News headlines and summaries for market sentiment analysis
- **Sentiment scoring**: Automatic polarity classification (positive/negative/neutral)
- **ML feature enrichment**: Sentiment features for price prediction models

## Quick Start

### 1. Get Finnhub API Key

1. Visit https://finnhub.io/
2. Register for a free account
3. Get your API key from the dashboard

### 2. Configure Environment

```bash
# Add to .env file
export FINNHUB_API_KEY=your_api_key_here

# Or source from .env.example
cp .env.example .env
# Edit .env and add your key
```

### 3. Fetch News

```bash
# Fetch news for specific tickers
equity-news --tickers AAPL,GOOGL,MSFT --date 2024-12-01

# Fetch with parallel processing (faster for many tickers)
equity-news --tickers AAPL,GOOGL,MSFT --max-workers 3

# Dry run (test without writing)
equity-news --dry-run --verbose
```

## CLI Usage

### Basic Commands

```bash
# Fetch news for yesterday
equity-news

# Fetch for specific date
equity-news --date 2024-12-01

# Fetch for specific tickers
equity-news --tickers AAPL,GOOGL,MSFT,AMZN,TSLA

# Maximum articles per ticker (default: 50)
equity-news --max-articles 100

# Minimum relevance score (0.0 to 1.0)
equity-news --min-relevance 0.7

# Parallel fetching with 3 workers
equity-news --max-workers 3

# Dry run mode
equity-news --dry-run --verbose
```

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--date` | Trading date (YYYY-MM-DD) | yesterday |
| `--tickers` | Comma-separated ticker symbols | (none) |
| `--max-articles` | Max articles per ticker | 50 |
| `--sentiment-method` | Sentiment analysis method | vader |
| `--min-relevance` | Minimum relevance score (0.0-1.0) | 0.0 |
| `--max-workers` | Max parallel workers | 1 (sequential) |
| `--api-key` | Finnhub API key | from env |
| `--dry-run` | Skip Parquet writes | false |
| `--verbose, -v` | Enable verbose logging | false |

## Data Schema

### News Data Columns

| Column | Type | Description |
|--------|------|-------------|
| `ticker` | STRING | Stock symbol (e.g., "AAPL") |
| `date` | DATE | Published date (partition key) |
| `datetime` | DATETIME | Exact publication timestamp |
| `source` | STRING | News source (e.g., "Reuters", "Bloomberg") |
| `headline` | STRING | Article title |
| `summary` | STRING | Article summary/description |
| `url` | STRING | Article URL |
| `category` | STRING | News category (e.g., "earnings", "merger") |
| `sentiment_score` | FLOAT | VADER compound score (-1.0 to 1.0) |
| `sentiment_label` | STRING | "positive", "negative", or "neutral" |
| `relevance_score` | FLOAT | API relevance score (0.0 to 1.0) |

### Storage Structure

```
data/lake/us_news/
├── date=2024-12-01/
│   └── 2024-12-01.parquet
├── date=2024-12-02/
│   └── 2024-12-02.parquet
└── ...
```

## Sentiment Analysis

### VADER Scoring

- **Positive**: compound score >= 0.05
- **Negative**: compound score <= -0.05
- **Neutral**: -0.05 < compound score < 0.05

### Example Sentiments

| Headline | Score | Label |
|----------|-------|-------|
| "AAPL stock surges on strong earnings" | +0.51 | positive |
| "GOOGL declines on weak guidance" | -0.34 | negative |
| "MSFT announces new CEO" | 0.00 | neutral |

### Accuracy

- **Speed**: ~100ms per article
- **Accuracy**: ~70% on financial text
- **Best for**: Daily batch processing, real-time updates

### Future: FinBERT

FinBERT is a transformer model fine-tuned on financial communications with ~90%+ accuracy but slower (~1-2s per article). Planned for Phase 5.

## Performance Tuning

### Parallel Fetching

For fetching news for many tickers, use parallel processing:

```bash
# Sequential (default): 1 ticker per second
equity-news --tickers AAPL,GOOGL,MSFT,AMZN,TSLA  # ~5 seconds

# Parallel (3 workers): 3 tickers concurrently
equity-news --tickers AAPL,GOOGL,MSFT,AMZN,TSLA --max-workers 3  # ~2 seconds
```

**Recommendations**:
- 1-10 tickers: Use `--max-workers 1` (sequential)
- 10-50 tickers: Use `--max-workers 3`
- 50+ tickers: Use `--max_workers 5`

### Rate Limiting

Finnhub free tier allows **60 calls per minute**. The fetcher includes:
- Automatic 1-second delay between tickers (sequential mode)
- Retry logic with exponential backoff
- Graceful degradation on API errors

### Storage Efficiency

Raw news data grows at ~100-500 MB per year for 100 tickers. To manage:

```bash
# Check storage usage
du -sh data/lake/us_news/

# List oldest dates
ls -lt data/lake/us_news/

# Remove old data (optional)
rm -rf data/lake/us_news/date=2024-*/
```

## Integration with Pipeline

### Include in Daily Ingestion

```bash
# Add us_news to markets
equity-daily --markets us,us_news

# Or run separately
equity-news --date $(date -v-1d +%Y-%m-%d)
```

### Feature Engineering

Sentiment features can be merged into ML features:

```python
from equity_lake.features.engineering import FeatureEngineer

engineer = FeatureEngineer()
features_df = engineer.generate_features(
    tickers=["AAPL"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)

# Merge sentiment (future feature)
features_with_sentiment = engineer.merge_sentiment_features(
    features_df,
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)
```

## Querying News Data

### DuckDB Queries

```sql
-- Latest news by ticker
SELECT ticker, headline, sentiment_label, date
FROM read_parquet('data/lake/us_news/**/*.parquet', hive_partitioning=1)
WHERE ticker = 'AAPL'
ORDER BY date DESC
LIMIT 10;

-- Sentiment distribution
SELECT sentiment_label, COUNT(*) as count
FROM read_parquet('data/lake/us_news/**/*.parquet', hive_partitioning=1)
GROUP BY sentiment_label;

-- Positive news for a date range
SELECT ticker, headline, sentiment_score
FROM read_parquet('data/lake/us_news/**/*.parquet', hive_partitioning=1)
WHERE date BETWEEN '2024-12-01' AND '2024-12-07'
  AND sentiment_label = 'positive'
ORDER BY sentiment_score DESC;
```

## Troubleshooting

### "FINNHUB_API_KEY not set"

```bash
# Check if key is set
echo $FINNHUB_API_KEY

# Add to .env file
echo "FINNHUB_API_KEY=your_key_here" >> .env

# Source .env file
source .env
```

### "No news articles fetched"

- Check if the date has news (weekends may have no news)
- Verify tickers are valid (use exact symbols like "AAPL", not "Apple")
- Increase `--max-articles` limit
- Check API quota at https://finnhub.io/dashboard

### "Rate limit exceeded"

```bash
# Add delay between requests
export API_RETRY_DELAY=2.0

# Reduce parallelism
equity-news --max-workers 1
```

### "ImportError: No module named 'vaderSentiment'"

```bash
# Install dependencies
uv sync

# Or install explicitly
uv pip install vaderSentiment
```

## Best Practices

### 1. Fetch During Off-Peak Hours

```bash
# Schedule cron job at 2 AM
0 2 * * * cd /path/to/equity_lake && equity-news
```

### 2. Use Explicit Ticker Lists

```bash
# Good: Fetch specific tickers
equity-news --tickers AAPL,GOOGL,MSFT

# Avoid: Fetching all tickers can be slow
equity-news --tickers $(cat all_tickers.txt)  # Use --max-workers
```

### 3. Monitor Sentiment Drift

```sql
-- Track sentiment over time
SELECT
    date,
    AVG(sentiment_score) as avg_sentiment,
    COUNT(*) as article_count
FROM read_parquet('data/lake/us_news/**/*.parquet', hive_partitioning=1)
WHERE ticker = 'AAPL'
  AND date >= '2024-01-01'
GROUP BY date
ORDER BY date DESC;
```

### 4. Deduplication

The writer automatically deduplicates by URL. If you fetch the same date twice:

```bash
# First run: 50 articles
equity-news --date 2024-12-01  # Writes 50 articles

# Second run: No new articles
equity-news --date 2024-12-01  # Skips 50 duplicates
```

## API Limits

### Finnhub Free Tier

| Limit | Value |
|-------|-------|
| API calls per minute | 60 |
| News articles | Unlimited |
| Historical data | Unlimited |
| Cost | $0/month |

### Rate Limit Handling

The fetcher implements:
- Exponential backoff retry (1s, 2s, 4s delays)
- Graceful degradation (continues on partial failures)
- Per-ticker error isolation

## Advanced Usage

### Python API

```python
from datetime import date
from equity_lake.ingestion.sources.news import FinnhubNewsFetcher

fetcher = FinnhubNewsFetcher(
    api_key="your_key",
    tickers=["AAPL", "GOOGL"],
    max_articles_per_ticker=50,
    max_workers=3,
)

df = fetcher.fetch(date(2024, 12, 1))

print(df.head())
print(df['sentiment_label'].value_counts())
```

### Custom Sentiment Analysis

```python
from equity_lake.sentiment import SentimentAnalyzer

analyzer = SentimentAnalyzer(method="vader")

# Single text
result = analyzer.analyze("AAPL stock surges on earnings")
print(result)  # {'compound': 0.51, 'label': 'positive', ...}

# Batch analysis
texts = ["Great earnings", "Terrible revenue", "Stock unchanged"]
df = analyzer.analyze_batch(texts)
print(df)
```

## Future Enhancements

- **FinBERT Integration**: Higher accuracy sentiment analysis
- **Social Sentiment**: Reddit/Twitter metrics (Phase 4)
- **Multi-Market Support**: CN/HK news sources
- **Real-Time Streaming**: WebSocket-based news updates
- **Sentiment Trends**: 7/30/90-day moving averages

## Resources

- **Finnhub API Docs**: https://finnhub.io/docs/api
- **VADER Paper**: https://github.com/cjhutto/vaderSentiment
- **Project Documentation**: See `CLAUDE.md` for architecture details

## Support

For issues or questions:

1. Check logs: `tail -100 logs/news_ingestion.log`
2. Run diagnostics: `equity-news --dry-run --verbose`
3. Review this guide's troubleshooting section
4. Check API status: https://finnhub.io/docs/api
