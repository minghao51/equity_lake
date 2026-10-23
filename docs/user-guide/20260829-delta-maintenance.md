# Delta Lake Maintenance

Cataloged lake tables are Delta tables with Parquet data files stored in the
numbered medallion layout (`data/lake/01_bronze/` … `04_platinum/`); partition
columns are dataset-specific (for example, `date` or corporate-action
`ex_date`) — see [the medallion layout decision](../decisions/0001-medallion-layout-and-generated-catalog.md).
Repeated ingests and feature runs can append or merge into date-partitioned
tables, so their files accumulate superseded merge versions and small-file
sprawl from repeated daily appends. The `equity delta-*` commands are the
maintenance surface for that accumulation.

## Additive evolution, concurrency, and replay status

Keyed `merge_delta` now uses native Delta nullable additive schema evolution;
it does not seed a rewritten copy of the entire existing table. Optimistic
concurrency conflicts refresh the committed table snapshot before retry, with a
maximum of three attempts. Incompatible types, unsupported evolution, corrupt
state, and retry exhaustion remain visible failures. This is the implementation
contract under ADR-0017, not authorization to migrate a production table.

Bronze-to-Silver replay now requires explicit source/date/market/instrument
scope. It processes at most 100 items per invocation in batches of at most 25,
uses Pydantic-validated auxiliary spend-ledger and checkpoint files independent
of authoritative Silver state, and advances its checkpoint only after rereading
durable Silver.
Historical replay does not send notifications. Paid work remains zero by
default and needs a separate finite cumulative estimate, actual-spend cap,
batch estimate, and provider-reported actual cost. No actual-cost adapter is
available for the unbounded LLM helper or SEC path: `equity sec --process`
intentionally exits nonzero before fetching. These paths are not
paid-runtime-ready.

ADR-0017 implementation/offline-contract review was accepted with C10 on
2026-10-18. This offline acceptance is not production readiness. Compatibility
or migration validation remains report-first on scoped temporary copies. Do not
infer production migration, repair, replay, or spend authorization from these
capabilities.

## When to run maintenance

- **After a heavy backfill or a long run of daily ingests** — many small
  Parquet files per `date=` partition slow DuckDB/Delta scans. Run
  `delta-compact` to rewrite them into fewer, larger files.
- **After big merge/upsert sessions** — merges leave superseded files that
  are still tracked by the transaction log (enabling time travel). Run
  `delta-vacuum` to actually delete them once the retention window passes.
- **Legacy stores only** — new data is always written as Delta; `delta-migrate`
  exists only for the one-time conversion of pre-Delta Hive-partitioned
  Parquet.

## `equity delta-vacuum`

Deletes stale/superseded files from Delta tables that are older than the
retention window. Files inside the window are kept, preserving time travel.

```bash
uv run equity delta-vacuum                          # preview only (default)
uv run equity delta-vacuum --markets us_equity,cn_ashare --no-dry-run
uv run equity delta-vacuum --retention-hours 336 --no-dry-run
```

| Flag | Default | Description |
|---|---|---|
| `--markets`, `-m` | `us_equity,cn_ashare,hk_sg_equity` | Comma-separated tables to vacuum |
| `--retention-hours` | `168` (7 days) | Files older than this window are removed |
| `--dry-run` | **on** | Preview only; nothing is deleted |
| `--verbose`, `-v` | off | Debug logging |

`--dry-run` defaults **on**, so a bare first run is always safe. To actually
delete files you must pass `--no-dry-run` explicitly.

Output lists the effect per table:

```text
  us_equity: would remove 214 stale files
  cn_ashare: would remove 0 stale files
Dry run — no files deleted. Use --no-dry-run to execute.
```

Note the default market set is three tables (`us_equity`, `cn_ashare`,
`hk_sg_equity`) — pass `--markets us_equity,cn_ashare,hk_sg_equity,jpx_equity,krx_equity`
to cover all five. Tables that are not Delta tables (or don't exist) report
`0 stale files` rather than failing.

## `equity delta-compact`

Compacts small files within partitions for faster reads — run it after heavy
backfills or weeks of daily ingestion.

```bash
uv run equity delta-compact
uv run equity delta-compact --markets us_equity
uv run equity delta-compact --markets jpx_equity,krx_equity
```

| Flag | Default | Description |
|---|---|---|
| `--markets`, `-m` | `us_equity,cn_ashare,hk_sg_equity` | Comma-separated tables to compact |
| `--verbose`, `-v` | off | Debug logging |

There is no `--dry-run` flag; compaction rewrites table files. Treat it as a
mutating maintenance operation, not as an implementation-review check. Establish
scope and verify on a temporary copy before any separately authorized
production operation. Output per table:

```text
  us_equity: added=12 removed=1541
  cn_ashare: added=3 removed=289
Compaction complete.
```

A table that isn't a Delta table reports `skipped (not a Delta table)`.
Compaction itself produces superseded files, so a common pattern after a big
backfill is compact first, then vacuum once the retention window has elapsed.

## `equity delta-migrate`

One-time migration of legacy Hive-partitioned Parquet (`date=YYYY-MM-DD/*.parquet`)
into a Delta table partitioned by `date`. This is **historical**: all new
writes have been Delta for a long time, and tables that are already Delta are
reported `OK` without changes.

```bash
uv run equity delta-migrate --dry-run    # preview row counts per table
uv run equity delta-migrate
```

| Flag | Default | Description |
|---|---|---|
| `--markets`, `-m` | all five equity tables | Comma-separated tables to migrate |
| `--dry-run` | **off** | Preview row counts without writing |
| `--verbose`, `-v` | off | Debug logging |

Unlike vacuum, `--dry-run` defaults to **off** here, so the first invocation
can mutate files. Use report-first inspection and a scoped temporary copy for
compatibility checks; the existence of this command is not production
migration authorization. During an explicitly authorized migration the old
`date=` directories are moved into a `.pre_delta_backup/` sibling directory
rather than deleted. Preserve and verify that snapshot before any rollback or
cleanup. Output per table is `OK` or `SKIPPED/FAILED` (missing directory or
unreadable Parquet).

## Market identifiers and path containment

All three commands accept short market aliases (`us`), canonical market names
(`us_equity`), named dataset routes (`features`, `predictions`, and the mapped
dataset identifiers), full lake-relative medallion paths
(`01_bronze/market_data/us_equity`), and other safe lake-relative table paths.
A catalog-only allowlist is not imposed, so noncatalog Delta tables within the
lake remain addressable.

Targets are resolved relative to the configured lake root. Absolute paths and
any path containing a `..` component are rejected. Symlinks resolving within
the lake are allowed and normalized to their effective relative route;
symlinks resolving outside the lake are rejected. The complete comma-separated
`--markets` list is validated before any vacuum, compaction, or migration call;
an invalid target exits nonzero without invoking maintenance for any target.

This is CLI-only operator containment. It does not change the generic storage
path handling used for auxiliary Delta artifacts outside the lake, and it is
not an OS boundary or protection against a malicious concurrent symlink swap.
See [ADR-0019](../decisions/0019-cli-delta-maintenance-lake-containment.md).

## Typical maintenance flow

After a multi-month backfill:

```bash
uv run equity delta-compact --markets us_equity,cn_ashare,hk_sg_equity,jpx_equity,krx_equity
uv run equity delta-vacuum                       # preview
# ...after the retention window has passed:
uv run equity delta-vacuum --no-dry-run
```

## Related

- [Pipeline user guide](pipeline.md) — daily ingest and pipeline operation
- [Ingestion user guide](ingestion.md) — sources, destinations, backfill
- [CLI reference](20260406-cli-reference.md) — full command and flag index
- [ADR 0001: medallion layout and generated catalog](../decisions/0001-medallion-layout-and-generated-catalog.md)
