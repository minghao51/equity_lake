---
name: verify-equity-pipeline
description: Verify Equity Lake pipeline changes against operational contracts.
---

# Verify

Read `docs/developer/architecture/pipeline-contracts.md` and
`docs/developer/architecture/data-flow.md`. For pipeline changes, test dry-run
no-write behavior, required-source blocking, optional degradation, explicit
backfill authorization, and deterministic CLI exit status. For storage changes,
test destination, partition, natural-key upsert, and reader behavior.

Use `uv run pytest`, `uv run ruff check .`, `uv run mypy src`,
`uv run equity catalog-generate`, and the MkDocs build as appropriate. Do not
run live network ingestion by default; report unverified provider assumptions.
