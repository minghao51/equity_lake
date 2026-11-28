# ADR-0038 — Retain recoverable H00 rollback bundles

**Status:** Proposed (owner review; no deletion authorized).

**Recorded:** 2026-11-27

## Context

[ADR-0035](0035-h00-sole-public-root.md) configures 90-day retention and leaves count-based
pruning to the operator. The [rollback rehearsal](https://github.com/minghao51/equity_lake/actions/runs/46021167248)
redeployed an existing release, so three recent runs need not represent three distinct recovery
points. Age-based expiry can leave fewer than three. Only the exact-name Actions artifact
`h00-release-bundle` from the H00 publisher is in scope: three sanitized synthetic-demo
files plus a receipt. Neither ML/processed data nor the `github-pages` artifact is a target.
See [H2–H3 in the detailed specification](../plans/20261127-03-operational-and-research-specification.md#2-h00-publication-and-retention)
for the rollback receipt repair and report-only inventory that precede any cleanup.

## Options

- **Age only:** no extra permissions or housekeeping; old copies can accumulate until expiry.
- **Read-only inventory plus manual deletion:** makes recovery points visible and lets the
  owner approve exact candidates if there is a retention or quota reason.
- **Automatic last-three cleanup:** lower operator effort, but needs deletion authority and
  can lose useful rollback copies if it ranks by run count, before deploy, or during a rollback.

## Decision (recommended)

Keep 90-day expiry. If housekeeping is needed, start with a read-only inventory; do not add
deletion permissions to the publisher or enable scheduled cleanup. Protect the active release
and at least the three most recent **distinct, receipt-verified, non-expired deployed
releases**, including active if it is one of those three. Count copies by the verified public
file digests, not by run number; protect the only readable copy of each protected release
and any owner-pinned recovery point. If there are fewer than three distinct releases,
no active-release mapping, incomplete inventory, or a pending publish/rollback, do not
delete. Keep the Pages artifact and unrelated workflow artifacts out of scope.

The owner would separately approve exact retained-bundle artifact IDs. Immediately before
any deletion, recheck expiry, protected copies and workflow state in a window with no
concurrent publish or rollback. Start with fixtures for duplicate bytes, failed or pending
deploys, unknown active mapping and impending expiry.

## Consequences

Rollback remains manual, reviewer-gated and fail-closed under ADR-0035. Three is a pruning
floor, not a guarantee against platform expiry. This draft neither changes the workflow
nor grants permission to delete an artifact.
