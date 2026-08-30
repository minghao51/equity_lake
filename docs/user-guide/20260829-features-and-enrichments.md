# Feature Generation and Enrichment User Guide

This guide covers the feature stage: how `03_gold/features` gets built, which
enrichments can be layered on top of the technical indicator set, and how to
recover missing history safely.

## Overview

The feature stage turns bronze OHLCV data into the model-ready dataset at
`data/lake/03_gold/features/`. It runs as stage 2 of `equity pipeline`
(ingestion → features → ML) and is the only writer to that table. Everything
downstream — `equity forecast`, `equity ml train`, `equity ml compare`,
`equity ml ablate` — reads pre-computed features from the same table via
`FeatureLoader`; nothing recomputes them on the fly.

The daily entrypoint is unchanged:

```bash
dotenvx run -- uv run equity pipeline
```

## The feature job contract

The feature stage calls `run_feature_job` (`src/equity_lake/features/__init__.py`),
which enforces three rules:

1. **120-day warm-up window.** Features are computed over
   `output_start_date - 120 days` through `output_end_date`. Indicators such as
   `volatility_20` and `volume_ma_20` need trailing history to be correct, so
   the job always computes over more history than it publishes. The same 120
   appears as `HISTORY_BACKFILL_WINDOW_DAYS` in `src/equity_lake/pipeline.py`
   when recovering missing history.
2. **Output-window filtering.** Only rows inside `[output_start_date,
   output_end_date]` are persisted; warm-up rows are computed and discarded.
   For a daily run both dates are the trading date, so exactly one row per
   ticker survives.
3. **Fail loudly on missing history.** If the computed frame is empty, or the
   requested output window is empty after filtering, the job raises
   `NoFeatureHistoryError` rather than writing a partial table.

Writes go through `upsert_dataset(output_df, "03_gold/features",
output_end_date)` — a Delta upsert keyed on the trading date, so re-running a
date replaces that date's rows instead of duplicating them.

Two guards run before the job:

- A required price market (`us`, `cn`, `hk_sg`, `jpx`, `krx`) that failed
  ingestion blocks the feature stage entirely (`features_blocked_required_source_failure`).
- `--dry-run` skips the feature stage (no feature output), per the project's
  dry-run contract.

Per-ticker minimum: `FeatureEngineer.generate_features` skips tickers with
fewer than 60 rows of OHLCV in the query window (`ticker_insufficient_data`)
and drops rows with nulls in `close`, `volume`, `rsi_14`, or `macd`.

## The Hamilton DAG

Feature computation is a layered Hamilton DAG assembled in
`FeaturePipeline` (`src/equity_lake/features/pipeline.py`) from four modules
under `src/equity_lake/features/dag/`:

| Module | Layer | Responsibility |
|---|---|---|
| `raw_01.py` | bronze | Extract OHLCV columns from the input `price_data` frame |
| `clean_02.py` | silver | Basic transforms (returns) and Pydantic boundary validation of the cleaned OHLCV frame |
| `features_03.py` | gold | Technical indicators — momentum, volatility, volume, calendar |
| `enrichments_04.py` | gold | External-data joins (news, social, enriched sentiment, analyst, SEC, macro) |

Execution is two-phase:

1. `compute_technical` runs **per ticker** and produces
   `FeaturePipeline.TECHNICAL_FEATURES` — RSI-14, MACD/signal/histogram,
   Bollinger bands, ATR-14, rates of change, multi-horizon returns, OBV, volume
   ratios, calendar features, and `volatility_20`. With
   `include_target=True` it also appends `TARGET_FEATURES`
   (`next_day_return`) — the labeling column for supervised runs.
2. `compute_enriched` runs **once for the whole batch** and applies the
   enabled external-data joins (see below). The chain terminates at the
   `enriched_features` node, a stable public name regardless of which
   enrichment nodes are enabled upstream.

Every output frame is stamped with `feature_schema_version`. The current value
is `FEATURE_SCHEMA_VERSION = 3`. A version bump means the column set or
computation of published features changed: downstream consumers can detect and
handle mixed-version history, and — per the change matrix in `AGENTS.md` — the
change requires regenerating the catalog (`uv run equity catalog-generate`),
since the gold-layer `technical_features` / `enriched_features` catalog
entries describe exactly these columns.

## Enrichment toggles

Enrichments are opt-in on `FeatureEngineer.generate_features` (and on
`run_feature_job`) with these exact parameter names:

| Toggle | Default | Joins from | Adds |
|---|---|---|---|
| `include_sentiment` | `False` | `02_silver/news_sentiment` | Finnhub news sentiment aggregates — daily mean/counts (`avg_daily_sentiment`, `news_count`, positive/negative/neutral counts) plus 3/7/30-day EWMA |
| `include_social_sentiment` | `False` | `02_silver/social_sentiment` | Social mention counts and scores (Reddit/Twitter splits), 5-day momentum, and 3/7/30-day EWMA |
| `include_enriched_sentiment` | `False` | `02_silver/processed_articles` | LLM-enriched article-ticker sentiment — `enriched_sentiment_mean`, confidence/relevance means, `bullish_ratio`, `breaking_news_flag`, EWMA |
| `include_analyst_ratings` | `False` | `02_silver/analyst_ratings` | Analyst consensus score, coverage count, mean price target, implied upside vs close, 7-day consensus EWMA |
| `include_sec_features` | `False` | `02_silver/sec_extractions` | Point-in-time (backward ASOF) SEC filing extractions — risk sentiment, management tone, guidance direction, new-vs-repeated risk flags |

`include_macro` is the one **opt-out** enrichment (default `True`): it pivots
`01_bronze/macro` (VIX, treasury yields, DXY, …) wide and forward-fills it
onto every feature row. Cross-modal interaction features
(`sentiment_x_log_volume`, `news_social_sentiment_gap`, …) are derived
automatically whenever the underlying enrichment columns exist.

Each enrichment is a separately tagged DAG node, so the catalog records real
per-enrichment lineage. If a required silver table does not exist, the merge
degrades gracefully — the enrichment's columns are added with neutral/empty
defaults rather than failing the stage.

**Prerequisite:** enrichment data must already exist in the lake. The feature
stage only joins silver/bronze tables; it never fetches. Run the ingestion
commands that populate those tables first (see
[Ingesting News and Structured Content](ingestion.md#ingesting-news-and-structured-content)).

When run through `equity pipeline`, the toggles are selected automatically:
`include_enriched_sentiment` and `include_sec_features` are enabled only if the
corresponding bronze→silver processing stages succeeded in the same run, and
`include_analyst_ratings` is enabled when `us_analyst_ratings` is among the
run's markets. (`include_sentiment` and `include_social_sentiment` are not
enabled by the pipeline driver; `include_macro` rides on its default.)

## Running features and recovering history

The feature stage runs as part of the pipeline; there is no standalone feature
command:

```bash
dotenvx run -- uv run equity pipeline --markets us
dotenvx run -- uv run equity pipeline --skip-ingestion   # features + ML only
dotenvx run -- uv run equity pipeline --skip-ingestion --skip-ml   # features only
dotenvx run -- uv run equity pipeline --dry-run --verbose
```

When `NoFeatureHistoryError` fires, the stage fails with a pointer to the
recovery flag — it never auto-backfills:

```text
Feature history is missing. Re-run with --allow-history-backfill
to authorize the 120-day recovery.
```

Authorizing recovery is deliberately explicit, per the AGENTS.md guardrails —
a backfill is a network-touching, multi-day ingestion, so it must be scoped:

```bash
dotenvx run -- uv run equity pipeline --markets us \
    --tickers AAPL,MSFT,NVDA --allow-history-backfill
```

Semantics of `--allow-history-backfill`:

- The pipeline backfills price history for the required price markets in scope,
  over the same `HISTORY_BACKFILL_WINDOW_DAYS = 120` calendar days, then retries
  the feature job once.
- Scope it with `--markets` and `--tickers`; do not run it unscoped against the
  full universe.
- Under `--dry-run` nothing is persisted — the backfill is planned and logged
  (`feature_history_backfill_authorized`) but not executed.
- Without the flag the feature stage fails and the ML stage is skipped
  (`ml_skipped_due_to_feature_failure`).

## What consumes features

- **`FeatureLoader`** (`src/equity_lake/ml/feature_loader.py`) exposes the gold
  features table as a DuckDB view and serves `load_features(ticker,
  start_date, end_date)` for one ticker's history. It is the read path for
  `ml/forecasting.py` — training (`equity forecast --mode train`,
  `equity ml train`) and inference — and for the ML-rigor harnesses
  ([ML Rigor](20260810-ml-rigor.md)).
- **Catalog lineage** — the gold-layer entries `technical_features` and
  `enriched_features` in `data/catalog.jsonl` describe the columns written
  here; they are generated from the DAG topology with
  `uv run equity catalog-generate` and must be regenerated whenever the
  feature schema or DAG changes.
- **The pipeline's ML stage** — `equity pipeline` runs inference for exactly
  the tickers the feature stage produced rows for.

## Related

- [Pipeline](pipeline.md) — the three-stage daily workflow and flags
- [ML Rigor](20260810-ml-rigor.md) — feature-history prerequisite for `ml compare` / `ml ablate`
- [Ingestion](ingestion.md) — populating the bronze/silver tables features join against
- [CLI Reference](20260406-cli-reference.md)
