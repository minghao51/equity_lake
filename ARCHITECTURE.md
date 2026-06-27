# Architecture

## Overview

Equity Lake is a multi-market equity data platform built on a four-layer
medallion architecture (Bronze / Silver / Gold / Platinum). It ingests
OHLCV market data, news sentiment, analyst ratings, and SEC filings,
computes technical and enriched features via a Hamilton DAG, and runs
ML inference for price direction prediction.

## Storage Layout

```
data/lake/
├── 01_bronze/                      # Immutable raw data
│   ├── market_data/
│   │   ├── us_equity/              # Hive Parquet, date= partitions
│   │   ├── cn_ashare/
│   │   ├── hk_sg_equity/
│   │   ├── jpx_equity/
│   │   └── krx_equity/
│   ├── raw_articles/               # Delta — RSS/Reddit/StockTwits text
│   └── macro/                      # Parquet — long-format macro indicators
├── 02_silver/                      # Validated, cleaned, deduped
│   ├── news_sentiment/             # Delta — Finnhub news + VADER sentiment
│   ├── social_sentiment/           # Delta — Finnhub social sentiment
│   ├── processed_articles/         # Delta — LLM-enriched article-ticker pairs
│   ├── sec_extractions/            # Delta — LLM-extracted SEC filing insights
│   ├── analyst_ratings/            # Delta — Analyst consensus + price targets
│   └── sec_financials/             # Delta — SEC XBRL financial data
├── 03_gold/                        # Feature engineering output
│   └── features/                   # Delta — computed feature frames
└── 04_platinum/                    # ML predictions and signals
    └── predictions/                # Delta — model outputs, confidence scores
```

Path constants live in `core/paths.py`. Legacy flat names (e.g.
`US_EQUITY_DIR`) are kept as deprecated aliases pointing to their new
medallion locations.

## DAG Architecture

The feature pipeline is a Hamilton DAG split into four medallion-layered
modules:

```
features/dag/
├── __init__.py           # Package init
├── raw_01.py             # Bronze: OHLCV column extraction (@check_output)
├── clean_02.py           # Silver: returns + validated_ohlcv boundary node
├── features_03.py        # Gold: technical indicators (@parameterize, @check_output)
├── enrichments_04.py     # Gold: external data joins (sentiment, analyst, SEC, macro)
├── schemas.py            # Pydantic models for layer boundary validation
└── polars_validators.py  # Custom Hamilton validators for pl.Series data types & ranges
```

### Two-Phase Execution

The DAG executes in two phases:

1. **Phase 1 — Per-ticker technical indicators** (`compute_technical`):
   Runs the Bronze→Silver→Gold subgraph for each ticker independently.
   Outputs are concatenated vertically. `include_target=True` adds
   `TARGET_FEATURES` (`return_1d`–`return_20d`) for training; omitted
   by default to prevent look-ahead leakage at inference time.

2. **Phase 2 — Batch external-data enrichments** (`compute_enriched`):
   Runs the `enriched_features` node once for all tickers. Injects the
   DuckDB connection and boolean enrichment flags via DAG inputs.
   SQL queries use parameterized `?` placeholders (not f-string
   interpolation) to prevent injection.

### `@parameterize`

Hand-written `roc_5`/`roc_10`/`roc_20` and `return_1d`/`return_5d`/
`return_10d`/`return_20d` functions are replaced by two `@parameterize`
decorated functions (`roc_pct`, `pct_return`). Hamilton generates nodes
with identical names, so downstream consumers are unaffected.

### Boundary Validation

- **Bronze→Silver**: `@check_output(data_type=np.float64)` on `close`
  and `volume` in `raw_01.py`; `validated_ohlcv` node in `clean_02.py`
  applies Pydantic schema validation and filters invalid rows
- **Silver→Gold**: `@check_output(data_in_range=(0, 100))` on `rsi_14`
  in `features_03.py`; `validated_features` node enforces Pydantic
  schema on the final feature frame
- **Write boundaries**: `validate_schema()` in `ingestion/writers.py`
  enforces pointblank schema contracts before partitioned Parquet writes
- **Platinum**: `validate_predictions()` uses pointblank to enforce
  probability range, direction values, and non-null keys; intercepts
  writes to `04_platinum/predictions/`
- **Custom validators**: `polars_validators.py` provides
  `PolarsDataTypeValidator` and `PolarsRangeValidator` with
  `default_validator_candidates` to integrate with Hamilton 1.89+

## Pipeline Orchestration

```
Stage 1: Ingestion     → run_daily_ingestion() → write to 01_bronze/
Stage 2: Features       → run_feature_job() → write to 03_gold/features/
Stage 3: ML             → run_prediction_job() → write to 04_platinum/predictions/
```

`PipelineOrchestrator.execute_eod_pipeline()` chains all three stages.
Each stage is independently callable via the `equity` CLI. Predictions
carry a `feature_schema_version` tag so downstream consumers can detect
feature-set drift between training and inference.

## Data Catalog

A Hamilton-powered catalog (`src/equity_lake/catalog/`) generates
`data/catalog.jsonl` from the DAG topology plus static dataset
definitions, then renders as an interactive Astro + React Flow site.

```
catalog/
├── models.py      # Pydantic: Catalog, DatasetEntry, NodeEntry, EdgeEntry, ColumnInfo
├── datasets.py    # 15 static DatasetEntry definitions (Bronze/Silver/Gold/Platinum)
├── builder.py     # Builds Hamilton driver, extracts nodes/edges from DAG tags
└── writer.py      # JSONL serialization (one object per line)
```

### Generation Flow

1. `build_catalog()` constructs a Hamilton `Driver` from the four DAG
   modules and calls `list_available_variables()`.
2. Each tagged node becomes a `NodeEntry` (tags supply `layer`,
   `category`, `produces`, `validators`). Nodes without a `layer` tag
   (Hamilton inputs like `price_data`) and internal wrappers
   (`*_raw`, `*_data_type_validator`, `*_range_validator`) are filtered
   out. `module` and `hamilton.*` tags are stripped from output.
3. `what_is_upstream_of()` traces edges into `EdgeEntry` records
   (self-references and duplicates removed).
4. Static `DatasetEntry` records from `datasets.py` anchor each
   medallion layer with paths, schemas, and descriptions.
5. `write_catalog_jsonl()` emits one JSON object per line (`type`:
   `catalog | dataset | node | edge`) for clean git diffs.

### CLI & Deployment

```bash
uv run equity catalog-generate              # regenerate data/catalog.jsonl
uv run equity catalog-generate -o /tmp.jsonl # custom output path
```

- **JSONL format** (`data/catalog.jsonl`): 15 datasets, ~45 nodes, ~72
  edges. Git-tracked (source of truth for the frontend).
- **Astro frontend** (`docs/catalog/`): static site built at build time
  from `catalog.jsonl`, uses `@xyflow/react` v12 for DAG visualization.
- **GitHub Pages**: deployed via `.github/workflows/catalog-deploy.yml`
  (official `actions/deploy-pages`, base path `/equity_lake`).
- **Freshness CI** (`.github/workflows/catalog-check.yml`): on PRs
  touching `features/dag/**` or `catalog/**`, regenerates the catalog
  and fails if `data/catalog.jsonl` would change — preventing stale
  catalogs from merging.

## Key Design Decisions

- **Polars** is the primary DataFrame engine. Pandas only at external
  library boundaries (yfinance, akshare, efinance).
- **DuckDB** for analytical queries over Parquet/Delta partitions.
- **Delta Lake** for ACID writes, merge/upsert, and time-travel.
- **Hamilton** for declarative DAG composition with lineage export.
- **pointblank** for Polars-native data validation at Platinum boundary.
- **structlog** for structured JSON logging with correlation IDs.
- **tenacity** for retry/backoff on all API fetchers.
