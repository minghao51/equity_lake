# Prediction Incident Follow-up: New-Agent Handoff

**Prepared:** 2026-10-20
**Repository baseline:** `main`, HEAD `0786e43a0d1c5f16bd4934e382599583bb87cae2`
**Purpose:** Hand off the three open incident follow-ups. This is a plan, not authorization to change code or data.

## Historical continuation snapshot — 2026-10-21

This handoff preserves its initial pending state as a historical snapshot. Current status is in the [durable follow-up update](20261021-prediction-follow-up-status.md), updated 2026-10-22: I02 and I03 implementation/offline-contract acceptance is unchanged; the owner-authorized exact-file backup/isolated-copy verification and two-row targeted I01 delete are complete. The single operation advanced v14→v15 and left zero active files; historical Delta files remain, and row origin, source actor, and MIC remain unknown. No further mutation is planned. The initial pending/proposal language below remains historical and is superseded by that current status. Historical Oct18 incident facts and C00–C10 acceptance scope remain unchanged.

## Start here and preserve the baseline

Read `AGENTS.md`, [the documentation map](../README.md), [the incident report](../finding/20261018-prediction-test-write-investigation.md), this handoff, and [the current findings register](../finding/20261016-repository-review-and-equity-alerts.md). Then inspect the actual relevant source, tests, contracts, and accepted ADRs before proposing any code change. Do not restart C00–C10, repeat the original repository audit, replay old decisions, or discard reviewed work.

At the start of this documentation task, runtime fields were checked before reading reports:

```text
PI_PROVIDER=openai-codex
PI_MODEL=gpt-6-luna
PI_REASONING_LEVEL=xhigh
```

They matched the requested Luna `xhigh` runtime. Recheck them for every new substantive worker; stop on a mismatch or unavailable model, with no fallback. The observed environment fields are not independent provider-routing telemetry.

The worktree was already extensively dirty at HEAD `0786e43` (`main...origin/main`), with tracked and untracked documentation, source, test, catalog, and configuration work. Preserve all existing tracked and untracked work; do not reset, clean, stash, or replace it with a HEAD-only checkout. The incident report, findings register, and 2026-10-16 implementation handoff were already untracked at task start. This record does not assert that any Oct18 production-table state is still current on Oct20.

## Incident facts and limits — inspection was 2026-10-18 only

The authorized inspection covered only the default `data/lake/04_platinum/predictions` table. It found AAPL and MSFT rows for `2024-01-02`, each with `direction=up`, `probability=0.8`, `model_mode=unknown`, and `model_version=fake`. The earliest retained version v0 already had those fake markers and `feature_schema_version=4`; v6–v14 had null schema version. All retained commits predate the cited passing test suites beginning around 20:10 local time. The writer, attribution, pre-v0 state, and any external use remain unknown; absence of a genuine retained predecessor does not prove no genuine predecessor ever existed. Do **not** say the table remains at v14 on Oct20 without a new separately authorized inspection.

The prior serial **1,533**, parallel **1,533**, and inspected integration **50** passes are historical code/test evidence for their tested scope. They are not fresh gates for future changes and do not prove production integrity. C00–C10 implementation/offline-contract acceptance remains historical and valid for that scope; post-closure findings **I01–I03 remain open**. The scoped C04/R14 raw API boundary is reopened for follow-up, without erasing the earlier reviewed work.

## Initial 2026-10-20 follow-ups and dispatch ledger (historical; superseded by the current status update)

Recommended order: **I02 raw API correctness first; I03 contract review before any containment fix; I01 remediation proposal only, with execution separately approved.**

| ID | Status | Bounded next work | Deliverable and acceptance |
| --- | --- | --- | --- |
| I02 — raw predictions API | Pending read-only source/contract plan; any behavior change is approval-gated | Re-read `src/equity_lake/api/deps.py:46–80` and `src/equity_lake/api/routers/predictions.py:16–24` (accepted finding references; verify current lines). Resolve policy against ADRs/owner before selecting response behavior. | Bounded implementation proposal; if approved, isolated API unit/integration tests for the cases below, deterministic bounded reads, and relevant CLI/docs contract. Unknown/invalid rows are explicit and not usable as current qualified predictions, while legitimate history remains visible. |
| I03 — Delta-maintenance path contract | Pending bounded contract review; a fix is not presumed | Re-read the current `cli/commands/data.py` resolver and `storage/delta.py` join, plus maintenance docs/contracts and accepted ADRs. Establish intended named and full-path routes. | Evidence-based decision and safe temporary-directory tests for relative/absolute/symlink escapes and legitimate routes. Close as documented intentional behavior if warranted; any accepted-boundary or compatibility change requires policy approval/ADR first. |
| I01 — fixture-row remediation | Proposal only; execution separately approval-gated | Draft a narrow, auditable option for the exact matched row fingerprints, without claiming origin is known. | Proposal includes authorization point, current-version check, backup and verification plan. No Parquet deletion, blanket reset, retraining, backfill, cleanup, or schema migration. |
| Optional observations | Not approved implementation | Consider only as a separate bounded follow-up if evidence and approval warrant it. | No expansion into another source audit or live inspection. |

### I02 — raw API correctness

The accepted source finding points to `api/deps.py` and `api/routers/predictions.py`: raw `GET /predictions` can return legacy/fixture records without an identity/provenance status. Its default no-date path materializes the full Delta table before ticker filtering and the returned-row limit. These are source findings, not proof of an HTTP call or consumer use. Re-read actual source and tests; do not rely on old line numbers.

Plan for legitimate historical visibility while making unknown or invalid identity, provenance, or schema explicit and unusable as current qualified predictions. Do not infer MIC/market, or substitute a hard-coded fake-marker blacklist for provenance. The built-in ML signal path calls its forecaster on demand; do not assume it consumes this predictions table. Cover canonical valid rows; unknown legacy identity/provenance/schema; fixture rows; the same symbol across venues; and deterministic, bounded read behavior. Resolve any response-breaking/API-policy choice against existing ADRs and the owner rather than predetermining it. Include relevant existing CLI/docs contract tests where applicable.

### I03 — generic Delta-maintenance path contract

The concern is a local operator CLI path-containment risk in the `cli/commands/data.py` resolver plus a plain path join in `storage/delta.py`; a remote exploit or production use was not demonstrated. Full-path support may be intentional. Read the applicable docs/contracts first. Preserve intended named routes and approved auxiliary Delta tables: not every valid table is cataloged under `data/lake`.

Propose tests with temporary directories for relative paths, absolute paths, symlink escapes, and legitimate routes. Never run vacuum, compact, or other maintenance against real tables. Do not change accepted path or compatibility boundaries without policy approval and an ADR. A documented-intended-behavior closure is acceptable if evidence supports it; do not invent a mandatory security fix.

### I01 — remediation proposal only

If proposing action, limit it to an auditable quarantine/remediation of the exact matched fixture-row fingerprints, and require explicit authorization, current-version checks, and a backup/verification plan before execution. Never delete Parquet files, reset the whole table, retrain, backfill, clean up broadly, or migrate schema. Unknown origin need not be falsely resolved to complete a scoped investigation. Give any new diagnostic artifact a typed write boundary where applicable.

## Optional bounded observations

These are not new approved implementation tasks: a legacy signals-history read may migrate data; an earlier temporary inspector could exit successfully after scan errors; and textual plan checks did not prove physical predicate pushdown. Do not claim the earlier runs all exited nonzero. If an inspector is reused, preserve original evidence and use a new copy with fail-closed validation and explicit recorded-result checks. Do not run API, server, or history readers against live data for inspection; reads may have side effects.

Evidence pointers are `/tmp/equity-incident-{trace,inspection,history}-report.md`, `/tmp/equity-incident-review-independent-20261018/review.md`, and `/tmp/equity-incident-final-readonly-review-ePIsxM`. They may not exist on another host. The durable incident report is a summary; do not invent missing evidence or make a global-clean claim. Continue in the same worktree: a HEAD-only checkout loses the uncommitted implementation. If the same worktree/evidence required for the task is unavailable, stop for an authorized transfer.

## Authorization, review, and validation gates — initial 2026-10-20 snapshot (historical)

The owner approved the scoped **read-only investigation** and now requests **handoff preparation**. Current authority is:

| Activity | Status |
| --- | --- |
| This documentation handoff | Requested and in scope. |
| Bounded read-only I02/I03 planning and I01 remediation proposal | Next task requested; source/policy review only, with no data access or writes. |
| New code/behavior changes | Not approved by this document. Present a bounded plan first per `AGENTS.md`; obtain approval for the plan and any genuinely new policy/scope decision. |
| Production-data remediation, maintenance, migrations, retraining, provider calls, paid processing, delivery, scheduler activation, deployment, commits, and push | Not authorized by this handoff. |

Do not repeat the prior blanket approval checklist; seek only genuinely new policy/scope decisions. The missing paid-cost adapter and native action-source proof remain honest operational gates—not proofs to fabricate or providers to call.

For substantive work, use Luna `xhigh` and separate read-only reviewers. Recheck the resolved agent definition: frontmatter may override a model argument. Capture starting and ending worktree hashes for reviewer scope and require them to be identical; reviewers do not fix findings. A prior documentation-review worker edited despite a read-only assignment, so use a fresh independent reviewer rather than treating that review as self-approved.

Before executing tests, inspect their fixtures and setup. The historical `/tmp/equity-continuation-offline.sh` wrapper was described as hiding live data for tests; verify it still exists and inspect its protections before use. If missing or insufficient, stop; recreate an equivalent sandbox only with approved scope—never run naked `pytest`. Use isolated temporary fixtures only; no paid, network, or live-data tests. After approved source changes, run focused tests first, then required broader gates serially and parallel **sequentially**, preserving complete logs and JUnit. Run fresh `source=lsp` diagnostics on changed paths, Ruff, format check, strict source mypy, docs build, and diff check. Generate the catalog only if catalog/schema changes warrant it; never edit JSONL manually. For this docs-only handoff, no pytest or application run is needed; the parent will run the docs build/link check and obtain a read-only review. Only a scoped `git diff --check` is appropriate here.

## Copyable new-agent starter prompt (historical; superseded by the current status update)

```text
Continue the prediction incident follow-ups in the existing Equity Lake worktree.
First verify PI_PROVIDER=openai-codex, PI_MODEL=gpt-6-luna, and
PI_REASONING_LEVEL=xhigh; stop on mismatch with no fallback. Recheck the
resolved agent definition because frontmatter can override a model argument.
Read AGENTS.md, docs/README.md,
docs/plans/20261020-prediction-incident-follow-up-handoff.md, and
docs/finding/20261018-prediction-test-write-investigation.md first. Preserve
all dirty tracked/untracked work; do not reset, clean, stash, or use a HEAD-only
checkout. Use the Oct18 table facts only as historical inspection results.

Do not restart C00-C10, repeat the repository audit, or inspect production
data. Start with a bounded read-only I02/I03 plan based on current source,
contracts, ADRs, and tests, and an I01 remediation proposal only. I02 is first;
review I03 policy before any containment fix. Include isolated test cases and
resolve API policy with ADRs/owner without predetermining a breaking response.
Do not execute code or production changes until a bounded plan is approved;
production remediation/maintenance and all other operational actions remain
separately unauthorized. No live API/history readers, maintenance, providers,
paid work, retraining, migration, deployment, commit, or push. Keep reviewers
separate and read-only with identical before/after worktree hashes. Stop and
request authorized transfer if required worktree/evidence is missing.
```
