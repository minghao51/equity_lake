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

## What a fresh clone does not have

- **No lake and no artifacts.** `data/*` is git-ignored, so `data/lake/`,
  `data/lake_real/`, `data/research/` and `data/portfolio/` exist only where
  something wrote them. Seed the synthetic demo with `uv run equity demo seed`;
  a real-source root is re-landed with the ADR-0028/0030 commands under its own
  authorization.
- **No provider credentials.** The provider seams in `.env.example`
  (`FRED_API_KEY`, `FINNHUB_API_KEY`, `DEEPSEEK_API_KEY`, `WANDB_*`, `AWS_*`,
  `STOCKTWITS_*`, `REDDIT_*`) ship empty; those commands are simply unavailable
  until a recorded authorization fills them.
- **Optional settings are genuinely optional.** Unset `EQUITY_CONSOLE__PRIVATE_DIR`
  renders the private console panels `unavailable` and attempts no private read;
  unset `EQUITY_MONITORING__*` falls back to the `config/settings.yaml` defaults.
  A missing key is not a broken install.

For the full gate set, sync all three groups in one command — syncing
`--group dashboard` alone removes the backtesting group and changes the skip
count (16 → 39):

```bash
uv sync --group dev --group backtesting --group dashboard
```

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

Seeding or preloading the canonical lake (`data/lake/`) leaves synthetic rows in
the tables real ingestion writes, and Bronze rows are not distinguishable from
real ingestion (`identity_provenance = current_ticker_config` is the same label).
`uv run equity doctor` reports the existing Silver/Gold coverage markers under
`synthetic_provenance`, and `make reset-lake` (with `make reset-lake DRY=1` to
list only) is the supported way back to an empty lake before real ingestion.

This quickstart intentionally contains no live-ingestion or deployment command.
Use provider-fetching and production workflows only after separate owner
authorization and review of their explicit scope.

## Fresh-Clone Profiles

A fresh clone has exactly three ways to get a lake. There is no implicit fourth path:
nothing creates a lake on import, on test collection, from an environment flag such as
`CI=true`, or from a marker file.

### (a) Sample lake - the default

`uv run equity demo seed` writes the synthetic demo to the auxiliary sample lake
(`data/sample/`), offline and without provider credentials. This is the default and the
safe first run above.

### (b) Canonical synthetic CI lake - disposable

H00 and CI need a canonical `data/lake/` whose rows carry the synthetic provenance
envelope. Build it in a throwaway checkout of the **tracked** source instead of your
own checkout:

```bash
uv run python -m equity_lake.devtools.bootstrap --end-date 2026-11-20 --keep
```

The command prints a JSON report: `allocation` is the fresh OS temporary directory it
owns and `lake_dir` is that checkout's canonical `data/lake/`. `--end-date` is
required because it pins the window; the same seed plus the same end date produces
identical logical data on any wall-clock day (the seed otherwise ends yesterday). On an
empty disposable runner the same guarantee is available directly with
`uv run equity demo seed --lake data/lake --overwrite-production-lake --end-date
2026-11-20`.

The bootstrap refuses to run unless the destination is a fresh, empty directory
resolving beneath its own allocation, the checkout contains tracked source only (no
`.env`, no ignored runtime data), the importing module is that checkout, and the
destination is not (and does not contain) the operator lake. Provider credentials are
stripped from the child process; optional/provider-backed datasets are reported
unavailable with a reason rather than stubbed. Build the H00 artifact from the reported
allocation, then delete it:

```bash
ALLOC="<reported allocation>"
PYTHONPATH="$ALLOC/checkout/src" uv run --project "$PWD" equity dashboard h00-build --output-dir "$ALLOC/h00"
rm -rf "$ALLOC"
```

### (c) Real-root restore/re-land - operator only

Re-landing real-source data is an explicit operator action, never implicit. It writes
the sibling root `data/lake_real/`, which ADR-0030 allows to be selected only by an
explicit `--lake-dir`: it is never cataloged, never mixed with the synthetic
`data/lake/`, and never created implicitly. Nothing in this quickstart selects it; use
the ADR-0028/0030 ingestion and re-land commands under their own authorization.

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
