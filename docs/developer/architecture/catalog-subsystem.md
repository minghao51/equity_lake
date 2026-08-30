# Catalog Subsystem

The catalog describes every dataset in the lake — its layer, path, columns,
and lineage — plus the node/edge topology of the Hamilton feature DAG. It is
a generated artifact: `data/catalog.jsonl` is produced by
`uv run equity catalog-generate` and never hand-edited (ADR-0001). The
generator lives in `src/equity_lake/catalog/`; the Astro site that renders
the catalog lives in `docs/catalog/`.

## Inputs and construction

The catalog merges two sources:

- **Static anchors** (`catalog/datasets.py`): hand-maintained
  `DatasetEntry` definitions per medallion layer — the five bronze OHLCV
  markets, `macro_indicators`, `raw_articles` (bronze);
  `news_sentiment`, `social_sentiment`, `processed_articles`,
  `analyst_ratings`, `sec_extractions`, `sec_financials` (silver);
  `technical_features` (gold); `predictions` (platinum). Paths point at
  `data/lake/`; columns are cross-referenced against the `core/schemas.py`
  constants through a shared dtype map.
- **DAG topology** (`catalog/builder.py`): `build_catalog()` instantiates a
  Hamilton driver over the four layered feature modules
  (`features/dag/raw_01`, `clean_02`, `features_03`, `enrichments_04`) with
  the Polars adapter, then reads node metadata from
  `list_available_variables()`. Only nodes carrying a `layer` tag are
  cataloged; Hamilton-generated wrappers (`*_raw`,
  `*_data_type_validator`, `*_range_validator`) and tooling tags
  (`hamilton.*`, `module*`) are filtered out. Edges come from
  `what_is_upstream_of()` per node, with self-edges removed and the
  relationship defaulting to `computed_from`.

Dataset lineage is layer adjacency: each dataset's upstream is all datasets
in the preceding medallion layer and its downstream all datasets in the next
(Agents work on copies, so the static definitions are never mutated). The
Pydantic models in `catalog/models.py` (`Catalog`, `DatasetEntry`,
`NodeEntry`, `EdgeEntry`, `ColumnInfo`) are the in-memory contract.

## Output

`catalog/writer.py` serializes the catalog as JSONL — one JSON object per
line, prefixed by a summary header line (`type: catalog` with dataset, node,
and edge counts), then `dataset`, `node`, and `edge` lines. One dataset
equals one line, so adding a dataset shows up as a single green line in git
diffs. The default destination is `data/catalog.jsonl` at the project root.

## Command

```bash
uv run equity catalog-generate                 # write data/catalog.jsonl
uv run equity catalog-generate -o other.jsonl   # alternate output path
uv run equity catalog-generate -v               # debug logging
```

The command needs no data in the lake — it extracts metadata from the DAG
modules and static definitions only — and prints the resulting dataset,
node, and edge counts.

## Catalog site

`docs/catalog/` is a standalone Astro site (`equity-lake-catalog`: Astro 5,
React 19, `@xyflow/react` for the lineage graph, dagre for layout). Pages:

| Page | Content |
|---|---|
| `index.astro` | Overview and per-layer stats (`LayerStats`) |
| `bronze/silver/gold/platinum.astro` | Dataset tables per layer (`DatasetTable`) |
| `lineage.astro` | Interactive node/edge graph colored by medallion layer |

`src/data/loadCatalog.ts` reads `data/catalog.jsonl` at build time, so the
published site always reflects the committed JSONL. The site builds with a
base path of `/equity_lake` into `docs/catalog/dist` (`npm run build` inside
`docs/catalog/`; `npm run dev` for local preview).

## CI workflows

- **`.github/workflows/catalog-check.yml`** (Catalog Freshness) — drift
  guard. On PRs and main-branch pushes touching the catalog inputs
  (`features/dag/**`, `catalog/**`, `core/schemas.py`, `core/paths.py`,
  `ingestion/writers.py`, and on PRs the JSONL itself), it regenerates the
  catalog and fails if `git diff --exit-code data/catalog.jsonl` reports a
  change — i.e. if the committed catalog is stale.
- **`.github/workflows/catalog-deploy.yml`** (Deploy Catalog) — on pushes to
  main touching `data/catalog.jsonl` or `docs/catalog/**` (plus manual
  dispatch), runs `npm ci && npm run build` in `docs/catalog/` and publishes
  `dist` to GitHub Pages.
- **`.github/workflows/pages.yml`** — the separate dashboard/docs Pages
  build. It is unrelated to the catalog site but runs the schedule-sync
  guard (`devtools/sync_schedule --check`) described in the developer
  guide's devtools page.

## When to regenerate

Per the `AGENTS.md` change matrix, regenerate and commit the catalog as part
of the same change when:

- a **new source** is added (catalog row required),
- a **schema change** lands (schema constants, catalog, reader compatibility),
- a **DAG feature change** is made (Hamilton tags, catalog regeneration,
  feature tests).

New markets and pipeline-structure changes fall under the same rule. The
`catalog-generator` skill documents the full procedure.

## Related

- [Data Flow](data-flow.md) — what the catalog describes.
- [CLI Reference](../../user-guide/20260406-cli-reference.md) — operator
  view of `catalog-generate`.
- [ADR-0001: Medallion layout and generated
  catalog](../../decisions/0001-medallion-layout-and-generated-catalog.md) —
  the decision that made the catalog generated.
