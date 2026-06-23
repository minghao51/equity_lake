# Roadmap Coverage

This file records how the current beta implementation maps to `docs/technical_roadmap.md`.

## Implemented In Code

### Phase 1: Configuration

- Unified runtime settings live in `config/settings.yaml`.
- Environment overrides are supported through `src/equity_lake/config/settings.py`.
- Config inspection is available through `uv run equity-config`.
- GitHub Actions schedule drift is checked with `uv run python -m equity_lake.devtools.sync_schedule --check`.

### Phase 2: Plugin Architecture

- Loader abstractions live in `src/equity_lake/loaders/base.py`.
- Loader discovery and registration live in `src/equity_lake/loaders/registry.py`.
- A built-in `yfinance` loader lives in `src/equity_lake/loaders/yfinance_loader.py`.
- Loader inspection and testing are available through `uv run equity-loader`.

### Phase 3: Smart Updates

- Update history persistence lives in `src/equity_lake/updates/history.py`.
- Smart update strategies live in `src/equity_lake/updates/engine.py`.
- CLI access is available through `uv run equity-update`.
- GitHub Actions remains the hosted scheduler by design for beta.

### Phase 4: Data Quality

- Existing monitoring and schema checks remain the project’s beta data-quality path.
- `equity-monitor` is the primary operational surface for freshness and null checks.
- The static dashboard surfaces exported health output.

### Phase 5: Feature Store

- Existing feature generation and persisted parquet outputs remain the active feature-store path in beta.
- The repo does not add Hamilton or Feast yet because that would materially increase complexity for the current phase.

### Phase 6: Dashboard

- The roadmap’s dashboard goal is implemented as a static GitHub Pages artifact instead of a live Next.js app.
- Static export lives in `src/equity_lake/dashboard/exporter.py`.
- Hosting is wired through `.github/workflows/pages.yml`.

### Phase 7: Alternative Data

- The repo already contains news and sentiment ingestion paths.
- SEC filings and options-flow specific loaders are not added in this pass.

## Intentional Beta Deviations

- GitHub Pages replaces a live app server.
- GitHub Actions replaces local cron and file-watch driven hosted automation.
- Existing monitoring and feature flows are used where the roadmap suggested much heavier frameworks.

## Next Best Enhancements

1. Add SEC filings and options-flow loaders behind the new plugin registry.
2. Add richer dashboard pages generated from the same static export pipeline.
3. Add a stronger validation layer if beta data-quality pain shows up in practice.
