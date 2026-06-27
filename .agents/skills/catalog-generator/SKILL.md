---
name: catalog-generator
description: >-
  Generate the data catalog (JSONL) from the Hamilton DAG topology.
  Maintain medallion-layer metadata: add new datasets, tag DAG nodes,
  regenerate, and deploy. Trigger: adding data sources, changing pipeline
  structure, new markets, feature schema changes, updating catalog docs.
license: MIT
compatibility: opencode
---

# Data Catalog Generator

Hamilton-powered medallion catalog: `equity catalog-generate` → `data/catalog.jsonl` → Astro site → GitHub Pages.

## When to Use

- Adding a new data source (market, news, alternative data)
- Changing pipeline structure (new DAG module, new feature)
- New market added to config
- Feature schema change (column added/removed/renamed)
- Updating dataset descriptions or column metadata
- Deploying updated catalog to GitHub Pages

## Workflow

1. **Define datasets** → edit `src/equity_lake/catalog/datasets.py`
   - Add `DatasetEntry` to correct layer list (BRONZE, SILVER, GOLD, PLATINUM)
   - Columns from `core/schemas.py` or inline `ColumnInfo` list
2. **Tag DAG nodes** → `@tag(layer=..., category=..., produces=...)` on functions in `features/dag/`
   - Tags: layer=bronze|silver|gold|platinum, category=raw_column|momentum|volatility|volume|calendar|price_structure|enrichment|validation|target, produces=pipe-separated columns (use `\|` between names)
3. **Regenerate** → `uv run equity catalog-generate`
4. **Verify diff** → `git diff data/catalog.jsonl`
5. **Commit + push** → GitHub Action deploys Astro site to GitHub Pages

## Rules

**DO:**
- Define datasets in `catalog/datasets.py` — never hand-edit `catalog.jsonl`
- Tag each DAG function with `@tag(layer=..., category=..., produces=...)` at function level
- Tag validator functions with `validators="check_output(...)"`
- Run `equity catalog-generate` after any DAG change that adds/removes/renames nodes
- Use `produces=col1|col2|col3` for `@parameterize` nodes that generate multiple columns
- Commit `data/catalog.jsonl` — it's the source of truth for the Astro site
- After adding a new dataset, verify it renders correctly in the Astro site

**DON'T:**
- Hand-edit `data/catalog.jsonl`
- Tag Hamilton input nodes (`price_data`, `duckdb_conn`, etc.) — untagged nodes are auto-filtered (a node must have a `layer` tag to appear)
- Tag private helper functions (e.g. `_merge_news_sentiment`) — only Hamilton DAG nodes
- Use `uv run equity catalog generate` (space, not hyphen) — it's `catalog-generate`

## Auto-Filtering

The builder automatically excludes:
- **Hamilton internal wrappers**: nodes ending in `_raw`, `_data_type_validator`, `_range_validator`
- **Untagged nodes**: any node without a `layer` tag (this catches Hamilton input nodes like `price_data`, `duckdb_conn`, `enrich_news`)
- **Framework tags**: `module` and `hamilton.*` tags are stripped from the `tags{}` output (they don't carry domain meaning)

## Data Flow

```
Hamilton DAG (raw_01, clean_02, features_03, enrichments_04)
  │
  ├─ list_available_variables() → filter (layer tag required, no internal wrappers)
  │                             → tags → NodeEntry
  ├─ what_is_upstream_of()     → edges → EdgeEntry
  └─ catalog/datasets.py      → DatasetEntry
       │
       ▼
  Catalog (Pydantic) → catalog.jsonl
       │
       ▼
  Astro site (docs/catalog/) → GitHub Pages (minghao.github.io/equity_lake)
```

## JSONL Format

Each line = one JSON object with `type` field:

| type | fields |
|------|--------|
| `catalog` | version, generated_at, dataset_count, node_count, edge_count |
| `dataset` | name, layer, path, description, format, partition, columns[], upstream[], downstream[] |
| `node` | name, layer, category, description, produces[], depends_on[], validators[], tags{} |
| `edge` | source, target, relationship |

## Tag Reference

| Tag | Values | Example |
|-----|--------|---------|
| `layer` | bronze, silver, gold, platinum | `@tag(layer="gold")` |
| `category` | raw_column, transform, momentum, volatility, volume, calendar, price_structure, enrichment, validation, target | `@tag(category="momentum")` |
| `produces` | comma-separated column names (use `\|` for @parameterize) | `@tag(produces="roc_5\|roc_10\|roc_20")` |
| `validators` | pipe-separated validator descriptions | `@tag(validators="check_output(data_type=float)")` |
| `description` | human-readable description | `@tag(description="Boundary validation")` |

## Files

| Path | Purpose |
|------|---------|
| `src/equity_lake/catalog/models.py` | Pydantic models |
| `src/equity_lake/catalog/datasets.py` | Static dataset definitions |
| `src/equity_lake/catalog/builder.py` | Hamilton Driver + topology extraction |
| `src/equity_lake/catalog/writer.py` | JSONL serialization |
| `src/equity_lake/cli/commands/catalog.py` | CLI command |
| `data/catalog.jsonl` | Generated artifact (git-tracked) |
| `docs/catalog/` | Astro frontend |
| `.github/workflows/catalog-deploy.yml` | GitHub Pages deployment |

## CLI

```bash
# Generate catalog
uv run equity catalog-generate

# Custom output path
uv run equity catalog-generate --output /tmp/catalog.jsonl

# Verbose logging
uv run equity catalog-generate --verbose
```
