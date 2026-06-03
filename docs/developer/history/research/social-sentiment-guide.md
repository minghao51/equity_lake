# Social Sentiment Ingestion Guide

This guide covers social sentiment data ingestion for US equities using the Finnhub Social API, which provides Reddit and Twitter mention metrics and sentiment scores.

## Overview

Social sentiment analysis captures retail investor sentiment from social media platforms:

- **Reddit**: Mentions and sentiment from financial subreddits (r/wallstreetbets, r/stocks, etc.)
- **Twitter**: Mentions and sentiment from tweets about stocks
- **Metrics**: Mention counts, positive/negative scores, normalized sentiment (-1.0 to 1.0)

## Data Schema

### Social Sentiment Schema

All social sentiment data conforms to the `SOCIAL_COLUMNS` schema:

```python
SOCIAL_COLUMNS = [
    'ticker',              # STRING: Stock symbol (e.g., 'AAPL')
    'date',                # DATE: Date of measurement (partition key)
    'datetime',            # DATETIME: Exact timestamp
    'source',              # STRING: 'reddit' or 'twitter'
    'mention_count',       # INT: Number of mentions
    'positive_score',      # FLOAT: Positive sentiment score (raw count)
    'negative_score',      # FLOAT: Negative sentiment score (raw count)
    'score',               # FLOAT: Normalized sentiment (-1.0 to 1.0)
    'social_metric',       # STRING: Metric type (default: 'mention_count')
]
```

### Example Data

```python
{
    "ticker": "AAPL",
    "date": "2024-12-01",
    "datetime": "2024-12-01 15:30:00",
    "source": "reddit",
    "mention_count": 1250,
    "positive_score": 800.0,
    "negative_score": 150.0,
    "score": 0.684,  # (800 - 150) / (800 + 150)
    "social_metric": "mention_count"
}
```

### Sentiment Score Normalization

The `score` field is normalized to the range [-1.0, 1.0]:

- **1.0**: All mentions are positive
- **0.0**: Equal positive and negative mentions
- **-1.0**: All mentions are negative

Formula: `score = (positive_score - negative_score) / (positive_score + negative_score)`

## Storage

### Directory Structure

Social sentiment data is stored in `data/lake/us_social_sentiment/` with Hive-style partitioning:

```
data/lake/us_social_sentiment/
├── date=2024-12-01/
│   └── 2024-12-01.parquet
├── date=2024-12-02/
│   └── 2024-12-02.parquet
└── ...
```

### Partitioning

- **Partition key**: `date` (YYYY-MM-DD format)
- **File format**: Apache Parquet with Snappy compression
- **Deduplication**: By `ticker + datetime + source`

## CLI Usage

### Fetch Social Sentiment

```bash
# Fetch for yesterday (default)
equity-sentiment

# Fetch for specific date
equity-sentiment --date 2024-12-01

# Fetch for specific tickers
equity-sentiment --tickers AAPL,GOOGL,MSFT

# Fetch with parallel workers (faster)
equity-sentiment --max-workers 4

# Dry run (test without writing)
equity-sentiment --dry-run --verbose
```

### Command Arguments

- `--date DATE`: Trading date (YYYY-MM-DD, default: yesterday)
- `--tickers SYMBOLS`: Comma-separated ticker symbols
- `--max-workers N`: Maximum parallel workers (default: 1, sequential)
- `--api-key KEY`: Finnhub API key (default: from `FINNHUB_API_KEY` env var)
- `--dry-run`: Skip actual Parquet writes (for testing)
- `--verbose, -v`: Enable verbose logging

### Integration with Daily Pipeline

Fetch social sentiment alongside other markets:

```bash
# Fetch social sentiment with EOD data
equity-daily --markets us_social_sentiment

# Fetch multiple markets including social sentiment
equity-daily --markets us,us_news,us_social_sentiment --parallel
```

## API Configuration

### Finnhub API

**Required**: Finnhub API key for social sentiment access.

**Get your free API key**:
1. Visit https://finnhub.io/
2. Sign up for a free account
3. Navigate to API Key section
4. Copy your API key

**Set environment variable**:

```bash
# Add to ~/.bashrc or ~/.zshrc
export FINNHUB_API_KEY=your_api_key_here

# Or load from .env file
echo "FINNHUB_API_KEY=your_api_key_here" >> .env
```

### Rate Limits

- **Free tier**: 60 calls/minute
- **Implementation**: 1 second delay between ticker requests
- **Parallel fetching**: Distributes rate limits across workers

## Feature Engineering

### Merging Social Sentiment Features

Social sentiment features can be merged with OHLCV features for ML models:

```python
from equity_lake.features import FeatureEngineer

engineer = FeatureEngineer()
features_df = engineer.generate_features(
    tickers=["AAPL", "GOOGL"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
    include_sentiment=False,  # News sentiment
)

# Merge social sentiment
features_with_social = engineer.merge_social_sentiment_features(
    features_df,
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)
```

### Social Sentiment Features

When merged, the following features are added:

- `social_mention_count`: Total mentions (Reddit + Twitter)
- `social_sentiment_score`: Average normalized sentiment (-1.0 to 1.0)
- `social_positive_score`: Total positive mentions
- `social_negative_score`: Total negative mentions
- `social_reddit_mentions`: Reddit mention count
- `social_twitter_mentions`: Twitter mention count
- `social_momentum`: 5-day change in mention count
- `social_sentiment_momentum`: 5-day change in sentiment score

### Querying Social Sentiment with DuckDB

```python
import duckdb
from equity_lake.core.runtime import LAKE_DIR

conn = duckdb.connect()

# Load social sentiment data
query = f"""
    SELECT
        ticker,
        date,
        source,
        mention_count,
        score,
        positive_score,
        negative_score
    FROM read_parquet('{LAKE_DIR}/us_social_sentiment/**/*.parquet', hive_partitioning=1)
    WHERE date >= '2024-12-01'
    ORDER BY ticker, date, source
"""

df = conn.execute(query).df()
```

### Analyzing Social Metrics

```python
# Top mentioned stocks
top_mentioned = df.groupby("ticker")["mention_count"].sum().nlargest(10)

# Sentiment distribution
avg_sentiment = df.groupby("ticker")["score"].mean().sort_values(ascending=False)

# Reddit vs Twitter comparison
reddit_vs_twitter = df.groupby(["ticker", "source"])["mention_count"].sum().unstack()

# Social momentum (growing/shrinking mentions)
daily_mentions = df.groupby(["ticker", "date"])["mention_count"].sum()
momentum = daily_mentions.groupby("ticker").pct_change(5)
```

## Architecture

### FinnhubSocialSentimentFetcher

**Location**: `src/equity_lake/ingestion/sources/sentiment.py`

**Key methods**:
- `fetch(trading_date)`: Fetch social sentiment for all tickers
- `_fetch_sequential(trading_date)`: Sequential fetching (one ticker at a time)
- `_fetch_parallel(trading_date)`: Parallel fetching with ThreadPoolExecutor
- `_fetch_sentiment_for_ticker(ticker, date)`: Fetch sentiment for single ticker
- `_parse_sentiment_metric(data, ticker, date, source)`: Parse API response

**Features**:
- Automatic retries with exponential backoff
- Parallel fetching support
- Rate limiting (1 second delay between requests)
- Graceful error handling (continues on individual ticker failures)

### Data Flow

```
Finnhub API → FinnhubSocialSentimentFetcher → Parquet Write
     ↓                    ↓                          ↓
Reddit/Twitter      Parse & Normalize        Partitioned Storage
Metrics             sentiment score          (date=*/)
```

## Testing

### Unit Tests

Run unit tests for social sentiment fetcher:

```bash
# Run all social sentiment tests
pytest tests/unit/test_social_sentiment.py -v

# Run specific test
pytest tests/unit/test_social_sentiment.py::TestFinnhubSocialSentimentFetcher::test_fetch_sequential_success -v

# Run with coverage
pytest tests/unit/test_social_sentiment.py --cov=equity_lake.ingestion.sources.sentiment --cov-report=html
```

### Mock API Responses

Unit tests use mocked API responses to avoid rate limits:

```python
# Mock Finnhub API response
mock_response = {
    "sentiment": {
        "reddit": {
            "mention": 1250,
            "positive": 800,
            "negative": 150,
        },
        "twitter": {
            "mention": 3400,
            "positive": 2100,
            "negative": 400,
        }
    }
}
```

## Best Practices

### 1. Ticker Selection

Social sentiment is most valuable for liquid, high-volume stocks:

```bash
# Top 100 tickers by priority (default if not specified)
equity-daily --markets us_social_sentiment

# Focus on specific sectors
equity-sentiment --tickers AAPL,MSFT,GOOGL,AMZN,META
```

### 2. Data Quality

Social sentiment data quality varies by:

- **Stock popularity**: Large-cap stocks have more reliable data
- **Market events**: Earnings, news spikes cause abnormal mention counts
- **Platform dynamics**: Reddit sentiment may differ from Twitter sentiment

**Mitigation strategies**:
- Use mention momentum rather than absolute counts
- Average sentiment across multiple days
- Compare Reddit vs Twitter trends
- Normalize by historical averages

### 3. Feature Engineering

**Recommended features**:

```python
# Social momentum (growing interest)
social_momentum = mention_count.pct_change(5)

# Sentiment momentum (improving/worsening)
sentiment_momentum = score.diff(5)

# Reddit vs Twitter divergence
platform_divergence = reddit_score - twitter_score

# Abnormal activity (z-score)
mention_zscore = (mention_count - mean) / std
```

**Avoid**:
- Raw mention counts (biased by stock popularity)
- Single-day sentiment spikes (noise)
- Absolute sentiment scores (context-dependent)

### 4. Performance

**Sequential fetching**: Safe for rate limits, slower
```bash
equity-sentiment --max-workers 1
```

**Parallel fetching**: Faster, distribute rate limits
```bash
equity-sentiment --max-workers 4
```

**Recommended**: Use parallel fetching for 100+ tickers

## Troubleshooting

### No Data Fetched

**Symptom**: `No social sentiment data fetched` message

**Solutions**:
1. Verify API key: `echo $FINNHUB_API_KEY`
2. Check Finnhub status: https://finnhub.io/status
3. Test API manually:
   ```bash
   curl "https://finnhub.io/api/v1/news-sentiment?symbol=AAPL&token=$FINNHUB_API_KEY"
   ```
4. Verify ticker symbols: Use uppercase (e.g., 'AAPL', not 'aapl')

### Rate Limit Errors

**Symptom**: `Too Many Requests` errors

**Solutions**:
1. Reduce parallel workers: `--max-workers 1`
2. Add delays between batches
3. Upgrade Finnhub API tier for higher limits

### Empty Sentiment Data

**Symptom**: API returns empty sentiment for some tickers

**Cause**: Not all stocks have social media coverage

**Solutions**:
1. Focus on popular stocks (large-cap, high volume)
2. Accept missing data (normal for smaller stocks)
3. Use feature engineering to handle missing values

### Schema Validation Errors

**Symptom**: `Schema validation failed` message

**Solutions**:
1. Check for required columns: `SOCIAL_COLUMNS`
2. Verify data types:
   - `mention_count`: int
   - `positive_score`, `negative_score`, `score`: float
   - `date`: date or datetime
3. Check for null values in required fields

## Advanced Usage

### Custom Fetching Logic

Extend `FinnhubSocialSentimentFetcher` for custom logic:

```python
from equity_lake.ingestion.sources.sentiment import FinnhubSocialSentimentFetcher

class CustomSocialSentimentFetcher(FinnhubSocialSentimentFetcher):
    def _parse_sentiment_metric(self, source_data, ticker, trading_date, source):
        # Add custom parsing logic
        result = super()._parse_sentiment_metric(source_data, ticker, trading_date, source)

        # Add custom fields
        if result:
            result["custom_field"] = "custom_value"

        return result
```

### Batch Historical Fetch

Fetch historical social sentiment for backtesting:

```bash
# Fetch last 30 days
for i in {1..30}; do
    date=$(date -d "$i days ago" +%Y-%m-%d)
    equity-sentiment --date $date --tickers AAPL,GOOGL,MSFT
done
```

### Integration with ML Pipeline

```python
from equity_lake.ml import MLTrainingPipeline

# Prepare training data with social sentiment
pipeline = MLTrainingPipeline()

features = pipeline.prepare_features(
    tickers=["AAPL", "GOOGL"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
    include_news_sentiment=True,
    include_social_sentiment=True,  # Add social sentiment
)

# Train model with social features
model = pipeline.train_model(
    features=features,
    target_col="next_day_return",
    feature_cols=[
        "rsi_14",
        "macd",
        "avg_daily_sentiment",  # News sentiment
        "social_mention_count",  # Social sentiment
        "social_sentiment_score",
        "social_momentum",
    ],
)
```

## References

- **Finnhub API Docs**: https://finnhub.io/docs/api/news-sentiment
- **News Ingestion Guide**: See news ingestion for news sentiment
- **Feature Engineering**: See ML platform documentation
- **Schema Reference**: `equity_lake.core.runtime.SOCIAL_COLUMNS`

## Changelog

### v0.1.0 (2025-01-23)

- Initial social sentiment ingestion
- Reddit and Twitter support
- Parallel fetching
- Feature merging with ML pipeline
- Comprehensive unit tests
