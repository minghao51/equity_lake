# Dashboard Hosting

Static-first, and **not published by default**. Two local surfaces exist and only one of
them has a publication path:

| Surface | Built by | Publication |
| --- | --- | --- |
| Sanitized demo-lake page (H00) | `equity dashboard h00-build` | one manual workflow, owner-approved |
| Canonical dashboard (`index`, `datasets`, `health`, `updates`, `config` pages) | `equity dashboard build` | none — local review only |

## Local Build

The canonical dashboard is a local review surface; it reads the local lake, health output
and update history:

```bash
dotenvx run -- uv run equity monitor --output-json site/health-report.json
dotenvx run -- uv run equity dashboard build --output-dir site
```

Open `site/index.html`, `site/datasets.html`, `site/health.html`,
`site/updates.html`, `site/config.html`. These files are **not** a publication
artifact: they carry absolute paths, health alert text and run metadata (review blockers
B1-B4), so no workflow uploads them any more.

The public artifact is the sanitized three-file H00 page instead:

```bash
uv run equity demo seed                 # if the synthetic demo lake is not seeded yet
uv run equity dashboard h00-build -o dist
uv run equity dashboard h00-preview     # loopback only: http://127.0.0.1:8765/
```

The artifact is exactly `index.html`, `payload.json` and `sanitization-report.json`;
`equity dashboard h00-build` composes them in memory, runs the negative scan, writes the
report inside the artifact and leaves nothing behind if the scan fails. The build and
`h00-preview` never publish: preview binds a loopback address only (owner decision 3 of
[the H00 owner decisions](../reports/20261121-h00-owner-decisions.md)).

## The single publication path

`.github/workflows/h00-publish.yml` is the **only** workflow in this repository that can
publish anything. It:

1. is triggered by `workflow_dispatch` only — no `push:`, no `schedule:`;
2. runs `uv sync --group dev`, builds the sanitized artifact into `dist` with
   `equity dashboard h00-build -o dist`;
3. verifies the bytes that are about to be public with
   `uv run python -m equity_lake.devtools.verify_h00_release --dir dist` — exact
   allowlist, no symlinks, size bounds, path containment, and a fresh negative rescan
   reusing the builder's needles, so a canary inserted after the build fails the job;
4. uploads only `dist` with short artifact retention, and deploys from a `deploy` job
   that is the only holder of `pages: write` / `id-token: write`;
5. pins every `uses:` to a full commit SHA with a version comment, and joins the single
   shared `github-pages` concurrency group with `cancel-in-progress: false`.

Two legacy deployers are **retired** and cannot publish:

- `.github/workflows/pages.yml` is deleted. It built the canonical dashboard plus a
  health report and deployed them, and it also ran a `sync_schedule --check` step whose
  target cron no longer existed, so the job failed before the deploy step anyway.
- `.github/workflows/catalog-deploy.yml` became
  `.github/workflows/catalog-site-build.yml`: the catalog site still builds (a compile
  check) but has no Pages permissions, no Pages artifact upload and no deploy job.
  `.github/workflows/catalog-check.yml` (catalog generation drift) is unchanged.

`devtools/sync_schedule.py` was retired with the workflow that called it: there is no
workflow cron to keep in sync because **no workflow deploys on a schedule**.

## What the repository cannot prove by itself

- **Environment protection is a platform setting.** The deploy job declares
  `environment: github-pages`, which is only a name. Whether that environment has
  required reviewers, who they are, and whether it allows this branch must be
  **verified in the repository settings** (Settings → Environments → `github-pages` →
  required reviewers / deployment branches), and the verification has to be recorded in
  the stage ledger with a date. Workflow YAML is not evidence of protection, and neither
  is this page.
- **Publication needs explicit owner approval.** Nothing here authorizes a publish. The
  owner's standing condition (owner decision 3, 2026-11-21) is that build and preview
  stay local; dispatching the publisher is a separate, deliberate act by the owner.
- **A route change needs an ADR first.** H00 owning the sole Pages root — and the catalog
  site and dashboard no longer appearing there — is a public-route change: it requires
  an accepted ADR recorded *before* the first publish. No ADR is created or accepted by
  this stage.
- **Prerequisites still open.** The publisher builds from the canonical synthetic demo
  lake (`data/lake/`), which a fresh checkout does not have, so it fails closed until the
  disposable-bootstrap stage seeds it in the job. Rollback bundles (the last three
  verified sanitized releases kept in access-controlled storage, plus the private
  artifact↔commit deployment receipt) are also not implemented yet.

## Notes

- The dashboard is intentionally static for beta.
- The repository has no hosted scheduler. Host scheduling, if an operator wants it, runs
  on the operator's host, not on GitHub Actions; GitHub Actions cron expressions are UTC
  and nothing in this repository ships one by default.
- The site is generated from local synthetic demo-lake data only. No vendor row, no
  private ledger, watchlist or `.env` value is publishable, and the scan enforces it.
