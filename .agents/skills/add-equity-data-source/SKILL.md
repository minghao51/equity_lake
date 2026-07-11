---
name: add-equity-data-source
description: Add a source while keeping routing, storage, validation, catalog, and docs aligned.
---

# Add a source

Read `docs/user-guide/ingestion.md`,
`docs/developer/architecture/data-flow.md`, and the change matrix in
`AGENTS.md`. Update the router, `Market`/`VALID_MARKETS`, destination map,
fetcher schema validation, configuration, focused tests, source docs, and
catalog definitions together. Regenerate with `uv run equity catalog-generate`;
never hand-edit `data/catalog.jsonl`.

Verify only the scoped source contract and tests first. Do not add a broad
backfill or live provider test without an explicit network marker.
