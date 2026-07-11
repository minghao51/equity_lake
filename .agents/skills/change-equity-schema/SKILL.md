---
name: change-equity-schema
description: Change an Equity Lake schema with reader, validation, catalog, and migration checks.
---

# Change a schema

Read `docs/developer/architecture/pipeline-contracts.md` and the change matrix
in `AGENTS.md`. Update schema constants, router/write validation, catalog
columns, readers, and compatibility or migration notes as applicable. Run the
focused schema and writer tests plus `uv run equity catalog-generate`; do not
edit generated JSONL by hand.

Keep verification scoped to the changed dataset and never use a schema change
as a reason to launch a full-universe backfill.
