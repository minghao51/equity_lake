# Pipeline User Guide

This guide covers the current operational pipeline exposed by the unified `equity` CLI.

## Overview

The daily workflow has three core stages:

1. `equity ingest` fetches market data and writes date-partitioned Delta tables with Parquet data files
2. the feature stage builds derived datasets under `data/lake/03_gold/features/`
3. the ML stage runs inference for the requested tickers

The main command is:

```bash
dotenvx run -- uv run equity pipeline
```

This is an operational command that may fetch live providers and write lake
state. It is shown for reference only; this documentation/review work does not
authorize source fetches, production writes, paid processing, repair, backfill,
retraining, or deployment.

## Core Commands

Run daily ingestion:

```bash
dotenvx run -- uv run equity ingest
dotenvx run -- uv run equity ingest --date 2026-06-06
dotenvx run -- uv run equity ingest --markets us_equity,cn_ashare
dotenvx run -- uv run equity ingest --tickers AAPL,MSFT,NVDA --markets us_equity
dotenvx run -- uv run equity ingest --dry-run --verbose
```

Run the full pipeline:

```bash
dotenvx run -- uv run equity pipeline
dotenvx run -- uv run equity pipeline --dry-run --verbose
dotenvx run -- uv run equity pipeline --date 2026-06-06
dotenvx run -- uv run equity pipeline --markets us_equity
dotenvx run -- uv run equity pipeline --tickers AAPL,MSFT,NVDA
dotenvx run -- uv run equity pipeline --skip-ingestion
dotenvx run -- uv run equity pipeline --skip-ingestion --skip-features
dotenvx run -- uv run equity pipeline --skip-ml
dotenvx run -- uv run equity pipeline --save-results
```

Monitor pipeline health:

```bash
dotenvx run -- uv run equity monitor
dotenvx run -- uv run equity monitor --verbose
dotenvx run -- uv run equity monitor --output-json logs/health-report.json
```

Query the lake with DuckDB:

```bash
dotenvx run -- uv run equity query
dotenvx run -- uv run equity query --query latest_summary
dotenvx run -- uv run equity query --query top_volume
dotenvx run -- uv run equity query --db data/equity_data.duckdb --verbose
```

The Compose `daily` service is a one-shot pipeline invocation. Run it from an
external scheduler (for example `docker compose --profile daily run --rm daily`);
Compose does not implement a cron schedule or automatically restart completed
pipeline runs. The query service runs all named queries when `--query` is
omitted. Sync expects a bucket root whose children follow the numbered
medallion layout, not a single-market prefix.

## Supported Pipeline Flags

`equity pipeline` currently supports:

- `--date`
- `--days-back`
- `--markets`
- `--tickers`
- `--skip-ingestion`
- `--skip-features`
- `--skip-ml`
- `--dry-run`
- `--allow-history-backfill`
- `--verbose`
- `--save-results` — writes `logs/pipeline_results_<date>.json` (where the
  dashboard exporter looks for it); skipped under `--dry-run`

`equity ingest` currently supports:

- `--date`
- `--markets`
- `--tickers`
- `--dry-run`
- `--verbose`

## Scope, Coverage, and Result Truthfulness

`equity pipeline` resolves the requested ticker scope explicitly:

- With no `--tickers`, every configured active ticker in the requested price
  markets is processed — there is no hidden limit. `hk_sg_equity` resolves the
  active union of the separate `hk` and `sg` configuration sections, preserving
  order and removing duplicates; it is scheduled when either exchange is open.
- `--tickers` is used exactly as given, deduplicated deterministically. An
  explicitly empty value (for example `--tickers ""`) is rejected with a
  non-zero exit; it never falls back to the configured universe.
- A request whose markets contain no price market (for example `--markets macro`)
  planned-skips features and ML with `reason: enrichment_only_no_price_scope`
  instead of inventing tickers.

The result JSON records `scope` (resolved tickers and markets), the ingestion
status per market, and feature `coverage`: requested and produced tickers plus
any missing ticker with a reason (`no_price_rows`, `insufficient_history`,
`missing_output_bar`, `invalid_output`).

## Feature availability and point-in-time adjustment

Gold features use `FEATURE_SCHEMA_VERSION = 7`,
`availability_schema_version = 4`, action-coverage proof schema version 2, and
canonical `(market, MIC, symbol)` identity. Split-only adjustment is read-time,
leaves raw Bronze prices unadjusted, excludes cash dividends, and requires
source-scoped proof whose own identity matches the feature row. The proof must
include a verified source publication time mapped to the first observable
venue session and source-issued expiry through the decision-session close. The
native yfinance corporate-action path currently supplies no such proof, so
adjusted outputs remain unavailable until verified proof is supplied; there is
no raw fallback or guessed TTL. Missing or unverified optional enrichment
values remain null with explicit coverage/availability flags, not neutral
values. Legacy features/models without current identity, availability, and
action-proof contracts fail closed; they are not automatically rewritten or
retrained. See [ML availability and provenance](20261016-ml-availability-and-provenance.md).

Outcome semantics:

- `written` — fetched and persisted; `skipped_existing` — a valid partition
  already existed.
- `not_scheduled` — the market's exchange is closed on the resolved plan date.
  This is a planned skip, not a failure: it does not block markets that are
  open, never counts as `partial`, and the run still exits zero when the
  scheduled required work succeeds.
- `no_data` — the source returned zero rows on a scheduled session (not an
  error by itself, and not a verified partition; a required price market with
  no data still blocks features/ML).
- `dry_run` — a direct `equity ingest --dry-run` simulated the write; it never
  reports `written`.
- `failed` — the fetch, schema check, or write failed; the per-market cause is
  kept in the result and does not erase other markets' outcomes.

Session planning: each requested price market is classified against the
resolved plan date using its exchange calendar (`not_scheduled` when closed).
Features and ML are planned only for the scheduled scope, and an explicit closed
date is never replaced with an earlier session. `hk_sg_equity` uses union
semantics — it is scheduled when either Hong Kong or Singapore is open — so an
open instrument is never skipped on a single-exchange session; per-instrument
coverage is reported by the feature stage.

Optional enrichment failures stay visible: they are listed in
`optional_failures`, set `partial: true`, and do not block core features or the
process exit. A requested ticker missing from the feature output fails the
feature stage and exits non-zero while still reporting everything that was
produced. Per-ticker ML results carry `persisted` so inference success is not
mistaken for durability.

### Preloading Gold features

One `equity pipeline` run computes one output session, and a label only becomes
computable on the next **observed** session (ADR-0014). A dense run of Gold
sessions therefore has to be written oldest → newest, one session per run, and
it must be bounded — roughly 8 seconds per session for five tickers.

```bash
make preload              # 60 sessions, five default US tickers
make preload DAYS=120     # explicit bound; the helper refuses DAYS > 500
```

The target calls `uv run python -m equity_lake.devtools.preload`, which drives
the same `run_feature_job` stage the pipeline uses (no hand-written rows). The
session list is the last N sessions actually stored in Bronze — the same
"observed session" the label lifecycle uses, so a synthetic lake that holds a
bar on a calendar holiday still has an unbroken finalization chain. Sessions
with nothing computable are skipped and reported, each session finalizes its
predecessor's `next_day_return` / `label_available_date`, and the newest session
stays pending because no next observed bar exists yet. Re-running is idempotent
(the Gold upsert keys on `(market, mic, symbol, date)`).

Preloading features does **not** write predictions. Training and inference stay
on the real path, which fails closed when identity, availability, or
action-coverage proofs are insufficient instead of fabricating rows:

```bash
uv run equity ml train --ticker AAPL --start 2026-08-06 --end 2026-10-27
uv run equity pipeline --skip-ingestion --date 2026-10-28 \
  --markets us --tickers AAPL,AMZN,GOOGL,MSFT,NVDA   # ML stage persists predictions
```

`forecast --mode predict` prints a prediction but does not persist one; the
Platinum write happens in the pipeline's ML stage (`run_prediction_job`), which
persists the row-bound forecast proof defined by
[ADR-0021](../decisions/0021-row-bound-forecast-provenance.md) (E1 artifact
identity, E2 temporal validity, E3 contract compatibility, E4 decision
semantics). Because that proof travels with the row, `/predictions` can report
`api_usability_status: current_usable` for a fresh `v1_direction` row instead of
hardcoding it unusable. `v2_meta_label` rows carry the same E1–E3 evidence but
stay `api_prediction_semantics_status: unknown` by design until a separate record
defines meta-label semantics, so they remain `not_current_usable`. With a short
history the honest outcome is still a trained model with no validation folds
(`folds=0`). Do not hand-write prediction rows to make those fields look better.

### Historical predictions: bounded as-of refits

One pipeline run writes predictions for one date, from the models available as
of that date. `PriceForecaster` refuses a model whose `refit_boundary` is after
the inference date, so a window of *historical* prediction dates needs its own
as-of model per refit boundary. `make refit-preload` drives that bounded loop
oldest → newest: train as of the boundary, then run the pipeline ML stage for
the sessions the boundary owns.

```bash
make refit-preload                     # 60 sessions, at most 12 boundaries
make refit-preload DAYS=40 REFITS=6    # 40 sessions, ~one boundary per week
uv run python -m equity_lake.devtools.refit_preload --days 60 --refits 12
```

`DAYS` and `REFITS` are not independent knobs: each boundary's model can only
serve a bounded calendar span, so a long window needs proportionally more
boundaries (see the plan-time refusal below).

The cost is one training per boundary (≈15 s measured for the five default US
tickers) plus one inference call per session, so the run is bounded: `--refits`
accepts 1–12 (default 12) and `--days` keeps the shared 500-session bound from
`make preload`. Sessions come from the stored Bronze bars through the same
`observed_sessions` helper, not from a calendar.

Boundaries are spread evenly over the window: with `m = min(refits, sessions)`
boundaries, boundary `i` is session index `floor(i * (n - 1) / (m - 1))`, so the
window's first and last session are always included. **With `--refits 1` the
single boundary is the window's first session**, so that one model owns every
session of the window (a last-session boundary would leave the rest
unpredicted). Boundary `i` owns every session from its date up to the next
boundary, which partitions the window without overlap. Flags:
`--days` (60), `--refits` (12), `--tickers`, `--end`, `--lookback-days` (365
calendar days before each boundary), `--train-window` (252), `--test-window`
(21), `--embargo-window` (1), `--max-model-age-days` (7), and `--model-dir`. The
validation windows are passed straight to `train_model` — they are never
auto-shrunk — and the applied values appear in the report.

#### The model-age ceiling, and why a plan can be refused

`predict()` enforces an inclusive calendar-day age bound against the artifact's
`refit_boundary` (`ml.forecasting.DEFAULT_MAX_MODEL_AGE_DAYS`, 7 days by
default). That bound is what every persisted prediction row records as
`model_max_age_days` next to `model_age_days`, and the read API's freshness
verdict (`api_usability_status`) refuses a row whose age exceeds it — so it is
persisted provenance, not a tuning knob. `--max-model-age-days` accepts
`0..DEFAULT_MAX_MODEL_AGE_DAYS` and is passed to `train_model`; it can only
**narrow** coverage, because a wider value would not be the bound the inference
path verifies and records. Values outside the range are refused up front.

Consequence: one boundary can only serve sessions whose calendar distance from
it is within the bound — about a week of sessions at the default. The helper
checks every planned window's span **before training or writing anything** and
refuses a plan that cannot be covered, naming the boundary, its span, and what
it would need:

```text
error: boundary 2026-03-22..2026-04-29 spans 38 days, above --max-model-age-days 7; use at least 6 refits
for this window or raise --max-model-age-days (capped at 7 by the forecaster's trusted age policy);
boundary 2026-03-22 owns 39 of 40 planned sessions
error: boundary 2026-01-01..2026-01-23 spans 22 days, above --max-model-age-days 7; no evenly spread plan of at most
12 refits covers this window (at this spread one boundary can own at most 7 calendar days, and the
even-spread placement is the only one this run makes), so cover the window with several smaller --days
preloads or raise --max-model-age-days (capped at 7 by the forecaster's trusted age policy)
```

The refit count in that message is the smallest one whose *evenly spread*
placement fits the bound, so it is directly usable. `--days 40 --refits 2` is
refused because its 2 boundaries sit on the window's first and last session,
leaving a 39-session window; 6 refits cover a weekday-dense window of that shape
(holiday-heavy calendars need slightly more, and the refusal message always
names the count that fits this window). `--days 120 --refits 6` cannot be
covered by any evenly spread plan inside the 12-boundary run bound, so it has to
be split into several smaller `--days` runs (or the window shortened). Note this
is a limit of the even-spread placement, not of the age bound: a gap-aware plan
with more boundaries could cover a window whose calendar span is larger. Either way nothing is
trained or written before the refusal.

Persistence goes only through `run_prediction_job` (the pipeline ML stage), so
backfilled rows carry the same [ADR-0021](../decisions/0021-row-bound-forecast-provenance.md)
forecast proof as live ones and every row satisfies `model_trained_at <= date`.
The helper refuses a lake that differs from the write root: session reads and
prediction writes must both observe `storage.delta.LAKE_DIR`, never a read-only
redirect. Re-running the same command is idempotent: a fresh artifact for the
boundary is reused (its validation verdict is read back from the artifact's
`*.training_summary.json` sidecar), and the prediction upsert keys on
`(market, mic, symbol, date)`, so row counts do not change.

Validation honesty follows R3: after training a boundary the helper reads the
training summary, and a boundary whose `validation_status` is not `verified` is
**skipped** — its sessions get no predictions — with the reason recorded as
`validation: unavailable (<reason>)`. If every boundary is skipped the run exits
`2` and prints those reasons; it never produces predictions from an unvalidated
model.

Accounting is per session, not per boundary: a session counts as predicted only
when `run_prediction_job` actually persisted its rows. Sessions that did not
(for example `model_expired`, when an older artifact is reused for a date beyond
the bound) appear in `missing` with their product-readable reason. Exit codes:
`0` only on full coverage, `1` when any requested session is missing, `2` for a
refused plan or an all-skipped run. `make refit-preload` aborts on either
non-zero code, and its final echo points at the report instead of claiming
completeness.

A run prints a short human summary — with coverage as `covered/total` — followed
by one JSON object. The shape below is a partial run: every boundary validated
and predicted, but some sessions still persisted nothing (a runtime refusal such
as `model_expired` from a reused artifact, which the plan-time check cannot see):

```text
{
  "market": "us_equity", "days_requested": 40,
  "sessions_requested": 40, "sessions_covered": 6, "sessions_missing": 34,
  "coverage": {"covered": 6, "total": 40, "complete": false},
  "window": {"start": "…", "end": "…"},
  "tickers": ["…"], "model_mode": "v1_direction",
  "max_model_age_days": 7, "lookback_days": 365,
  "validation": {"mode": "purged_walk_forward", "train_window": 21, "test_window": 5, "embargo_window": 1},
  "refits_requested": 2, "refits_applied": 2, "refits_skipped": 0,
  "sessions_predicted": 6, "predictions_persisted": 6,
  "boundaries": [
    {"date": "…", "tickers_trained": ["…"], "validation_status": "verified", "reason": null,
     "sessions": ["…"], "sessions_covered": 6, "predictions_persisted": 6,
     "missing": [{"session": "…", "reason": "model_expired"}]}
  ]
}
```

### Synthetic rows and returning to an empty lake

A lake that was seeded or preloaded may hold synthetic rows alongside ingested
data. Only two markers make that visible, and `uv run equity doctor` reports them
under `synthetic_provenance` (`synthetic` / `empty` / `unknown`):

| Table | Marker |
|---|---|
| `02_silver/corporate_action_coverage` | `source = synthetic_demo_seed` |
| `03_gold/features` | `action_coverage_source = synthetic_demo_seed` |

Bronze price rows are **not** distinguishable: `identity_provenance =
current_ticker_config` is exactly the label a real config-resolved ingestion
writes, so `doctor` never reports `real` — an unmarked but populated lake is
`unknown`. Nothing writes a new provenance label for this.

The observation also walks **every** Delta dataset under the reset targets
(`01_bronze`, `02_silver`, `03_gold`, `04_platinum/predictions`) — any directory
containing a `_delta_log` is observed, not a hand-enumerated list — including the
tables that carry no marker (`01_bronze/market_data/<market>`,
`02_silver/news_sentiment`, `01_bronze/macro`, `04_platinum/predictions`, …), each
reported as its own `tables` entry. `empty` therefore means *no rows in any Delta
dataset under those four reset targets*; `data/models` is a derived-artifact
directory with no rows and takes no part in that judgement. A lake that only has
ingested Bronze rows — the state a real `equity ingest` run leaves before coverage
or features exist — is therefore `unknown`, not `empty`, and so is a lake whose
only rows live in an unenumerated table such as `02_silver/news_sentiment`.

Before real ingestion, reset the synthetic artifacts with the supported target:

```bash
make reset-lake DRY=1   # lists exactly the five paths below; writes nothing
make reset-lake         # destructive: confirmation prompt, refuses unless synthetic/empty

# Equivalent non-interactive command
uv run python -m equity_lake.devtools.reset_lake --yes
```

It removes exactly `data/lake/01_bronze`, `data/lake/02_silver`,
`data/lake/03_gold`, `data/lake/04_platinum/predictions` (the subpath only — the
rest of `04_platinum` survives), and `data/models`. The guard refuses (exit
non-zero, nothing deleted) unless the doctor observation reports `synthetic` or
`empty`, and it never follows a symlinked target. `empty` is the absence of rows in
any Delta dataset under those reset targets (models hold no rows, so they are not
part of the judgement); an empty lake is a no-op success. `DRY` accepts only `0`,
`1`, or empty — anything else (`DRY=2`, `DRY=yes`) is an error rather than a silent
destructive run.

## Configuration

Primary config files:

- `config/settings.yaml`: application defaults
- `config/tickers.yaml`: ticker metadata by market
- `data/watchlists/watchlist.yaml`: managed private canonical watchlist used by default signal/tracking commands
- `config/watchlist.yaml`: legacy ticker-only watchlist; only read when explicitly supplied with a market (and MIC when ambiguous)
- `config/signals.yaml`: signal thresholds and formatting
- `.env.example`: local environment template

Configuration precedence is:

1. command-line options
2. `EQUITY_` environment variables
3. `.env`
4. `config/settings.yaml`

Settings are grouped (`project`, `ingestion`, `schedule`, `dashboard`, `monitoring`) and
environment overrides use the `EQUITY_` prefix with `__` as the nested delimiter, so a
setting like `dashboard.output_dir` maps to `EQUITY_DASHBOARD__OUTPUT_DIR`. Because
`Settings` loads with `extra="forbid"`, a misspelled or flat unknown `EQUITY_*` variable
fails loudly at load time instead of being ignored. Common overrides include:

- `EQUITY_PROJECT__ENVIRONMENT` (development | production | testing)
- `EQUITY_INGESTION__DEFAULT_MARKETS`
- `EQUITY_INGESTION__RETRY_ATTEMPTS`
- `EQUITY_SCHEDULE__CRON`
- `EQUITY_SCHEDULE__TIMEZONE`
- `EQUITY_DASHBOARD__OUTPUT_DIR`
- `EQUITY_DASHBOARD__TITLE`
- `EQUITY_MONITORING__MAX_AGE_DAYS`
- `EQUITY_MONITORING__NULL_THRESHOLD_PCT`

## Data Layout

Important runtime directories:

- `data/lake/01_bronze/market_data/us_equity/`
- `data/lake/01_bronze/market_data/cn_ashare/`
- `data/lake/01_bronze/market_data/hk_sg_equity/`
- `data/lake/01_bronze/market_data/jpx_equity/`
- `data/lake/01_bronze/market_data/krx_equity/`
- `data/lake/03_gold/features/`
- `data/models/`
- `logs/`

Market data is stored as:

```text
data/lake/<layer>/<dataset>/_delta_log/
data/lake/<layer>/<dataset>/date=YYYY-MM-DD/*.parquet
```

## Typical Operating Flows

Bootstrap and first run:

```bash
dotenvx run -- uv run equity sync --bucket s3://your-bucket
dotenvx run -- uv run equity pipeline --dry-run --verbose
dotenvx run -- uv run equity pipeline
```

The bucket is a root URL whose remote tree mirrors the local numbered medallion layout
without a `data/lake/` prefix — each market is pulled from
`<bucket>/01_bronze/market_data/<market_dir>`.

Daily local run:

```bash
dotenvx run -- uv run equity ingest
dotenvx run -- uv run equity monitor
```

Daily full run:

```bash
dotenvx run -- uv run equity pipeline
dotenvx run -- uv run equity monitor --output-json site/health-report.json
dotenvx run -- uv run equity dashboard build --output-dir site
```

## Related Workflows

Signals and private tracking:

```bash
uv run equity signal scan
uv run equity signal scan --watchlist config/watchlist.yaml --market us_equity
uv run equity tracking readiness
uv run equity tracking scan --dry-run
```

Default signal scans use the canonical private watchlist. A legacy
ticker-only watchlist requires `--market`; pass `--mic` when its active source
mapping is ambiguous. Tracking and scanning use stored data only and do not
fetch or backfill on watchlist changes. `equity tracking scan --dry-run` does
not read, infer, persist, or deliver.

Backtesting:

```bash
dotenvx run -- uv run equity backtest --help
```

Dashboard:

```bash
dotenvx run -- uv run equity dashboard build --output-dir site
dotenvx run -- uv run equity dashboard serve --port 8501
```

## Scheduling

Example weekday cron jobs:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity ingest >> logs/cron-daily.log 2>&1
0 20 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity monitor >> logs/cron-monitor.log 2>&1
```

Or, if you prefer a single scheduled run:

```bash
0 19 * * 1-5 cd /path/to/equity-lake && dotenvx run -- uv run equity pipeline >> logs/cron-pipeline.log 2>&1
```

## Troubleshooting

Check the live CLI:

```bash
uv run equity --help
uv run equity ingest --help
uv run equity pipeline --help
uv run equity query --help
uv run equity monitor --help
```

Common checks:

- Verify `config/settings.yaml` and `.env` agree on paths and credentials
- Run with `--dry-run --verbose` before changing a scheduled workflow
- Export a health report with `equity monitor --output-json ...` before building the static dashboard

## Notes

- The unified `equity` command is the supported CLI surface.
- China ingestion currently defaults to the shipped `akshare` path.
- The pipeline is local-first after bootstrap; DuckDB reads directly from Parquet.
