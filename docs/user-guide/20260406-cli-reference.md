# CLI Reference

This beta build adds a few new operational commands on top of the existing ingestion and pipeline CLIs.

## Config

```bash
uv run equity config validate
uv run equity config show
uv run equity config get schedule.cron
uv run equity config export
```

Use this to inspect the unified application settings from `config/settings.yaml`.

## Dashboard

```bash
dotenvx run -- uv run equity dashboard build --output-dir site
```

This generates:

- `site/index.html`
- `site/datasets.html`
- `site/health.html`
- `site/updates.html`
- `site/config.html`
- `site/dashboard-data.json`

## Loaders

```bash
uv run equity loader list
uv run equity loader show yfinance
uv run equity loader show options_flow
uv run equity loader test yfinance
```

Built-in loaders now include:

- `yfinance`
- `reddit_sentiment`
- `sec_filings`
- `options_flow`

## ML Rigor

The `equity ml` group runs the Phase 2A ML comparisons and ablations that
write FindingCards to `data/findings/`. See the [ML Rigor guide](20260810-ml-rigor.md)
for the full workflow; the commands are:

```bash
# Labeling (v1 vs v2) + backend (XGBoost vs LightGBM) comparison
#   -> meta-label-vs-direction.json, xgb-vs-lgbm.json
dotenvx run -- uv run equity ml compare  --universe demo

# Feature-enrichment ablation (enriched vs technical-only)
#   -> enrichment-ablation.json
dotenvx run -- uv run equity ml ablate   --universe demo

# Train one backend classifier (mirrors `intelligence forecast --mode train`)
dotenvx run -- uv run equity ml train --ticker AAPL --backend lightgbm --model-mode v2_meta_label
```

- Requires feature history under `03_gold/features`; if missing the commands
  exit non-zero and point at `equity pipeline --allow-history-backfill`.
- `--universe demo` runs the **first** ticker of the group (AAPL); override with
  `--ticker`. `--backend` selects `xgboost` (default) or `lightgbm`.
- Weights & Biases runs/Reports are logged best-effort when `WANDB_API_KEY` is
  set; local cards are always written.

## Existing Pipeline Commands

```bash
dotenvx run -- uv run equity ingest --markets us,cn,hk_sg
dotenvx run -- uv run equity pipeline --save-results
dotenvx run -- uv run equity monitor --output-json site/health-report.json
```

Recommended hosted flow:

1. Run `equity monitor` to export health.
2. Run `equity dashboard build` to render the static site.
3. Let GitHub Actions deploy `site/` to GitHub Pages.
