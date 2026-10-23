# Signal Scanner User Guide

## Overview

The Signal Scanner generates buy/sell/hold signals for your watchlist using backtest strategies, news sentiment, and ML predictions.

## Quick Start

### 1. Configure Your Canonical Private Watchlist

The default `equity signal scan` watchlist is the managed private YAML at
`data/watchlists/watchlist.yaml`, keyed by `(market, MIC, symbol)`. Use the
unified `equity tracking` CLI to add or update instruments; it validates exact
active source configuration and does not fetch or backfill data:

```bash
uv run equity tracking add --market us_equity --mic XNAS --symbol SYMBOL --watch-only
uv run equity tracking list
```

Replace `SYMBOL` with an active instrument from `config/tickers.yaml`. For an
explicit legacy ticker-only file, pass `--watchlist PATH --market MARKET`;
`--mic` is also required if the configured symbol is ambiguous. Ticker text is
never treated as a global identity.

### 2. Configure Signal Rules

Edit `config/signals.yaml` to adjust thresholds:

```yaml
backtest:
  enabled: true
  strategies:
    - name: "sma_deviation_20d"   # rule descriptor, not a registry strategy
      lookback_days: 20
      buy_threshold: 0.02
      sell_threshold: -0.01

sentiment:
  enabled: true
  buy_threshold: 0.5

ml:
  enabled: false  # opt in only when a trusted compatible local artifact exists
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

# Preview scope without constructing generators, inference, or any writes
uv run equity signal scan --dry-run

# Specific trading date
uv run equity signal scan --date 2024-12-02
```

Dry-run prints a JSON plan to stdout, including the resolved date, tickers,
enabled generators and intended output path. Even with `--output`, it does not
write a rendered report, signal history, or structured scan report; create
runtime/model directories; or run inference. Invalid configuration, format,
date, or unsupported watchlist identity exits nonzero before generator
construction. A normal scan writes the requested rendered output, saves
nonempty signal history, and persists a structured report even when there are
zero signals.

Scans run sequentially in watchlist order: generators own non-thread-safe
DuckDB connections/model state. The Python `max_workers` argument is retained
for compatibility but no longer enables parallel scans.

The default `equity signal scan` reads the managed canonical private watchlist
across the five configured price markets. An explicitly supplied legacy
ticker-only list requires `--market` and an unambiguous `--mic`; unsupported,
inactive, unknown, or ambiguous identities are rejected rather than guessed.
Results are research candidates, not validated trading advice, and no
performance or freshness guarantee is implied.

The richer [`equity tracking` workflow](20261016-private-tracking-preview.md)
reports exact-session readiness, records local transitions/events, and supports
explicitly opted-in webhook delivery. `equity doctor` is a read-only
all-market observation; `equity signal history` reads existing legacy/Delta
history without migration. Neither signal scanning nor tracking places orders
or verifies account holdings.

Before constructing generators, YAML roots, nested types, unknown keys, and
threshold/range constraints are validated. Enabled generator/ticker evaluations
are recorded as `signal`, `no_condition`, `unavailable`, `missing_model`,
`error`, or `not_scheduled`. The separate structured run report is written under
`data/signal_scans/`; `--format json` remains the legacy JSON signal array.
Reports distinguish `success`, `partial`, `failed`, and `not_scheduled`. A
scheduled scan exits nonzero when all enabled evaluations fail; partial results
are preserved and reported without imposing an additional partial-failure
trading policy.

## Signal Types

### Backtest Signals

Based on the current price's deviation from a rolling moving average:

- **BUY**: Deviation is at or above the configured positive threshold
- **SELL**: Deviation is at or below the configured negative threshold

These are persistent threshold states, not detected crossings or backtest
performance estimates. Repeated scans can report the same condition.

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
- A non-`None` horizon from prediction output is reported as-is. If all supported output horizon fields are missing or `None`, metadata reports a 1-day (next-day) horizon. The legacy `ml.horizon_days` config field remains accepted for backwards compatibility but is deprecated/informational and ignored for this v1 fallback.

For `v2_meta_label`:

- signals are only produced when a candidate entry is accepted by the model
- the stored optimized threshold takes precedence; `meta_label_threshold` is the configured fallback
- the selected `ml.candidate_strategies`, or `backtest.strategies` from the same selected config when that field is omitted, are passed explicitly to the forecaster; an explicit empty candidate list is rejected and no repository-default rules are substituted
- the explanation and scan output/report metadata use the effective `threshold_used` and `threshold_source` returned by inference. Legacy prediction output without a source is shown as `unknown`.
- the legacy Delta signal-history schema remains unchanged: history retains the effective value in `meta_label_threshold` and the source in the explanation text; the structured scan report preserves both provenance fields.
- `vertical_barrier_days` remains the v2 vertical-barrier horizon; `pt_mult` and `sl_mult` define its profit-taking and stop-loss barriers. The v1 fallback described above does not change v2 barrier semantics.

For v2 threshold precedence, the stored optimized threshold remains first and
the configured threshold is only the fallback; this change does not add an
execution override. These outputs are research candidates, not validated ML alerting or evidence
of profitable performance. The current private price path also requires
verified split-adjustment coverage; native yfinance action ingestion does not
currently provide the source clock, complete coverage assertion, and expiry
proof needed for adjusted output. Until verified proof exists, adjusted
price-based signals are unavailable rather than raw-price fallbacks.

The scanner chooses the ML path from `ml.mode` in `config/signals.yaml`. `v1_direction` uses `MLPredictionSignalGenerator`; `v2_meta_label` uses `MetaLabelSignalGenerator`.

## ML Config Knobs

Current `ml` settings in `config/signals.yaml`:

- `enabled`: turns the ML generator on or off
- `model_dir`: where trained model artifacts are stored
- `mode`: `v1_direction` or `v2_meta_label`
- `horizon_days`: legacy field retained for backwards compatibility; deprecated/informational and ignored when v1 prediction output has no horizon (the v1 fallback is always 1 day). It does not control the v2 barrier horizon, which is `vertical_barrier_days`.
- `buy_probability_threshold`: minimum probability for `BUY` in `v1_direction`
- `sell_probability_threshold`: maximum probability for `SELL` in `v1_direction`
- `min_confidence`: confidence floor for ML signals
- `vertical_barrier_days`: v2 barrier horizon
- `pt_mult`: v2 profit-taking barrier multiplier
- `sl_mult`: v2 stop-loss barrier multiplier
- `embargo_days`: v2 validation embargo input
- `meta_label_threshold`: fallback v2 execution threshold when no optimized threshold is stored

## Cron Setup

```bash
# Daily signal scan at 9:00 AM in the scheduler's timezone
# Create reports/ first; do not put rendered JSON inside the data/signals Delta table.
0 9 * * * cd /path/to/equity-lake && uv run equity signal scan --format json --output reports/signals-latest.json
```

## Output Formats

- **table**: Colored terminal tables (default)
- **md**: Markdown report with tables
- **json**: Machine-readable JSON array (kept compatible)

Every non-dry-run legacy signal scan also writes a Pydantic-validated
structured outcome record under `data/signal_scans/`, including no-signal and
zero-event scans. This auxiliary record is separate from the Delta-backed
signal history and is not part of the cataloged medallion lake. A short
coverage summary is written to stderr so JSON stdout remains an array.

`equity signal scan --dry-run` only plans the legacy signal scan. The separate
`equity tracking scan --dry-run` returns a no-read/no-inference/no-persistence/
no-delivery plan. Both are safe previews, not authorization for real source
fetches, paid processing, live delivery, or production changes.
