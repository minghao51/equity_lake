# 🚀 ML Pipeline Usage Guide

Complete guide for running the equity ML pipeline from ingestion to AI/ML inference.

---

## 📁 What's New

Three stable CLI entrypoints are available to automate your pipeline:

1. **`equity-pipeline`** - Main pipeline orchestrator
2. **`equity-monitor`** - Health monitoring and data quality checks
3. **`equity-query`** - Query helper for inspecting results

---

## 🎯 Quick Start

### Option 1: Python Orchestrator (Recommended)

```bash
# Full pipeline for yesterday
uv run equity-pipeline

# Full pipeline for specific date
uv run equity-pipeline --date 2024-12-01

# Custom tickers
uv run equity-pipeline \
    --tickers AAPL,GOOGL,MSFT,NVDA \
    --markets us

# Dry run (test without writing)
uv run equity-pipeline --dry-run --verbose
```

---

## 📋 Pipeline Stages

The pipeline runs in **3 stages** automatically:

### Stage 1: Data Ingestion (2-5 min)
- Fetches EOD OHLCV data from US, CN, HK, SG markets
- Uses parallel fetching for 3x speedup
- Writes to `data/lake/{us_equity,cn_ashare,hk_sg_equity}/date=YYYY-MM-DD/*.parquet`

### Stage 2: Feature Engineering (1-3 min)
- Computes 40+ technical indicators (RSI, MACD, Bollinger Bands, ATR)
- Calculates return features (1/5/10/20-day lagged returns)
- Computes volume features (OBV, volume ratio)
- Adds time features (day of week, month, quarter)
- Writes to `data/lake/features/date=YYYY-MM-DD/*.parquet`

### Stage 3: ML/AI Inference (1-2 min)
- Loads XGBoost models for each ticker
- Predicts next-day price movements
- Outputs predictions to `data/predictions/`

**Total Time: 4-10 minutes for 10 tickers**

---

## 🔧 Common Usage Patterns

### 1. Daily Automation (Recommended)

```bash
# Add to crontab (runs weekdays at 7 PM ET)
crontab -e

# Add this line:
0 19 * * 1-5 cd /path/to/equity-lake && uv run equity-pipeline >> logs/cron.log 2>&1
```

### 2. Skip Ingestion (Data Already Exists)

```bash
# Run only feature engineering + ML
uv run equity-pipeline --skip-ingestion
```

### 3. Run Only ML Inference

```bash
# Skip ingestion and feature engineering
uv run equity-pipeline --skip-ingestion --skip-features
```

### 4. Custom Market Coverage

```bash
# US markets only
uv run equity-pipeline --markets us

# US + China markets
uv run equity-pipeline --markets us,cn

# All markets
uv run equity-pipeline --markets us,cn,hk_sg
```

### 5. Different Ticker Sets

```bash
# FAANG stocks
uv run equity-pipeline --tickers META,AAPL,AMZN,GOOGL,NFLX

# Top 10 US stocks (default)
uv run equity-pipeline

# Single ticker
uv run equity-pipeline --tickers AAPL
```

### 6. Backtesting

```bash
# Run for multiple dates
for date in 2024-11-01 2024-11-04 2024-11-05; do
    uv run equity-pipeline --date $date
done
```

---

## 🔍 Pipeline Monitoring

### Health Checks

```bash
# Run health check
uv run equity-monitor

# Verbose mode
uv run equity-monitor --verbose

# Save report to JSON
uv run equity-monitor --output-json health_report.json

# Custom thresholds
uv run equity-monitor \
    --max-age-days 1 \
    --null-threshold-pct 3.0
```

**What it checks:**
- ✅ Data freshness (are markets up-to-date?)
- ✅ Data quality (null values, missing data)
- ✅ Pipeline logs (errors, warnings)
- ✅ Feature store (recent features available)

**Output:**
```
======================================================================
PIPELINE HEALTH MONITOR
======================================================================

✅ PASS       Data Freshness
✅ PASS       Data Quality
✅ PASS       Pipeline Logs
✅ PASS       Feature Store

======================================================================
✅ Pipeline is HEALTHY
======================================================================
```

---

## 📊 Viewing Results

### Check Logs

```bash
# Pipeline logs
tail -100 logs/run_pipeline.log

# Ingestion logs
tail -100 logs/ingest_daily.log

# Feature engineering logs
tail -100 logs/feature_engineering.log

# All logs today
ls -lt logs/*.log | head -10
```

### Query Data with DuckDB

```bash
# Latest data
uv run equity-query --query latest_summary

# Top volume stocks
uv run equity-query --query top_volume --days 14

# Gainers and losers
uv run equity-query --query gainers_losers
```

### Inspect Results

The built-in Streamlit dashboard has been removed. Use the query commands above,
or connect DuckDB directly from a notebook or external visualization tool.

---

## 🛠️ Advanced Usage

### Continue on Error

```bash
# Don't stop if ingestion fails
uv run equity-pipeline --continue-on-error
```

### Save Pipeline Results

```bash
# Save execution results to JSON
uv run equity-pipeline --save-results

# Results saved to: logs/pipeline_results_YYYY-MM-DD.json
```

### Custom Date Range

```bash
# Ingest last 3 days
for i in {1..3}; do
    date=$(date -d "$i days ago" +%Y-%m-%d)
    uv run equity-pipeline --date $date
done
```

### Parallel Execution

```bash
# Run multiple dates in parallel (use with caution)
for date in 2024-11-01 2024-11-04 2024-11-05; do
    uv run equity-pipeline --date $date > logs/pipeline_$date.log 2>&1 &
done
wait  # Wait for all to complete
```

---

## 🐛 Troubleshooting

### Issue: "No data found for tickers"

**Solution:**
```bash
# Check if ingestion ran successfully
ls -la data/lake/us_equity/date=*/

# If empty, run ingestion first
uv run equity-daily --date 2024-12-01 --verbose
```

### Issue: "Feature engineering failed"

**Solution:**
```bash
# Check if you have enough historical data (need 60+ days per ticker)
uv run equity-query --query latest_summary

# If insufficient data, backfill more history
uv run python -m equity_lake.backfill_data --days-back 90 --parallel
```

### Issue: "ML inference failed"

**Solution:**
```bash
# Check if features exist
ls -la data/lake/features/date=*/

# If empty, run feature engineering first
uv run python -m equity_lake.features.engineering \
    --date 2024-12-01 \
    --tickers AAPL,GOOGL,MSFT
```

### Issue: Pipeline is slow

**Solutions:**
```bash
# 1. Enable parallel fetching (already enabled by default)
uv run equity-pipeline --verbose

# 2. Reduce number of tickers
uv run equity-pipeline --tickers AAPL,GOOGL,MSFT

# 3. Use fewer markets
uv run equity-pipeline --markets us

# 4. Check for rate limiting
tail -f logs/ingest_daily.log | grep -i "rate limit"
```

---

## 📈 Performance Benchmarks

| Configuration | Ingestion | Features | ML Inference | Total |
|--------------|-----------|----------|--------------|-------|
| 10 tickers, US only | 1-2 min | 30-60s | 20-30s | **2-4 min** |
| 10 tickers, all markets | 3-5 min | 30-60s | 20-30s | **4-7 min** |
| 50 tickers, US only | 2-3 min | 2-3 min | 1-2 min | **5-8 min** |
| 50 tickers, all markets | 5-8 min | 2-3 min | 1-2 min | **8-13 min** |

**Bottlenecks:**
- Ingestion: API rate limits (yfinance, akshare)
- Features: Rolling window calculations
- ML: Model loading (one-time cost per ticker)

---

## 🔄 Automation Strategy

### Daily Pipeline (Recommended)

```bash
# Crontab: Weekdays at 7 PM ET
0 19 * * 1-5 cd $(pwd) && uv run equity-pipeline >> logs/cron.log 2>&1

# Health check: Every 6 hours
0 */6 * * * cd $(pwd) && uv run equity-monitor >> logs/health.log 2>&1
```

### Weekly Model Retraining

```bash
# Crontab: Sunday 2 AM
0 2 * * 0 cd $(pwd) && uv run python -m equity_lake.price_forecaster --mode backtest --ticker AAPL >> logs/retrain.log 2>&1
```

### Hourly Feature Updates (Intraday)

```bash
# Crontab: Every hour during market hours (9 AM - 4 PM ET)
0 9-16 * * 1-5 cd $(pwd) && uv run python -m equity_lake.features.engineering --date $(date +\%Y-\%m-\%d) --tickers AAPL,GOOGL,MSFT >> logs/intraday.log 2>&1
```

---

## 🎓 Architecture Highlights

**Key Design Decisions:**
- **Idempotent Operations** - Safe to re-run without data duplication
- **Graceful Degradation** - Continues processing other markets if one fails
- **Observable Operations** - Structured logs with correlation IDs
- **Modular Design** - Each stage is independent and testable
- **Parallel Fetching** - 3x speedup with concurrent market data fetching
- **Hive Partitioning** - Fast time-range queries on Parquet data
- **Zero-Copy Queries** - DuckDB reads Parquet files directly without loading

**Technology Stack:**
- **Ingestion**: yfinance (US/HK/SG), akshare (CN)
- **Storage**: Hive-partitioned Parquet with Snappy compression
- **Features**: pandas_ta (40+ technical indicators)
- **ML**: XGBoost for price forecasting
- **Query**: DuckDB for SQL-on-Parquet
- **Visualization**: Streamlit dashboard

---

## 📚 Command Reference

### run_pipeline.py

| Argument | Description | Default |
|----------|-------------|---------|
| `--date` | Trading date (YYYY-MM-DD) | Yesterday |
| `--days-back` | Days back from today | 1 |
| `--markets` | Markets to ingest | us,cn,hk_sg |
| `--tickers` | Tickers for features/ML | Top 10 US stocks |
| `--skip-ingestion` | Skip Stage 1 | False |
| `--skip-features` | Skip Stage 2 | False |
| `--skip-ml` | Skip Stage 3 | False |
| `--continue-on-error` | Continue on failure | False |
| `--dry-run` | Simulate without writing | False |
| `--verbose` | Verbose logging | False |
| `--save-results` | Save to JSON | False |

### equity-monitor

| Argument | Description | Default |
|----------|-------------|---------|
| `--max-age-days` | Max data age (days) | 2 |
| `--null-threshold-pct` | Max null % | 5.0 |
| `--output-json` | Save report to file | None |
| `--verbose` | Verbose logging | False |

---

## 🎓 Next Steps

1. **Test the pipeline:**
   ```bash
   uv run equity-pipeline
   ```

2. **Check health:**
   ```bash
   uv run equity-monitor --verbose
   ```

3. **Set up automation:**
   ```bash
   crontab -e
   # Add daily pipeline schedule
   ```

4. **Monitor results:**
   ```bash
   tail -f logs/run_pipeline.log
   uv run equity-query --query latest_summary
   ```

---

**Last Updated:** 2025-01-24
**Pipeline Version:** 1.0.0
