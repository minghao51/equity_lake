# Devtools

`src/equity_lake/devtools/` holds developer-only helpers: demo/sample data
seeding, the test-data generator, a release verifier, and a one-off
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
  walk per ticker, `numpy.random.default_rng`), exchange-session dates ending
  yesterday (weekday window filtered through `core.calendar.is_trading_day`,
  so no bar ever lands on a venue holiday). `equity bootstrap sample` seeds a
  mixed-venue market only on intersection sessions
  (`core.calendar.trading_days_between`), since its rows carry no MIC. No
  network or API keys are needed.
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

## Release verification — `devtools/verify_h00_release.py`

Checks the composed H00 artifact immediately before it is uploaded, so the publish
path re-proves the allowlist instead of trusting the build: exactly
`index.html`, `payload.json` and `sanitization-report.json`, no symlinks, size
bounds, path containment (flat files only), a report consistent with the payload
and with the current forbidden classes, and a fresh negative rescan of the
bytes-to-publish using `dashboard/h00.py`'s needles.

```bash
uv run python -m equity_lake.devtools.verify_h00_release --dir dist
```

Exit 1 with a **value-free** message on any violation (a position, a count or a
forbidden class — never a needle, a file's contents or an unexpected name), so CI
logs cannot become the leak the scan prevents. Covered by
`tests/unit/test_h00_release_verification.py` and wired as a step in
`.github/workflows/h00-publish.yml`, the only publication path.

The former `devtools/sync_schedule.py` (workflow-cron sync against
`config/settings.yaml`) was retired with `.github/workflows/pages.yml`: no workflow
publishes on a schedule any more, so there is no workflow cron to keep in sync.

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

## Lake reset — `make reset-lake` / `devtools/reset_lake.py`

The canonical lake can hold synthetic rows from `demo seed --lake` and
`make preload`. Only two existing markers make that visible —
`02_silver/corporate_action_coverage.source` and
`03_gold/features.action_coverage_source` set to `synthetic_demo_seed`; Bronze
price rows carry `identity_provenance = current_ticker_config`, the same label a
real config-resolved ingestion writes. `uv run equity doctor` reports the counts
under `synthetic_provenance` (`synthetic` / `empty` / `unknown`, never `real`).

`devtools/reset_lake.py` is the supported way back to an empty lake before real
ingestion. It removes exactly `data/lake/01_bronze`, `data/lake/02_silver`,
`data/lake/03_gold`, `data/lake/04_platinum/predictions` (the subpath only, not
the rest of `04_platinum`) and `data/models`, all derived from `core.paths`
constants.

```bash
make reset-lake DRY=1   # DRY=1 -> --dry-run: lists the five paths, writes nothing
make reset-lake         # destructive: confirmation prompt
uv run python -m equity_lake.devtools.reset_lake --yes   # non-interactive equivalent
```

Flags: `--dry-run`, `--yes`, `--lake-dir`, `--models-dir`. The guard reuses the
doctor observation: it refuses (exit non-zero, nothing deleted) unless the lake
is `synthetic` or `empty`, and it refuses a symlinked target instead of following
it. `empty` means **no rows in any Delta dataset under the reset targets** —
`01_bronze`, `02_silver`, `03_gold`, and `04_platinum/predictions`, discovering any
directory that contains a `_delta_log` rather than trusting a list of known tables
— so a lake holding rows only in `02_silver/news_sentiment` or `01_bronze/macro` is
`unknown`, not `empty`. `data/models` is a derived-artifact directory with no rows:
it stays a reset target but is excluded from the emptiness judgement. An empty lake
is a no-op success; `DRY` accepts only `0`, `1`, or empty (`DRY=2`/`DRY=yes`
error out instead of defaulting to the destructive form). Unit coverage lives
in `tests/unit/test_reset_lake.py` (guard, exact artifact set, symlink refusal) and
`tests/unit/test_make_targets.py` (the `DRY=1` target contract, the `DRY` value
guard, and the provenance state on a test-owned lake).

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
