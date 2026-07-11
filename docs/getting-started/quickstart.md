# Quick Start

Fastest path to a working local install.

## Prerequisites

- Python 3.12 or 3.13
- `uv`
- Git

## Install

```bash
git clone <your-repo-url>
cd equity-lake
uv sync
cp .env.example .env      # optional env overrides
```

## Verify

```bash
uv run equity --help
uv run equity ingest --help
uv run equity pipeline --help
uv run equity query --help
```

## First Run

```bash
# Dry run first
dotenvx run -- uv run equity pipeline --dry-run --verbose

# Real run
dotenvx run -- uv run equity pipeline --verbose
```

## Notes

- A built-in dashboard ships with the repo. Use `equity dashboard build` for
  the static site or `equity dashboard serve` for the local Streamlit view.
- China ingestion defaults to `akshare`; `efinance` exists in code but is not
  active by default.
- `config/settings.yaml` is the default app config. `EQUITY_*` values in
  `.env` override it.
- The dry-run pipeline performs no writes, backfills, feature generation, or
  inference. Missing feature history requires the explicit
  `--allow-history-backfill` option.

## Next Reading

- [Pipeline Usage](../user-guide/pipeline.md) — full command reference, config, scheduling
- [Signals Guide](../user-guide/signals.md) — signal scanning and generation
- [Backtesting Guide](../user-guide/backtesting.md) — strategy testing
- [Project Structure](../developer-guide/project-structure.md) — package layout and import policy
