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
| `raw_01.py` | bronze | Extract OHLCV columns from the input `price_data` frame; normalize the EOD `date` key to `pl.Date` (malformed keys raise) |
| `clean_02.py` | silver | Basic transforms (returns) and the connected `validated_ohlcv` boundary — full-frame vectorized validation of the cleaned OHLCV frame; Pydantic row sampling is diagnostic-only |
| `features_03.py` | gold | Technical indicators — momentum, volatility, volume, calendar — all consuming the same `validated_ohlcv` frame, plus the `validated_features` emitted-output boundary |
| `enrichments_04.py` | gold | External-data joins (news, social, enriched sentiment, analyst, SEC, macro) on `pl.Date` keys |

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
is `FEATURE_SCHEMA_VERSION = 7`. The shared point-in-time contract uses
`availability_schema_version = 4`; source-scoped corporate-action coverage
proofs use schema version 2. Gold rows persist that proof in
`action_coverage_market`, `action_coverage_mic`, `action_coverage_symbol`,
`action_coverage_source`, `action_coverage_start`, `action_coverage_end`,
`action_source_available_at`, `action_source_valid_until`,
`action_coverage_status`, and `action_coverage_schema_version`. Its identity
must match the feature row's own `(market, MIC, symbol)`. The timezone-aware
source clock maps to the first observable venue session, and the source-issued
expiry must cover the decision-session close. This verified proof is mandatory
for adjusted reads; ingestion time and guessed expiry are not substitutes, and
there is no raw-price fallback.

A version bump means the column set or computation of published features
changed: downstream consumers can detect and handle mixed-version history, and
— per the change matrix in `AGENTS.md` — the change requires regenerating the
catalog (`uv run equity catalog-generate`), since the gold-layer
`technical_features` / `enriched_features` catalog entries describe these
columns.

### Data contracts (W01)

- **EOD keys are `pl.Date`.** Technical output, every enrichment join, and the
  persisted gold layer all use `pl.Date` keys. `compute_enriched` normalizes
  its input at the boundary, so Datetime-keyed feature frames still join
  correctly; malformed string keys raise rather than producing nulls. (Only
  the `compute_enriched` entry normalizes — callers driving the DAG nodes
  directly must pass Date-keyed frames.)
- **Invalid required price input fails the compute.** `validated_ohlcv`
  enforces non-null ticker/date, strictly positive OHLC, non-negative volume,
  and OHLC price relationships on the full frame — violations raise
  `OHLCVValidationError` and fail the per-ticker computation visibly instead
  of silently dropping rows. Rows are sorted by `(ticker, date)` and duplicate
  identities keep the first input occurrence, deterministically, before any
  rolling/shift calculation.
- **Emitted output is enforced.** The `validated_features` boundary (and
  `compute_technical` itself) reject null/duplicate identity, ±inf/NaN in any
  float feature, and nulls in row-defined features beyond each ticker's first
  row. Warm-up nulls (RSI, Bollinger, ROC windows) and the unavailable final
  `next_day_return` are legitimate and never rejected.
- **Undefined ratios are null, never infinity.** Zero baselines in
  `volume_roc_5` and social `social_momentum` produce null — an honest
  "undefined" rather than an invented finite value or `inf`.

### Label availability (ADR-0014)

With `compute_target=True`, `run_feature_job` also writes
`label_available_date`: the next observed session on which `next_day_return`
became computable. A row's target is null (pending) until that next session's
bar exists — a missing next-session bar leaves the label pending and is
reported in the feature stage's `labels` block. On each daily run the preceding
observed session's label is finalized from the new session's bar; the newest
row stays pending. Finalization is a matched-only update of the label columns:
it never inserts a missing key, evolves the schema, or rewrites technical or
enrichment feature values. `compute_target=False` neither stamps availability
nor finalizes labels, so historical completion is an explicit label-only path
(see [Pipeline Contracts](../developer/architecture/pipeline-contracts.md)).

## Enrichment toggles

Enrichments are opt-in on `FeatureEngineer.generate_features` (and on
`run_feature_job`) with these exact parameter names:

| Toggle | Default | Joins from | Adds |
|---|---|---|---|
| `include_sentiment` | `False` | `02_silver/news_sentiment` | News context covers seven calendar days and is available only with at least three distinct scored article URLs and trusted source clocks; `news_coverage_7d` and `news_available` expose coverage. |
| `include_social_sentiment` | `False` | `02_silver/social_sentiment` | Separate seven-calendar-day social context (not news articles), with source-specific availability and coverage flags. |
| `include_enriched_sentiment` | `False` | `02_silver/processed_articles` | Legacy LLM article features remain unavailable until canonical identity and trusted publication clocks are present; null values carry `enriched_available = 0`. |
| `include_analyst_ratings` | `False` | `02_silver/analyst_ratings` | Analyst fields remain unavailable until canonical identity and trusted publication vintages are present; null values carry `analyst_available = 0`. |
| `include_sec_features` | `False` | `02_silver/sec_extractions` | Identity-scoped SEC state may carry for at most 95 calendar days when its trusted source-availability clock and filing date are known. |

`include_macro` is the one **opt-out** enrichment (default `True`). The current
macro path does not forward-fill observations: without a verified historical
release/vintage clock its values remain null with `macro_available = 0`, and
are excluded from strict point-in-time predictors. An unverified value must not
be presented as known merely because it is used as market context.
Cross-modal interaction features are derived only when their underlying
observations are available; missing inputs remain null.

Every source timestamp must be timezone-aware and is mapped to the first
observable session for the instrument's MIC (including off-session and
post-close publications). Optional absence or unverified provenance is
represented as null plus an availability/coverage flag, never a neutral zero.
Thus an absent or unusable Silver table does not fail the feature stage, but it
also does not manufacture measured values. The native yfinance action path
currently supplies neither a verified publication clock nor a complete,
source-issued coverage/expiry proof, so adjusted output remains unavailable.

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
dotenvx run -- uv run equity pipeline --markets us_equity \
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
  ([ML Rigor](20260810-ml-rigor.md)). Pass
  `require_available_by=<cutoff>` for strict as-of reads: labels finalized after
  the cutoff, and legacy rows with unknown availability, are excluded.
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
