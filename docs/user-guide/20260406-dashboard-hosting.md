# Dashboard Hosting

The current beta hosting path is static-first and GitHub Pages friendly.

## Local Build

```bash
dotenvx run -- uv run equity monitor --output-json site/health-report.json
dotenvx run -- uv run equity dashboard build --output-dir site
```

Open:

- `site/index.html`
- `site/datasets.html`
- `site/health.html`
- `site/updates.html`
- `site/config.html`

## GitHub Pages

The Pages deployment is defined in:

- `.github/workflows/pages.yml`

The workflow does three important things:

1. Verifies the workflow cron expression matches `config/settings.yaml`.
2. Builds the dashboard artifact.
3. Deploys the `site/` folder to GitHub Pages.

GitHub Actions scheduled cron expressions use **UTC**. The sync utility copies
the configured expression; it does not convert `schedule.timezone` or apply a
local-time offset. The dashboard labels this hosted cron as UTC. For a local
host cron, use the host scheduler's timezone instead.

## Schedule Sync

`config/settings.yaml` is the canonical source for the hosted schedule.

To sync or verify locally:

```bash
uv run python -m equity_lake.devtools.sync_schedule
uv run python -m equity_lake.devtools.sync_schedule --check
```

## Notes

- The dashboard is intentionally static for beta.
- GitHub Actions is the hosted scheduler.
- The site is generated from local lake data, exported health output, and update history.
