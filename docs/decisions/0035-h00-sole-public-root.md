# ADR-0035 — H00 owns the sole public root

**Status:** **Accepted (2026-11-24)** by the owner's explicit approval to publish. It fixes
*what* may be public and *how* it gets there; it authorizes no data acquisition, no
additional public route and no automated publication.

**Recorded:** 2026-11-24

**Supersedes:** the implicit assumption in the earlier plans that the dashboard and the
catalog site could share the Pages site. **Preserves:** ADR-0030 (root isolation), ADR-0015
(split-only adjustment), ADR-0018 (outbox), ADR-0024 (ledger) and ADR-0031 (backup) without
change.

## Context

The repository ended up with two workflows publishing to the same Pages site: the dashboard
workflow (`pages.yml`, dashboard plus a health report) and an independent catalog deployer
(`catalog-deploy.yml`, the MkDocs catalog build). Neither published the H00 sanitized page,
and the dashboard workflow additionally ran a schedule-sync check whose cron had already been
removed, so it failed closed on every dispatch. "Publish" therefore had no single meaning:
two artifacts competed for one root, one of them was unverified, and the artifact the owner
actually wants public — the three-file sanitized demo page — was not in either path.

H00 already exists as a sanitized artifact with its own allowlist, containment, size and
negative-scan checks (`dashboard/h00.py`), and `devtools/verify_h00_release.py` re-proves
those properties on the exact bytes about to be uploaded. What was missing was a decision
about which of the competing surfaces is public.

## Decision

1. **The public surface is the H00 artifact alone.** It is exactly three files —
   `index.html`, `payload.json`, `sanitization-report.json` — composed by
   `equity dashboard h00-build` from the canonical synthetic demo lake. No dashboard, no
   health report, no catalog site, no private book, no real-source slice and no operator
   metadata may appear on any public route.
2. **One publishing path, manual only.** `.github/workflows/h00-publish.yml` is the only
   workflow holding `actions/deploy-pages`. It triggers on `workflow_dispatch` and nothing
   else: no `push`, no `schedule`. No default schedule is deployed anywhere.
3. **Least privilege is scoped to the deployment.** The workflow is `contents: read`;
   `pages: write` and `id-token: write` exist only on the `deploy` job; `actions: read`
   exists only on the build job because the rollback path downloads this repository's own
   retained bundle. Every `uses:` is pinned to a full commit SHA.
4. **Environment protection is a platform fact, not a YAML fact.** The deploy job declares
   `environment: github-pages`, and the required-reviewer protection must be verified in the
   repository settings with the evidence recorded in the stage ledger. The workflow file is
   not evidence of protection.
5. **Both legacy deployers are retired from publishing.** `pages.yml` is deleted; the catalog
   workflow became a build-only check (`catalog-site-build.yml`) with no Pages artifact, no
   deploy step and no Pages permissions. The catalog remains buildable and reviewable; it is
   simply not published.
6. **Rollback is by retained, verified bundle.** Each publication writes a private
   deployment receipt binding the three files' sha256 digests to the commit, retains the
   receipt with the verified bundle for 90 days in repository-scoped CI artifact storage, and
   can redeploy a chosen earlier bundle after re-verifying it against that receipt. The
   receipt is never written into the public directory, because the allowlist is exactly three
   files. If no clean recoverable bundle exists, the site is withdrawn rather than rebuilt
   from private inputs.
7. **Freshness must survive elapsed viewing time.** The published page shows its stale banner
   once the artifact is older than 30 days even if it was fresh when built, so a static page
   cannot quietly present year-old numbers as current.
8. **Any further route change needs its own ADR.** Adding a route, publishing a second
   artifact, or exposing the catalog site again is a new decision, not a configuration
   tweak.

## Consequences

- Publication becomes a deliberate, owner-initiated act with exactly one artifact and one
  reviewer-gated environment. There is no path by which a merge or a cron can publish.
- The dashboard and the catalog site stop being public. Anyone who relied on the catalog URL
  loses it; that is the intended trade, and the catalog remains available locally and in CI.
- Rollback depends on retained CI artifacts. GitHub retains artifacts by age, not by count,
  so 90-day retention is configured but pruning down to "the last three" stays an operator
  action rather than automation. Losing the artifacts loses rollback, not correctness: the
  next publication rebuilds from tracked source.
- The receipt is private by placement, not by encryption. Anyone with repository read access
  can read CI artifacts, which is the same audience that can read the commit history.
- A first publication still depends on the disposable synthetic lake being seeded in the job,
  which is RS-E01's bootstrap; until that is wired, the build fails closed rather than
  publishing a substitute.

## Alternatives considered

- **Keep the dashboard public and add H00 as a second route.** Rejected: two unverified
  surfaces, more public bytes, and no single artifact to roll back.
- **Keep the catalog site public alongside H00.** Rejected: it is a second content surface
  with its own drift, and it makes "the public site" ambiguous again.
- **Publish from a `push` trigger on tracked changes.** Rejected: publication is an owner
  decision, and a push-triggered publisher cannot be reviewer-gated in practice.
- **Rebuild-on-rollback from tracked source instead of retaining bundles.** Rejected: a
  rebuild produces different bytes than the reviewed release, so a "rollback" would deploy
  something never verified.
- **Put the receipt inside the public directory as a fourth file.** Rejected: it breaks the
  three-file allowlist and would publish digests and operator metadata, which the
  sanitization allowlist forbids.

## References

- `docs/plans/20261122-01-residual-spec-and-implementation-plan.md` §2.1, §2.3, §2.4, RS-H01
- `.github/workflows/h00-publish.yml`, `.github/workflows/catalog-site-build.yml`
- `src/equity_lake/dashboard/h00.py`, `src/equity_lake/devtools/verify_h00_release.py`
- `docs/user-guide/20260406-dashboard-hosting.md`
- [ADR-0030](0030-second-real-source-lake-root.md), [ADR-0031](0031-p00-4-ledger-backup-restore-migration-compaction.md)
