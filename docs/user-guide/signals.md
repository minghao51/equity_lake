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
uv run equity signal scan

# Markdown report
uv run equity signal scan --format md --output signals.md

# JSON for automation
uv run equity signal scan --format json --output signals.json

# Specific date
uv run equity signal scan --date 2024-12-01
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
The ML generator supports two shipped modes through `config/signals.yaml`:

- `v1_direction`: XGBoost predicts next-day direction directly.
- `v2_meta_label`: the scanner first builds candidate entries from the configured backtest strategy, then the ML model decides whether to execute that candidate.

For `v1_direction`:
- **BUY**: Up-day prediction with probability above the buy threshold
- **SELL**: Down-day prediction with probability below the sell threshold

For `v2_meta_label`:
- signals are only produced on dates where a candidate entry exists
- `meta_label_threshold` controls whether the candidate is executed
- `vertical_barrier_days`, `pt_mult`, and `sl_mult` define the barrier settings used during training and reported at inference time

The scanner chooses the ML path from `ml.mode` in `config/signals.yaml`. `v1_direction` uses `MLPredictionSignalGenerator`; `v2_meta_label` uses `MetaLabelSignalGenerator`.

## ML Config Knobs

Current `ml` settings in `config/signals.yaml`:

- `enabled`: turns the ML generator on or off
- `model_dir`: where trained model artifacts are stored
- `mode`: `v1_direction` or `v2_meta_label`
- `horizon_days`: inference horizon shown in signal metadata
- `buy_probability_threshold`: minimum probability for `BUY` in `v1_direction`
- `sell_probability_threshold`: maximum probability for `SELL` in `v1_direction`
- `min_confidence`: confidence floor for ML signals
- `vertical_barrier_days`: v2 barrier horizon
- `pt_mult`: v2 profit-taking barrier multiplier
- `sl_mult`: v2 stop-loss barrier multiplier
- `embargo_days`: v2 validation embargo input
- `meta_label_threshold`: v2 execution threshold

## Cron Setup

```bash
# Daily signal scan at 9:00 AM
0 9 * * * cd /path/to/equity-lake && uv run equity signal scan --format json > data/signals/latest.json
```

## Output Formats

- **table**: Colored terminal tables (default)
- **md**: Markdown report with tables
- **json**: Machine-readable JSON array
