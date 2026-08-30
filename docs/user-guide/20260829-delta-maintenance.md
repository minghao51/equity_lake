# Delta Lake Maintenance

All lake tables are date-partitioned Delta tables with Parquet data files stored
in the numbered medallion layout (`data/lake/01_bronze/` … `04_platinum/`) — see
[the medallion layout decision](../decisions/0001-medallion-layout-and-generated-catalog.md).
Every ingest and feature run appends or merges into these tables, so over time
the underlying files accumulate: superseded versions from merges, and small-file
sprawl from repeated daily appends. The `equity delta-*` commands are the
maintenance surface for that accumulation.

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
uv run equity delta-vacuum --markets us_equity,cn_ashare --dry-run=false
uv run equity delta-vacuum --retention-hours 336 --dry-run=false
```

| Flag | Default | Description |
|---|---|---|
| `--markets`, `-m` | `us_equity,cn_ashare,hk_sg_equity` | Comma-separated tables to vacuum |
| `--retention-hours` | `168` (7 days) | Files older than this window are removed |
| `--dry-run` | **on** | Preview only; nothing is deleted |
| `--verbose`, `-v` | off | Debug logging |

`--dry-run` defaults **on**, so a bare first run is always safe. To actually
delete files you must pass `--dry-run=false` explicitly.

Output lists the effect per table:

```text
  us_equity: would remove 214 stale files
  cn_ashare: would remove 0 stale files
Dry run — no files deleted. Use --dry-run=false to execute.
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

There is no `--dry-run` flag; compaction is a table rewrite that preserves
table contents and is safe to run at any time. Output per table:

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

Unlike vacuum, `--dry-run` defaults to **off** here, so check first if you are
unsure of a table's state. During migration the old `date=` directories are
moved into a `.pre_delta_backup/` sibling directory rather than deleted; the
backup can be removed once the migrated table is verified. Output per table is
`OK` or `SKIPPED/FAILED` (missing directory or unreadable Parquet).

## Market identifiers

All three commands accept short names (`us`), long table names (`us_equity`),
or full medallion paths (`01_bronze/market_data/us_equity`).

## Typical maintenance flow

After a multi-month backfill:

```bash
uv run equity delta-compact --markets us_equity,cn_ashare,hk_sg_equity,jpx_equity,krx_equity
uv run equity delta-vacuum                       # preview
# ...after the retention window has passed:
uv run equity delta-vacuum --dry-run=false
```

## Related

- [Pipeline user guide](pipeline.md) — daily ingest and pipeline operation
- [Ingestion user guide](ingestion.md) — sources, destinations, backfill
- [CLI reference](20260406-cli-reference.md) — full command and flag index
- [ADR 0001: medallion layout and generated catalog](../decisions/0001-medallion-layout-and-generated-catalog.md)
