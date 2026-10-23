# Repository Review: Usability, Equity Tracking, and Actionable Alerts

**Date:** 2026-10-16
**Code baseline:** `0786e43a0d1c5f16bd4934e382599583bb87cae2`
**Status:** C00–C10 implementation and offline-contract review accepted on 2026-10-18 after independent C10 review. This is not live operational readiness.

### Current follow-up status — 2026-10-22

The [durable prediction follow-up status](../plans/20261021-prediction-follow-up-status.md), updated 2026-10-22, is current: I02 and I03 implementation/offline-contract acceptance is unchanged; I01's owner-authorized exact-file backup/isolated-copy verification and two-row targeted Delta delete are complete. The single operation advanced v14→v15 at 2026-10-22T16:38:23Z and left zero active files; historical Delta files remain. Row origin, source actor, and MIC remain unknown, and no further mutation is planned. The [2026-10-20 handoff](../plans/20261020-prediction-incident-follow-up-handoff.md) retains its pending statuses and proposal instructions as historical. Oct18 table facts below remain historical, not a claim about current table state. C00–C10 acceptance remains valid for its tested scope, not production operational readiness.

### Historical incident findings recorded 2026-10-18 13:20 UTC

The owner authorized and the team completed a bounded **read-only** investigation of only `data/lake/04_platinum/predictions`, plus related source review. Two AAPL/MSFT rows for `2024-01-02` with `model_version=fake`, `model_mode=unknown`, and `probability=0.8` are confirmed in retained Delta versions 0–14, already present at earliest retained v0. A specific writer/process, pre-v0 state, or replacement of any real row is not established; external-reader use is unknown. The saved ~20:10 JUnit suites postdate every retained commit and are not attribution evidence. See the [dated investigation report](20261018-prediction-test-write-investigation.md) for methods, independent-review caveats, and evidence paths.

**Open findings as recorded on 2026-10-18:** I01 fixture-row origin/remediation (unknown origin);
I02 raw `GET /predictions` reader-boundary validity/identity/provenance gap;
I03 source-only generic Delta-maintenance path-containment risk requiring
contract review. Full-path support may be intentional; no probe or production
use was observed, and this is not a confirmed remote exploit or authorization
bypass. The scoped C04/R14 API boundary is reopened for follow-up; prior C04
acceptance remains valid for its reviewed implementation/offline contracts.
R18 training/inference provenance acceptance does not cover the raw API.
C00–C10 acceptance and historical test counts remain valid for their scopes,
but are not data-integrity proof and must not be summarized as “all findings
resolved.” Only the read-only investigation was authorized/performed;
quarantine, deletion, repair, restore, rollback, migration, training,
live/provider/paid activity, maintenance, commits, and publishing remain
unauthorized and unperformed. Later historical snapshots in this document
that say the investigation/inspection was unauthorized or had not occurred are
point-in-time records superseded by this current status.

**Review assistance:** Multi-agent scouting, implementation and review; see the model-provenance note below

## Executive assessment — initial-review baseline

At the initial review baseline, Equity Lake had a substantial data and research
foundation but lacked an end-to-end personal equity monitoring workflow. The
C00–C10 implementation/offline-contract review has since been accepted as
recorded below; that synthetic/local acceptance is not live operational
readiness. A trustworthy watchlist-to-notification loop remains the product
goal, not more sources, ML models, or a new frontend framework.

The baseline journey was fragmented:

```text
Ticker/source configuration → ingest → features → predictions
Watchlist YAML → separate signal scan → terminal/report + signal history
Pipeline health monitor → console + optional health webhook
```

The missing connection is a reliable, explainable, deduplicated signal-delivery
workflow. Before adding it, fix several defects that can produce a crash,
misleadingly empty scans, incorrect configuration behavior, missing health
reports, or credential disclosure.

**Recommendation:** Treat generated BUY/SELL outputs as research candidates,
not validated trade recommendations. This review does not establish profitable
signals, reliable live-provider coverage, or suitability for automated trading.

## Historical continuation directive — superseded by 2026-10-18 closure

The owner requested that **all remaining scope** be implemented in a new
thread through subagents using **`openai-codex/gpt-6-luna` with `xhigh`
reasoning**. This covers open/partial R findings, the watchlist/notification
MVP, private tracking views, and final validation—not just another review.
The current thread prepares documentation only; it does not launch that work.

Use the [new-thread implementation handoff](../plans/20261016-equity-alerts-remaining-scope-handoff.md)
for the full C00–C10 work packets, dependencies, per-agent role/ownership
contracts, acceptance criteria, stop gates, dispatch ledger and copyable starter
prompt. Preserve this report's completed-batch fixes and the current uncommitted
worktree, including untracked tests. Do not resume from the review's HEAD commit
alone or redo the original audit.

**Model-provenance note:** earlier scout/implementation dispatches requested
Luna, but a dispatch override does not prove every reviewer used that model.
The local generic `reviewer` definition pins another provider/model and its
frontmatter takes precedence. Do not treat the previous blanket reviewer-model
attribution as verified. For the remaining scope, use a verified Luna-pinned
agent (currently `assay-luna-worker`) in separate scout, implementer and
read-only reviewer invocations; set `thinking="xhigh"` explicitly and record
resolved model evidence. No silent model/reasoning fallback is authorized.
This clarification does not change the recorded test results.

The coordinator should dispatch bounded plans and integrate independently
reviewed results. This is the historical pre-closure directive: ADR-0013 and
ADR-0015 through ADR-0018 are now implementation/offline-contract reviewed and
accepted as recorded in the final closure below. Production repairs, live
notification/deployment activation, paid processing, and incident remediation
remain separately gated. The read-only investigation has since been separately
authorized and completed; see the current post-closure status at the top of this
report.

## Historical continuation register snapshot — updated 2026-10-18

### Historical owner-approval snapshot — 2026-10-17

The owner approved **“1-9 and C10 implementation”.** The accepted contracts
are recorded in ADR-0013 and ADR-0015 through ADR-0018. This approves the
specified implementation scope, not implementation completion or review; C10
still requires the full synthetic integrated journey, independent reviews, and
offline gates. The existing sandbox evidence and smoke pass predate this
report update; no tests or smoke were rerun here, so no fresh test claim is
made. The possible synthetic AAPL/MSFT prediction write was **not** approved
for investigation, production-data inspection, cleanup, or rollback. Live
provider/delivery calls, paid processing, production migration/repair/backfill/
retraining, deployment, commits, and push remain unauthorized.

The existing dirty worktree was retained at HEAD `0786e43`; no commit, reset,
stash, clean, push, live delivery, backfill, production retraining, or deployment
was authorized or performed. The following register superseded older batch-status statements at that
snapshot; original finding evidence remains historical. The final C00–C10
closure below is the current status.
The four-dispatch wave was interrupted: parent outputs were aborted, while
on-disk outputs/logs were preserved. Interrupted implementers used the verified
Luna-pinned `assay-luna-worker` definition and explicit
`model="openai-codex/gpt-6-luna"`, `thinking="xhigh"` dispatch parameters, but
runtime was not independently observed for every interrupted dispatch.
Subsequent independent reviewers in that earlier review wave confirmed runtime
`PI_PROVIDER=openai-codex`, `PI_MODEL=gpt-6-luna`, and
`PI_REASONING_LEVEL=xhigh`. The present final closeout session observed
`PI_PROVIDER=openai-codex` and `PI_MODEL=gpt-6-luna`, but no reasoning-level
runtime field; its `xhigh` setting is dispatch/configuration evidence. See the
[dispatch ledger](../plans/20261016-equity-alerts-remaining-scope-handoff.md#7-dispatch-ledger)
for the final packet status and evidence.

| Findings | Current resolution and remaining limits |
| --- | --- |
| R01 | **Implementation/offline contracts reviewed and accepted.** Settings/export and bounded retry/provider logs have synthetic token coverage; analyst transient retries are preserved. This is not a universal provider-exception scrubber or security audit. |
| R02–R04, R10 | **Implementation/offline contracts reviewed and accepted.** Sequential scans, truthful monitor persistence, HK/SG default union, and no-side-effect signal dry-run are covered by final evidence. |
| R05 | **Implementation/offline contracts reviewed and accepted.** Per-refit fit and threshold samples require mature labels; interval purging, pending right-edge events, early-hit maturity, and per-refit threshold state are covered synthetically. Passing fixtures establish no alpha. |
| R06, R15 | **Implementation/offline contracts reviewed and accepted.** ADR-0015 split-only read-time semantics, fail-closed action/vintage handling, versioned missing-aware predictors, and `source_valid_until` propagation through persisted features/loaders/model freshness are covered. Native yfinance action proof is unavailable, so adjusted reads remain unavailable; no raw fallback, production repair, or migration is claimed. |
| R07–R09 | **Implementation/offline contracts reviewed and accepted.** Config injection, effective-threshold provenance, explicit outcomes, zero-event reports, finite scored-article coverage, exact-session freshness, and optional ML age/schema/identity gates are covered. No signal-quality claim. |
| R11, R14 | **Implementation/offline contracts reviewed and accepted.** Composite identity, provenance-only legacy reads, and supported deterministic paths across all five markets are covered by synthetic fixtures and C10. Unknown identity remains unavailable; no production migration. |
| R12 | **Implementation/offline contracts reviewed and accepted.** Private CRUD, readiness, UI/API projections, SQLite event/outbox states (`pending`, `sent`, `failed`, `expired`), retries, revalidation, removal cancellation, and ML-neutral re-arm were reviewed on synthetic fixtures. Live notification and scheduler activation remain separate operational gates. |
| R13 | **Implementation/offline contracts reviewed and accepted.** Loopback default/exposure consent, Compose/query/S3 contracts, and documented local-port publishing are covered. No container/deployment smoke or deployment-readiness claim. |
| R16 | **Implementation/offline contracts reviewed and accepted.** NULL-safe native nullable additive Delta evolution, OCC up to three retries, concurrent creation, scoped locking, and visible failures are covered synthetically. No production migration. |
| R17 | **Implementation/offline contracts reviewed and accepted.** Explicit locked scope, independent checkpoint state, ≤25 items per batch and ≤100 items per invocation, finite caps, durable checkpoint ordering, and fail-closed pending/provider-crash behavior are covered. No actual provider-cost adapter exists; SEC `--process` fails before fetch, paid authorization is zero, and default paid work is a planned skip. |
| R18 | **Implementation/offline contracts reviewed and accepted.** Provenance, feature/model compatibility, age, and `source_valid_until` checks are covered. This does not create native source proof or live model availability; no automatic retraining. |
| R19 | **Implementation/offline contracts reviewed and accepted.** Offline-default/network-marker policy, format CI gate, temporary paths, synthetic fixtures, and ML/SEC seams are covered. Network/provider coverage, Python 3.13 CI, dependency-security audit, and deployment remain unverified. |
| R20 | **Implementation/offline contracts reviewed and accepted.** Config validation, Makefile/examples, optional install guidance, UTC scheduling labels, and documentation fixes are covered. This does not imply operational onboarding or live delivery readiness. |

### Historical coordinator update — 2026-10-18 (superseded by final closure below)

Three bounded implementation/review waves followed the previously recorded
review blockers. The bounded C01–C09 source and documentation reviews are
accepted, but this does not close any full C packet or C10. Reviewed
synthetic/offline evidence covers seven source-feature cases, four availability
cases, two source-proof cases, one identity case, and three model-provenance
cases. The C03 proof-expiry review is accepted; coordinator catalog regeneration
through the offline wrapper resolved its last review item. The 18-dataset,
52-node, 372-edge catalog includes Silver `corporate_action_coverage` and all
three Gold proof-identity fields: `action_coverage_market`,
`action_coverage_mic`, and `action_coverage_symbol`. Four bounded documentation
fixes were accepted. Older review blocks and logs below are historical evidence
and remain preserved; they are not the current gate result.

C10 remains in progress. Initial full serial evidence is **1485 passed, 47
failed, 94 deselected, 0 skipped**, recorded in
`/tmp/equity-final-serial.{log,xml}` and
`/tmp/equity-final-serial-failures.json`. The assigned miscellaneous test
repairs now pass **5 focused tests** and **32 tests across the three assigned
modules**, with final evidence in `/tmp/equity-c10-misc-fix-focused-final.{log,xml}`
and `/tmp/equity-c10-misc-fix-modules-final2.{log,xml}`. Earlier successful
attempts remain under `/tmp/equity-c10-misc-fix-*`. The full serial gate has not been
rerun after these repairs; no parallel suite has run. Other failures from the
initial capture outside the assigned modules remain open, so no full-green
claim is made.

Separate static evidence: `uv sync --frozen --inexact --offline` audited 288
packages; Ruff passed, formatting passed for 304 files, and mypy passed for
170 source files. Scoped `git diff --check` passes for the files in this repair;
the repository-wide check flags pre-existing trailing whitespace at unowned
`tests/unit/test_fetchers.py:827`, which was left untouched. A fresh focused
product review recorded 354 passes plus one passing real all-five-market
integration; a separate proof-binding review passed 59 tests. Documentation review
passed 42 tests and the docs build succeeded with existing README,
navigation, and archive-link warnings. These checks do not substitute for
C10's full gates. Fresh product-review evidence is under
`/tmp/equity-final-product-review*`.

The native action source still has no qualifying source-availability proof, so
strict adjusted reads remain unavailable for it. No actual paid-cost adapter
exists; SEC `--process` remains disabled and fails nonzero. No live-provider,
paid-work, delivery, or production-readiness claim is made; no scope deferrals
are accepted. Fresh LSP evidence is not warning-free: the primary finding is a
verified false positive at unchanged `ml/_metrics.py:68` (runtime
`precision_zero_division=0` versus stub type `str`; evidence under
`/tmp/equity-proof-triage*`); other review-LSP findings are inconclusive. The
possible synthetic AAPL/MSFT prediction write dated 2024-01-02 remains
unresolved and unauthorized for investigation, production inspection, cleanup,
or rollback. No commit or push was made.

### Final C00–C10 implementation and offline-contract acceptance — 2026-10-18

This section preserves the acceptance event and its evidence as recorded at
that time. Its incident-authorization wording is historical and is superseded
by the current post-closure incident status at the top of this report and the
[dated investigation report](20261018-prediction-test-write-investigation.md).

A separate, read-only independent reviewer verified runtime
`openai-codex/gpt-6-luna` at `xhigh`, inspected the final evidence, and returned
**NO C10 must-fix**. The reviewer's 191-file worktree hash was unchanged across
that review at HEAD `0786e43a0d1c5f16bd4934e382599583bb87cae2`; this documents the
reviewer's read-only boundary, not the later documentation-only edits recorded
here. C00–C10 implementation and offline-contract review are accepted. This is
not live operational readiness, and no implementation scope was deferred.

| Packet | Current status | Scope/limits retained |
| --- | --- | --- |
| C00 | Implementation/offline contract reviewed and accepted | Offline-default safeguards and deterministic temporary paths; network tests remain separate. |
| C01 | Implementation/offline contract reviewed and accepted | Config injection, truthful outcomes and scan safety; no signal-quality claim. |
| C02 | Implementation/offline contract reviewed and accepted | Mature-label/refit boundaries and provenance; passing fixtures do not establish alpha. |
| C03 | Implementation/offline contract reviewed and accepted | Proof-expiry propagation and generated-catalog alignment accepted; native action-source proof remains unavailable. |
| C04 | Implementation/offline contract reviewed and accepted | Canonical identity and five-market synthetic scope; production migration remains unauthorized. |
| C05 | Implementation/offline contract reviewed and accepted | Bounded replay and additive Delta/OCC safety; actual cost adapter is unsupported and paid authorization remains zero. |
| C06 | Implementation/offline contract reviewed and accepted | Offline operational/documentation repairs; no deployment or universal security-clean claim. |
| C07 | Implementation/offline contract reviewed and accepted | Private watchlist/readiness behavior on synthetic fixtures; no backfill or live operation. |
| C08 | Implementation/offline contract reviewed and accepted | SQLite event/outbox states, retries, revalidation and removal cancellation on synthetic fixtures; no live sends or active scheduler. |
| C09 | Implementation/offline contract reviewed and accepted | Private API/UI projection and privacy checks; loopback is not authentication. |
| C10 | Full implementation/offline-contract review accepted | Independent reviewer found no must-fix after the integrated synthetic journey and final gates; not live operational readiness. |

**Final gate evidence** (captured before this documentation-only closure; serial
and parallel runs were sequential, with bounded native threads, not concurrent):

- Serial: **1,533 passed, 0 failed/errors/skipped, 94 deselected, 3 warnings**;
  `/tmp/equity-final-serial-repaired.{log,xml}`.
- Parallel: **1,533 passed, 0 failed/errors/skipped, 9 warnings**;
  `/tmp/equity-final-parallel.{log,xml}`.
- Inspected mocked integrations: **50 passed, 0 failed/errors/skipped, 2 warnings**;
  `/tmp/equity-final-integrations.{log,xml}`. The inspected modules were
  `dashboard_exporter`, `signal_integration`, `pipeline_orchestrator`, and
  `private_tracking_journey`. C10 exercised six canonical identities across
  all five markets with real calendars, Pointblank/Delta, adjusted-price
  availability, readiness, changed/repeat/neutral/stale/closed/error cases,
  event/outbox states, delivery retries/revalidation/removal cancellation,
mocked sends, and private API/UI projections. Optional ML error
  coverage uses a fake forecaster; it is not model-score or alpha evidence.
- The earlier initial serial failure (**1,485 passed, 47 failed, 94
  deselected, 0 skipped**) remains preserved at
  `/tmp/equity-final-serial.{log,xml}` and is historical, superseded by the
  repaired final serial/parallel evidence. The 47 fixtures were repaired, not
  deleted or skipped.
- Static gates: `uv sync --frozen --inexact --offline --group backtesting
  --group docs --group ml --group dashboard` audited 288 packages;
  Ruff passed (`/tmp/equity-final-ruff-verified.log`), formatting passed for
  305 files (`/tmp/equity-final-format-verified.log`), and mypy passed for
  170 source files (`/tmp/equity-final-mypy-verified.log`). Fresh LSP covered
  156 changed Python files, with zero diagnostics across
  `/tmp/equity-final-lsp-{1,2,3}.txt`. The separate unchanged
  `src/equity_lake/ml/_metrics.py:68` Pyright/stub mismatch is triaged as a
  false positive: installed runtime accepts `zero_division=0`, documentation
  and mypy agree; this was not a source change or a blanket security-clean
  finding.
- Official catalog generation produced 18 datasets, 52 nodes, and 372 edges;
  the generated output was byte-identical to `data/catalog.jsonl`. It includes
  all ten proof columns, the three proof-identity columns, feature schema 7,
  availability schema 4, identity schema 1, and model provenance 3. Raw
  Bronze remains immutable; private auxiliary artifacts remain uncataloged.
- MkDocs succeeded (`/tmp/equity-final-docs-verified.log`) with four existing
  link warnings (README/index, two archived links, and the handoff README link),
  plus unlisted-page information and the Material banner. Archived docs were
  not widened/fixed.

**Operational and compatibility limits:** all accepted statuses mean
implementation plus offline contracts only. The current native yfinance action
source has no qualifying publication/coverage/expiry proof, so strict adjusted
reads remain unavailable; no proof was fabricated and no native provider
operational readiness is claimed. No actual paid-cost adapter exists; paid
authorization is zero, `SEC --process` fails nonzero before fetch, and default
paid pipeline work is a planned skip. There is no claim of profitable alpha,
calibrated/fixed scores, exactly-once delivery, authenticated API, security
audit, Python 3.13 CI, container/deployment validation, or provider coverage.
At the time of this closure, the possible synthetic AAPL/MSFT prediction write
dated 2024-01-02 remained unresolved and investigation/content inspection had
not been authorized. A later, separately authorized read-only investigation is
now recorded at the top of this report and in the [dated incident report](20261018-prediction-test-write-investigation.md);
it did not establish causation or certify unchanged state. No cleanup, restore,
rollback, quarantine, or other remediation was performed or authorized. The
original closure itself made no production writes, provider fetches, sends,
paid-work actions, retraining, backfill, migration, deployment, commit, push,
reset, stash, or clean. Compatibility and rollback evidence remains limited to
synthetic reader/proof/label/migration fixtures and temporary copies; no
production rollback is certified.

### Historical independent-review progress (pre-repair; superseded by final closure; evidence retained)

- C05's bounded implementation and tests were independently accepted after a
  separate fresh Luna `xhigh` review and scope-hash checkpoint. Fresh evidence:
  **99 tests** (73 replay + 23 Delta + 3 CLI/pipeline paid-stage-skip); retained
  logs/JUnit are `/tmp/equity-resume-c05-review-{focused,delta,integration-focused}.{xml,log}`.
  Contracts include at most 25 items per batch and 100 per invocation, finite caps, zero paid
  authorization, fail-closed pending/provider-crash behavior, and OCC up to
  three retries. There is no actual provider cost adapter; paid work remains
  unavailable/unauthorized.
- Fresh foundation review: **158 passed plus 1 targeted pipeline failure**.
  Separate ML review: **80 passed**, plus 3 status cases and 3 upstream-proof
  tests. Backtest per-decision availability, social-null behavior, and proof /
  Pointblank repairs were reviewed as passing. The unresolved shared must-fix
  is proof-expiry propagation: `source_valid_until` is missing from persisted
  features, loaders and the model freshness guard. The normal pipeline test
  with stubs fails Pointblank when proof is missing. Disjoint repairs are in
  progress, not independently accepted.
- Fresh bounded product review: **146 passed, 2 failed (148 total)**;
  JUnit: `/tmp/equity-resume-product-review-focused.xml`. The ML-neutral re-arm
  must-fix remains. A C10 fixture with a missing Silver action table produces
  native `NotFound` before JSON; the model fixture hash/provenance is stale.
  C07–C09 implementation remains under review despite bounded slices reviewed;
  C08 outbox implementation is present.
- C01's bounded canonical-default signal CLI/custom-config/dry-run slice and
  C04's bounded signals/history identity slice were independently accepted.
  New unknown identity is rejected; unknown legacy identity is not mapped.
  Global C04 foundation fixtures and C10's full all-five-market journey remain
  open. No scope deferrals have been accepted.
- An interrupted four-dispatch wave left parent outputs aborted but on-disk
  outputs/logs preserved. Earlier implementers touched some coordinator-owned
  docs and extra adjacent files; those deviations are under review, not blanket
  acceptance. Full serial/parallel suites and complete CLI journey gates have
  **not** been rerun for this wave.

### Historical broad verification (superseded by final closure; not fresh review-wave evidence)

- The earlier `uv sync --frozen --inexact --group backtesting --group docs
  --group ml --group dashboard` passed; optional LightGBM and private Streamlit
  tests ran.
- Historical coordinator offline serial: **1366 passed, 93 deselected, 0
  skipped**; four-worker parallel: **1366 passed, 0 skipped**. Logs/JUnit are
  `/tmp/equity-continuation-serial-verified.{log,xml}` and
  `/tmp/equity-continuation-parallel-verified.{log,xml}`. These are prior
  evidence, not tests of the current review wave.
- Ruff, format (**293 files**) and `mypy src` (**165 source files**) pass;
  `git diff --check` passes. Fresh LSP probes of **73 changed Python files**
  show no primary type errors and **3 unchanged auxiliary findings** in
  Streamlit dataset SQL and legacy Delta migration/backup helpers. Trusted
  local paths and intentional exception propagation were inspected; no
  warning-free or universal SQL-safety claim is made. Existing suppressed
  findings and workflow action-pinning advisories remain disclosed limits.
- One coordinator catalog regeneration, to a temporary file in the isolated
  environment, is byte-identical to the tracked catalog: **17 datasets,
  52 nodes, 372 edges**. No cataloged schema or operational-artifact entry added.
- MkDocs builds to `/tmp/equity-continuation-doc-site`; README/index and
  archived-link/navigation warnings remain. Dependency/deprecation/SHAP
  warnings are retained, not evidence of security or live-provider health.

Broad tests ran through `/tmp/equity-continuation-offline.sh`: the repository
is read-only, live data/logs and `.env` are overlaid, credentials are not
inherited, network egress is disabled, and only the cached public DuckDB
extension is copied into a temporary HOME. This is a local validation wrapper,
not a product deployment or a complete host-confidentiality sandbox. Early
pytest temporary-root isolation supplements it. The initial sandbox broad run
failed (**1253 passed, 80 failed, 11 errors**) because the sanitized HOME lacked
that cached extension and two SEC fixtures attempted unmocked bootstrap DNS;
network egress was blocked. Those modules subsequently passed **166 tests**
without enabling network, before the final full runs. Failures were not skipped
or hidden.

**Safety incident requiring owner authorization:** before the isolation repair,
`test_run_prediction_job_uses_package_forecaster` passed without a persistence
mock. It may have merged synthetic AAPL/MSFT predictions dated `2024-01-02` into
the default Platinum table. No production contents were inspected, cleaned,
restored, or certified unchanged. The test now captures/asserts the merge seam;
feature-loader constructors are mocked where appropriate. Investigation or
rollback of that possible change is separately gated; passing later tests does
not resolve it.

At this historical snapshot, the remaining scope was **not complete**. The
interim packet-level review states recorded here and in the earlier coordinator
update are superseded by the final 2026-10-18 C00–C10 closure below. The
five-market scope remains unchanged, no deferral has been accepted, and
separate operational bans plus the unauthorized-write investigation gate
remain in force.

## Implementation progress — Phase A operational batch (historical)

After the initial review, the owner approved proceeding. This first batch
addresses operational defects without enabling live signal delivery or changing
ML decision/price-adjustment policy. The detailed findings below retain their
original baseline evidence; this table is a historical phase snapshot, not current resolution status.

| Finding | Status in this batch |
| --- | --- |
| R01 | Fixed for dashboard/settings show/get/export and health-webhook retry/outcome logs. Shared redaction lives in `core/logging.py`; raw HTTP-client debug/info logs are muted. This is not a universal scrubber for every provider exception string. |
| R02 | Fixed: all ticker scans are sequential; legacy `max_workers` remains accepted. Synthetic real-DuckDB subprocess and calling-thread/order regressions added. |
| R03 | Fixed: computed unhealthy results save the report before exit 1 and cleanup always runs. Nested report paths work; additive `healthy`/`check_results` fields preserve old fields, and zero checks do not imply healthy. |
| R04 | Fixed: default HK/SG scope uses the shared configuration-key mapping. Tests cover default union, explicit overrides, a one-exchange-open session and a fully closed session. |
| R10 | Fixed: signal dry-run validates and prints a plan before scanner construction; no inference, runtime/model directories, report or history writes. Normal-run initialization/persistence is also tested. |
| R13 | Partly fixed: Compose daily is externally scheduled one-shot work, query uses the supported all-query invocation, and S3 defaults use a bucket root. The API container's noninteractive exposure policy remains open. |
| R19/R20 | Targeted regressions and documentation corrected; remaining coverage/onboarding issues are not closed wholesale. |

Neighboring diagnostic cleanup adds guarded nullable/optional CN fallback
handling with offline tests, and annotates the legacy unified-CLI test module
so the per-file type-check hook also passes. No provider was contacted to test
these changes. There is no feature/history repair or model retraining here.

Verification of this batch:

- Offline serial suite: **1171 passed, 17 skipped, 92 deselected**.
- Offline four-worker `-n auto` suite: **1171 passed, 17 skipped**;
  network/integration/serial-marked tests excluded.
- The 17 skips are 16 optional LightGBM cases and the existing real-lake signal
  test; these are not live-market coverage evidence.
- Ruff, format check, `mypy src`, explicit CLI/monitor test type checks, and
  fresh error-level LSP checks pass. Four reviewed trusted-path SQL findings
  were marked false positives, not silently treated as repaired vulnerabilities.
- Catalog regenerated: 17 datasets, 52 nodes, 372 edges; no tracked catalog diff.
- Changed Markdown passes lint and MkDocs builds to a temporary site. The
  non-strict build still reports pre-existing README/index and archived-link
  warnings; these are not presented as a warning-free documentation build.
- Declared backtesting dependencies were installed after three arena tests
  failed for missing `polars-backtest`; those tests and the final suites pass.
- Concurrent serial/parallel verification attempts timed out under native-thread
  contention. Final suites ran separately with bounded threads, preserving the
  interrupted logs rather than calling those attempts successful.

Reproduce the final suite runs:

```bash
uv sync --frozen --inexact --group backtesting
POLARS_MAX_THREADS=4 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  uv run --no-sync pytest -m 'not network and not integration' \
  --junitxml=/tmp/equity-phase-a-serial.xml > /tmp/equity-phase-a-serial.log 2>&1
POLARS_MAX_THREADS=2 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
  PYTEST_XDIST_AUTO_NUM_WORKERS=4 uv run --no-sync pytest -n auto \
  -m 'not network and not integration and not serial' \
  --junitxml=/tmp/equity-phase-a-parallel.xml > /tmp/equity-phase-a-parallel.log 2>&1
```

**Historical remaining gate (pre-approval):** At that point, R05–R09,
including availability/adjustment policy, v2 config and threshold contracts,
and explicit scan coverage still needed follow-up before claiming Phase A
complete. The full watchlist notification/delivery workflow and private views
were not implemented then. The earlier recommendation to choose an initial
market before the alert MVP is superseded by the 2026-10-17 owner approval and
ADR-0016: identity, instrument-session behavior, and supported deterministic
signal paths for all five markets are required through C10; an initial US
configuration is not a multi-market deferral. Accepted implementation policy
is recorded in ADR-0015 through ADR-0018; implementation was pending at this
historical pre-closure snapshot and is accepted in the final register above.
Historical repairs, identity/schema migrations, and live notification enablement
remain separately gated.

## Scope, evidence, and limits

Reviewed architecture and accepted decision records; CLI/onboarding; ticker,
watchlist and signal configuration; ingestion/storage/recovery; feature and
label lifecycle; ML inference and walk-forward backtesting; signal generators,
history and formatting; monitoring/alerting; dashboards/read API; deployment,
Makefile, CI and relevant tests. This is a broad repository review, not a
line-by-line audit of every source or dependency. Source references such as
`signals/scanner.py` are relative to `src/equity_lake/`; configuration,
workflow, test and documentation paths are repository-relative. Line numbers
refer to the code baseline above.

Evidence terms:

- **Reproduced:** an offline synthetic probe or focused execution demonstrated
  the behavior. A synthetic crash does not establish a historical production crash.
- **Code-confirmed:** the relevant path and conditions were read; no live
  production reproduction is claimed.
- **Capability gap:** a missing user workflow, not necessarily a broken promise.
- **Latent risk:** a supported configuration can expose a problem; current
  production occurrence has not been demonstrated.

Prior findings in [the DAG audit](20261006-dag-pipeline-audit.md) were treated as
historical leads, not automatically carried forward. For example, EOD join-key
normalization, bounded label finalization, vendor split-column handling, and
much of pipeline coverage/failure reporting have since improved. R01–R20 below
are this review's identifiers; they are not replacements for the earlier F IDs.

The initial review was read-only apart from its findings document and index
link; the subsequently approved implementation is recorded above. Verification
did not require live provider calls or sending notifications. No `.env` contents
are included in this report. No production history repair, backfill, model
retraining, or container deployment was performed. Network/integration suites,
container execution and a dependency vulnerability audit remain unverified;
there is no full-repository runtime certification.

## Existing strengths to preserve

- Numbered medallion Delta storage, common write seams, and explicit import
  boundaries give the project a coherent foundation.
- Hamilton/Polars feature computation, validation boundaries, generated catalog
  checks, and extensive targeted tests are useful investments, not rewrite targets.
- The unified Typer CLI has discoverable help and explicit pipeline scope,
  required/optional failure reporting, and recovery authorization.
- ADR-0014 adds meaningful label availability and matched-only label updates.
- Signal history has a closed Pydantic write-boundary model and keyed Delta
  upserts. **History deduplication already exists**; notification deduplication does not.
- A read API already exposes signals, predictions, models and findings. Do not
  build a duplicate API just to add a watchlist screen.
- Health webhooks already have a settings knob and retry seam. The monitoring
  guide's claim that this knob is absent is stale.

## Priority register

P1 means fix before relying on the affected operational or investment output.
P2 means important correctness, usability, or reliability work. P3 is polish.
A product gap's priority reflects the requested user goal, not exploit severity.

| ID | Priority | Category | Finding |
| --- | --- | --- | --- |
| R01 | P1 | Security | Webhook credentials can appear in public dashboard exports and logs |
| R02 | P1 | Reliability | Parallel non-ML signal scans share unsafe DuckDB connection state |
| R03 | P1 | Operations | Unhealthy monitor exits before report persistence; fresh output parents fail |
| R04 | P1 | Coverage | Default HK/SG features/ML scope resolves to zero tickers |
| R05 | P1 | Research integrity | Walk-forward threshold selection can see labels crossing the test boundary |
| R06 | P1 | Signal integrity | Feature/ML consumers still use split-affected raw prices |
| R07 | P1 | Configuration | Custom scan config does not consistently reach v2 candidate generation |
| R08 | P2 | Configuration | Stored/configured thresholds and displayed explanations can disagree |
| R09 | P1 | Operability | Failed or incomplete scans can look like successful “no signals” |
| R10 | P1 | Safety contract | Signal dry-run still scans/infers and can write output |
| R11 | P2 | Multi-market | Signal paths and date resolution are not fully market-aware |
| R12 | P1 goal gap | Product | No end-to-end signal notification or watchlist dashboard workflow |
| R13 | P1/P2 | Deployment | Compose daily restart loop and noninteractive API startup failure |
| R14 | P2 | Latent integrity risk | Shared feature/model identity is ticker-only across markets |
| R15 | P2 | Data semantics | Missing enrichment, event timing and vintage semantics remain incomplete |
| R16 | P2 | Storage risk | Broad schema-error fallback materializes and rewrites whole tables |
| R17 | P2 | Cost/recovery | Silver dedup read failures can resubmit the Bronze corpus to LLM processing |
| R18 | P2 | Model provenance | Schema stamps and model freshness are insufficiently explicit |
| R19 | P2 | Verification | Important signal behaviors are unpinned; fast CI still selects network tests |
| R20 | P2/P3 | Quality of life | Onboarding, validation, scheduling and documentation contain misleading paths |

## Detailed findings

### R01 — Webhook credentials can escape through exports and logs

**Evidence:** Reproduced with a fake URL; exposure is conditional on configuring
a credential-bearing webhook. No actual secret exposure was demonstrated.

`dashboard/exporter.py:20–30,60–73` redacts secret-looking keys but not
`webhook_url`, then publishes settings in `dashboard-data.json` and the Config
page. `core/settings.py:109–116` defines that URL as a plain string.
`cli/commands/admin.py:12–18,93–105` also dumps unredacted settings.
`monitoring/alerting.py:50,54` logs the complete URL on success and failure.
Webhook URLs commonly contain bearer credentials in their path/query.

**Next:** Prefer an explicit safe public-settings projection, or at minimum
redact this field everywhere, including logs and exception text. Test a synthetic
token through JSON, HTML, config export and captured logs. If an actual webhook
was previously published, inspect artifacts/history and rotate it; do not assume
that merely fixing the exporter retracts prior exposure.

### R02 — Disabling ML exposes a native concurrency failure

**Evidence:** Reproduced by reviewers in isolated synthetic subprocesses,
including a scanner-path drill; concurrent use caused exit 139/SIGSEGV.

`signals/scanner.py:59–85` serializes only when an ML generator exists.
Backtest and sentiment generators each retain a DuckDB connection reused by
multiple ticker workers (`signals/generators/backtest.py:29–40,70–75`;
`sentiment.py:30–40,54–58`). These generators do not share one connection with
each other; each shares its own connection across its worker calls.

**Next:** Use sequential scans initially, or correctly isolate each worker's
connection/cursor lifecycle. Test multiple synthetic tickers with ML disabled
in a subprocess so a native crash cannot kill the test runner. This matters
particularly for the simpler non-ML alerting MVP.

### R03 — Monitoring loses the report exactly when health fails

**Evidence:** Reproduced with a fake unhealthy monitor and a temporary directory.

`cli/commands/analysis.py:126–137` raises exit 1 before `save_report()` and the
`finally: close()` block. `monitoring/health.py:505–514` does not create the
report's parent directory. Thus an unhealthy run writes no new report, while a
healthy export to a fresh `site/health-report.json` can fail as well.
`.github/workflows/pages.yml:55–59` runs monitor before dashboard creation and
masks the failure with `|| true`. A previous report can remain misleadingly stale.

**Next:** Persist report and close reliably before choosing the health exit
status; create parent directories at the save boundary. Include overall/per-check
status in the durable report. Test unhealthy output, nested output paths and
stale-report replacement. Preserve the nonzero exit for unhealthy data.

### R04 — Configured HK/SG equities disappear from default features/ML scope

**Evidence:** Reproduced with the repository's ticker configuration.

`pipeline.py:58–72` resolves `hk_sg_equity` via `LONG_TO_SHORT` to `hk_sg`, but
configuration has separate `hk` and `sg` sections. The resolver returned zero
tickers despite **15 active HK and 11 active SG** tickers. Ingestion uses the
correct `PRICE_MARKET_CONFIG_KEYS` mapping (`ingestion/types.py:59–65`), so
this is a downstream scope defect, not proof that ingestion drops the market.

**Next:** Reuse the shared mapping and test the exact default HK+SG union,
including scheduled sessions and feature/ML stage planning. Avoid inventing
another market registry.

### R05 — Walk-forward backtests have an outer-boundary availability leak

**Evidence:** Code-confirmed; synthetic label checks demonstrated that labels
can end beyond their starting row. No claim about a particular live return curve.

`ml/forecasting.py:456–493` loads with availability bounded only by the final
backtest end date, constructs the labeled frame, then uses each preceding
window's validation tail to optimize the next threshold. V2 labels inspect
future bars (`ml/labeling.py:62–102`), potentially including the upcoming test
period. The existing inner fit/validation embargo does **not** purge that
validation tail against the outer test boundary. The misleading outcome can
therefore enter through threshold selection even when estimator fitting itself
uses an earlier slice. `model_trained_at` also records a window start rather
than the actual refit boundary (`forecasting.py:507`).

**Next:** At every retrain, restrict training and threshold-selection labels to
fully matured events available at that decision cutoff; purge crossing event
intervals. Keep incomplete right-edge labels pending rather than shortening
horizons silently. Test interval endpoints, cutoff availability and refit
provenance. Do not present the current accuracy as certified out-of-sample evidence.

### R06 — Raw split discontinuities still contaminate features and labels

**Evidence:** Code-confirmed; synthetic 2-for-1 arithmetic produces a phantom
−50% raw-close return. This is an acknowledged deferred migration in ADR-0011.

`features/engineering.py:63–80,118–134` supplies raw OHLCV to the DAG;
`features/dag/features_03.py:446–447` computes next-day returns from raw closes.
V2 barriers use raw entry close/high/low (`ml/labeling.py:56–70`), and label
repair also recomputes from raw closes (`features/labels.py:425–426`). A split
can consequently become a false loss/stop event and distort indicators.

ADR-0011 deliberately keeps stored prices raw and consumer adjustment opt-in;
this review does not propose silently replacing Bronze prices with vendor
adjusted-close history. The shared adjustment helper and backtest loader's
opt-in already exist, but feature/ML consumers have not completed migration.

**Next:** Agree on read-time, point-in-time adjustment semantics, then migrate
feature/label consumers together with tests, schema/model compatibility and
explicitly authorized repair. Until then flag or exclude action-affected samples
and suppress untrustworthy alerts. Test splits, dividends, missing action
coverage and idempotent repair. Default raw-price backtests need an explicit
warning when action events occur within the evaluated window.

### R07 — Custom v2 scans can use default candidate rules

**Evidence:** Code-confirmed and reviewer synthetic config-seam probe.

The CLI loads `--config`, but scanner construction passes only its `ml` block
to `MetaLabelSignalGenerator` (`cli/commands/intelligence.py:122–131`;
`signals/scanner.py:42–50`). Unless explicit `ml.candidate_strategies` are
provided, `PriceForecaster._load_candidate_strategies()` reads the default
signals YAML (`ml/forecasting.py:122–136`). Custom `backtest.strategies` can
therefore differ from the v2 candidates used in the same scan.

**Next:** Pass selected candidate rules explicitly and record their identity in
training/inference metadata. Test two distinct config files. The original review
recorded ADR-0013 as Proposed and recommended obtaining acceptance before its
boundary change. That status and advice are historical: the owner accepted
ADR-0013 on 2026-10-17, and its implementation/offline-contract review was
accepted on 2026-10-18. The accepted decision, not the pre-approval
recommendation, governs the change.

### R08 — Threshold precedence and explanations do not form a clear contract

**Evidence:** Reproduced by a reviewer with p=0.70, configured v2 threshold 0.99
and stored optimized threshold 0.50: the candidate executed at 0.50.

`ml/forecasting.py:396–417` gives the stored optimized threshold precedence.
`signals/generators/meta_label.py:68–78` can describe the configured threshold
instead. The guide says the config threshold controls execution. V1 additionally
combines model direction, probability thresholds and `min_confidence`, so some
apparently independent knobs interact (`signals/generators/ml.py:68–82`).

Stored-threshold precedence may be intentional; the defect is the undisclosed,
misleading public contract and explanation, not merely the existence of optimization.

**Next:** Define explicit precedence and allow a deliberate user override.
Persist/display the effective threshold and its source. Validate incompatible
threshold combinations and test divergent stored/configured values.

### R09 — “No signals” does not establish a successful scan

**Evidence:** Code-confirmed; several query/config failure cases reproduced by reviewers.

Scanner exceptions are reduced to warnings (`signals/scanner.py:91–117`),
view setup suppresses errors, and query/model failures commonly return `None`.
`signal scan` then formats an empty list without a degraded outcome or nonzero
exit (`cli/commands/intelligence.py:122–141`). Missing data, insufficient
history, missing models, non-trading dates, true neutral conditions and broken
generators are not distinguishable in the result. Some warnings exist; this is
not a claim that every failure is completely unlogged.

**Next:** Add per-instrument/per-generator outcomes and counts: evaluated,
no-condition, missing/stale data, missing model, error, not scheduled. Preserve
partial output, but make all-required-input failure nonzero. Record zero-event
runs too. Notifications must never translate an unknown/error state into “safe,
no action.” Test each outcome using deterministic fixtures.

### R10 — Signal dry-run violates the project's safety contract

**Evidence:** Reproduced with a fake scanner and temporary output file;
construction and scan occurred, output was written, history alone was skipped.

`cli/commands/intelligence.py:111,129–141` treats dry-run as “don't save
history,” but still constructs generators, scans (including ML inference when
enabled), and writes `--output`. Forecaster construction can create a model
directory (`ml/forecasting.py:104–109`). AGENTS.md and ADR-0014 define dry-run
as no persistence or inference.

**Next:** Resolve/validate and print the plan before constructing side-effecting
generators. No inference, model-directory creation, history, report or delivery
writes in dry-run. If a separate “evaluate without saving history” mode is
wanted, name it differently. Test all prohibited seams with spies.

### R11 — Multi-market signal scope is incomplete

**Historical finding and scope note:** This section preserves the original
code evidence and pre-approval sequencing. Owner approval on 2026-10-17 and
ADR-0016 supersede any initial-US-only/expand-later interpretation: identity,
instrument-session behavior, and supported deterministic signal paths for all
five markets were required through C10. At the time of this historical finding,
implementation remained pending; the final all-five-market synthetic review is
recorded in the closure section. There is no multi-market deferral.

**Evidence:** Code-confirmed; actual cross-market corruption not demonstrated.

Backtest signals read `US_EQUITY_DIR`; sentiment reads `US_NEWS_DIR`
(`signals/generators/backtest.py:10,35–40`; `sentiment.py:9,34–40`). The CLI
uses the default US calendar (`cli/commands/intelligence.py:130`;
`cli/_app.py:46–48`). Watchlists/signals have no market identity
(`signals/models.py:17–43`). ML reads shared Gold, so claiming *all* signal
paths are US-only would overstate the evidence.

**Historical next-step recommendation (superseded by ADR-0016):** The
pre-approval recommendation was to be honest about initial US-only alert
support and add market-aware identity/calendars before expanding. Owner approval
on 2026-10-17 instead requires identity, instrument-session behavior, and
supported deterministic signal paths for all five markets through C10; an
initial US configuration is not a deferral. The original suggested tests—HK/SG
union sessions, non-US holidays, unsupported sentiment coverage, and an
explicit Sunday no-session result (the guide's example is `2024-12-01`)—remain
useful evidence criteria.

### R12 — The requested watchlist-to-alert product loop is missing

**Evidence:** Capability gap; code-confirmed scan/monitor/dashboard/API paths.

Signals are formatted and persisted, but not delivered. Health alerting is
separate. Neither dashboard currently presents a watchlist signal inbox.
`pipeline` does not run the scanner. There is no durable notification-delivery
state, changed-condition policy, cooldown, acknowledgement or daily scan digest.
`GET /signals` already reads a day's history (`api/routers/signals.py:17–31`).

History upsert keys `(ticker,date,signal_type)` prevent duplicate history rows
but are not notification idempotency keys; a same-day change can overwrite a
prior state without an append-only alert/audit event (`signals/history.py:24–41`).

The scanner also appends conflicting generator actions without a decision
policy, and SMA deviation is a persistent threshold state, not the crossing
promised in the guide. Hardcoded backtest/sentiment confidence values are not
calibrated probabilities (`backtest.py:127–152`; `sentiment.py:105–120`).
V1 metadata says five-day horizon by default despite next-day training.

**Next:** Implement the modest, staged product slice below. Distinguish a
research signal, an alert event and a delivery attempt. Do not average
incomparable “confidence” scores or label every repeated state as a new buy.

### R13 — Deployment recipes do not match their execution model

**Evidence:** Code-confirmed; noninteractive API abort reproduced in the initial
review. Docker services/images were not built or launched.

- **P1:** `docker-compose.yml:58–64` runs a one-shot pipeline with
  `restart: unless-stopped`, not the scheduled job its comments describe.
  Completion causes restart/repeated work. Repeated provider calls depend on
  partition coverage; they are not guaranteed on every restart.
- **P2:** `Dockerfile:86` binds the API to `0.0.0.0`, triggering interactive
  confirmation (`cli/commands/api.py:30–45`); non-TTY stdin aborts. Preserve
  the unauthenticated-exposure guard rather than deleting it casually.
- Compose query uses `--query all`, which is not a named query. Omit the option
  for the existing “all queries” behavior. S3 placeholder paths still reflect
  a pre-medallion layout (`docker-compose.yml:20,30`).

**Next:** Choose one explicit scheduler for one-shot jobs. Add a deliberate
noninteractive API deployment policy with network/auth protection, and test the
actual image entrypoint and Compose commands in CI.

### R14 — Instrument identity is ticker-only across shared tables and models

**Evidence:** Latent risk; reviewer config checks found no active symbol collision
in the current configured universe.

Market is present in the feature union but projected away before computation
(`features/engineering.py:63–80,118–134`). Gold keys, feature reads, label
repair grouping and model filenames remain ticker-based
(`ingestion/writers.py:41–43`; `ml/feature_loader.py:69–79`;
`features/labels.py:425–426`; `ml/forecasting.py:573–614`). CN and KRX can use
identical six-digit symbols. A future overlap can mix observations or overwrite
shared-key outputs.

**Next:** Immediately reject ambiguous cross-market duplicates. A durable
market/instrument key requires an accepted ADR, migrations, reader/model
compatibility, catalog changes and collision tests. Adding a market label only
to the UI does not solve the storage/model identity problem.

### R15 — Missing enrichment and historical availability remain ambiguous

**Evidence:** Code-confirmed semantics; no blanket claim that enriched history leaks.

News/social/SEC paths zero-fill missing values, and date-equality joins can
lose off-session observations (`features/dag/enrichments_04.py:227–259,291–334`).
SEC's bounded source window can omit the prior filing needed for carried state
(`:495–550`). Predictor release/vintage availability is not established by the
loader's label-availability filter (`ml/feature_loader.py:64–79`). Macro date/value
joins do not establish a historical unrevised information set (`enrichments_04.py:602–659`).

**Next:** Specify coverage, source availability, carry/staleness limits and
session mapping per source. Add coverage/age flags instead of treating no feed
as measured neutral. Map events to the first session whose decision cutoff can
actually observe them. Preserve/label vintage limitations; do not describe
revised historical macro as point-in-time data without evidence.

### R16 — Schema recovery is broad and expensive

**Evidence:** Code-confirmed; concurrent lost-update scenario is a risk, not reproduced.

`storage/delta.py:172–193` treats any error mentioning “schema” or “column” as
schema mismatch, reads the entire existing table and overwrites an evolved
seed before re-merging. The replacement is an atomic Delta commit, but the
read/transform/overwrite sequence is not tied to a checked source snapshot.
An intervening writer and table growth make concurrency and memory behavior
important; “atomic commit” alone does not prove safe read-modify-write semantics.

**Next:** Narrow recognized errors, bound migration work, and verify optimistic
concurrency/version checks or adopt supported additive schema evolution.
Test concurrent evolution/merge and unrelated column errors before claiming
all rows are preserved. This overlaps historical audit F21/W06.

### R17 — Dedup read errors can amplify paid processing

**Evidence:** Code-confirmed; no paid processing was invoked in review.

`ingestion/bronze_silver.py:190–213` catches any Silver dedup read failure and
returns an empty processed-ID set at debug level. The caller can then submit
the unprocessed-looking Bronze corpus to `process_fn` (`:120–136`), including
LLM work. Missing table and corrupt/unreadable table are not equivalent.

**Next:** Only absent Silver should mean empty history. Fail the affected stage
on unreadable dedup state, report the cause, cap batch size and expose a planned
item/cost budget before paid reprocessing. Test read corruption and bounded replay.
This overlaps historical audit F22/W06.

### R18 — Model/schema provenance needs clearer enforcement

**Evidence:** Code-confirmed provenance gap; older-model mismatch not reproduced.

`run_prediction_job` defaults missing schema information to the current feature
constant (`ml/__init__.py:73–82`). `predict()` returns no schema version and
training metadata does not record it (`ml/forecasting.py:429–435,636–684`).
This does not identify the model's actual training schema. The as-of model
resolver prevents choosing future-trained models but has no inference-time age
bound (`:598–614`).

**Next:** Record training and inference schema versions separately; verify
feature names/dtypes and semantic compatibility. Define model expiry/retrain
policy and show age in alerts. Treat unknown legacy provenance explicitly,
not as current compatibility. Align next-day model horizon with signal metadata.

### R19 — Green gates do not pin the risky signal behavior

**Evidence:** Read tests/CI; focused suite below passes despite these findings.

`tests/unit/test_backtest_generator.py:46–70` permanently skips the real-data
BUY/SELL test and conditionally asserts only if a signal exists.
`tests/integration/test_signal_integration.py:8–45` can pass with zero signals;
its history roundtrip is conditional and uses default paths rather than an
isolated synthetic lake. No focused test exercises the real v2 generator's
threshold/config decision contract.

`.github/workflows/quality.yml:62` selects `not integration`, which still admits
network-marked unit tests such as
`tests/unit/test_ingestion_orchestrator.py:406–445`. Pytest defaults do not
exclude network tests either; the collection hook only marks directories.
CI also lacks `ruff format --check` despite the local validation contract.

**Next:** Add deterministic BUY, SELL, no-condition, error and conflicting-rule
fixtures, v2 config/threshold tests, subprocess concurrency, and isolated
end-to-end scan-to-delivery tests. Explicitly exclude network tests from fast
CI/default policy; retain a separately authorized network suite. A help-scan
smoke test is useful but does not validate an operational workflow.

### R20 — Smaller usability and documentation problems compound friction

| Area | Evidence | Recommended correction |
| --- | --- | --- |
| Demo target | `Makefile:200–221`: `make demo` continues into setup/dev-setup/validate; confirmed with `make -n` | Separate demo and quickstart targets; no surprise environment reinstall |
| Dead examples | `Makefile:191–197` references absent `examples/` scripts | Replace with supported commands/tests or remove targets |
| Fresh install | Quickstart uses dotenvx without listing it; dashboard/backtesting need optional groups | Provide an offline demo and explicit minimal/optional install paths |
| Config validation | `cli/commands/admin.py:40–90` defaults to ticker-only validation although help names three files | Validate all by default or clearly label selected scope |
| Dropped warnings | `config/validators.py:56–58` returns errors but drops group-reference warnings | Surface warnings and missing ticker membership prominently |
| Watchlist input | `Watchlist.validate_against_tickers` exists but scan does not call it; YAML loaders pass directly to dataclasses | Validate at scan entry; reject malformed YAML, typos and invalid thresholds with actionable messages |
| Scheduling | `devtools/sync_schedule.py:23–55` copies cron only; dashboard labels it with configured timezone | State scheduler timezone explicitly; GitHub Actions cron is UTC; do not imply timezone conversion occurs |
| Health guide | Monitoring guide says webhook setting is absent, but current CLI wires `settings.alerting.webhook_url` | Document the existing knob and distinguish health from trade-signal alerts |
| Report path | Pipeline guide uses `health_report.json`; dashboard expects `health-report.json` | Use one canonical name throughout |
| Signal guide | Describes SMA crossings and a Sunday scan date; cron writes JSON inside `data/signals/` Delta root | Describe deviation states, use a session date, put rendered reports outside table roots |
| Decision status | ADR-0011 header/index say Accepted; footer says Proposed | Reconcile status so implementation gates are unambiguous |

## Recommended product direction

The active [portfolio roadmap](../plans/20260804-portfolio-roadmap.md) optimizes
for a research/engineering showcase. The user's current goal is different:
**maintain equities of interest and receive timely, understandable opportunities**.
Make that operating loop the next milestone; research comparisons can supply
supporting evidence later. Do not start another large architecture migration
or frontend rewrite to achieve it.

### 1. One explicit watchlist scope

Keep YAML as a portable backing format initially. Add a small management CLI
and an existing-dashboard view rather than a separate service.

Each tracked equity should have:

- Canonical instrument identity, exchange/market and display name.
- Watchlist/group, tags, interest thesis/notes and enabled rules.
- Optional price zones and alert preferences.
- Data coverage, latest completed session, last successful scan and model age.
- A distinction between “watching” and “held.” Position size/cost basis can be
  optional later; a watchlist is not automatically a portfolio ledger.

Adding an equity should validate symbol/source/market and show history readiness.
If history is missing, offer a scoped, explicit backfill plan—not an automatic
network or persistence side effect. The pre-approval draft proposed supporting
US equities first and expanding later; owner approval on 2026-10-17 superseded
that sequencing. ADR-0016 requires all five markets through C10; an initial US
configuration is not a multi-market deferral.

**Historical proposal:** `equity watchlist list/add/remove` management remains
pending implementation. The read-only `equity signal history` and `equity doctor`
commands have since been implemented and reviewed in the current register.

### 2. Start with understandable rules, then earn ML alerting

Begin with opt-in price-zone and simple technical conditions whose reason can
be reproduced from displayed inputs. Fix the current threshold-state versus
crossing distinction. Keep sentiment as context until source coverage and
availability are visible. Require evidence before promoting ML output to an
alert gate.

For every event display the session/as-of time, observed price, rule/version,
threshold and actual indicator values, previous state, data freshness and
limitations. Separate a deterministic rule-strength score from an empirical
probability; “70 confidence” from a constant is not a 70% chance of profit.

Show conflicting rules side by side, or define a transparent conflict policy.
Do not silently manufacture a single authoritative BUY from disagreements.
A SELL candidate for an unheld equity should not imply a short order. Add
“no condition,” “not evaluated,” and “unavailable” statuses rather than using
HOLD to hide missing inputs.

### 3. Add one durable signal delivery path

Extend existing orchestration/alert seams rather than adding Kafka, Celery,
a plugin framework or a new top-level package by default.

```text
Resolve watchlist/session scope
    → refresh scoped data/features (authorized behavior only)
    → verify per-instrument readiness/freshness
    → scan deterministic rules
    → persist run outcomes + changed-condition events
    → attempt configured delivery
    → record delivery outcome
    → render watchlist/inbox/daily digest
```

Choose one initial channel (for example Telegram, email, ntfy, or a generic
webhook). Existing health webhook JSON is not automatically a Slack/Discord/
Telegram-native message; implement/test the chosen adapter's payload contract.
Keep channel credentials private and health alerts separate from opportunity alerts.

Delivery policy should include:

- A stable event identity based on instrument, rule/version, session and state
  transition—not just current history-row identity.
- A small durable outbox/delivery ledger with pending/sent/failed status,
  retry counts and redacted errors. External delivery cannot be assumed
  exactly-once; retry an ambiguous send without pretending duplicates are impossible.
- Per-rule cooldown/re-arm behavior, optional quiet hours and daily digest.
- Durable “last successful scan” and an independent failure/stale-run warning,
  so a crashed process cannot silently masquerade as a quiet market.
- Best-effort acknowledgement/mute controls after basic delivery works.

Persist new auxiliary artifacts under `data/<name>/` with Pydantic write models,
not in cataloged medallion tables. Existing `SignalRecord` whitelist must be
updated deliberately for new metadata. Any storage/schema/import boundary
change requires the corresponding ADR and change-matrix work before implementation.

### 4. A useful daily screen, not just a data-platform status page

Extend Streamlit first with Watchlist, Signals and Delivery Status views. Reuse
the read API where useful. Each watchlist row should show current price/session,
active condition, why it fired, conflicts, freshness, and next action/status.

The daily digest should answer: “What changed?”, “What needs attention?”, and
“What could not be evaluated?” Never imply an unavailable ticker is healthy.
Keep private positions, notes and delivery credentials out of public Pages
artifacts; an authenticated/private UI is a different surface from a public demo.

## Suggested implementation sequence and acceptance gates

### Phase A — Safety and correctness first

**Scope:** R01–R10, daily restart policy, and deterministic regression tests.
Separate small operational fixes from research/schema migrations.

**Done when:** configured webhook tokens cannot appear in exports/logs;
unhealthy reports persist; dry-run has no prohibited effects; HK/SG scope is
correct; non-ML scans cannot share unsafe connections; v2 selected config and
effective thresholds agree; broken scans report degraded/failed coverage.
Corporate-action and outer-fold availability policies are tested before any
ML-based investment claim or history repair. No broad repair is implicit.

### Phase B — Minimal daily watchlist alert MVP

**Scope:** One small US watchlist, one understandable rule set, one delivery
channel, one explicit scheduler, run/coverage outcomes and durable dedup/retry.
ML remains optional/off for alert decisions until Phase A's integrity gates hold.
No broker integration or automated execution.

**Done when:** a synthetic add-equity → ready data → triggered condition →
notification → recorded outcome works end to end; a second identical run does
not create a second local event or needless send; a changed/re-armed condition
can notify; stale data suppresses opportunity alerts but raises a readiness
warning; delivery failure is visible and retryable; dry-run sends nothing.
Run several scheduled sessions in shadow/paper mode before trusting real alerts.

### Phase C — Usable personal tracking

**Scope:** Watchlist management, group filtering, notes/price zones, history,
explainable inbox, digest, quiet hours and delivery visibility in Streamlit.

**Done when:** daily operation no longer requires editing YAML or reading raw
logs; every tracked instrument has a visible evaluation status; private state
never enters public export; adding a ticker explains any backfill requirements.

### Phase D — Validated ML and multi-market expansion (historical roadmap framing)

**Historical sequencing note:** This phase grouping predates owner approval on
2026-10-17; its expansion framing is not a multi-market deferral. ADR-0016
requires identity, instrument-session behavior, and supported deterministic
signal paths for all five markets through C10. The accepted ADR implementation
work remains pending; this old roadmap grouping does not gate that scope.

**Scope:** Point-in-time enrichments, instrument-key migration, actual model/
schema provenance and expiry, calibrated probabilities, and honest cost-aware
out-of-sample comparisons against simple baselines.

**Done when:** candidate alerts have a reproducible rule/model/data identity;
all retrain decisions use available labels/predictors; reports disclose fees,
slippage, adjustment policy and universe/coverage limitations. Measure precision
and returns after costs, turnover/drawdown, and alert fatigue—not accuracy alone.
Track false positives and outcomes without cherry-picking or asserting alpha
from an unrepresentative watchlist.

## Initial review verification

Focused offline run before implementation:

```bash
uv run --no-sync pytest \
  -o addopts='--strict-markers --strict-config -ra --import-mode=importlib' \
  -m 'not network and not integration' \
  tests/unit/test_signal_config.py tests/unit/test_signal_models.py \
  tests/unit/test_signal_scanner.py tests/unit/test_signal_history.py \
  tests/unit/test_backtest_generator.py tests/unit/test_ml_generator.py \
  tests/unit/test_sentiment_generator.py tests/unit/test_price_forecasting.py \
  tests/unit/test_label_lifecycle.py tests/unit/test_ml_validation.py \
  tests/unit/test_price_adjustment.py tests/unit/test_alerting.py \
  --junitxml=/tmp/equity-review-20261016.xml \
  > /tmp/equity-review-20261016-tests.log 2>&1

uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src
```

Results: **111 passed, 1 skipped** (the permanently skipped real-data signal
test); Ruff clean; 268 Python files formatted; mypy clean across 163 source
files. `--no-sync` reused the review environment; these are not lockfile
reinstallation results. Requests dependency-compatibility and transitive
`pkg_resources` deprecation warnings were also observed; investigate separately
rather than treating passing tests as dependency health certification.

Temporary synthetic probes confirmed dashboard webhook redaction failure,
zero HK/SG scope, unhealthy-report omission, nested report-path failure, and
signal dry-run scan/output side effects. Reviewer subprocess probes confirmed
the DuckDB concurrency failure; reviewer checks also exercised v2 threshold and
candidate-config behavior. These probes were not added as committed regression
tests. LSP/structural diagnostics were advisory; generic unchecked-call findings
were not promoted into confirmed defects without reviewing the relevant path.

## Remaining rollout decisions (historical pre-approval planning)

This section records planning before the 2026-10-17 owner approval and is
retained as historical context, not current implementation scope or a
multi-market deferral. ADR-0013 and ADR-0015 through ADR-0018 now record the
accepted implementation contracts; ADR-0016 requires all five markets through
C10, with no multi-market deferral. Implementation and independent review
remained pending at this historical pre-approval snapshot; implementation and
offline-contract acceptance is recorded above, while operational activation
remains separately gated. The following list preserves the earlier unresolved
product/policy choices:

1. Initial market scope, notification channel and cadence.
2. Desired rules and whether tracked equities are candidates, holdings, or both.
3. Adjustment/identity policy and any separately authorized historical repair.
4. Live-delivery enablement after a private deterministic MVP and paper/shadow
   period; ML-based alerts remain gated on research-integrity validation.
