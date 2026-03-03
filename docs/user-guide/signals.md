# Signal Scanner User Guide

## Overview

The Signal Scanner generates buy/sell/hold signals for your watchlist using backtest strategies, news sentiment, and ML predictions.

## Quick Start

### 1. Configure Your Watchlist

Edit `config/watchlist.yaml`:

```yaml
name: "My Portfolio"
tickers:
  - AAPL
  - GOOGL
  - MSFT
```

### 2. Configure Signal Rules

Edit `config/signals.yaml` to adjust thresholds:

```yaml
backtest:
  enabled: true
  min_win_rate: 0.55

sentiment:
  enabled: true
  buy_threshold: 0.5

ml:
  enabled: true
  model_dir: data/models
  min_confidence: 60
```

### 3. Run Signal Scan

```bash
# Terminal output
equity-signal scan

# Markdown report
equity-signal scan --format md --output signals.md

# JSON for automation
equity-signal scan --format json --output signals.json

# Specific date
equity-signal scan --date 2024-12-01
```

## Signal Types

### Backtest Signals
Based on historical strategy performance:
- **BUY**: Price crosses above moving average
- **SELL**: Price crosses below moving average

### Sentiment Signals
Based on news sentiment analysis:
- **BUY**: Positive sentiment score
- **SELL**: Negative sentiment score

### ML Prediction Signals
Based on XGBoost next-day direction forecasts:
- **BUY**: Up-day prediction with probability above the buy threshold
- **SELL**: Down-day prediction with probability below the sell threshold

## Cron Setup

```bash
# Daily signal scan at 9:00 AM
0 9 * * * cd /path/to/equity-lake && equity-signal scan --format json > data/signals/latest.json
```

## Output Formats

- **table**: Colored terminal tables (default)
- **md**: Markdown report with tables
- **json**: Machine-readable JSON array
