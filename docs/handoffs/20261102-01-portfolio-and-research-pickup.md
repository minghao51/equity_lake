# Next Thread — Portfolio and Research Implementation Pickup

**Recorded:** 2026-11-02
**Status:** Planning handoff; implementation scope/contracts await owner approval.
**Start here:** [Implementation specification and stage queue](../plans/20261102-01-portfolio-and-research-implementation-spec.md).
**Evidence:** [Tracked A01–A11 audit and counterexamples](../finding/20261102-product-readiness-audit.md).
**Preserved history:** [Oct31 closure/R12 brief](20261031-01-next-thread-and-r12-watch.md).

## 1. Owner direction

The owner wants to use this product for personal equity portfolio monitoring
and raw data → structured data → optional ML signals. They explicitly consider
operational ML refitting premature before strategy/ML utility and backtesting
scale are established. Keep the seven-day freshness bound; do not create a
freshness/refit enhancement just because it was next in an older handoff.

The present request was to **draft** specifications and hand off implementation
to another thread with workers and independent reviewers. No correctness fix,
ledger, source integration, experiment, live operation or new ADR was authorized
by the act of drafting. Ask for approval of the next bounded stage plan, not an
unqualified mandate to implement the whole roadmap.

## 2. Baseline: preserve the dirty tree

Last committed baseline at drafting: `00dad39` on `main`. Existing carried work:

- Accepted `docs/decisions/0023-writer-side-string-view-drift.md` and ADR index row.
- `features/engineering.py` routes Delta price reads through `read_delta`, preserving
  date pushdown and applying string filters in Polars. No writer/migration change.
- `tests/unit/test_feature_jobs.py` and `test_feature_loader_string_view.py` pin
  pushdown policy and a merge-rewritten Bronze read; mutation proof was captured.
- Oct30/Oct31 handoffs record containment and historical gates.
- `.gitignore` excludes `.agent-progress/` for sanitized local worker checkpoints.

New drafting files are this handoff, the linked specification and tracked audit,
plus continuation links in `docs/README.md` and the Oct31 handoff. They may still
be untracked/uncommitted at pickup. Inspect actual state rather than assuming
this inventory is current. No runtime lake/model data belongs in a commit;
commit generated catalog changes only when the change matrix requires them,
produced by the generator rather than hand edits.

**B00 first:** read the diff and classify carried code vs new planning docs. Obtain
an owner-approved baseline/commit strategy. Do not discard work to get clean, do
not use blanket staging, and do not mix ADR-0023 with an unrelated repair without
explicit approval. If commits are not authorized, record a local stage-start
patch/hash/file snapshot and diff against it for review. Treat untracked source
and docs as part of that snapshot. Workers never commit; coordinator commits only
with owner permission. Accepted ADR-0023 is immutable; future status/evidence goes
in the implementation ledger/handoff, not by editing its decision text.

## 3. What was proved, and what was not

Historical ADR-0023 tree: serial and parallel each **1739 passed / 17 skipped**;
ruff/format/mypy clean, generated catalog unchanged, Markdown lint passed. These
are historical counts, not a fresh check of the pickup tree.

Read-only audit: **78 existing focused tests passed**, yet four separate
counterexamples reproduced. Full source/reproductions are in the tracked audit;
ignored `.agent-progress/` and `/tmp` logs are optional context only.

| Stage | Old behaviour that must fail the regression | Proposed target requiring owner approval |
| --- | --- | --- |
| F01 | A 100→200→100 and B 100→100→100 gives benchmark 112.5 from initial 100 | Fixed initial shares: terminal 100, full identity, explicit missing-mark failure |
| F02 | Ticker-major vs date-major input gives different momentum weights | Sorted, instrument-local return/skip/fill, own-history warmup, input-order invariance |
| F03 | Sharpe -1→-2 generates positive/“turnover is low” cost card | Negative signed cost effect; missing data inconclusive; no invented turnover |
| F04 | No completed arena runs exits 0 | Zero or partial requested-run failure exits 1; other runs still execute |

There was no independent scout report (delegation aborted). No real provider,
private account reconciliation, deployment, capacity benchmark or profitable ML
claim was established. `current_usable` means row-contract consistency, not
alpha, calibrated profit probability, or actionability of old predictions today.

## 4. First implementation session

1. Read `AGENTS.md`, this handoff, the linked audit/specification, relevant accepted
   ADRs and actual source/test bodies. Check current tree and environment.
2. Present B00 and the F01–F04 frozen contract choices for approval. Highlight
   conservative complete-panel benchmark policy, signed cost-delta policy,
   partial-run exit 1 and unchanged strategy defaults. If the owner wants different
   semantics, revise the draft before any worker edits code.
3. Implement only F01 first, then F02, F03, F04 in separate reviewed stages. Keep
   one writer on shared files; don't dispatch a “fix everything” worker.
4. After that tranche, ask which useful branch to take: P00 portfolio-accounting
   ADR, D00 offline provider feasibility, or E00 research-evidence ADR. Do not
   silently continue into private data, production ingestion or training.
5. Full gates on the frozen integrated tranche, then update stage outcomes and
   obtain owner acceptance/commit permission. A green helper test alone is not done.

### Open decisions that must not become implicit defaults

- Portfolio broker/CSV format, selected market/base currency, accounting/basis,
  correction/split policies, storage/privacy/backup contract (P00).
- Immutable research layout/latest/read compatibility and actual snapshot binding
  (E00). Next free ADR number must be checked, not assumed to be 0024.
- Real-data provider feasibility, source proof and licensing/spend/target-root
  authorization (D00/D01). Do not fabricate expiry or relabel synthetic lake data.
- Experiment hypothesis, tradable timing, holdout, statistical acceptance and
  resource budgets (R00–R02). Evaluating ML must not alter operational refit policy.
- Public routing/artifact producer/privacy (H00). No deployment just to show a demo.

## 5. Worker/reviewer operating model

Use **separate subagent invocations/contexts** for implementation and adversarial
review. The coordinator owns the parent task list, approvals, shared-tree safety,
mutation drills and final verification. A worker cannot approve its own stage.
Do not start another writing worker because a long tool call has not returned.

### Check presets before dispatch

At drafting, the local `worker` preset was a general implementation worker;
`reviewer` and `codex-luna-reviewer` both demanded that `git status` be clean on
exit. That conflicts with dirty-tree review and preservation of carried changes.
Do **not** dispatch an incompatible preset and hope a lower-priority brief fixes
it. Select an available independent reviewer-capable preset without that
instruction, or ask for approval to configure one. A general worker preset may
serve a separate strictly read-only reviewer role if its instructions allow it.
Do not edit global agent definitions or install project agents as a side effect.
Check model/provider availability and actual agent scope; do not hardcode the
model names from a historical thread. Failure to launch is a blocker, not review.

### Checkpoints, bounded stages and interruption recovery

Allocate a unique file per invocation:
`.agent-progress/portfolio-research/<YYYYMMDD>-<stage>-<role>-<attempt>.md`.
The directory is ignored by `.gitignore`; verify with `git check-ignore`.
Never store credentials, real account balances/holdings/notes or raw transcripts.
Tracked stage outcomes carry durable evidence; checkpoints are local scratch and
will not exist in another clone. Do not make the handoff depend on them.

The dispatch brief must require an initial checkpoint **before extensive work**,
then an update after each meaningful stage and before a long command. Fields:
status (`started`, `in_progress`, `blocked`, `interrupted`, `completed`), scope,
files actually read/changed, last completed step, exact command/exit, evidence
paths, blocker and next action. If a preset/tools prohibit checkpoint writes,
require a short checkpoint response and have the coordinator persist it; never
bypass a read-only restriction through bash. Each discovery dispatch should be
bounded to one specified deliverable and return before beginning implementation.

On abort/no report: mark that attempt interrupted, inspect its checkpoint and
session-owned process children, and distinguish live process from actual progress.
Do not kill unrelated sessions, invent completed work or blindly retry the whole
parallel batch. Re-dispatch only a bounded missing step, or continue directly
while recording that independent review is still owed. Tool/runtime timeout
support must be verified; do not invent an unsupported timeout parameter.

### Copyable worker brief

> Implement **STAGE_ID only**, after the coordinator confirms approval and
> dependencies from `docs/plans/20261102-01-portfolio-and-research-implementation-spec.md`.
> Read AGENTS.md, accepted ADRs, matching skills, current source and tests first.
> Frozen contract: **PASTE THE ENTIRE SELECTED STAGE CONTRACT AND TEST MATRIX HERE**.
> Allowed product paths: **EXPLICIT FILE LIST**. Carried/unrelated paths: **LIST**.
> No new abstraction/dependency/ADR acceptance, provider calls, real account access,
> lake/model writes, live delivery, deployment, operational fitting or commits.
> Use temporary synthetic fixtures. Do not reset/stash/checkout others' changes.
> Present your minimal file plan and stop on undecided policy. After approval,
> add named regressions, implement minimally, run only agreed focused tests and
> capture logs. Do not manage the parent's todo list or start a full suite.
> Checkpoint: **UNIQUE IGNORED PATH**; save initial/stage/blocker progress, or
> return a checkpoint for the coordinator if your tool policy prohibits writes.
> Return files/symbols changed, compatibility, exact tests, known gaps and any
> deviations. Do not claim completion or independent acceptance from your own tests.

### Copyable independent reviewer brief

> Independently review **STAGE_ID** against the frozen contract below, not against
> the worker's explanation. Read the stage-start snapshot and the dirty stage diff,
> including new/untracked files. The tree contains owner/previous-stage changes;
> preserve them byte-for-byte. No source/test/doc edits, commits, cleanup or restores.
> Frozen contract: **PASTE THE SAME COMPLETE STAGE CONTRACT**.
> Stage paths/diff identity: **LIST**. Existing carried changes: **LIST**.
> Falsify each acceptance rule with numeric oracles, adversarial edge cases and
> non-vacuous assertions. Trace call sites/readers/CLI exits. Look for hidden
> missing→zero/neutral conversion, identity collapse, future data, false success,
> loss of old evidence and private-data exposure. Do not access real private state,
> providers or network; coordinate any focused test so no pytest overlaps.
> Checkpoint: **UNIQUE PATH if tools/preset permit**, otherwise return progress
> for the coordinator to persist. Return verified issues with severity/path/line,
> reproduction, contract violated, tests/gaps and a clear pass/blocked conclusion.
> Do not fix issues or approve a policy that the owner has not accepted.

### Review, mutation and completion loop

Worker → independent reviewer → worker resolves findings → reviewer rechecks
changed behaviour → coordinator serializes mutation proofs → focused checks →
full frozen-tree gates → owner acceptance/authorized coordinator commit.

A mutation backs up the **current dirty version** to `/tmp`, removes one rule,
proves a named test fails, restores from that copy and verifies `diff -q`. If
restore fails, stop everything and recover before another agent runs. No worker
or reviewer runs pytest while another run or mutation is active. Review blockers
stay open; a checkpoint saying completed is not independent evidence of success.

## 6. Verification and deliverable packet

Use specification §9 for commands/change-matrix requirements. Install the
backtesting group for engine-backed checks. Capture serial and parallel suite
output to separate files/JUnit and run them sequentially. Serial is mandatory
(R13 was serial-order-only). Full gates last; reuse captured summaries.

Each stage packet must include scope/approval, source diff identity, tests that
fail on the old rule, mutation+restore evidence, reviewer verdict and resolved
issues, actual command/counts, compatibility/docs/catalog status, residuals and
next entry gate. Records belong in the specification's stage outcome log or a
linked dated tracked handoff; no secrets/private data or giant raw logs.

Drafting validation is Markdown/link/diff checks, not a rerun of Python suites
or regeneration of the canonical lake. Implementation must revalidate its actual
changed code. Missing independent reviewer access must be stated as blocked,
not silently replaced by coordinator self-approval.

## 7. Paste this into the next thread

```text
Pick up docs/handoffs/20261102-01-portfolio-and-research-pickup.md.
Read its linked implementation specification and tracked A01–A11 audit first.
Preserve the dirty ADR-0023 baseline; do B00 and present the F01–F04 minimal
implementation plan/frozen policy choices for my approval before changing code.
After approval, use one bounded worker per stage and a separate compatible
adversarial reviewer on each dirty diff. Require ignored per-attempt Markdown
checkpoints, named regression/mutation evidence, and serialized pytest.
Start with F01; do not implement portfolio/storage/source ADRs, access private
accounts, ingest real data, refit models or deploy without their separate gates.
```
