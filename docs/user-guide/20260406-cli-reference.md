# CLI Reference

Every command lives on the unified `equity` CLI. This page maps the full
surface by workflow area; the live help (`uv run equity --help` and
`uv run equity <command> --help`) is always authoritative.

Key-dependent commands — `sync`, `macro`, `news`, `sentiment`, `transcripts`,
`ratings`, `sec`, `financials`, and the ingestion/pipeline commands that read
`.env` — must run through `dotenvx run --` (see
[API keys](../20260406-api-keys.md)). Local-only commands run fine with plain
`uv run`.

Unless noted otherwise, every command accepts `--verbose` / `-v` for debug
logging, and mutating commands accept `--dry-run` to simulate without writes.

## Command overview

| Area | Commands |
|---|---|
| Ingestion & data | `ingest`, `backfill`, `auto-backfill`, `label-repair`, `sync`, `macro`, `pipeline` |
| Unstructured & news | `news`, `sentiment`, `transcripts`, `ratings`, `sec`, `financials` |
| Intelligence & ML | `forecast`, `ml compare`, `ml ablate`, `ml train` |
| Analysis | `query`, `signal scan`, `signal history`, `tracking`, `backtest`, `arena run`, `report backtest` |
| Private portfolio | `portfolio import`, `portfolio positions`, `portfolio summary`, `portfolio reconcile` |
| Maintenance & ops | `monitor`, `delta-vacuum`, `delta-compact`, `delta-migrate`, `catalog-generate`, `dashboard build`, `dashboard serve` |
| Config & validation | `config show`, `config get`, `config validate`, `config export`, `validate check`, `validate profile`, `validate drift` |
| Utilities | `doctor`, `bootstrap sample`, `demo seed`, `api serve` |

`doctor --watchlist … --ticker-config … --as-of YYYY-MM-DD` reports read-only
observations for canonical instruments across the configured markets; it does
not write, infer, or backfill. The report also carries a `storage_schema`
observation per watched price table plus `03_gold/features` and
`04_platinum/predictions`: it reads Parquet footers only (no data pages) and
reports string columns stored as Arrow `string_view` (`drifted`), which merge
rewrites introduce and which readers filter in Polars instead of pushing down. A
`synthetic_provenance` observation answers "is this lake synthetic?" from markers
that already exist: `02_silver/corporate_action_coverage.source` and
`03_gold/features.action_coverage_source` equal to `synthetic_demo_seed`, with
per-table row/mark counts and the earliest/latest affected date. It also counts rows in
every other Delta dataset under the reset targets (`01_bronze`, `02_silver`,
`03_gold`, `04_platinum/predictions` — any directory containing a `_delta_log`),
each reported as its own `tables` entry (capped at 50, with `tables_truncated`),
so `empty` means no rows in any Delta dataset the reset would delete; `data/models`
holds derived artifacts, not rows, and is not part of that judgement. Its `state` is
`synthetic` (markers present, which wins even when unmarked datasets coexist), `empty`,
or `unknown` (data without a marker — including a Bronze-only lake left mid-pipeline,
or rows only in an unenumerated table — or a table that cannot be read). It never
reports `real`, because
Bronze rows carry `identity_provenance = current_ticker_config`, the same label a
real config-resolved ingestion writes. `make reset-lake` uses that state as its
guard. Observed dates are not a configured freshness
policy. `signal history --date YYYY-MM-DD --limit N` reads existing Delta or
legacy Parquet history without migration. Both history options are required.
See the [private tracking guide](20261016-private-tracking-preview.md) for the
`equity tracking` YAML CRUD, readiness, local event, and opt-in delivery
workflow. These observation commands do not send notifications.

## Ingestion & data

### `equity ingest`

Ingest daily equity market data. Prints a per-market outcome summary and
exits non-zero when a required price market (`us`, `cn`, `hk_sg`, `jpx`,
`krx`) fails or is missing from the results — optional enrichments only
degrade with a warning (mirrors `equity pipeline`).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date `YYYY-MM-DD` |
| `--markets`, `-m` | str | from settings | Comma-separated markets |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--dry-run` | flag | off | Simulate without writes |
| `--lake-dir` | str | canonical `data/lake` | Explicit lake root inside `data/` (e.g. `data/lake_real`); never mixed, never defaulted. A non-canonical root accepts `us_equity` only, so name it in `--markets` — the configured defaults contain enrichment datasets, which are refused (ADR-0030) |

Identity comes from the exact current ticker config (`identity_provenance=current_ticker_config`)
because the vendor supplies no MIC/exchange; a ticker absent from `config/tickers.yaml` still
fails closed rather than guessing a venue. Against a real root, the write also reconstructs raw
prices from that root's own split actions and refuses when they cannot be proven (ADR-0029
Part I; see [ingestion](ingestion.md)).

```bash
dotenvx run -- uv run equity ingest --date 2026-07-10 --markets us_equity,cn_ashare
dotenvx run -- uv run equity ingest --dry-run --markets us_equity --tickers AAPL
```

### `equity backfill`

Backfill historical data. One of `--start` or `--days-back` is required, else
the command exits non-zero. Backfill deliberately runs one market/date at a
time.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--start` | str | — | Start date `YYYY-MM-DD` |
| `--end` | str | yesterday | End date `YYYY-MM-DD` |
| `--days-back` | int | — | Calendar days back from `--end` |
| `--markets`, `-m` | str | `us,cn,hk_sg` | Comma-separated markets |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers; the scoped-landing flag. An explicitly empty value is refused, never broadened |
| `--dry-run` | flag | off | No writes |
| `--lake-dir` | str | canonical `data/lake` | Explicit lake root inside `data/` (e.g. `data/lake_real`); never mixed, never defaulted. A non-canonical root accepts `us_equity` only, so pass `--markets us_equity` (ADR-0030) |

```bash
dotenvx run -- uv run equity backfill --days-back 30 --markets us_equity
dotenvx run -- uv run equity backfill --start 2024-01-02 --end 2024-12-31 --markets us_equity --tickers AAPL,NVDA
```

### `equity auto-backfill`

Auto-detect and fill data gaps across markets.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--days-back` | int | `90` | Days to scan for gaps |
| `--markets`, `-m` | str | all | Comma-separated markets |
| `--max-gap-days` | int | `30` | Skip gaps larger than this |
| `--dry-run` | flag | off | Show gaps without filling |

```bash
dotenvx run -- uv run equity auto-backfill --days-back 90 --dry-run
```

### `equity label-repair`

Inventory unlabeled rows in the Gold feature table and, only when explicitly
authorized, apply a bounded label-only repair (ADR-0014 / D09). **Report-only by
default** — it prints the finalizable scope (a next-session bar exists, so the
label can be computed with known availability) and the pending scope (no
next-session bar yet) and writes nothing.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--start` | str | `--end` minus 30 days | Start date `YYYY-MM-DD` |
| `--end` | str | yesterday | End date `YYYY-MM-DD` |
| `--tickers`, `-t` | str | all tickers | Comma-separated ticker subset |
| `--repair` | flag | off | Authorize the bounded label-only update |
| `--authorize-broad` | flag | off | Owner authorization (D09) when finalizable rows exceed `--max-keys` |
| `--max-keys` | int | `1000` | Bounded repair limit |

```bash
# Report the unlabeled scope without writing
uv run equity label-repair --start 2026-09-01 --end 2026-09-30 --tickers AAPL,MSFT

# Apply a bounded repair for the reported scope
uv run equity label-repair --start 2026-09-01 --end 2026-09-30 --tickers AAPL,MSFT --repair
```

A repair never inserts missing keys, evolves the schema, or rewrites feature
values; it updates only `next_day_return` and `label_available_date` for rows
that already exist. A scope larger than `--max-keys` refuses to run without
`--authorize-broad`, and no broad or automatic repair is ever performed.

### `equity sync`

Sync the data lake from S3 (bootstrap). The remote tree must mirror the local
numbered medallion layout without a `data/lake/` prefix — each market is pulled
from `<bucket>/01_bronze/market_data/<market_dir>`.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--bucket`, `-b` | str | `S3_BUCKET` env | S3 bucket root URL, e.g. `s3://my-bucket` |
| `--workers`, `-w` | int | `16` | Download workers |
| `--dry-run` | flag | off | Simulate |

```bash
dotenvx run -- uv run equity sync --bucket s3://your-bucket
```

### `equity macro`

Fetch macro indicators (FRED/yfinance; FRED series require `FRED_API_KEY`,
yfinance indicators work without it) and upsert them to `01_bronze/macro`.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--indicators` | str | all configured | Comma-separated indicators |
| `--dry-run` | flag | off | Simulate |

```bash
dotenvx run -- uv run equity macro --date 2026-07-10
```

### `equity pipeline`

Run the full EOD pipeline (ingest → features → ML).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date `YYYY-MM-DD` |
| `--days-back` | int | `1` | Days back |
| `--markets`, `-m` | str | all configured | Comma-separated markets |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--skip-ingestion` | flag | off | Skip Stage 1 |
| `--skip-features` | flag | off | Skip Stage 2 |
| `--skip-ml` | flag | off | Skip Stage 3 |
| `--allow-history-backfill` | flag | off | Authorize a 120-day feature-history recovery |
| `--save-results` | flag | off | Save JSON results to `logs/pipeline_results_<date>.json` (skipped with `--dry-run`) |
| `--dry-run` | flag | off | Simulate |

```bash
dotenvx run -- uv run equity pipeline --markets us_equity --tickers AAPL,MSFT
dotenvx run -- uv run equity pipeline --markets us_equity --tickers AAPL --allow-history-backfill
```

Without `--allow-history-backfill`, missing warm-up history fails the feature
stage instead of starting a backfill.

### `equity features`

Run a scoped feature (Gold) build for explicit tickers and one window.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--tickers`, `-t` | str | required | Comma-separated tickers; an explicit scope is required |
| `--start` | str | required | First output session `YYYY-MM-DD` |
| `--end` | str | required | Last output session `YYYY-MM-DD` |
| `--coverage-basis` | str | `source_clock` | `source_clock` (strict) or `vendor_events_no_clock` (weak opt-in, ADR-0032) |
| `--lake-dir` | str | canonical lake | Explicit lake root (e.g. `data/lake_real`, ADR-0030) |
| `--dry-run` | flag | off | Compute and validate the run without writing Gold rows |

```bash
dotenvx run -- uv run equity features --tickers AAPL,MSFT --start 2024-01-02 --end 2024-12-31
dotenvx run -- uv run equity features --lake-dir data/lake_real --tickers AAPL,MSFT \
    --start 2020-01-02 --end 2026-08-20 --coverage-basis vendor_events_no_clock --dry-run
```

The run exits non-zero when the requested basis has no matching action coverage
evidence, when the window passes the instrument's action coverage bound, or when
the instrument has no coverage proof at all. See the feature guide for the
refusal wording.

## Unstructured & news

These commands need credentials from `.env`, so run them via
`dotenvx run --`. The Finnhub commands (`news`, `sentiment`, `transcripts`,
`ratings`) read `FINNHUB_API_KEY` from the environment and exit non-zero when
no key is found. Keys are env-only by design — there is no `--api-key` option
(avoiding shell-history/`ps` leaks).

### `equity news`

Fetch market news with sentiment analysis.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--max-articles` | int | `50` | Max articles per ticker |
| `--sentiment-method` | str | `vader` | `vader` or `finbert` |
| `--min-relevance` | float | `0.0` | Min relevance 0.0–1.0 |
| `--max-workers` | int | `1` | Parallel workers |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity news --tickers AAPL,MSFT --sentiment-method finbert
```

### `equity sentiment`

Analyze market sentiment (Finnhub social sentiment).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--max-workers` | int | `1` | Parallel workers |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity sentiment --tickers AAPL
```

### `equity transcripts`

Fetch earnings call transcripts from Finnhub.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity transcripts --tickers AAPL
```

### `equity ratings`

Fetch analyst ratings from Finnhub (structured data, no LLM needed).

Same flags as `transcripts` (`--date`, `--tickers`, `--dry-run`).

```bash
dotenvx run -- uv run equity ratings --tickers AAPL,MSFT
```

### `equity sec`

Fetch SEC 10-K/10-Q filings from EDGAR full-text search. `--process` currently
fails closed before fetch because no bounded provider actual-cost adapter is
available; no LLM processing is runtime-ready.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--lookback` | int | `120` | Filing lookback days |
| `--process` | flag | off | Currently exits nonzero before fetching; no bounded provider actual-cost adapter is available |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity sec --tickers AAPL --lookback 90
# Avoid --process: it intentionally exits nonzero before fetching.
```

### `equity financials`

Fetch SEC XBRL structured financials (balance sheet, income statement,
ratios).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--date` | str | resolved trading date | Trading date |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--lookback` | int | `120` | Filing lookback days |
| `--dry-run` | flag | off | Skip writes |

```bash
dotenvx run -- uv run equity financials --tickers AAPL,MSFT
```

### `equity corporate-actions`

Fetch dividends and splits (yfinance) into the corporate-actions dataset
(ADR-0011). Event-driven — there is no per-date fetch: the run is incremental
with **per-ticker watermarks** (W03/F14), each ticker fetching only events
with `ex_date` after its own max stored value (full history on a ticker's
first run, never suppressed by another ticker's recent event). The same
quality-gated frame lands in `01_bronze/corporate_actions/<market>` and
`02_silver/corporate_actions/<market>`, partitioned by `ex_date`, upserted on
`(ticker, ex_date, action)` — overlapping reruns are idempotent. yfinance
split multipliers are converted to the lake's old/new share-ratio convention
(`2.0` → `0.5` for a 2-for-1 split).

Boundary: the watermark is exclusive, so vendor corrections at or below a
ticker's own max stored `ex_date` are **not** re-detected — there is no
unlimited historical correction detection. The per-ticker window edges are
logged (`corporate_actions_fetch_start`) and returned in the ingest result
payload (`since_by_ticker`).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--market` | str | `us_equity` | Price market (ADR-0010 long key); yfinance-backed markets only |
| `--tickers`, `-t` | str | all configured | Comma-separated tickers |
| `--dry-run` | flag | off | Fetch but skip writes |
| `--lake-dir` | str | canonical `data/lake` | Explicit lake root inside `data/`; a non-canonical root accepts `us_equity` only (ADR-0030) |

```bash
dotenvx run -- uv run equity corporate-actions            # incremental, configured tickers
dotenvx run -- uv run equity corporate-actions -t AAPL,MSFT
```

## Intelligence & ML

### `equity forecast`

Price forecasting (train/predict/backtest). For model training prefer
`equity ml train`, which surfaces `--backend`.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--mode` | str | `predict` | `train`, `predict`, or `backtest` |
| `--ticker` | str | `AAPL` | Ticker symbol |
| `--start` / `--end` | str | past year → today | Train/backtest window `YYYY-MM-DD` |
| `--date` | str | today | Single prediction date (predict mode) |
| `--model-dir` | str | default location | Model directory |
| `--model-mode` | str | `v1_direction` | `v1_direction` or `v2_meta_label` |
| `--tune` | flag | off | Hyperparameter tuning |

```bash
uv run equity forecast --mode predict --ticker AAPL
uv run equity forecast --mode backtest --ticker MSFT --start 2025-01-01
```

### `equity ml`

The ML rigor harness — comparisons and ablations that write FindingCards to
`data/findings/`. See the [ML Rigor guide](20260810-ml-rigor.md) for the full
workflow.

| Command | Purpose |
|---|---|
| `ml compare` | Compare v1 vs v2 labeling and XGBoost vs LightGBM, emit 2 FindingCards |
| `ml ablate` | Ablate enriched vs technical-only features, emit the enrichment-ablation card |
| `ml train` | Train one backend classifier (canonical training entrypoint) |

`ml compare` and `ml ablate` share these flags:

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--universe` | str | `demo` | Config ticker group in `config/tickers.yaml` |
| `--ticker`, `-t` | str | first of universe | Run on a specific ticker |
| `--start` / `--end` | str | ~2y ago → today | Window `YYYY-MM-DD` |
| `--output-dir`, `-o` | str | `data/findings` | Findings dir |
| `--train-window` | int | `252` | Walk-forward train window (rows) |
| `--test-window` | int | `21` | Walk-forward test window (rows) |
| `--embargo-window` | int | `1` | Post-test embargo (rows) |

`ml train` flags:

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--ticker`, `-t` | str | `AAPL` | Ticker symbol |
| `--backend` | str | `xgboost` | `xgboost` or `lightgbm` |
| `--start` / `--end` | str | ~1y ago → today | Window `YYYY-MM-DD` |
| `--model-mode` | str | `v1_direction` | `v1_direction` or `v2_meta_label` |
| `--tune` | flag | off | Hyperparameter tuning |

```bash
dotenvx run -- uv run equity ml compare  --universe demo
dotenvx run -- uv run equity ml ablate   --universe demo
dotenvx run -- uv run equity ml train --ticker AAPL --backend lightgbm --model-mode v2_meta_label
```

`ml compare` and `ml ablate` require feature history under `03_gold/features`;
when it is missing they exit non-zero and point at
`equity pipeline --markets us_equity --tickers <t> --allow-history-backfill` rather
than auto-backfilling.

## Analysis

### `equity query`

Query the data lake via DuckDB.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--query`, `-q` | str | run all | Named query |
| `--db` | str | `data/equity_data.duckdb` | DuckDB path |

Named queries: `latest_summary`, `top_volume`, `gainers_losers`,
`cross_market`, `moving_avg`, `volatility`, `market_stats`, `price_range`.

Invalid SQL or an unknown named query fail loudly with a red `QueryError`
message and exit code 1; a valid query that returns no rows prints a yellow
"No results" warning and exits 0.

```bash
uv run equity query --query top_volume
```

### `equity signal scan`

Scan watchlist and generate signals.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--format`, `-f` | str | `table` | `json`, `md`, or `table` |
| `--date`, `-d` | str | resolved trading date | Target date `YYYY-MM-DD` |
| `--watchlist`, `-w` | str | managed private watchlist | Explicit legacy ticker-only YAML; requires `--market` |
| `--market` | str | — | Canonical market for an explicit legacy ticker-only list |
| `--mic` | str | — | Exact MIC for legacy entries when required to disambiguate |
| `--ticker-config` | str | configured universe | Active source configuration used to resolve identities |
| `--config`, `-c` | str | `config/signals.yaml` | Signal config path |
| `--output`, `-o` | str | stdout | Save output to file |
| `--dry-run` | flag | off | Print a plan; no scanning, inference, report/history writes or runtime directory creation |

```bash
uv run equity signal scan
uv run equity signal scan --format json --output signals.json
```

### `equity tracking`

Private canonical watchlist and outbox operations are exposed as `equity
tracking list|add|update|remove|migrate-legacy|settings|scan|readiness|inbox|digest|schedule|deliver`.
This is a local workflow; watchlist changes do not fetch/backfill data. See the
[private tracking guide](20261016-private-tracking-preview.md) for field-level
options and privacy boundaries. `scan --dry-run` is a no-read/no-inference/
no-persistence/no-delivery plan. Delivery is disabled unless `--enable-live` is
explicitly passed; the opportunity webhook is distinct from health webhooks.

### `equity portfolio`

`equity portfolio import` is the only write path: it accepts the canonical CSV
only (no broker-export mapping, no plugin) and `--dry-run` validates fully and
writes nothing.

`equity portfolio migrate` upgrades the ledger schema (v1 -> v2, which adds the
`SPLIT` event kind). It is report-only by default: it verifies the file and prints
its schema version, row count and record digest without writing anything. `--apply`
first writes a verified `<ledger>.<utc>.v1.bak` copy beside the ledger, then rebuilds
the table in one transaction, proves the record is unchanged and re-verifies the
result (ADR-0031). No command upgrades implicitly, a v1 ledger refuses reads and
writes with a message naming this one, and there is no down-migration — the verified
backup is the reversal.

```bash
uv run equity portfolio migrate            # report only: writes nothing
uv run equity portfolio migrate --apply    # verified backup, then the v1 -> v2 rebuild
```

`equity portfolio positions|summary|reconcile` are read-only reports over the same
private ledger. Each needs an explicit timezone-aware `--as-of` plus `--marks`
(valuation) or `--statement` (reconciliation), and each takes the neutral
`--account` label (default `main`). The valuation commands accept
`--max-stale-sessions` as `0` or `1` only. Reads never create a database,
directory, journal, mark file or cache, and a missing ledger is unavailable rather
than an empty account.

```bash
uv run equity portfolio positions --as-of 2024-01-05T21:00:00Z --marks marks.json
uv run equity portfolio summary --as-of 2024-01-05T21:00:00Z --marks marks.json
uv run equity portfolio reconcile --as-of 2024-01-05T21:00:00Z --statement statement.csv
```

`positions` prints holdings with basis status, average cost, realized gain, the
selected mark (price, source, provenance, session, completed-session age), value,
unrealized gain and blockers. `summary` prints cash, flows, fees, net dividend
income, gains, the valid marked subtotal and — only when every required mark is
valid — complete NAV and allocation. Gross dividend income and withholding are
unavailable (schema v1 stores net dividend cash only), never zero. `reconcile`
compares statement quantities and cash line by line with inclusive tolerances of
`0.00000001` share and `0.01` USD.

Exit codes: `0` only for a complete result (for `reconcile`: every line matched);
`1` for required unavailability, a mismatch, or invalid input. Partial reports stay
visible, and a nonzero exit never means they were complete. Private values appear
only in the requested local report, never in error text.

See the [private portfolio guide](20261103-private-portfolio-ledger.md) for the
canonical CSV, the stored mark JSON, the staleness rule and the accounting
contract.

### `equity backtest`

Backtest trading strategies (shares the strategy registry with
`equity report backtest`). This is a flat top-level command — there is no
`backtest` sub-app.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--strategy`, `-s` | str | `momentum` | `momentum`, `mean_reversion`, or `trend_following` |
| `--tickers`, `-t` | str | `AAPL,MSFT` | Comma-separated tickers |
| `--start-date` | str | **required** | `YYYY-MM-DD` |
| `--end-date` | str | **required** | `YYYY-MM-DD` |
| `--initial-cash` | float | `100000` | Initial capital |
| `--adjust` | str | `splits` | Point-in-time split-only adjustment. Legacy `none` is an alias for split-only, never a raw-price bypass. Verified action proof is required; missing proof makes adjusted evaluation unavailable, with no raw fallback. Dividend-inclusive total-return adjustment is unsupported. |
| `--coverage-basis` | str | `source_clock` | ADR-0032 action-coverage basis for adjusted reads: `source_clock` (strict, point-in-time; the only basis a historical decision may use) or `vendor_events_no_clock` (explicit weak opt-in for vendor-sourced roots; null clocks, weak stamp). One basis per run; a frame already read on another basis is refused, not reinterpreted. |
| `--lake-dir` | str | canonical `data/lake` | Explicit lake root such as `data/lake_real` (ADR-0030); one root per run, no discovery, fallback or cross-root mixing. |
| `--output`, `-o` | str | stdout only | Output JSON |

```bash
uv run equity backtest --strategy momentum --tickers AAPL,MSFT \
  --start-date 2025-01-01 --end-date 2025-12-31
# A normal source fetch is a separate, explicitly authorized operation.
# Adjusted backtests need verified source proof; the native yfinance path does not currently provide it.
# On the real root the vendor evidence is the weak, explicitly opted-in basis:
#   uv run equity backtest ... --lake-dir data/lake_real --coverage-basis vendor_events_no_clock
```

### `equity arena run`

Run the strategy arena (strategies × cost regimes) and publish one immutable run per
requested completed pair plus one attempt directory per invocation.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--tickers`, `-t` | str | `AAPL,MSFT,GOOGL,AMZN,NVDA` | Comma-separated tickers |
| `--start-date` | str | **required** | `YYYY-MM-DD` |
| `--end-date` | str | **required** | `YYYY-MM-DD` |
| `--markets` | str | `us_equity` | Comma-separated market codes |
| `--initial-cash` | float | `100000` | Initial capital |
| `--strategies` | str | all | Comma-separated strategy names |
| `--cost-regimes` | str | all | Comma-separated regimes (`zero`, `realistic`, `high`) |
| `--output-dir`, `-o` | str | `data/findings` | Findings root (runs + attempts) |
| `--lake` | str | canonical lake | Lake root to read prices and action coverage from |

```bash
uv run equity arena run --start-date 2025-01-01 --end-date 2025-12-31 \
  --strategies momentum,mean_reversion

# Backtest the offline demo lake instead of the canonical lake
uv run equity arena run --lake data/sample \
  --start-date 2025-01-02 --end-date 2026-06-30
```

The lake must contain point-in-time action coverage (ADR-0015) in addition to
ticker identity; `equity demo seed` writes both, so a seeded sample lake is
backtestable without any network access.

Every requested `(strategy, cost regime)` pair is attempted independently. The
summary prints requested/completed/failed/non-evaluable counts, the failed pairs
(sanitized error type and reason, no absolute paths or credentials) and each
run's evaluation status (`evaluated`, `no_trades`, `ineligible`).

| Outcome | Exit |
| --- | --- |
| every requested pair completed | `0` |
| any requested pair failed — labelled `PARTIAL`; the successful runs' artifacts are still written | `1` |
| a requested pair is missing without a recorded failure — labelled `PARTIAL`, with `N requested pair(s) neither completed nor recorded a failure` naming each pair | `1` |
| a pair is recorded both completed and failed — labelled `PARTIAL`, with `N requested pair(s) are recorded both completed and failed` naming each pair | `1` |
| no requested run completed — no success banner, no cards, artifacts untouched (writer not called); every requested pair with neither outcome is named | `1` |
| a completed run or recorded failure outside the request — named as out of scope; never counted as success, never published | unchanged |
| artifact write failure | `1` |

Completion is measured against the requested pair set (recorded by `run_arena`;
legacy outcomes without it fall back to their completed plus failed pairs), so
`1 of 2 requested pair(s) completed` with no recorded failure is a partial
invocation and exits `1`. The zero-completion guard uses the same measure: an
outcome that carries a completed run *outside* the request publishes nothing,
exits `1`, and names those pairs as outside the invocation's request — only the
requested set is ever published or counted as success. The requested set is
deduplicated in first-occurrence order before any count is printed, and
`run_arena` collapses duplicate selections the same way: `--strategies
momentum,momentum` is one requested pair, reports `1 of 1`, and is not an error.

Only `evaluated` runs (trades present and the universe at least the strategy's
`min_stocks`) feed the FindingCards; `no_trades`/`ineligible` runs are printed
with their status and excluded from card evidence. The default five-ticker
universe is below momentum's `min_stocks=10`, so that run reports
`ineligible — universe 5 < min_stocks 10` without failing the invocation. There
is no partial-allowance flag. Artifacts are immutable: each invocation writes one
`runs/<ts>-<hash>/` per requested completed pair and one `attempts/<ts>-<hash>/`
that owns the three comparison cards and the benchmark copy; nothing is deleted
automatically, and the pre-E01b legacy `<strategy>__<regime>/` directories are a
frozen read-only view that no command writes any more. See the
[Arena and findings guide](20260829-arena-and-findings.md#artifact-layout-under-datafindings)
for the full layout, the `last_attempt`/`last_success` fields and the display-only
`run:`/`attempt:`/`unevidenced:` card references.

### `equity report backtest`

Run a single backtest under one cost regime and publish one immutable run
(`data/findings/runs/<ts>-<hash>/`) plus one attempt record
(`data/findings/attempts/<ts>-<hash>/`). No FindingCard, and no legacy
`<strategy>__<regime>/` directory. An undated result is refused (exit `1`, nothing
written). The fee/tax pair actually applied is read from the engine and recorded in
the run manifest: a single report uses the static `COST_REGIMES` pair, not the
arena's per-market `realistic` defaults.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--strategy`, `-s` | str | `momentum` | Strategy name |
| `--tickers`, `-t` | str | `AAPL,MSFT,GOOGL,AMZN,NVDA` | Comma-separated tickers |
| `--start-date` | str | **required** | `YYYY-MM-DD` |
| `--end-date` | str | **required** | `YYYY-MM-DD` |
| `--markets` | str | `us_equity` | Comma-separated market codes |
| `--cost-regime` | str | `realistic` | `zero`, `realistic`, or `high` |
| `--initial-cash` | float | `100000` | Initial capital |
| `--output-dir`, `-o` | str | `data/findings` | Findings root for the run and attempt evidence |

```bash
uv run equity report backtest --strategy mean_reversion --cost-regime high \
  --start-date 2025-01-01 --end-date 2025-12-31
```

## Maintenance & ops

### `equity monitor`

Monitor pipeline health and data quality. Always writes a report (default
`logs/health-report.json`) so dashboards can render it. **Exits 1 when the
pipeline is unhealthy** (cron/schedulers branch on the exit code; the report
is still written).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--max-age-days` | int | from settings | Max data age |
| `--null-threshold` | float | from settings | Null % threshold |
| `--output-json` | str | `logs/health-report.json` | Save full report |
| `--lake-dir` | str | canonical `data/lake` | Report on one explicit root; roots are never combined (ADR-0030) |

A **real-source root** carries the `us_equity` slice only, so the report scopes to it: the
freshness/quality sweeps judge `us_equity` alone instead of erroring on four markets the root
may not hold, and checks that cannot be evaluated there (Feature Store, Unstructured
Freshness — no Gold or enrichment tables are written to a real root) are reported `N/A` with
their reason rather than `PASS`. `check_results` keeps its boolean shape for dashboards; the
additions are `check_status`, `check_notes` and a `scope` block. An *absent* price table is
still a failure — scoping is not leniency. The canonical root's verdicts are unchanged. A
closed historical slice legitimately fails freshness; state the expectation with
`--max-age-days` (e.g. `--max-age-days 800` for a 2024 window).

```bash
dotenvx run -- uv run equity monitor --output-json site/health-report.json
dotenvx run -- uv run equity monitor --lake-dir data/lake_real --max-age-days 800
```

### `equity delta-vacuum`

Remove stale files from Delta Lake tables. Accepts short names (`us`), long
table names (`us_equity`), or full medallion paths
(`01_bronze/market_data/us_equity`).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--markets`, `-m` | str | `us_equity,cn_ashare,hk_sg_equity` | Comma-separated datasets |
| `--retention-hours` | int | `168` | Retention window in hours |
| `--dry-run` | flag | **on** | Preview only; pass `--no-dry-run` to execute |

```bash
uv run equity delta-vacuum --markets us_equity --no-dry-run
```

### `equity delta-compact`

Compact small files in Delta Lake tables.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--markets`, `-m` | str | `us_equity,cn_ashare,hk_sg_equity` | Comma-separated datasets |

```bash
uv run equity delta-compact --markets us_equity,cn_ashare
```

### `equity delta-migrate`

Migrate Hive-partitioned Parquet to Delta Lake format.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--markets`, `-m` | str | all five markets | Comma-separated datasets |
| `--dry-run` | flag | off | Preview only |

```bash
uv run equity delta-migrate --dry-run
```

### `equity catalog-generate`

Generate `data/catalog.jsonl` from the Hamilton DAG topology (one JSON line per
dataset, node, and edge). Never edit the JSONL directly.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--output`, `-o` | str | `data/catalog.jsonl` | Output JSONL path |

```bash
uv run equity catalog-generate
```

### `equity dashboard build`

Build the static HTML dashboard (`index.html`, `datasets.html`,
`health.html`, `updates.html`, `config.html`, plus `dashboard-data.json`).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--output-dir`, `-o` | str | settings default | Output directory |

```bash
dotenvx run -- uv run equity dashboard build --output-dir site
```

### `equity dashboard serve`

Serve the Streamlit dashboard locally.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--port`, `-p` | int | `8501` | Port |

```bash
uv run equity dashboard serve --port 8501
```

## Config & validation

### `equity config`

Inspect and validate the unified application settings.

| Command | Purpose |
|---|---|
| `config show` | Show configuration JSON with sensitive values redacted |
| `config get <path>` | Get a dotted value; sensitive keys/nested values stay redacted |
| `config validate` | Validate YAML configuration files |
| `config export [file]` | Export redacted configuration to a file (stdout if omitted) |

`config validate` always validates `tickers.yaml` by default; watchlist and
signal files are not selected unless their path option is provided or `--all`
is set. Missing selected files are errors. Ticker-group reference problems are
reported as nonfatal warnings and do not change the exit status.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--tickers`, `-t` | str | `config/tickers.yaml` | Ticker config path; always validated |
| `--watchlist`, `-w` | str | `config/watchlist.yaml` | Additional watchlist path; not selected by default |
| `--signals`, `-s` | str | `config/signals.yaml` | Additional signals path; not selected by default |
| `--all` | flag | off | Also validate watchlist and signals |

```bash
uv run equity config show
uv run equity config get schedule.cron
uv run equity config validate --all
```

### `equity validate`

Pointblank-backed data quality checks over Parquet files or directories.

| Command | Purpose |
|---|---|
| `validate check <path>` | Validate data against schema and produce quality metrics |
| `validate profile <path> --name <n>` | Profile a dataset and display quality metrics |
| `validate drift <current> <baseline>` | Compare two datasets for drift detection |

Flags: `validate check` takes `--type` (`price`, `macro`, or `news`; default
`price`) and `--strict` (fail on warnings); `validate drift` takes
`--threshold` (float, default `0.1`).

```bash
uv run equity validate check data/lake/01_bronze/market_data/us_equity --type price
uv run equity validate drift current.parquet baseline.parquet --threshold 0.1
```

## Utilities

### `equity bootstrap sample`

Generate mock Parquet data for quick testing. **This is a loose sandbox layout,
not a medallion lake:** each market's table is written to
`<output-dir>/<market>` (no `01_bronze/market_data/<market>` prefix), rows carry
no canonical identity columns (ADR-0016) and no corporate-action coverage proofs
(ADR-0015). Strict consumers — `03_gold/features`, the arena loader, the ML
feature loader, and `equity validate check` — reject that output by design, so
do not point them at it. Use `equity demo seed` when you need a sample lake that
strict readers accept (ADR-0020).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--days`, `-d` | int | `30` | Number of trading days |
| `--tickers`, `-t` | str | default set | Comma-separated tickers |
| `--output-dir`, `-o` | str | `data/sample` | Output directory |
| `--seed`, `-s` | int | `42` | Random seed |

```bash
uv run equity bootstrap sample --days 30 --seed 42
```

### `equity demo seed`

Seed a demo US universe (synthetic by default, offline-safe) into the auxiliary
sample lake (`data/sample/`) for the Strategy Lab showcase. The canonical lake
(`data/lake/`) is never touched implicitly: overwriting it requires `--lake`
pointing at `data/lake` plus an interactive confirmation (or the explicit
`--overwrite-production-lake` flag — the overwrite is still logged).

Seeded rows carry the canonical composite identity (ADR-0016: `market`/`mic`/
`symbol` resolved from `config/tickers.yaml`), plus one complete synthetic
corporate-action coverage proof per instrument in
`02_silver/corporate_action_coverage/`. That makes the sample lake directly
usable by `equity arena run --lake data/sample` (ADR-0020). A symbol with no
configured exchange identity fails closed — the venue is never guessed from
ticker text.

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--years` | float | `5.0` | Years of history to seed |
| `--tickers`, `-t` | str | demo group | Comma-separated tickers (must be configured in `config/tickers.yaml`) |
| `--real` | flag | off | Attempt live yfinance fetch (falls back to synthetic) |
| `--seed` | int | `42` | Synthetic RNG seed |
| `--lake` | str | `data/sample` | Target lake root (`data/lake` requires confirmation) |
| `--overwrite-production-lake` | flag | off | Skip the confirmation prompt for `--lake data/lake` (requires `--lake`) |
| `--dry-run` | flag | off | Preview the seed summary without writing |

```bash
uv run equity demo seed
uv run equity demo seed --real
uv run equity demo seed --dry-run
uv run equity demo seed --lake data/lake --overwrite-production-lake   # destructive, logged
uv run equity demo seed --years 5                                     # then: arena run --lake data/sample
```

Seeding or preloading the canonical lake (`data/lake/`) leaves synthetic rows in
the same tables real ingestion uses. Bronze rows are not distinguishable
(`identity_provenance = current_ticker_config`); only the Silver coverage
`source` and Gold `action_coverage_source` markers are. `uv run equity doctor`
reports them under `synthetic_provenance`, and `make reset-lake` (`DRY=1` lists,
the default removes with confirmation) is the supported way back to an empty lake
before real ingestion.

### `equity api serve`

Serve the read-only FastAPI API over the equity data lake. OpenAPI docs at
`/docs`, health at `/health`. See the [REST API guide](20260811-read-api.md).

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--host` | str | `127.0.0.1` | Bind host; non-loopback requires confirmation unless explicitly opted in |
| `--allow-unauthenticated-exposure` | flag | off | Noninteractive opt-in to a warned, unauthenticated non-loopback bind |
| `--port`, `-p` | int | `8000` | Bind port |
| `--reload` | flag | off | Auto-reload on code changes (dev) |

```bash
uv run equity api serve --port 8000
```

## Related guides

- [Ingestion](ingestion.md)
- [Pipeline](pipeline.md)
- [Signals](signals.md)
- [Backtesting](backtesting.md)
- [ML Rigor](20260810-ml-rigor.md)
- [REST API](20260811-read-api.md)
- [API keys](../20260406-api-keys.md)
