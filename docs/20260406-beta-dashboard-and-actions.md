# Beta Dashboard And Actions

Equity Lake now has a simpler beta-oriented hosting path:

- Runtime settings live in `config/settings.yaml`.
- Environment overrides use the `EQUITY_LAKE_*` prefix.
- Static dashboard output is generated with `uv run equity-dashboard build`.
- GitHub Pages deployment is handled by `.github/workflows/pages.yml`.
- Scheduled runs and file-change publishing are both driven by GitHub Actions instead of local cron or file watchers.

Recommended beta workflow:

1. Keep `config/tickers.yaml` as the source of ticker scope.
2. Keep `config/settings.yaml` focused on runtime paths, dashboard output, and schedule defaults.
3. Use GitHub Actions schedules for hosted refreshes.
4. Use push-based GitHub Actions deployments for dashboard/code changes.
5. Avoid adding a live web server until the product surface is more stable.

Useful commands:

```bash
uv run equity-monitor --output-json site/health-report.json
uv run equity-dashboard build --output-dir site
uv run equity-pipeline --continue-on-error --save-results
```
