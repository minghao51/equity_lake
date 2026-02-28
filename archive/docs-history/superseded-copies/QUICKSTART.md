# 🚀 Quick Start Guide

Get your ML pipeline running in 5 minutes!

---

## ✅ Step 1: Verify Installation (30 seconds)

```bash
# Run a dry-run pipeline check
uv run equity-pipeline --dry-run --verbose
```

Expected output:
```
✅ PASS: run_pipeline executes
✅ PASS: monitor_pipeline imports
✅ PASS: query_example imports
✅ All checks passed!
```

---

## 🎯 Step 2: Test Pipeline (1 minute)

```bash
# Dry run (no data written)
uv run equity-pipeline --dry-run --verbose
```

This will test all 3 stages without writing any data:
- Stage 1: Ingestion ✅
- Stage 2: Feature Engineering ✅
- Stage 3: ML Inference ✅

---

## 🚀 Step 3: Run Full Pipeline (5-10 minutes)

```bash
# Run the full pipeline
uv run equity-pipeline --verbose
```

**What happens:**
1. Downloads EOD data for US, CN, HK, SG markets (2-5 min)
2. Computes 40+ technical indicators (1-3 min)
3. Runs ML predictions for your tickers (1-2 min)

**Expected output:**
```
======================================================================
PIPELINE EXECUTION SUMMARY
======================================================================
Date: 2024-12-01
Markets: US, CN, HK_SG
Tickers: 10 (AAPL, GOOGL, MSFT, NVDA, TSLA...)
Duration: 8.45 seconds

ingestion                      ✅ SUCCESS        125.32s
feature_engineering            ✅ SUCCESS        45.21s
ml_inference                   ✅ SUCCESS        12.08s
======================================================================
```

---

## 🔍 Step 4: Check Health (30 seconds)

```bash
# Run health check
uv run equity-monitor --verbose
```

Expected output:
```
======================================================================
PIPELINE HEALTH MONITOR
======================================================================

✅ PASS       Data Freshness
✅ PASS       Data Quality
✅ PASS       Pipeline Logs
✅ PASS       Feature Store

✅ Pipeline is HEALTHY
```

---

## 📊 Step 5: View Results

### Option A: Query CLI
```bash
# Latest data summary
uv run equity-query --query latest_summary

# Top volume stocks
uv run equity-query --query top_volume --days 14
```

### Option B: Build Your Own Visualization
The built-in Streamlit dashboard has been removed. Use the query CLI and load the
resulting data into your notebook, BI tool, or a separate UI project instead.

---

## ⏰ Step 6: Set Up Automation (Optional)

### Daily Pipeline (Weekdays at 7 PM ET)
```bash
# Edit crontab
crontab -e

# Add this line:
0 19 * * 1-5 cd $(pwd) && uv run equity-pipeline >> logs/cron.log 2>&1

# Verify it's scheduled
crontab -l
```

### Health Check (Every 6 hours)
```bash
# Add to crontab
0 */6 * * * cd $(pwd) && uv run equity-monitor >> logs/health.log 2>&1
```

---

## 🎓 Common First-Time Tasks

### Change Tickers
```bash
# Custom ticker list
uv run equity-pipeline \
    --tickers AAPL,GOOGL,MSFT,NVDA,TSLA,META \
    --markets us
```

### Run for Specific Date
```bash
# Historical date
uv run equity-pipeline --date 2024-11-01
```

### Run Only ML Inference (Data Already Exists)
```bash
# Skip ingestion and features
uv run equity-pipeline --skip-ingestion --skip-features
```

---

## 🐛 Troubleshooting

### "No data found"
```bash
# Solution: Check if data directories exist
ls -la data/lake/

# If empty, data will be downloaded on first run
```

### "ImportError: No module named 'x'"
```bash
# Solution: Install dependencies
uv sync
```

### Pipeline takes too long
```bash
# Solution 1: Reduce tickers
uv run equity-pipeline --tickers AAPL,GOOGL,MSFT

# Solution 2: Use fewer markets
uv run equity-pipeline --markets us

# Solution 3: Check what's slow
tail -f logs/run_pipeline.log
```

---

## 📈 What's Next?

1. **Customize your ticker list** in `config/tickers.yaml`
2. **Explore results with DuckDB** via `uv run equity-query`
3. **Train your own models** using `uv run equity-price-forecast`
4. **Add more features** in `src/equity_lake/features/`
5. **Set up alerts** by modifying `src/equity_lake/monitoring/`

---

## 📚 Full Documentation

- **Pipeline Usage**: [Pipeline Usage Guide](../../docs/user-guide/pipeline.md)
- **Development Guide**: [claude.md](../../claude.md)
- **Project README**: [README.md](../../README.md)

---

## 💡 Tips

- Start with **US markets only** for faster testing
- Use **--dry-run** flag to test without writing data
- Check **logs/** directory for detailed execution logs
- Run **health checks** regularly to ensure data quality
- Use the query CLI or a notebook to inspect output data quickly

---

**Ready to automate?** → Set up the cron job and let it run daily!

**Questions?** → Check [Pipeline Usage Guide](../../docs/user-guide/pipeline.md) or [claude.md](../../claude.md)
