# Remaining Repository Scope: New-Thread Implementation Handoff

**Date:** 2026-10-16
**Owner direction:** Implement the remaining review scope in a new thread using GPT 6 Luna subagents at `xhigh`.
**Status:** C00–C10 implementation and offline-contract review accepted on 2026-10-18 after independent C10 review. This is not live operational readiness.
**Findings:** [Repository review and resolution status](../finding/20261016-repository-review-and-equity-alerts.md)

## Historical next-task snapshot — 2026-10-20

This handoff's former next-task direction is a historical snapshot, not active instructions. The [2026-10-20 prediction incident handoff](20261020-prediction-incident-follow-up-handoff.md) retains its original plan and pending/proposal language as history. Use the [canonical prediction follow-up status](20261021-prediction-follow-up-status.md), updated 2026-10-22, for current I01–I03 status; it records the completed targeted I01 cleanup and its limits. The C00–C10 closure and Section 8 starter below are historical context, not an active implementation dispatch.

## Incident status recorded 2026-10-18 13:20 UTC

The requested read-only investigation is authorized and completed; see the
[dated incident report](../finding/20261018-prediction-test-write-investigation.md).
The runtime was observed as `openai-codex/gpt-6-luna`, `xhigh`, with no fallback
reported or indicated in exposed metadata. Only the default
`data/lake/04_platinum/predictions` table was in production-data scope. Its
retained v0–v14 history already contains two fixture-marked AAPL/MSFT rows for
2024-01-02 at earliest retained v0; the record establishes neither which
process wrote them nor any prior real-row replacement. The saved ~20:10 JUnit
suite starts postdate all retained commits. Independent review accepted the
bounded row/history findings with tooling caveats: do not claim exact physical
predicate pushdown, table cleanliness, complete corpus audit, a specific cause,
or external consumer non-use.

**Current open items:** I01 fixture rows/origin/remediation unknown; I02 raw
predictions API reader-boundary validity/identity/provenance gap (scoped C04/R14
follow-up reopened; prior implementation acceptance retained); and I03 a
source-only generic Delta-maintenance path-containment risk requiring contract
review. Full-path support may be intentional; no probe or production use was
observed. R18 training/inference provenance
acceptance does not close the raw API boundary. The prior C10 acceptance and
serial **1,533**, parallel **1,533**, and mocked integration **50** pass counts
remain valid historical evidence for their code/test scopes; they were not
rerun for this incident update and are not data-integrity proof. Read-only investigation is complete; all repair/quarantine/deletion,
rollback, migration, training, live/provider/paid work, maintenance, commits,
and publishing remain unauthorized and unperformed. Older paragraphs below
that say the investigation/inspection was unauthorized or had not occurred are
point-in-time records superseded by this current status and Section 7 update.
Initial failures and acceptance logs below remain historical and are not erased.

> **Historical C00–C10 closure:** [Section 7](#7-dispatch-ledger) records the accepted packet ledger, final gate evidence, independent reviewer verdict, and operational/safety limits as of 2026-10-18. For the active next task, use the [2026-10-20 incident follow-up handoff](20261020-prediction-incident-follow-up-handoff.md). The original dispatch plan and Section 8 starter prompt below are historical, not active instructions.

## Historical owner approval received — 2026-10-17

The owner approved **“1-9 and C10 implementation”.** ADR-0013 and ADR-0015
through ADR-0018 record the accepted dependency, adjustment/availability,
instrument identity, bounded evolution/replay, and private watchlist/event/
delivery contracts. Their full criteria govern implementation; this approval
is not evidence that implementation is complete or independently reviewed.
C10's synthetic integrated journey, independent reviews, and full offline gates
remain required. This documentation-only update ran no implementation tests
and does not assert a fresh smoke/test result. **At this historical approval
snapshot**, the possible synthetic AAPL/MSFT prediction write was unresolved and
investigation, inspection, cleanup, and rollback had not been approved. The
separate read-only investigation authorization/completion and current limits
are recorded under [Current post-closure incident status](../finding/20261018-prediction-test-write-investigation.md).
Operational bans on live delivery/provider calls, paid processing, production
migration, repair, backfill, retraining, deployment, commits, and push remain in
force.

## 1. Start here

Continue in `/home/howt/work/equity_lake` using the **existing worktree**. Read
`AGENTS.md`, [the documentation map](https://github.com/minghao/equity_lake/blob/main/docs/README.md), the linked report, accepted
ADRs, and relevant canonical contracts/skills before implementation. Do not
repeat the repository audit or redo the completed operational batch. Revalidate
individual open findings against current source before editing them.

The original review baseline is `0786e43a0d1c5f16bd4934e382599583bb87cae2`.
At handoff, Phase A changes are **uncommitted**, including new untracked tests
and the findings document. That commit alone does not contain the working
implementation. Inspect `git status --short`, `git diff`, and untracked files;
preserve existing changes. Do not reset, clean, stash, auto-commit, or create a
fresh HEAD-only checkout that drops this baseline. If the next thread has a
different checkout, stop and establish an authorized baseline transfer first.

The owner has requested the rest of the scope, not merely another plan. Use a
coordinator to present each bounded implementation plan, delegate substantive
scouting/implementation/review, integrate reviewed changes, and maintain the
ledger below. Do not repeatedly seek approval for routine in-scope repairs;
stop only at unresolved policy, boundary/migration, or operational gates.

## 2. Mandatory model and agent contract

Every substantive scout, implementer and independent reviewer must use:

- **Provider/model:** `openai-codex/gpt-6-luna`
- **Reasoning:** `xhigh`
- A separate invocation/context for implementation and independent review.

Confirm the resolved agent definition and reasoning setting before dispatch,
and record dispatch parameters plus available runtime model metadata. A model
argument alone is not proof: this harness lets agent frontmatter override it.
If the requested model is unavailable/rate-limited, preserve progress and
report a blocker; do not silently substitute another model or reasoning level.

### Available agent definition and a trap to avoid

At preparation time, the user-scoped `assay-luna-worker` definition pins
`openai-codex/gpt-6-luna`. Use it for implementation and, with explicit
read-only role instructions, separate scout/reviewer invocations. Recheck its
resolved definition in the new thread rather than assuming it is unchanged.

The user-scoped **`reviewer` agent currently pins
`opencode-go/mimo-v2.6-pro`**. Do not dispatch it expecting a per-call Luna
argument to override that pin. Do not edit global agent definitions without
permission. An existing correctly pinned agent with a role-specific task is
sufficient; a new agent framework is unnecessary.

Example single dispatch using the existing tool's fields:

```json
{
  "agent": "assay-luna-worker",
  "agentScope": "user",
  "model": "openai-codex/gpt-6-luna",
  "thinking": "xhigh",
  "cwd": "/home/howt/work/equity_lake",
  "task": "Implement only C01's approved subtask. Read the handoff and current source first. Obey the assigned file ownership, safety gates and required delivery packet. Do not commit, publish, access live providers, send notifications or modify production data."
}
```

For a scout/reviewer invocation, replace the task with **READ-ONLY** instructions.
Reviewers must leave the starting worktree byte-identical: no fixes, mutation
drills, stash/restore experiments, commits, notifications, or production writes.
Their job is to falsify completion claims using source inspection and safe tests.
Do not demand a clean worktree: the baseline is deliberately dirty; demand no
reviewer-created delta instead.

## 3. Preserve the completed batch

Keep these fixes and their regressions intact:

| Finding | Completed behavior to retain |
| --- | --- |
| R01 | Redacted settings/dashboard output and safe health-webhook outcome/retry logs; muted HTTP wire debug/info logs |
| R02 | Sequential ticker scanning; legacy `max_workers` accepted without unsafe workers |
| R03 | Computed unhealthy reports persisted before exit 1; cleanup, nested output paths, truthful empty-check status |
| R04 | Default HK/SG scope is the active `hk` + `sg` union; explicit/scheduling semantics preserved |
| R10 | Signal dry-run prints a plan without generator construction, inference, runtime/model directories or report/history writes |
| R13, partial | Compose daily is one-shot, query invocation is valid, S3 default is a medallion bucket root |

Important new baseline tests:

- `tests/unit/test_settings_redaction.py`
- `tests/unit/test_signal_scan_safety.py`
- `tests/unit/test_monitor_cli_safety.py`
- `tests/unit/test_pipeline_scope_safety.py`
- `tests/unit/test_compose_contracts.py`
- `tests/unit/test_cn_hybrid_safety.py`

Also preserve the updated alerting/unified-CLI tests, guarded CN fallback,
shared redaction helper, guides, report and navigation changes. Prior
verification: **1171 passed / 17 skipped** in each offline serial/parallel suite,
plus **2 passed** dashboard-export integration tests; Ruff/format/mypy passed.
These counts describe the handoff tree, not a target to freeze by deleting tests.

## 4. Historical implementation scope and work packets (pre-closure plan)

The remaining scope includes **all open or partially addressed R findings and
the watchlist/delivery/private-dashboard product work**. The C packets below
are a continuation namespace, not the older DAG audit's W waves. The historical
DAG handoffs may contain related work; reuse current implementations and ADRs
rather than executing both plans independently.

| Packet | Findings / work | Dependencies and acceptance focus |
| --- | --- | --- |
| C00 | Preflight and safe test selection; open R19 | Capture dirty baseline and resolved model settings. Ensure fast/default CI excludes network tests explicitly, and include format checks. Keep an independently authorized network suite. Establish deterministic temporary data/model paths. |
| C01 | R07–R09; signal input-validation part of R20 | Selected custom config reaches v2 candidates; effective threshold/source agrees with execution and explanation. Explicit per-instrument/generator outcomes distinguish no-condition, unavailable/stale input, missing model, error and not-scheduled. Preserve partial diagnostics; all-required-input failure exits nonzero. Zero-event runs remain observable. |
| C02 | R05 and R18 | Per-refit labels used by fitting **and threshold selection** are matured/available at that decision cutoff; purge crossing event intervals. Record actual refit boundary, candidate/config provenance, training/inference schema compatibility and model age/expiry. Fix next-day horizon metadata. Do not claim alpha from passing tests. |
| C03 | R06 and R15 | Implement accepted [ADR-0015](../decisions/0015-point-in-time-adjustment-and-predictor-availability.md): shared split-only read-time adjustment, fail-closed action/vintage handling, and versioned missing-aware predictors across consumers. No raw-Bronze rewrite or automatic historical repair. |
| C04 | R11 and R14 | Implement accepted [ADR-0016](../decisions/0016-canonical-instrument-identity-and-migration.md): composite market/MIC/symbol identity and provenance-only unique legacy compatibility. C10 covers all five markets; a US initial configuration is not a scope deferral. |
| C05 | R16 and R17 | Implement accepted [ADR-0017](../decisions/0017-bounded-additive-evolution-and-replay.md): native nullable additive Delta evolution with OCC and bounded retries, plus explicit, bounded, idempotent replay/checkpoint behavior. No default paid processing or historical sends. |
| C06 | Remaining R13; operational/security/QoL residuals | Deliberate noninteractive API startup policy without weakening unauthenticated-exposure protection. Fix `make demo`, dead examples, configuration-validation scope/warnings, onboarding optional-group instructions, timezone claims and stale decision status. Carry forward R01's provider-exception/logging residuals with evidence; do not claim a universal secret scrubber. |
| C07 | Watchlist management/readiness; R12 and R20 | Implement [ADR-0018](../decisions/0018-private-watchlist-events-and-delivery.md): private YAML CRUD, groups/tags/notes/zones, exact-session readiness, 20-valid-bar SMA20, no automatic backfill, watching-not-held semantics, and approved re-arm/cooldown behavior. |
| C08 | Durable signal events and delivery; R12 | Implement ADR-0018's separate disabled-default generic opportunity webhook, durable local event/outbox states, retries (not exactly-once), readiness-first daily target, quiet hours/revalidation, independent stale-run warnings, and no scheduler activation. Auxiliary Pydantic artifacts, not cataloged lake tables. |
| C09 | Private Streamlit/API tracking experience; R12 | Implement ADR-0018's loopback-only readiness, explanations/conflicts, history, and delivery views. Notes, holdings, and credentials stay out of public exports/default notifications; watch-only SELL is caution and held SELL is a candidate, never an order. |
| C10 | Integrated closure; remaining R19/R20 and every open ID | Approved, but not implemented or reviewed. Deterministic add-equity → readiness → scan → event → mocked delivery → private view journey across the approved scope; include repeats/changes, failures, stale data, retries, session edges, no-side-effect dry-run, all five markets, independent review, and full offline gates. |

### Acceptance detail that must not be lost in packet splitting

- **C01:** Do not treat logging warnings as coverage reporting. A missing model
  or broken query is not a valid neutral result. Keep history-reader
  compatibility and deliberately extend metadata whitelists when needed.
  Document threshold precedence rather than silently choosing a different
  trading policy. ADR-0013 is accepted; implement the explicit one-way seam and boundary enforcement.
- **C02/C03:** An inner train/validation embargo does not prove outer test-fold
  availability. Pending right-edge events must not become shortened mature
  labels. Adjustment, release time and model-schema compatibility must be
  consistent across training, inference, repair and backtests. Document limits
  when historical vintage data are unavailable instead of inventing them.
- **C04:** UI labels alone do not repair ticker-only shared keys. Use temporary
  copied datasets to test collisions and legacy readers; no production rewrite
  without approval. Do not infer market from an ambiguous bare ticker.
- **C05:** An atomic Delta commit alone is not proof that a multi-step
  read/transform/overwrite cannot lose an intervening writer. Include explicit
  concurrency/snapshot tests. Do not solve replay by suppressing real errors.
- **C07/C08:** Price-zone/simple technical conditions first; distinguish
  persistent states from crossings. Conflicting generators remain explainable.
  Fixed rule scores are not calibrated probabilities. Repeated state should
  not create needless notifications; re-armed/changed state may notify.
- **C08/C09:** Show both opportunity and failure/readiness information. A quiet
  scan that did not evaluate its watchlist must not read as a healthy day.
  Health webhooks remain separate from investment-opportunity notifications.

### Recommended order and parallelism

1. Run C00 preflight. Scouts may inspect C01, C02, C05 and C06 concurrently.
2. Implement C01, C02, C05 and C06 as small reviewed slices **only when their
   exact file ownership is disjoint**. Shared `forecasting.py`, signal models,
   CLI registration, storage and documentation require serialization.
3. Apply accepted ADR-0015/0016 contracts in C03/C04 using collision, reader,
   compatibility, and rollback evidence on synthetic fixtures and temporary
   copies. Production operations remain separately gated.
4. Build C07, then C08 and C09 against accepted ADR-0016/0018 contracts. An
   initial US configuration is permitted, but C10 must implement and test the
   approved identity/session/supported signal paths for all five markets.
5. Finish the full remaining scope through C10. Approved scope is not complete
   until implementation, independent review, and required offline gates pass.

The coordinator owns integration, shared documentation/CLI registration and
catalog regeneration. Assign every worker allowed/forbidden files and tests;
stop/reassign if ownership overlaps. Exactly one catalog generator per wave.
Never run the large serial and parallel suites concurrently: that previously
caused native-thread contention/timeouts.

## 5. Accepted implementation contracts and remaining operational gates

The owner approved **“1-9 and C10 implementation”** on 2026-10-17. The
accepted ADRs define implementation policy; they do not authorize live or
production operations. Use synthetic fixtures, temporary artifacts, and mocked
clients unless a distinct operational authorization is recorded.

| Accepted contract | Separate operational gate that remains |
| --- | --- |
| ADR-0013 one-way `signals` → `ml`, explicit candidate injection, import-boundary enforcement, on-demand retained | None for implementing/testing the contract; no unrelated scope expansion. |
| ADR-0015 split-only shared read-time transform, fail-closed adjustment and source availability, missing-aware versioned predictors, selected family age/coverage rules, compatible-model requirement | No production adjustment migration, historical repair/backfill, or retraining. |
| ADR-0016 composite `(market, MIC, symbol)`, provenance-proven unique legacy reads, explicit symbol-change mapping, all five markets through C10 | No production data/model migration or repair. Initial US configuration is not a scope deferral. |
| ADR-0017 native nullable additive evolution/OCC and bounded explicit replay | No production migration/replay or paid processing. Default paid authorization is zero; paid work still requires separate explicit actual-spend cap and approved estimate. |
| ADR-0018 private watchlists, deterministic candidates, disabled-default opportunity webhook, schedule support, loopback views | No live delivery, scheduler activation, financial-provider calls, or broker execution. |
| Synthetic C10 journey, independent reviews, full offline gates | Completed and reviewed on 2026-10-18; implementation/offline-contract acceptance is not live operational readiness. |
| Prediction-table incident | **Historical status (superseded by the [canonical prediction follow-up status](20261021-prediction-follow-up-status.md), updated 2026-10-22):** Bounded read-only investigation was separately authorized and completed on 2026-10-18; see the [incident report](../finding/20261018-prediction-test-write-investigation.md). Fixture-marked rows were confirmed but origin/pre-v0 state and any replaced real row were unknown. At that historical point, cleanup, quarantine, restore, rollback, migration, or other remediation remained unauthorized. |
| Commits, push, publishing | Not authorized. |

Follow existing Settings/env conventions. New `EQUITY_<GROUP>__*` options need
nested Pydantic fields and `.env.example` changes together. Raw SDK credentials
stay at client seams, not in Settings. Never read actual `.env` files or expose
credentials in an agent prompt, report, test fixture or public artifact.

No new services, generic plugin/scheduler framework or frontend rewrite by
default. Use existing registries, persistence seams, Typer apps, Streamlit and
read API. New auxiliary artifacts belong under `data/<name>/`, have Pydantic
write boundaries, and are not pointblank-validated/cataloged lake datasets.

## 6. Verification and required delivery packet

Read test fixtures before calling a test offline. Default markers currently
need the C00 repair; directory names alone are not network isolation. Preserve
full logs/JUnit once, then inspect saved summaries. For a known failure rerun
its tests with `-x`; do not repeatedly rerun unchanged large suites.

Environment and broad offline gates (run suites separately):

```bash
uv sync --frozen --inexact --group backtesting --group docs
# Add the declared --group ml when validating optional LightGBM paths.

POLARS_MAX_THREADS=4 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  uv run --no-sync pytest -m 'not network and not integration' \
  --junitxml=/tmp/equity-continuation-serial.xml \
  > /tmp/equity-continuation-serial.log 2>&1

POLARS_MAX_THREADS=2 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run --no-sync pytest -n auto \
  -m 'not network and not integration and not serial' \
  --junitxml=/tmp/equity-continuation-parallel.xml \
  > /tmp/equity-continuation-parallel.log 2>&1

uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src
uv run --no-sync equity catalog-generate
uv run --no-sync mkdocs build --site-dir /tmp/equity-continuation-doc-site
git diff --check
```

Run focused behavioral suites and explicitly inspected mocked cross-module
integration tests in addition to these gates. Probe changed files with fresh
LSP diagnostics; an empty session cache is not proof. Triage genuine errors
versus trusted-path/static false positives with evidence. Do not weaken gates
or type coverage to hide failures. Retain optional-dependency skip counts and
non-strict MkDocs warnings as limits, not completed coverage.

Each implementer returns:

1. Packet/subtask and R IDs, selected model/reasoning evidence, exact changed
   files, actual behavior, compatibility decisions and accepted ADR references.
2. Regression tests with deterministic positive/negative cases, old-behavior
   failure evidence where safe, dry-run and error-path coverage.
3. Exact commands and pass/fail/skip counts, dependency groups, fresh diagnostics,
   catalog diff or unchanged regeneration, docs and unverified external limits.
4. Migration/rollback needs, operational gates and remaining issues.

A separate Luna `xhigh` reviewer inspects requirements and actual source/tests,
not just worker claims. Return must-fix issues with locations; no self-approval.
The coordinator fixes/retests reviewed defects before marking a packet complete
and updates both this ledger and the report. A blocked item stays blocked, not
complete. Local passing tests do not establish profitable investment signals.

## 7. Dispatch ledger

This section retains the C00–C10 acceptance ledger and evidence. The incident-specific status below is a historical snapshot, superseded by the [canonical prediction follow-up status](20261021-prediction-follow-up-status.md), updated 2026-10-22. Historical operational-limit wording below records its state at that time, not current instructions. Link commits only if separately authorized; no commit was made for this documentation-only update.

### Historical incident status recorded 2026-10-18 13:20 UTC — superseded by the [canonical prediction follow-up status](20261021-prediction-follow-up-status.md), updated 2026-10-22

Read-only investigation of only the default Platinum predictions table is
complete. Versions 0–14 are contiguous: v0 is an Append WRITE at 12:20:09.475
+08 with two fixture-marked rows; v1–v14 are MERGEs, with the only selected
value change at v6 (`feature_schema_version` 4 → null). These 15 operations do
not represent 15 distinct value changes. The rows are already fixture-marked
at the earliest retained v0; no retained genuine predecessor or overwrite is
shown. The table history cannot exclude pre-v0 deletion/recreation, other roots,
or other data. The cited ~20:10 JUnit suite starts are after all retained
commits and do not attribute them.

Open incident follow-ups: **I01** origin/remediation unknown; **I02** raw
`GET /predictions` can return unvalidated legacy rows if run against this root
(no HTTP use observed; scoped C04/R14 reader boundary reopened); **I03** a
source-only generic Delta-maintenance path-containment risk requiring contract
review. Full-path support may be intentional; no probe or maintenance run was
performed, and no remote exploit, authorization bypass, or production use is
claimed.
The independent reviewer accepted the retained findings with limits: earlier
text-plan selection/scan checks do not prove physical predicate pushdown, the
earlier inspector could report success on a row-scan error, and preliminary
reader attempts returning `None` have no recorded exit code. Saved final and
per-version filtered reads succeeded. See the linked incident report for exact
artifact hashes and limits. No production table beyond this one was inspected.

Only read-only investigation/findings were authorized and performed. Fixture
quarantine/remediation (prefer an auditable Delta commit only after approval
and backup planning), API reader policy changes, and maintenance-path fixes
require separate approval. No remediation or maintenance was performed; no
live/provider/paid/training/migration/rollback/commit/publish authorization is
implied. The C10 acceptance event remains historically valid and does not mean
that all post-closure findings were resolved at that time. This incident
status snapshot is superseded by the [canonical prediction follow-up status](20261021-prediction-follow-up-status.md), updated 2026-10-22.

| Packet | State | Implementer / effective model | Independent reviewer | Evidence / blocker |
| --- | --- | --- | --- | --- |
| C00 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate Luna `xhigh` review | Offline opt-in/CI/format and temporary runtime roots; canonical path contract isolated via runpy, no test-specific runtime restoration. Catalog/ML/SEC fixture seams repaired. |
| C01 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` review | Selected/custom config injection, threshold provenance, truthful outcomes, scan/dry-run safety, readiness and coverage; no signal-quality claim. |
| C02 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` reviews | R05 refit/CV interval maturity, right-edge pending labels and threshold reset; truthful provenance/next-day horizon; optional ML age/compatibility gates. Not a profitability claim. Incident follow-ups were unresolved in that historical snapshot; see the [canonical prediction follow-up status](20261021-prediction-follow-up-status.md), updated 2026-10-22. |
| C03 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` review | ADR-0015 proof expiry is carried through persisted features/loaders/model freshness; generated catalog includes Silver coverage and all proof-identity fields. Strict native action-source coverage remains unavailable without proof; no production repair. |
| C04 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` review | Composite identity and provenance-only legacy compatibility reviewed across five markets; unknown legacy identity remains unavailable. No production migration. |
| C05 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` review after scope-hash checkpoint | Resolved-Silver-independent checkpoint; ≤25 items per batch, ≤100 per invocation, finite caps, zero paid authorization, fail-closed pending/provider-crash handling, scope lock, NULL-safe additive Delta evolution and OCC up to 3 retries. No actual provider-cost adapter; SEC `--process` fails nonzero before fetch and default paid pipeline work is a planned skip. |
| C06 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate Luna `xhigh` review | Exposure opt-in; bounded news/social/analyst and retry logs, transient retries, validation/warnings, optional groups, UTC and Makefile/docs repairs. No universal scrubber or deployment claim. |
| C07 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` review | Private CRUD, session/readiness, config revalidation, removal, retry and ML-neutral re-arm reviewed on synthetic fixtures; no backfill or live operation. |
| C08 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` review | SQLite event/outbox states, session/config revalidation, removal cancellation, retry and ML-neutral re-arm reviewed on synthetic fixtures. No live sends or scheduler activation. |
| C09 | Implementation/offline contract reviewed and accepted 2026-10-18 | Luna `xhigh` dispatch | Separate fresh Luna `xhigh` review | Private API/UI projection, session/readiness, privacy, revalidation, removal, retry and ML-neutral re-arm reviewed on synthetic fixtures; loopback is not authentication. |
| C10 | Full implementation/offline-contract review accepted 2026-10-18 | Coordinator integration | Separate final reviewer: `openai-codex/gpt-6-luna`, `xhigh`; verdict: no must-fix | The integrated five-market synthetic journey and final serial/parallel/static/docs gates passed. Exact evidence and retained limits follow; no operational readiness is implied. |

### Final review evidence and operational limits — 2026-10-18 (historical acceptance snapshot)

The test, static, catalog, and C10 acceptance evidence below remains valid for
its recorded scope. Incident authorization wording in the original snapshot is
superseded by the [canonical prediction follow-up status](20261021-prediction-follow-up-status.md), updated 2026-10-22, and the [dated investigation report](../finding/20261018-prediction-test-write-investigation.md).

The separate read-only final reviewer verified runtime
`openai-codex/gpt-6-luna` at `xhigh` and returned **NO C10 must-fix**. The
reviewer's 191-file worktree hash was unchanged across review at HEAD
`0786e43a0d1c5f16bd4934e382599583bb87cae2`. Some implementation invocations
have explicit pinned-agent/model/`xhigh` dispatch evidence without exposed
runtime fields; that dispatch evidence is not upgraded to observed runtime.
This closeout session observed `PI_PROVIDER=openai-codex` and
`PI_MODEL=gpt-6-luna`; no `PI_REASONING_LEVEL` field was exposed. The reviewer
runtime is independently verified as stated above.

- Final serial: **1,533 passed, 0 failed/errors/skipped, 94 deselected, 3 warnings**;
  `/tmp/equity-final-serial-repaired.{log,xml}`.
- Final parallel: **1,533 passed, 0 failed/errors/skipped, 9 warnings**;
  `/tmp/equity-final-parallel.{log,xml}`. These suites ran sequentially with
  bounded native threads, not concurrently.
- Inspected mocked integrations: **50 passed, 0 failed/errors/skipped, 2 warnings**;
  `/tmp/equity-final-integrations.{log,xml}`. The four modules were
  `dashboard_exporter`, `signal_integration`, `pipeline_orchestrator`, and
  `private_tracking_journey`. C10 covered six canonical identities across all
  five markets, real calendars, Pointblank/Delta, adjusted-price availability,
  readiness, changed/repeat/neutral/stale/closed/error states, SQLite
  event/outbox states, delivery retry/revalidation/removal cancellation,
  mocked sends, and private API/UI projection. Optional ML error coverage used a fake
  forecaster, not model-score performance.
- Earlier serial evidence **1,485 passed, 47 failed, 94 deselected, 0 skipped**
  remains at `/tmp/equity-final-serial.{log,xml}` as historical failure
  evidence. The 47 fixture failures were repaired, not deleted or skipped;
  repaired serial/parallel evidence supersedes the earlier gate result.
- Frozen offline sync with groups `backtesting`, `docs`, `ml`, `dashboard`
  audited 288 packages (`/tmp/equity-final-sync.log`). Ruff passed; 305 files
  passed format check; mypy passed for 170 source files. Fresh LSP covered 156
  changed Python files with zero diagnostics across
  `/tmp/equity-final-lsp-{1,2,3}.txt`. The separate unchanged
  `src/equity_lake/ml/_metrics.py:68` Pyright/sklearn stub mismatch is triaged:
  runtime `zero_division=0` is valid, documentation and mypy agree. No source
  fix or blanket security-clean claim follows.
- Official catalog generation yielded 18 datasets, 52 nodes, 372 edges, and a
  byte-identical `data/catalog.jsonl`; it includes all ten proof columns and
  three identity fields, feature schema 7, availability schema 4, identity
  schema 1, and model provenance 3. Raw Bronze stays immutable; private
  auxiliary artifacts remain uncataloged.
- MkDocs succeeded with four existing link warnings (README/index, two
  archived links, and the handoff README link), unlisted-page information, and
  the Material banner (`/tmp/equity-final-docs-verified.log`). Archived docs
  were not widened or fixed.

The native yfinance action source still has no qualifying source publication,
complete-coverage, or expiry proof; adjusted reads remain unavailable until
verified source proof exists. No proof was fabricated and no native provider
readiness is claimed. No actual paid-cost adapter exists: paid authorization is
zero, `SEC --process` fails nonzero before fetch, and default paid pipeline work
is a planned skip. There is no claim of profitable alpha, calibrated/fixed
scores, exactly-once delivery, API authentication, security audit, Python 3.13
CI, container/deployment readiness, or provider coverage. No user deferral was
accepted. **At the time of this historical acceptance snapshot**, the possible
synthetic AAPL/MSFT prediction write dated 2024-01-02 remained unresolved and
investigation/inspection had not been authorized. The later read-only
investigation is recorded above; it did not establish causation or certify
unchanged state. Cleanup, restore, rollback, quarantine, or other remediation
remains unauthorized and was not performed. Compatibility/rollback evidence is
limited to synthetic reader/proof/label/migration fixtures and temporary copies;
no production rollback is certified. The original documentation-only closure
did not run tests or other full gates and performed no production write,
provider fetch/send, paid work, retraining, backfill, migration, deployment,
commit, push, reset, stash, or clean.

Exact static gate commands recorded for this evidence are:

```bash
uv sync --frozen --inexact --offline --group backtesting --group docs --group ml --group dashboard
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run mkdocs build --site-dir /tmp/equity-final-doc-site
```

The pytest log/JUnit artifacts above preserve final counts; their complete shell
argv was not captured in those logs, so this status record does not invent or
claim a command line or rerun. See the findings register's final closure block
for the expanded evidence summary.

### Historical coordinator update — 2026-10-18 (superseded by final closure above)

Three bounded implementation/review waves have followed the earlier ledger
blockers. The bounded C01–C09 source and documentation reviews are accepted;
this is not acceptance or closure of the full C packets or C10. Reviewed
synthetic/offline evidence includes seven source-feature cases, four
availability cases, two source-proof cases, one identity case, and three
model-provenance cases. The C03 proof-expiry review is accepted; coordinator
catalog regeneration through the offline wrapper resolved its final review
item. The generated catalog has 18 datasets, 52 nodes, and 372 edges, including
the Silver `corporate_action_coverage` table and the three feature proof identity
columns (`action_coverage_market`, `action_coverage_mic`,
`action_coverage_symbol`). Four documentation fixes were accepted in the bounded
docs review. The earlier failure/review logs below
are retained as historical evidence, not deleted or relabeled as passing.

C10 remains open. Its initial full serial run was **1485 passed, 47 failed, 94
deselected, 0 skipped**, captured in `/tmp/equity-final-serial.log`,
`/tmp/equity-final-serial.xml`, and
`/tmp/equity-final-serial-failures.json`. The assigned miscellaneous
expectation repairs subsequently passed **5 focused tests** and **32 tests in
the three assigned modules**, using the final logs
`/tmp/equity-c10-misc-fix-focused-final.{log,xml}` and
`/tmp/equity-c10-misc-fix-modules-final2.{log,xml}`. Earlier successful
attempts remain in `/tmp/equity-c10-misc-fix-focused.{log,xml}` and
`/tmp/equity-c10-misc-fix-modules*.{log,xml}`. The full serial gate has not
been rerun after those changes, and no parallel suite has run. Other captured
failures outside the assigned test modules remain outside this repair; there
is no full-green claim.

Additional current evidence: offline `uv sync --frozen --inexact --offline`
audited 288 packages; Ruff passed, formatting passed for 304 files, and mypy
passed for 170 source files. Scoped `git diff --check` passes for the files in
this repair; the repository-wide check flags pre-existing trailing whitespace
at unowned `tests/unit/test_fetchers.py:827`, which was left untouched. A fresh
focused product review recorded 354 passes and one passing real all-five-market
integration;
separate proof-binding review passed 59 tests. Docs review passed 42 tests and
the docs build succeeded with existing README/navigation/archive-link warnings.
These do not substitute for the full C10 gates. Fresh product-review logs are under
`/tmp/equity-final-product-review*`.

The native action source has not provided qualifying source-availability proof;
strict adjusted reads must remain unavailable for it. There is no actual paid
cost adapter, and SEC `--process` remains disabled with nonzero failure. No
live provider, paid-work, delivery, or production-readiness claim is made, and
no scope deferrals are accepted. LSP reports are explicitly not warning-free:
the only primary finding is a verified false positive in unchanged
`ml/_metrics.py:68` (runtime `precision_zero_division=0`; installed stub says
`str`, with triage under `/tmp/equity-proof-triage*`); other review-LSP counts
are inconclusive. The possible synthetic AAPL/MSFT prediction write dated
2024-01-02 remains unresolved and unauthorized for investigation, inspection,
cleanup, or rollback. No commit or push was made.

### Appended owner-approval evidence — 2026-10-17

Owner statement: **“Approve 1-9 and C10 implementation”.** The accepted
requirements are recorded in ADR-0013 and ADR-0015 through ADR-0018; this
approves implementation scope, not completion or self-review. The supplied
baseline evidence is preserved at
`/tmp/equity-continuation-approval-baseline.GYvVT0`; its sandbox smoke passed
before this documentation update. No test/smoke was rerun for this update, so
there is no fresh test claim. The possible synthetic AAPL/MSFT prediction write
remains unresolved: investigation, production-data inspection, cleanup, and
rollback were not approved. Operational bans remain unchanged.

### Historical independent-review wave and dispatch/safety evidence (pre-repair)

- The four-dispatch wave was interrupted: parent outputs were aborted, while
  on-disk outputs and logs were preserved. For interrupted implementers, the
  pinned Luna `xhigh` agent definition and explicit model/thinking dispatch
  parameters are known, but runtime was **not independently observed for every
  interrupted dispatch**. Subsequent independent reviewers all confirmed
  `PI_PROVIDER=openai-codex`, `PI_MODEL=gpt-6-luna`, and
  `PI_REASONING_LEVEL=xhigh`. This session also confirmed those runtime values
  before making this documentation update.
- The fresh C05 review accepted only its bounded implementation/tests slice;
  its exact 99-test evidence and logs are recorded in the ledger row above.
  Foundation review: **158 passed plus 1 targeted pipeline failure**. Separate
  ML review: **80 passed**, plus 3 status cases and 3 upstream-proof tests.
  Backtest per-decision availability, social-null semantics, and proof/Pointblank
  repairs were reviewed as passing. The unresolved must-fix is missing
  `source_valid_until` propagation through persisted features, loaders and the
  model freshness guard; proof-expiry propagation is required, and the normal
  pipeline test with stubs fails Pointblank when proof is missing. Disjoint
  repairs are underway and have not been accepted.
- The latest bounded product review was **146 passed, 2 failed (148 total)**;
  `/tmp/equity-resume-product-review-focused.xml` is the retained JUnit. The
  must-fix is ML-neutral re-arm. The C10 synthetic fixture also exposes native
  `NotFound` before JSON when the Silver action table is missing, and its model
  fixture hash/provenance is stale. All remaining implementation is under
  review; prior implementers touched some coordinator-owned docs and extra
  adjacent files, which are not blanket-accepted and remain under review.
- No accepted deferrals: C10's all-five-market requirements are unchanged.
  Full serial/parallel suites and the complete CLI journey gates have **not**
  been rerun for this review wave.
- Original HEAD remains `0786e43`; the existing dirty worktree and untracked
  files are preserved, as are the offline wrapper and prior evidence. This was
  a documentation-only update: no re-audit, implementation redo, test/CLI
  journey rerun, network/provider call, paid work, backfill, retraining,
  deployment, reset, stash, clean, commit or push occurred. The possible
  synthetic write below remains unresolved and was not investigated.
- **Possible unauthorized test write:** the original ML package-forecaster test
  lacked a persistence mock and passed during implementation/review, potentially
  merging synthetic AAPL/MSFT predictions dated `2024-01-02` into default
  `04_platinum/predictions`. No production contents were inspected, deleted,
  restored, or certified unchanged. The test now captures/asserts persistence;
  default feature-loader constructors in selected tests are mocked. Broad tests
  were paused while C00 added early per-process temporary runtime roots. Those
  roots are a pytest safeguard, not an OS sandbox. Any investigation or rollback
  of the possible existing-data change requires separate owner authorization.

### Historical verification evidence (not new-code evidence) and remaining operational gates

Final coordinator runs used the network-disabled, read-only-checkout wrapper
`/tmp/equity-continuation-offline.sh`. It overlays live data/logs and `.env`,
clears inherited credentials, allows temporary output, and copies only the
already-installed public DuckDB Delta extension into its temporary HOME.
Host filesystem reads and host `/tmp` remain possible; this is not a complete
confidentiality sandbox. The wrapper and logs are local temporary evidence,
not a deployed service or committed framework.

```bash
uv sync --frozen --inexact --group backtesting --group docs --group ml --group dashboard
POLARS_MAX_THREADS=4 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  bash /tmp/equity-continuation-offline.sh pytest -p no:cacheprovider \
  -m 'not network and not integration' \
  --junitxml=/tmp/equity-continuation-serial-verified.xml \
  > /tmp/equity-continuation-serial-verified.log 2>&1
POLARS_MAX_THREADS=2 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  bash /tmp/equity-continuation-offline.sh pytest -p no:cacheprovider -n auto \
  -m 'not network and not integration and not serial' \
  --junitxml=/tmp/equity-continuation-parallel-verified.xml \
  > /tmp/equity-continuation-parallel-verified.log 2>&1
bash /tmp/equity-continuation-offline.sh pytest -p no:cacheprovider \
  -m 'integration and not network' tests/integration/test_dashboard_exporter.py \
  tests/integration/test_signal_integration.py \
  --junitxml=/tmp/equity-continuation-mocked-integration.xml \
  > /tmp/equity-continuation-mocked-integration.log 2>&1
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src
bash /tmp/equity-continuation-offline.sh equity catalog-generate \
  --output /tmp/equity-continuation-catalog.jsonl
cmp data/catalog.jsonl /tmp/equity-continuation-catalog.jsonl
bash /tmp/equity-continuation-offline.sh mkdocs build \
  --site-dir /tmp/equity-continuation-doc-site
git diff --check
```

- **Historical, not fresh review-wave evidence:** serial **1366 passed, 93
  deselected, 0 skipped**; parallel **1366 passed, 0 skipped**; inspected
  mocked integration **4 passed**. ML/dashboard optional cases ran. Initial
  failed sandbox run and corrected dependency/fixture retest are retained, not
  substituted for passing evidence. Full serial/parallel suites and the complete
  CLI journey have not been rerun for the current review wave.
- Ruff and format (**293 files**) pass; mypy **165 source files** clean.
  Fresh LSP covered **73 changed Python files**: no primary type errors;
  three unchanged auxiliary findings in Streamlit dataset SQL and legacy
  Delta migration/backup were inspected. Existing suppressed findings and
  workflow action-pinning advisories are not a warning-free security verdict.
- Regenerated catalog is byte-identical: **17 datasets, 52 nodes, 372 edges**.
  Sandbox missing-data warnings are expected, not a production health probe.
  MkDocs builds, retaining README/index, archived-link and navigation warnings.
  Dependency/deprecation/SHAP warnings remain; no network/provider, container,
  Python 3.13 CI or vulnerability-audit certification is claimed.
- Review deviations: one worker-invoked reviewer ran an extra broad suite despite
  coordinator-only instructions; it is not the final evidence. The coordinator
  subsequently ran both final suites separately. A final focused retry reviewer
  used sanitized environment/mocked fixtures rather than the requested wrapper;
  the same regressions also passed in the coordinator's sandbox broad runs.

At the historical snapshot recorded in this section, no policy choice among
approved items 1–9 remained open, but implementation, C10, independent review,
and full offline gates were still incomplete. The final 2026-10-18 closure
above supersedes that status: C00–C10 implementation/offline contracts are
reviewed and accepted. **At that earlier snapshot**, the separate possible
synthetic prediction-write investigation was not approved, and no cleanup,
restore, rollback, or production-data inspection had occurred. The subsequent
bounded read-only investigation is documented in the [current incident report](../finding/20261018-prediction-test-write-investigation.md);
remediation remains unauthorized. Live provider/delivery calls, paid
processing, production migration/repair/backfill/retraining, deployment,
commits, and push remain unauthorized. This historical documentation update
predates and does not describe the final gate evidence above.

## 8. Historical starter prompt (superseded by 2026-10-18 closure)

```text
Continue the remaining Equity Lake review scope in this existing worktree.
Read AGENTS.md, docs/README.md, the findings report at
  docs/finding/20261016-repository-review-and-equity-alerts.md
and the complete implementation handoff at
  docs/plans/20261016-equity-alerts-remaining-scope-handoff.md.

Use openai-codex/gpt-6-luna with xhigh reasoning for ALL substantive scout,
implementation and independent-review subagents. Confirm effective agent model
selection: the generic reviewer is pinned to another model, so a dispatch
model override is insufficient. Use the correctly pinned assay-luna-worker in
separate role-specific invocations, or another verified Luna definition.
Do not silently substitute models. The coordinator should orchestrate and
integrate, with independent agent review of each bounded implementation slice.

Preserve the uncommitted Phase A fixes and untracked tests. Do not restart the
audit, redo completed work, reset/stash/clean, commit or push. Present each
bounded plan before edits. Use the current ledger and preserve reviewed slices;
revalidate only the next open subtask, and parallelize only disjoint file ownership. Work through ALL remaining C packets and open/partial findings,
not just the notification MVP. Implement the owner-accepted ADR-0013 and
ADR-0015 through ADR-0018 contracts; keep production and operational bans
explicit, and continue safe unrelated packets when separately gated. No live providers,
notifications, paid processing, production repair/backfill/retraining,
deployment or broker execution without separate operational authorization.

Add deterministic regressions and user documentation, run the handoff's
bounded offline gates, regenerate the catalog when required, and update both
the report and dispatch ledger after every reviewed packet. Do not call the
scope complete until implementation, independent review, required offline gates,
and finding closure are complete.
```
