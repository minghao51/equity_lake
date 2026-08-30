# Devtools

`src/equity_lake/devtools/` holds developer-only helpers: demo/sample data
seeding, the test-data generator, a schedule-sync checker, and a one-off
corpus seeder. Nothing in the runtime pipeline imports this package — it
exists so contributors can populate a lake, test the showcase flow, and keep
CI configuration honest without touching production sources. Two of its
tools are surfaced through the CLI (`equity demo`, `equity bootstrap`); two
run as modules.

## Demo Lake — `equity demo seed` / `make demo`

`devtools/seed_demo.py` seeds a demo US universe for the Strategy Lab showcase.
By default it writes to the auxiliary sample lake (`data/sample/`), mirroring
`equity bootstrap sample` — the canonical lake (`data/lake/`) is never touched
implicitly.

- Offline-safe by default: deterministic synthetic OHLCV (geometric random
  walk per ticker, `numpy.random.default_rng`), Mon–Fri dates ending
  yesterday. No network or API keys are needed.
- `--real` attempts a live `yfinance` pull and falls back to synthetic on
  any failure.
- Ticker resolution order: explicit `--tickers` → the `demo` group in
  `config/tickers.yaml` → the built-in 50-symbol `DEMO_UNIVERSE`.
- Flags: `--years` (default 5), `--tickers`, `--real`, `--seed` (default
  42), `--lake`, `--overwrite-production-lake`, `--dry-run`, `--verbose`.
- Idempotent by overwrite: the frame is written with `write_delta(...,
  mode="overwrite")`, so re-seeding replaces the table rather than merging
  into it.
- `--dry-run` prints the seed summary (tickers/rows/days/source/target)
  without writing anything.

**Production-lake guard**: writing to the canonical lake requires
`--lake <path>` targeting `data/lake/` **plus** either an interactive
confirmation prompt or the explicit `--overwrite-production-lake` flag
(script-friendly; the overwrite is still logged as
`seed_demo_overwrite_production_lake`). Without the authorization the module
itself raises — a `--lake data/lake` run that is declined at the prompt or an
unauthorized programmatic call exits non-zero without writing. There is no
way to reach `data/lake/**` through the default path.

`make demo` wraps `uv run equity demo seed` (sample-lake default) and prints
the suggested follow-up (`equity arena run`). Unit coverage lives in
`tests/unit/test_seed_demo.py` (generation) and
`tests/unit/test_seed_demo_safety.py` (guard rails).

## Sample data — `equity bootstrap sample`

`cli/bootstrap.py` (`cmd_sample`, exposed as `equity bootstrap sample`)
builds a small self-contained sample lake under `data/sample/` (override
with `--output-dir`). It first tries to reuse real data from the existing
lake via a DuckDB scan (last N trading days); if the lake is empty it
generates synthetic frames per market with a curated five-ticker set for
`us_equity`, `cn_ashare`, and `hk_sg_equity`. Output is one Delta table per
market written with the canonical writer. Flags: `--days` (default 30),
`--tickers` (US-format validated), `--output-dir`, `--seed`, `--verbose`.

Use this for onboarding and dashboard checks; for unit tests prefer the
polars fixtures in `tests/conftest.py`.

## Test data generator — `devtools/test_data.py`

`TestDataGenerator` produces realistic OHLCV at scale for manual
experiments: geometric Brownian motion with configurable trend and
volatility, occasional price gaps, lognormal volumes, and data-quality
filters (high ≥ open/close, low ≤ open/close, positive prices and volumes).
It writes Hive-partitioned Parquet (`date=<YYYY-MM-DD>/`) into the auxiliary
sandbox `data/sandbox/test_data/<market>/` (one directory per market for
`us_equity`, `cn_ashare`, and `hk_sg_equity`), skipping partition files that
already exist. It never writes into the canonical lake (`data/lake/**`) —
those Delta tables are reserved for the canonical writer + validation
boundary; use `equity pipeline` for real ingestion or `equity demo seed
--lake` for the showcase lake.

Run it as a module (there is no CLI subcommand for it; `make
generate-test-data` runs the curated `equity bootstrap sample` instead):

```bash
uv run python -m equity_lake.devtools.test_data --days 365
uv run python -m equity_lake.devtools.test_data --markets us_equity --num-tickers 20
uv run python -m equity_lake.devtools.test_data --volatility 0.05 --trend 0.001
```

Flags: `--start-date`, `--end-date`, `--days` (default 365), `--markets`,
`--num-tickers`, `--volatility`, `--trend`, `--seed`, `--verbose`. Output
lands under `data/sandbox/test_data/` — delete that directory to reset it.

## Schedule sync — `devtools/sync_schedule.py`

Keeps the GitHub Pages workflow cron aligned with configuration:
`config/settings.yaml` (`schedule.cron`) is the single source of truth, and
the script rewrites the first `cron:` entry in
`.github/workflows/pages.yml` to match.

```bash
uv run python -m equity_lake.devtools.sync_schedule          # rewrite
uv run python -m equity_lake.devtools.sync_schedule --check  # verify only
```

`--check` exits non-zero on drift and is run as a CI step in `pages.yml`
before any dashboard build. Covered by `tests/unit/test_schedule_sync.py`.

## Transcript corpus seeding — `devtools/seed_transcripts.py`

Loads the HuggingFace `kurry/sp500_earnings_transcripts` dataset (~33k
earnings-call transcripts, 2005–2025) into the lake:

- **Bronze**: the full base is merged into `01_bronze/raw_articles`,
  mapping onto the SEC bronze article schema. Deterministic `article_id`
  (`uuid5` over `symbol/year/quarter`) makes re-runs idempotent on that key.
- **Silver**: a ticker-scoped subset is enriched through the production
  DeepSeek processor (`run_llm_processing`) into
  `02_silver/processed_articles`, merging on `(article_id, ticker)`.
  Scoping happens on the in-memory frame — ticker is not a bronze column —
  which is what bounds LLM token cost. Enrich the HF base via this script,
  not `equity pipeline --markets us_earnings_transcripts` (no pre-LLM ticker
  scope exists there yet).

```bash
uv run python -m equity_lake.devtools.seed_transcripts --tickers AAPL,MSFT,GOOGL
uv run python -m equity_lake.devtools.seed_transcripts --dry-run
```

Flags: `--tickers` (default `AAPL,MSFT,GOOGL`), `--skip-bronze`,
`--skip-silver`, `--force-download`, `--dry-run`, `--verbose`. `--dry-run`
previews the bronze row count and the scoped silver enrichment with no lake
writes or LLM tokens; a cold cache still downloads the source parquet.
Exit code is 0 only when every
requested step reports success; a failed bronze or silver step exits 1.
The downloaded parquet is cached under `data/.cache/`. The operator runbook
is the [RAG Corpus Seeding guide](../user-guide/20260813-rag-corpus-seeding.md).

## Placement

`devtools/` is a top-level package like any other (see
[Project Structure](project-structure.md)), but it is developer-only: the
runtime pipeline, dashboard, and CLI command paths never import it. Demo
and sample seeding write to the auxiliary sample lake (`data/sample/`);
the test-data generator writes to `data/sandbox/test_data/`. Writing into
the canonical lake (`data/lake/**`) is possible only through the explicit,
logged `equity demo seed --lake ... --overwrite-production-lake` override —
otherwise devtool output is indistinguishable from auxiliary data, not
pipeline data. Nothing here changes catalog, schema, or boundary contracts.
