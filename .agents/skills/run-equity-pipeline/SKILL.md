---
name: run-equity-pipeline
description: Run a scoped Equity Lake pipeline with explicit safety checks.
---

# Run the pipeline

Read `docs/user-guide/ingestion.md` and
`docs/developer/architecture/pipeline-contracts.md` first. Use `dotenvx run --
uv run` when `.env` is required. Start with `equity pipeline --dry-run`.

Never authorize a broad history recovery implicitly. Use
`--allow-history-backfill` only with explicit `--markets` and `--tickers`, then
verify the saved result JSON and `equity monitor`. Do not run live network
ingestion as a default verification step.
