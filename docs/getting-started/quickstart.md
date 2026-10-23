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
cp .env.example .env      # optional env overrides; do not commit secrets
```

Optional dependency groups are installed only when you need those workflows:

```bash
uv sync --group dashboard    # Streamlit dashboard UI
uv sync --group backtesting  # VectorBacktestEngine integration
uv sync --group ml           # optional ML tooling, including LightGBM
```

`dotenvx` is a separate executable; `uv sync` does not install it. Install it
using the [dotenvx setup guide](https://dotenvx.com/docs), or set required
provider variables in your shell/secret manager and run a scoped operational
command directly. The synthetic demo is offline, requires no provider
credentials, and writes only to `data/sample/`; see the safe first-run step
below.

## Verify

```bash
uv run equity --help
uv run equity ingest --help
uv run equity pipeline --help
uv run equity query --help
```

## Safe Offline First Run

The repository demo is synthetic and offline. It writes sample artifacts only;
it does not fetch providers, run LLM processing, send webhooks, or execute
orders:

```bash
uv run equity demo seed
```

This quickstart intentionally contains no live-ingestion or deployment command.
Use provider-fetching and production workflows only after separate owner
authorization and review of their explicit scope.

## Notes

- A built-in dashboard ships with the repo. Use `equity dashboard build` for
  the static site or `equity dashboard serve` for the local Streamlit view.
- China ingestion defaults to `akshare`; `efinance` exists in code but is not
  active by default.
- `config/settings.yaml` is the default app config. `EQUITY_*` values in
  `.env` override it.
- The dry-run pipeline performs no writes, backfills, feature generation, or
  inference. Missing feature history requires the explicit
  `--allow-history-backfill` option. Dry-run is not a provider or deployment
  authorization.

## Next Reading

- [Pipeline Usage](../user-guide/pipeline.md) — full command reference, config, scheduling
- [Signals Guide](../user-guide/signals.md) — signal scanning and generation
- [Private tracking guide](../user-guide/20261016-private-tracking-preview.md) — canonical local watchlists, readiness and disabled-by-default opportunity delivery
- [Backtesting Guide](../user-guide/backtesting.md) — strategy testing
- [Project Structure](../developer-guide/project-structure.md) — package layout and import policy
