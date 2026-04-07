# Dashboard Hosting

The current beta hosting path is static-first and GitHub Pages friendly.

## Local Build

```bash
uv run equity-monitor --output-json site/health-report.json
uv run equity-dashboard build --output-dir site
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

1. Verifies the workflow cron matches `config/settings.yaml`.
2. Builds the dashboard artifact.
3. Deploys the `site/` folder to GitHub Pages.

## Schedule Sync

`config/settings.yaml` is the canonical source for the hosted schedule.

To sync or verify locally:

```bash
uv run python scripts/sync_pages_schedule.py
uv run python scripts/sync_pages_schedule.py --check
```

## Notes

- The dashboard is intentionally static for beta.
- GitHub Actions is the hosted scheduler.
- The site is generated from local lake data, exported health output, and update history.
