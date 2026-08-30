# Archive

Superseded and deprecated material lives only here, per `AGENTS.md`. Nothing
in this directory is authoritative: living guidance comes from the
documentation map (`docs/README.md`) and accepted decision records
(`docs/decisions/`).

## Contents

- `migrate_to_delta.py` — one-time Hive → Delta Lake migration (executed
  2026-06)
- `migrate_to_medallion.py` — one-time flat → medallion layout migration
  (executed 2026-06; see `docs/plans/20260615-medallion-architecture-migration.md`)
- `parallel-ingestion.md` — superseded performance/observability guide
  (archived 2026-08-29; flags and APIs referenced no longer exist — see the
  Architecture page Ingestion section for current parallel fetching)
- `CONCERNS.md` — superseded tech-debt register (archived 2026-08-29; most
  concerns addressed — current state is tracked in ADRs and architecture pages)

These scripts are retained for audit provenance and are not run in normal
operation.

## Rules

- Superseded plans, guides, and spent scripts move here via `git mv` with
  their history preserved.
- Archived pages keep their original filenames; no edits beyond a move note.
- This directory is excluded from the MkDocs site.
