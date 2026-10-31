# equity-lake

> **An evidence-backed quantitative equity research platform.** A local-first data
> lake feeds a strategy arena and (Phase 2) an ML model-comparison harness; every
> result is a machine-readable **FindingCard** carrying a verdict — *including
> honest negatives.* Built as a Data Scientist / Quant / AI Engineer portfolio.

🔗 **Live demo (coming)** — for now, walk the [Strategy Lab notebook](notebooks/11-strategy-lab.ipynb).

## The showcase path (~2 minutes, fully offline)

```bash
make showcase        # seed the sample lake, then run the arena against it
ls data/findings/    # → 3 evidence-backed FindingCards (strategy / cost / benchmark)
```

Under the hood that is two commands, if you prefer them separately:

```bash
uv run equity demo seed --years 5                     # synthetic, offline-safe, writes data/sample/
uv run equity arena run --lake data/sample \
  --start-date 2025-01-02 --end-date 2026-06-30       # 3 strategies × 3 cost regimes vs benchmark
```

The seeded lake carries canonical instrument identity and a synthetic
corporate-action coverage proof, so the arena's point-in-time gate passes without
any network access (see [ADR-0020](docs/decisions/0020-synthetic-demo-lake-as-backtestable-surface.md)).
Runtime is dominated by the point-in-time rebuild — every decision date is
re-evaluated — so widen the window deliberately, not by default.

```mermaid
flowchart LR
    Seed["make demo<br/>seed sample lake"] --> Arena["equity arena run<br/>--lake data/sample"]
    Arena --> FC["FindingCards<br/>verdict + evidence"]
    Arena --> NB["Strategy Lab<br/>notebook"]
    FC --> P2["Phase 2: ML comparison<br/>XGBoost vs LightGBM"]
    P2 --> P3["Phase 3: hosted<br/>Findings surface"]
```

### Synthetic rows in the local lake

The canonical lake (`data/lake/`) may hold synthetic rows produced by the demo
and preload workflows, not only vendor ingestion. Two markers that already exist
make that visible, and `uv run equity doctor` reports them under
`synthetic_provenance`:

- `02_silver/corporate_action_coverage` rows carry `source = synthetic_demo_seed`;
- `03_gold/features` rows carry `action_coverage_source = synthetic_demo_seed`.

Bronze price rows are **not** distinguishable: they carry
`identity_provenance = current_ticker_config`, the same label a real
config-resolved ingestion writes. For that reason `doctor` reports `synthetic`,
`empty`, or `unknown` — never `real` — when it cannot prove real ingestion.

Before real ingestion, return to an empty lake with the supported reset target:

```bash
make reset-lake DRY=1   # lists data/lake/{01_bronze,02_silver,03_gold,04_platinum/predictions} and data/models
make reset-lake         # destructive: asks for confirmation, refuses unless the lake is synthetic or empty
```

`make reset-lake` calls `uv run python -m equity_lake.devtools.reset_lake`
(`--dry-run`, `--yes` for non-interactive use); nothing else removes the
synthetic artifacts deterministically.

<details><summary><b>How to read this repo</b></summary>

The data platform below is the **supporting credential**; the **findings are the
lead narrative**. The 3-month roadmap turns this into a hosted showcase — see the
[portfolio roadmap](docs/plans/20260804-portfolio-roadmap.md) and
[Phase 1 handoff](docs/plans/20260804-portfolio-phase1-handoff.md).

</details>

### Phase 2A — ML rigor (public W&B)

The [`equity ml`](docs/user-guide/20260810-ml-rigor.md) harness adds three more
FindingCards and mirrors every comparison to a **public** Weights & Biases
project, with one Report per comparison:

- **W&B project:** <https://wandb.ai/howt51/equity_lake>
  - [meta-label-vs-direction × xgb-vs-lgbm — Report](https://wandb.ai/howt51/equity_lake/reports/meta-label-vs-direction-xgb-vs-lgbm--VmlldzoxNzcwMzYxNA==)
  - [enrichment-ablation — Report](https://wandb.ai/howt51/equity_lake/reports/enrichment-ablation--VmlldzoxNzcwMzU4OQ==)
- `ls data/findings/` → **6** cards (strategy / cost / benchmark +
  meta-label-vs-direction / xgb-vs-lgbm / enrichment-ablation)

Verdicts are honest single-ticker (AAPL), short-window out-of-sample comparisons
— negatives included. Each card's `scope` (ticker / windows / folds) is the
reproducibility contract; see the W&B Reports for the full metrics.

---

**Underlying platform** — a local-first equity pipeline: bootstrap historical data,
append daily market updates across markets, run a three-stage ingestion → features
→ ML pipeline, and query the lake with DuckDB.

## What It Does

- Bootstraps historical data from S3 into a local Parquet lake
- Appends daily EOD data across supported equity markets
- Runs a three-stage pipeline: ingestion, features, and ML
- Exposes local analysis, monitoring, signals, backtesting, and dashboard workflows through one `equity` CLI

## Pipeline

```mermaid
flowchart LR
    S3["S3 historical parquet<br/>bootstrap"] --> Lake["Local lake<br/>data/lake/01_bronze..04_platinum"]
    APIs["Market data APIs<br/>yfinance, akshare, others"] --> Ingest["equity ingest"]
    Ingest --> Lake
    Lake --> Query["equity query<br/>DuckDB on Delta/Parquet"]
    Lake --> Features["Feature engineering"]
    Features --> ML["ML inference"]
    ML --> Signals["Signal scan / backtesting"]
    Lake --> Monitor["Health monitoring"]
    Lake --> Dashboard["Static + Streamlit dashboard"]
    Monitor --> Dashboard
    Signals --> Dashboard
```

## Quick Start

### Prerequisites

- Python 3.12 or 3.13
- [`uv`](https://github.com/astral-sh/uv)
- [`dotenvx`](https://dotenvx.com/) for commands that rely on `.env`
- AWS CLI or `s5cmd` if you want to bootstrap from S3

### Install

```bash
uv sync
cp .env.example .env
```

Core defaults live in `config/settings.yaml`. Environment overrides use the `EQUITY_` prefix.

### Verify The CLI

```bash
uv run equity --help
uv run equity ingest --help
uv run equity pipeline --help
```

### Common Workflows

Bootstrap from S3:

```bash
dotenvx run -- uv run equity sync --bucket s3://your-bucket
```

The bucket is a root URL whose remote tree mirrors the numbered medallion layout without
a `data/lake/` prefix — each market is pulled from
`<bucket>/01_bronze/market_data/<market_dir>`.

Run daily ingestion:

```bash
dotenvx run -- uv run equity ingest
dotenvx run -- uv run equity ingest --markets us,cn --date 2026-06-06
```

Run the full pipeline:

```bash
dotenvx run -- uv run equity pipeline
dotenvx run -- uv run equity pipeline --dry-run --verbose
dotenvx run -- uv run equity pipeline --markets us --tickers AAPL,MSFT,NVDA
```

Inspect data quality and query results:

```bash
dotenvx run -- uv run equity monitor --output-json site/health-report.json
dotenvx run -- uv run equity query --query latest_summary
```

Build or serve the dashboard:

```bash
dotenvx run -- uv run equity dashboard build --output-dir site
dotenvx run -- uv run equity dashboard serve --port 8501
```

## Canonical CLI

The supported interface is the unified Typer app:

```bash
uv run equity --help
```

Key commands:

- `equity ingest`
- `equity pipeline`
- `equity query`
- `equity monitor`
- `equity signal scan`
- `equity backtest`
- `equity demo seed` — seed the showcase lake (synthetic/offline-safe)
- `equity arena run` — strategies × cost regimes → FindingCards
- `equity report backtest` — serialize a backtest to report artifacts
- `equity ml compare` — XGBoost vs LightGBM comparisons mirrored to W&B
- `equity forecast` — price forecasting (train/predict/backtest)
- `equity api serve` — read-only FastAPI over the data lake
- `equity dashboard build`
- `equity dashboard serve`

## Data Layout

The local lake follows a numbered medallion architecture with Hive-style date partitions:

```text
data/lake/
├── 01_bronze/
│   ├── market_data/
│   │   ├── us_equity/date=YYYY-MM-DD/*.parquet
│   │   ├── cn_ashare/date=YYYY-MM-DD/*.parquet
│   │   ├── hk_sg_equity/date=YYYY-MM-DD/*.parquet
│   │   ├── jpx_equity/date=YYYY-MM-DD/*.parquet
│   │   └── krx_equity/date=YYYY-MM-DD/*.parquet
│   ├── macro/
│   └── raw_articles/
├── 02_silver/
│   ├── news_sentiment/
│   ├── social_sentiment/
│   ├── analyst_ratings/
│   ├── processed_articles/
│   ├── sec_extractions/
│   └── sec_financials/
├── 03_gold/
│   └── features/
└── 04_platinum/
    └── predictions/
```

DuckDB queries run directly on these Parquet files.

## Docs Map

- [Getting Started](docs/getting-started/quickstart.md): first install and first run
- [Pipeline Guide](docs/user-guide/pipeline.md): pipeline stages, config, monitoring, scheduling
- [CLI Reference](docs/user-guide/20260406-cli-reference.md): config, loader, update, and dashboard commands
- [Signals Guide](docs/user-guide/signals.md): watchlists and signal outputs
- [Backtesting Guide](docs/user-guide/backtesting.md): strategy workflows
- [Dashboard Hosting](docs/user-guide/20260406-dashboard-hosting.md): static site build and Pages flow
- [API Keys And Credentials](docs/20260406-api-keys.md): optional integrations and secret setup
- [Architecture](docs/developer/architecture/ARCHITECTURE.md): system design and module boundaries
- [Project Structure](docs/developer-guide/project-structure.md): package layout and contributor orientation
- [Documentation Index](docs/README.md): entry point for the full docs tree

## Project Structure

```text
src/equity_lake/
├── cli/          Unified Typer CLI
├── ingestion/    Market ingestion orchestration
├── storage/      DuckDB, parquet, Delta, S3 sync
├── features/     Feature engineering
├── ml/           Forecasting and model workflows
├── signals/      Signal generation and formatting
├── backtesting/  Strategy execution and analysis
├── dashboard/    Static export and Streamlit app
└── core/         Runtime, paths, logging, config
```

## Notes

- The CLI is local-first after bootstrap; it does not require a long-running cloud service.
- China ingestion currently defaults to the shipped `akshare` path.
- Static hosting is generated from local artifacts; GitHub Pages is the documented deployment target.

## License

MIT License.
