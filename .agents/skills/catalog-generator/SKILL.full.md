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

# Data Catalog Generator — Full Reference

## Architecture

Equity Lake's data catalog is a **Hamilton-powered, JSONL-based, Astro-rendered** system. It combines three sources of truth:

1. **Static dataset definitions** (`catalog/datasets.py`) — paths, schemas, descriptions for each medallion dataset
2. **DAG topology** (Hamilton `list_available_variables()` + `what_is_upstream_of()`) — node names, types, dependencies, edges
3. **Node tags** (`@tag` decorator on DAG functions) — layer, category, produces, validators metadata

These are merged into a `Catalog` Pydantic model and serialized to `data/catalog.jsonl` (one JSON object per line for clean git diffs).

## Adding a New Dataset

When you add a new data source (e.g., "crypto_ohlcv"), follow these steps:

### Step 1: Define the dataset

Edit `src/equity_lake/catalog/datasets.py` and add a new `DatasetEntry` to the appropriate layer list:

```python
# For a new bronze dataset
BRONZE_DATASETS.append(
    DatasetEntry(
        name="crypto_ohlcv",
        layer="bronze",
        path="data/lake/01_bronze/crypto/",
        description="Daily cryptocurrency OHLCV data. Source: Binance API.",
        format="parquet",
        partition="date=",
        columns=[
            ColumnInfo(name="symbol", dtype="string"),
            ColumnInfo(name="date", dtype="datetime"),
            ColumnInfo(name="open", dtype="float64"),
            ColumnInfo(name="high", dtype="float64"),
            ColumnInfo(name="low", dtype="float64"),
            ColumnInfo(name="close", dtype="float64"),
            ColumnInfo(name="volume", dtype="float64"),
        ],
    )
)
```

### Step 2: Tag any new DAG nodes

If your dataset feeds into the feature DAG, tag the relevant functions:

```python
@tag(layer="gold", category="momentum", produces="crypto_momentum")
def crypto_momentum(close: pl.Series) -> pl.Series:
    """Compute crypto-specific momentum indicator."""
    ...
```

### Step 3: Regenerate

```bash
uv run equity catalog-generate
```

### Step 4: Verify and commit

```bash
git diff data/catalog.jsonl
# Should show one new dataset line and any new node/edge lines
git add data/catalog.jsonl src/equity_lake/catalog/datasets.py
git commit -m "feat(catalog): add crypto_ohlcv dataset"
```

## Tag Conventions

### Layer Tags

| Layer | When to use |
|-------|------------|
| `bronze` | Raw column extraction (ticker, date, close, volume, etc.) |
| `silver` | Transforms and boundary validation (returns, validated_ohlcv) |
| `gold` | Technical indicators, enrichment joins, feature assembly |
| `platinum` | Prediction outputs (future, not yet in DAG) |

### Category Tags

| Category | Examples |
|----------|---------|
| `raw_column` | ticker, date, open_price, high, low, close, volume |
| `transform` | returns (pct_change) |
| `momentum` | rsi_14, macd, roc_5, roc_10, roc_20, return_1d |
| `volatility` | bollinger_frame, bb_upper, atr_14, volatility_20 |
| `volume` | volume_ma_20, volume_roc_5, obv, volume_ratio |
| `calendar` | day_of_week, month, quarter, trading_day_of_month |
| `price_structure` | overnight_return, intraday_return, hl_range |
| `enrichment` | enriched_features (DuckDB joins) |
| `validation` | validated_ohlcv, validated_features (Pydantic boundary) |
| `target` | next_day_return |

### Tagging @parameterize Nodes

When a function uses `@parameterize` to generate multiple nodes, use `|` to separate produced column names:

```python
@tag(layer="gold", category="momentum", produces="roc_5|roc_10|roc_20")
@parameterize(
    roc_5={"length": value(5)},
    roc_10={"length": value(10)},
    roc_20={"length": value(20)},
)
def roc_pct(close: pl.Series, length: int) -> pl.Series:
    return roc(close, length=length)
```

The tag applies to all generated nodes, and downstream edges will correctly point to each individual node (e.g., `roc_5`, `roc_10`, `roc_20`).

## JSONL Format Reference

### Catalog metadata line

```json
{"type": "catalog", "version": "1.0", "generated_at": "2026-06-26T09:45:02.787533+00:00", "dataset_count": 15, "node_count": 56, "edge_count": 126}
```

### Dataset line

```json
{
  "type": "dataset",
  "name": "us_equity_ohlcv",
  "layer": "bronze",
  "path": "data/lake/01_bronze/market_data/us_equity/",
  "description": "US equity OHLCV data...",
  "format": "parquet",
  "partition": "date=",
  "columns": [
    {"name": "ticker", "dtype": "string", "nullable": true, "description": ""},
    {"name": "close", "dtype": "float64", "nullable": true, "description": ""}
  ],
  "upstream": [],
  "downstream": []
}
```

### Node line

```json
{
  "type": "node",
  "name": "rsi_14",
  "layer": "gold",
  "category": "momentum",
  "description": "",
  "produces": ["rsi_14"],
  "depends_on": [],
  "validators": ["check_output(range=(0,100))"],
  "tags": {
    "layer": "gold",
    "category": "momentum",
    "produces": "rsi_14",
    "validators": "check_output(range=(0,100))"
  }
}
```

### Edge line

```json
{
  "type": "edge",
  "source": "close",
  "target": "rsi_14",
  "relationship": "computed_from"
}
```

## Internal Node Filtering

Hamilton generates internal wrapper nodes for `@check_output` decorated functions:
- `close_raw` — raw function output
- `close_data_type_validator` — validator node
- `close` — actual node (with tags)

The builder filters out nodes ending in `_raw`, `_data_type_validator`, `_range_validator`. Only the tagged parent node is included in the catalog. Self-referencing edges (`source == target`) are also filtered.

## Astro Site Structure

```
docs/catalog/
├── src/
│   ├── layouts/
│   │   └── Layout.astro      # Shared nav + footer + styles
│   ├── pages/
│   │   ├── index.astro       # Overview with LayerStats cards
│   │   ├── lineage.astro     # React Flow interactive DAG
│   │   ├── bronze.astro      # Bronze datasets table
│   │   ├── silver.astro      # Silver datasets table
│   │   ├── gold.astro        # Gold datasets table
│   │   └── platinum.astro    # Platinum datasets table
│   ├── components/
│   │   ├── LineageGraph.tsx  # React Flow component
│   │   ├── DatasetTable.tsx  # Per-layer dataset table
│   │   └── LayerStats.tsx    # Overview statistics cards
│   └── data/
│       └── catalog.ts        # JSONL parser + types
├── astro.config.mjs
├── package.json
└── tsconfig.json
```

Each page reads `data/catalog.jsonl` via `fs.readFileSync` at build time. The React components are client-rendered (`client:load` for static, `client:only="react"` for interactive).

## GitHub Action

`.github/workflows/catalog-deploy.yml` triggers on push to `main` when `data/catalog.jsonl` or `docs/catalog/**` changes. It:

1. Checks out the repo
2. Sets up Node.js 22
3. `npm ci` (installs from lockfile)
4. `npm run build` (Astro builds to `dist/`)
5. Deploys `dist/` to `gh-pages` branch via `peaceiris/actions-gh-pages`

The site is served at `https://minghao.github.io/equity_lake/catalog`.

## Local Development

```bash
# Install Astro dependencies
cd docs/catalog && npm install

# Start dev server
npm run dev

# Build for production
npm run build
```

## Troubleshooting

| Problem | Solution |
|---------|---------|
| `ModuleNotFoundError: No module named 'hamilton'` | Run `uv sync` |
| Missing tags on new nodes | Add `@tag(layer=..., category=..., produces=...)` |
| Edges include internal nodes | Check suffix filter in builder.py `_HAMILTON_INTERNAL_SUFFIXES` |
| Astro can't find catalog.jsonl | Verify `data/catalog.jsonl` exists, regenerate if missing |
| GitHub Pages not updating | Check Actions tab for build failures |
