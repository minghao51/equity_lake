# ADR-0001: Numbered medallion layout and a generated catalog

**Status:** Accepted
**Recorded:** 2026-08-29 (backfilled; migrated to the numbered layout 2026-06, see `docs/plans/20260615-medallion-architecture-migration.md`)

## Context

The lake grew from flat per-market directories. Readers needed to know which
tables were raw versus curated, and dataset metadata was drifting from the
actual Hamilton DAG topology.

## Decision

- Storage is a numbered medallion layout: `data/lake/01_bronze/`, `02_silver/`,
  `03_gold/`, `04_platinum/`, holding date-partitioned Delta tables with
  Parquet data files.
- `data/catalog.jsonl` is a generated artifact, produced from
  `catalog/datasets.py` via `uv run equity catalog-generate`, and deployed to
  the catalog site. It is never edited directly.

## Consequences

- Layer ordering is self-documenting; promotion between layers is an explicit
  pipeline act.
- Catalog edits go through catalog definitions and regeneration, keeping
  metadata in sync with the DAG (see the `catalog-generator` skill).
- Any change to the layer set or catalog flow requires a schema/storage change
  per the AGENTS.md change matrix and this record's successor, if any.
