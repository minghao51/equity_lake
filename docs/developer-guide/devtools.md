# Devtools

`src/equity_lake/devtools/` holds developer-only helpers: the shared synthetic-seeding
primitives (`seeding.py`), demo and sample data seeding (`seed_demo.py`, plus the
`equity bootstrap sample` command in `cli/bootstrap.py`), the disposable synthetic-lake
bootstrap (`bootstrap.py`), the test-data generator (`test_data.py`), two artifact
verifiers (`verify_h00_release.py`, `verify_r02_artifacts.py`), the recursive Markdown
lint gate (`markdown_lint.py`), a one-off corpus seeder (`seed_transcripts.py`), the lake
reset (`reset_lake.py`) and the bounded preload helpers (`preload.py`,
`refit_preload.py`). Nothing in the runtime pipeline imports this package — it
exists so contributors can populate a lake, test the showcase flow, and keep
CI configuration honest without touching production sources. `seed_demo` is surfaced
through the CLI (`equity demo seed`); `bootstrap`, `verify_h00_release`,
`verify_r02_artifacts`, `markdown_lint`, `seed_transcripts`, `test_data` and `reset_lake`
run as modules (`python -m equity_lake.devtools.<name>`, several behind `make` targets),
and `seeding` is a library the other generators share.

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

## Synthetic seeding primitives — `devtools/seeding.py`

`seeding.py` is the single polars home for the synthetic generation that
`equity bootstrap sample`, `equity demo seed` and the sandbox test-data generator each
used to implement separately: the random walk (`synthetic_ohlcv`), the Mon–Fri
calendar helpers, the curated per-market universes and the config-derived identity
resolver. Callers keep their own `OhlcvProfile` — the distribution parameters are
per-tool tuning, not shared policy — but the walk, the calendar and the universes
live here. It is a library: no CLI, no module entry point. The RS-R01 scale fixtures
are generated with `synthetic_ohlcv` (`MARKET_CONFIGS` profile), which is fixture data,
not market data.

## Disposable synthetic lake — `devtools/bootstrap.py`

`bootstrap.py` materialises a canonical synthetic `data/lake/` without ever touching
the operator's checkout (RS-E01, profile (b) of the fresh-clone profiles in the
[quickstart](../getting-started/quickstart.md)). `bootstrap_disposable_lake()` allocates
a fresh OS temporary directory, extracts the tracked source from `git archive` (refusing
`.env`, untracked runtime `data/` and symlink members), seeds that checkout's lake with
the synthetic generator, and proves the isolation before anything is written: the
checkout and the destination lake must both resolve beneath the allocation, the child
process must report that it imported the disposable checkout's `equity_lake`, the target
lake must be empty, and an operator lake is refused as a destination in either
containment direction regardless of the legacy overwrite flag. Unseeded optional
datasets are reported `available: false` with a reason — never stubbed with generated
rows. `BootstrapRefused` marks a preflight refusal, `BootstrapError` a failure after
allocation; the temporary checkout is disposed unless `--keep`.

```bash
uv run python -m equity_lake.devtools.bootstrap --end-date 2026-11-20
uv run python -m equity_lake.devtools.bootstrap --end-date 2026-11-20 --keep --seed 42 --years 3 --tickers 10
```

Flags: `--end-date` (pins the synthetic window, as `equity demo seed --end-date` does),
`--keep` (retain the allocation), `--seed`, `--years`, `--tickers`.

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

## R02 artifact verification — `devtools/verify_r02_artifacts.py`

Read-only verifier for the archived R02 research artifacts (RS-R02-D1): it checks every
archived file's sha256 against the tracked anchor
([recovery receipt](../finding/20261123-01-r02-recovery-receipt.md)), re-derives the
protocol↔prespecification digests from the frozen code, and recomputes every reported
metric with `ml/experiment.py`'s own estimators — no model is fitted, so this is not a
refit. It never opens a file for writing, so an in-place verification cannot mutate the
evidence it verifies. Exit 0 = verified, 1 = tamper or mismatch, 2 = nothing to verify.
With `--lake-dir` the economic metrics and their bootstrap intervals are recomputed too;
without a lake root they are reported **UNVERIFIED** with that reason rather than
assumed. It also reproduces the two estimands behind the −2.79 pp / [−0.42 pp,
+0.31 pp] pairing and reports the pairing as a finding (recovery receipt §4).

```bash
uv run python -m equity_lake.devtools.verify_r02_artifacts \
  --research-dir data/research/r02 \
  --anchor docs/finding/20261123-01-r02-recovery-receipt.md \
  --lake-dir data/lake_real
```

## Markdown lint gate — `devtools/markdown_lint.py`

The recursive Markdown gate behind `make lint-md`: `pymarkdown` scanned recursively so
`docs/` subdirectories are covered, checked against the reviewed baseline at
`config/markdown-lint-baseline.json`, which records preexisting findings as
`file -> rule -> count` (314 findings across 35 files when recorded by RS-Q00,
2026-11-23). `--check` fails on any finding absent from the baseline or any raised
count — and also fails when a recorded finding no longer reproduces, so the baseline can
only ratchet down through an explicit `make lint-md-baseline` (`--update-baseline`);
the baseline is never edited to hide a finding. Disabled rules are recorded, not silent:
the `disabled_rules` entries name the rule, a justification and the measured legacy cost
(the MD013 exclusion is one), and the gate fails closed when the recorded set does not
match its own. Coverage: `tests/unit/test_markdown_lint_gate.py`.

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
runtime pipeline and dashboard never import it, and the only CLI callers are
`equity demo seed` and `equity bootstrap sample`, which import `seed_demo.py`
and `seeding.py` (`cli/commands/demo.py:16`, `cli/bootstrap.py:18`). Demo
and sample seeding write to the auxiliary sample lake (`data/sample/`);
the test-data generator writes to `data/sandbox/test_data/`. Writing into
the canonical lake (`data/lake/**`) is possible only through the explicit,
logged `equity demo seed --lake ... --overwrite-production-lake` override —
otherwise devtool output is indistinguishable from auxiliary data, not
pipeline data. Nothing here changes catalog, schema, or boundary contracts.
