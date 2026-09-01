# Handoff 04 — P1: Safety rails (devtools, CLI, secrets)

Priority: P1. Depends on: 01. Independent of 02/03 (disjoint files — can run in the same
wave). Suggested dispatch: **one `worker`** (small, scattered fixes), then `reviewer`.
CLI changes require: help text + CLI test + user-guide touch per the change matrix.

## 1. `equity demo seed` can destroy production bronze ✅ verified
`devtools/seed_demo.py:222-226`: `write_delta(df, US_EQUITY_MARKET, mode="overwrite")`
against `LAKE_DIR` — no prompt, no dry-run, no scope guard. The module docstring openly
says it targets the canonical bronze path so the arena reads the same data.

Fix: default the target to the auxiliary sample lake (`data/sample/`, mirroring
`cli/bootstrap.py cmd_sample` which already does the right thing) and require an explicit
`--lake` plus an interactive confirmation (or a `--overwrite-production-lake` flag) to
touch `data/lake/`. Add `--dry-run` (summary only). Update `equity demo seed --help` and
the demo user guide.

## 2. `devtools/test_data.py` writes foreign layout into Delta table dirs ✅ verified
`:32,43,95,139,417,650`: `write_partitioned_parquet` (hand-rolled hive partitioning)
targets `US_EQUITY_DIR` / `CN_ASHARE_DIR` / `HK_SG_EQUITY_DIR` — mixing non-Delta parquet
into canonical Delta table directories, bypassing writer + validation.

Fix: redirect all generated output to an auxiliary dir (`data/sandbox/test_data/<dataset>/`)
by default; or delete the module in favor of `equity bootstrap sample` if the owner agrees
(it duplicates it three ways — see handoff 07 item 8). Also fix the orphaned argparse
entrypoint docs (`:575` references `equity-generate-test-data`, which doesn't exist;
`Makefile` actually calls `equity bootstrap sample`; `docs/devtools.md` + `TESTING.md`
point at the module CLI — align all of them).

## 3. Dry-run persistence: `pipeline --save-results` ✅ verified
`cli/commands/pipeline.py:62-65`: `--save-results` writes `pipeline_results_<date>.json`
to **CWD even under `--dry-run`** (contract violation), while
`dashboard/exporter.py:111` reads them from **`LOGS_DIR`** — broken end-to-end.

Fix: skip persistence on dry-run (or document the scoped exception — prefer skipping);
write to `LOGS_DIR` so the dashboard finds it; keep the flag's help text accurate.

## 4. Secrets hygiene ✅ verified
- `cli/commands/ingest.py:81,113,139,164` — `--api-key` options put Finnhub keys into
  shell history/`ps`. Delete the options; `_require_finnhub_api_key` already falls back
  to env (`FINNHUB_API_KEY` via dotenvx). Update help text + user guide.
- Companion env hygiene: confirm `.env.example` lists everything referenced.

## 5. Devtools exit codes & CLI discovery
- `devtools/seed_transcripts.py:176-207` — `main()` always exits 0 even when bronze/
  silver steps report `ok=False`. Map failures to exit 1. Also add `--dry-run` (this tool
  spends DeepSeek tokens).
- `devtools/sync_schedule.py:70` — uses `print` (fine for a devtool entrypoint, but note
  it in the module docstring); keep `python -m` (invoked by
  `.github/workflows/pages.yml:49`).

## 6. CLI consistency nits (batch, mechanical)
- `cli/commands/data.py:62` — `ingest` discards `run_daily_ingestion` outcomes: print a
  summary and `raise typer.Exit(1)` when required markets fail (mirror `pipeline.py`).
- `cli/commands/ingest.py:217,265` — `sec`/`financials` ignore `upsert_dataset()`'s
  return; exit 1 on write failure (the Finnhub helper at `:64-70` already does this).
- `cli/commands/data.py:185` — hardcoded `Path("data/lake")` → `LAKE_DIR`.
- `cli/commands/data.py:258` — help text for `--dry-run` (Typer toggles via `--no-dry-run`).
- `cli/commands/analysis.py:48` — `query --db` default → a `paths.py` constant.
- `cli/bootstrap.py:307` — "next steps" hint suggests `query --sql`, which doesn't exist.
- `cli/commands/analysis.py:32-40` — wrap backtest failure in the clean
  error+`typer.Exit(1)` pattern used by `arena.py:117-121`.
- `cli/commands/ml.py:139` — imports private `_load_feature_engineer`; expose a public
  accessor in `features/` instead.

## 7. Help-scan test debt (can be its own follow-up PR)
`tests/unit/test_cli_unified.py` lacks help-scan coverage for ~20 commands (backfill,
auto-backfill, sync, macro, delta-vacuum/compact/migrate, news, sentiment, transcripts,
ratings, sec, financials, forecast, query, monitor, backtest, catalog-generate, config
get/export). Extend the existing parametrized scan rather than adding new tests.

## Acceptance criteria

- `equity demo seed` cannot modify `data/lake/` without an explicit, logged override.
- No devtool writes into `data/lake/**` (grep + test).
- Dry-run leaves zero new files (test in `test_cli_unified.py` style).
- No secret-bearing CLI options remain; docs updated.
- Failed writes/ingestions produce exit 1.

## Validation

```bash
uv run pytest tests/unit/test_cli_unified.py -q
uv run equity --help && uv run equity demo seed --help   # manual smoke of help text
uv run pytest -n auto && uv run ruff check . && uv run mypy
```

## Out of scope

Consolidating the three synthetic-data generators (handoff 07 item 8) — only redirect
their outputs here.

## Outcome (closed 2026-08-31)

- **Landed:** `b74b713`.
- `equity demo seed` defaults to `data/sample`; production lake requires
  `--lake` + confirmation or `--overwrite-production-lake` (case-insensitive-
  safe path comparison); `--dry-run` writes nothing.
- `test_data.py` kept (owner call deferred in 07) but redirected to
  `data/sandbox/test_data/`; orphaned script docs fixed.
- `--save-results`: skipped on dry-run, written to `LOGS_DIR` (dashboard aligned).
- All four `--api-key` options removed (env-only); devtools exit codes + `--dry-run`.
- Beyond the brief: review found `typer.Option("--dry-run", ...)` creates no
  `--no-dry-run` negation — swept and fixed at **16 sites** (`delta-vacuum`
  could never execute for real); seed_demo lake guard hardened.
- Deferred: section 7 help-scan test debt (~20 commands) — still open.
