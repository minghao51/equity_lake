# Next Agent — P02 Fixture-Only Implementation

**Recorded:** 2026-11-07 UTC

**Status:** The owner approved the bounded P02 plan and accepted ADR-0026 and
ADR-0027. Next implementation scope is **P02 only**, under ADR-0024 as amended
by ADR-0026. No P02 code has been written in this pickup. ADR-0027 acceptance is
policy approval, not permission to implement E01-6 in the same stage.

**Checked baseline:** HEAD `603fc8d` on `main`, with carried E01b.1/E01b.2/E01c
and documentation changes. After this acceptance/handoff update, expect
25 modified + 11 untracked, nothing staged; verify actual status rather than
trusting counts. No commit has been authorized.

## 1. Read first and preserve the tree

1. `AGENTS.md` and the status block in [docs/README](../README.md).
2. [ADR-0024](../decisions/0024-private-portfolio-ledger-and-accounting.md) and
   [accepted ADR-0026](../decisions/0026-portfolio-projection-precision-marks-and-reconciliation.md)
   in full. These are the frozen P02 policy, not suggestions to redesign.
3. [Specification §5.3](../plans/20261102-01-portfolio-and-research-implementation-spec.md#53-p02--projections-and-valuation-contract),
   §9 and the latest §10 approval entry. Earlier dated drafting gates are history.
4. [Nov07-01 pickup](20261107-01-portfolio-projections-and-open-items.md) §1–§3
   for carried implementation inventory/evidence, and §8 for worker/reviewer rules.
5. [ADR-0027](../decisions/0027-row-bound-price-source-provenance.md) for the
   separate E01-6 boundary, not as an instruction to implement it.
6. Existing `portfolio/{models,store,csv_import,__init__}.py`, portfolio CLI,
   exact-venue calendar helpers, relevant tests and matching tool/engineering
   skills before editing their consumers.

At pickup capture `git status`, staged/unstaged diff identity and hashes of all
modified/untracked paths outside the repo. Preserve untracked files too. Do not
stash/reset/checkout/clean the tree or create a fresh worktree from HEAD that
omits the carried work. Review P02 deltas against this dirty stage-start snapshot,
not against HEAD alone.

**Do not restart:** B00, F01–F04, P01a/P01b, E01a, E01b.1, E01b.2 or E01c.
The E01b/E01c paths and their protocol-v3 semantics must survive byte-for-byte
unless an expressly listed shared test/doc receives a scoped P02 addition.
Historical green counts are not fresh test evidence. No new E01 work follows
from running integration regression tests over the final P02 tree.

## 2. Confirm a minimal file plan, then implement P02 only

Present the concrete file/function/test plan before code. The bounded P02 plan
and its accounting policy are already approved; do not ask to reopen them.
Proceed within that scope, and stop for owner approval if a dependency, policy
ambiguity or required path change would exceed it. Do not change an accepted
ADR to match implementation; a policy change needs an amendment.

Use these as sequential work packages, not concurrent writers or separate
permission to expand scope:

| Package | Deliverable | Must prove before moving on |
| --- | --- | --- |
| P02a — replay | Pure deterministic positions/cash/basis/gain/fee/net-income projections | Hand oracle, exact Decimal behavior, unknown-basis propagation, temporal/refusal rules, restart/order invariance |
| P02b — marks and reconcile | Typed stored fixture marks, exact-MIC session age, complete/partial valuation; canonical statement comparisons | Mark/no-future/staleness/blocking rules, no partial denominator, tolerances/omissions, no writes |
| P02c — CLI and integration | Working `positions`, `summary`, `reconcile`; guides/help/architecture/ledger | Honest output/status/exit codes, privacy/isolation, unchanged import, independent review and final gates |

Do not register any of the three commands before its real projection exists.
No private UI, API route or operational mark producer is part of any package.

### Intended implementation paths

New modules:

- `src/equity_lake/portfolio/projections.py`
- `src/equity_lake/portfolio/marks.py`
- `src/equity_lake/portfolio/reconcile.py`

Existing product paths:

- `src/equity_lake/portfolio/__init__.py` — additive exports only if needed.
- `src/equity_lake/cli/commands/portfolio.py` — the three frozen read commands;
  preserve canonical import behavior and existing sub-app wiring.
- `src/equity_lake/core/calendar.py` — only if necessary, a minimal exact-venue
  scheduled-close helper for mark validation/age. Reuse the existing mapping;
  do not duplicate a venue registry or change existing session semantics.

Tests:

- New `tests/unit/test_portfolio_{projections,marks,reconcile}.py`.
- `tests/unit/test_portfolio_cli.py` — working read-command contracts replace
  the historical import-only registry expectation; preserve import regressions.
- `tests/unit/test_cli_unified.py` — help scans only, preserving carried edits.
- `tests/unit/test_calendar.py` — only for the narrowly needed calendar helper.
- Existing ledger/import/boundary tests are regression inputs; do not weaken
  them or rewrite P01 event/import/store code to make P02 convenient.

Documentation:

- `docs/user-guide/20261103-private-portfolio-ledger.md`
- `docs/user-guide/20260406-cli-reference.md` — scoped portfolio additions only.
- `docs/developer/architecture/ARCHITECTURE.md` — additive portfolio structure.
- `docs/plans/20261102-01-portfolio-and-research-implementation-spec.md` — dated
  stage outcomes, actual evidence, blockers and next gate.
- This handoff may gain a dated outcome; `docs/README.md` only its current status
  block. Preserve the owner's remaining prose and all carried documentation.

The coordinator must freeze exact allowed paths in each worker brief. If another
path is necessary, explain why and obtain scope approval before changing it.
No Settings/env/dependency addition, new top-level package, broker plugin,
lake schema or catalog dataset is expected.

## 3. Frozen acceptance checklist

The complete contract is ADR-0026; this checklist does not replace it:

- One invented `us_equity` account, USD; preserve SQLite schema v1, event fields
  and read-only store. Fixtures and test databases live only under temporary roots.
- Sequence order, aware UTC `--as-of`, effective-date reconstruction, no implied
  historical-knowledge proof. Refuse ambiguous timelines, oversells, margin and
  inconsistent monetary relationships without rewriting history.
- Decimal local precision 256; no unexpected rounding of cash/quantity or exact
  arithmetic. Partial-sale basis rounded directly half-even at 38 fractional USD
  digits; full liquidation consumes the exact residual. Display cents never feed
  calculations. Fees counted once; external flows are not profit.
- Unknown pooled basis and affected realized/aggregate gains remain unavailable,
  even across liquidation/reopening; valid marks can still establish NAV when only
  basis is unknown. Net dividends are known where supplied; gross/withholding are
  unavailable from schema v1, not guessed.
- Strict versioned stored mark JSON with exact identity, source, session-close
  timestamp, raw/unadjusted price and explicit `synthetic|unknown` provenance.
  Fixture writers validate before serialization; consumers validate on read.
- Latest eligible mark at or before `T`; no wrong venue/currency, adjusted-price
  fallback or same-time conflict selection. Missing/invalid required valuation
  coverage never disappears from the denominator.
- Age counts completed exact-MIC sessions, including early closes and holidays;
  default bound one session, optional stricter zero, never above one. Known
  quantity-adjustment blocks withhold valuation; P02 does not discover splits.
- Cash/quantities/valid marked subtotal survive missing/stale marks, with named
  blockers in the report. Complete NAV/allocation/valuation-dependent P&L do not.
  Do not confuse missing basis with missing market value, or subtotal with total.
- Canonical statement CSV only; no marks required for reconciliation. Inclusive
  unrounded quantity tolerance `0.00000001` share, cash tolerance `0.01` USD.
  Missing required lines/time disagreement are unavailable; never balance the
  ledger to agree. Keep matched/mismatched/unavailable line and overall states.
- Native read CLI: required `--as-of` and `--marks` or `--statement`, neutral
  `--account`, help/Annotated options; exit 1 on required failure/unavailability
  or mismatch. Partial reports are explicit, not successful complete results.
- No DB/directory/sidecar/mark/cache/checkpoint creation by reads or dry-run.
  Filesystem snapshots and DB hashes prove this on success and rejection paths.
  Private reports are local output, never logs/errors/telemetry/public exports.

**Independent hand oracle:** opening 10 shares at total basis 1000 plus cash
1000; buy 5 at 120 fee 5 → cash 395, quantity 15, basis 1605; sell 4 at 130 fee 2
→ cash 913, quantity 11, basis 1177, realized gain 90; net dividend 11 and mark
125 → cash 924, value 1375, NAV **2299**, unrealized **198**, total gain **299**.
Add fractional/repeating-cost, half-even, full-close, unknown-basis, mark-conflict,
future/age/holiday, statement-boundary and no-write counterexamples independently
of implementation helper outputs.

## 4. Worker, review and final verification

Use one bounded writing worker at a time, with explicit allowed paths and no
commits. A separate adversarial reviewer invocation reviews each dirty package
delta against the same frozen contract and stage-start snapshot. Reviewer first
line must be exactly `PASS`, `NEEDS-REVISION` or `BLOCKED`; resolve and re-review
changed behavior. Verify installed preset compatibility before dispatch; do not
change agent definitions, credentials or models as a side effect. Missing
independent reviewer access is a blocker, not coordinator self-approval.

Checkpoints belong outside the repo, e.g. `/tmp/p02a-worker-checkpoint.md`;
sanitized summaries may use the existing ignored progress directory. The
coordinator owns one-rule mutation proofs: back up the current dirty file, show
a named test fails, restore from the backup, and prove `diff -q` identical. No
pytest overlaps another pytest or a mutation. Never restore from HEAD.

Focused checks: new projection/mark/reconciliation tests, portfolio ledger/import/
CLI regressions, unified CLI help, import boundaries and calendar dependants if
changed. Use active LSP diagnostics on changed code. Then follow spec §9:
serial suite, then parallel suite, ruff check/format, strict mypy, Markdown/link
and whitespace checks, with complete logs and JUnit summaries outside the repo.
Run offline suites only; do not enable network/integration broadly. Final gates
validate the integrated tree; they are not instructions to redo completed stages.

No new catalog row or pipeline change is expected. If performing the prescribed
catalog-regeneration consistency gate, run it against an isolated temporary copy
and prove byte identity; do not mutate runtime `data/` in this pickup. A
non-identical catalog is a blocker to investigate, not permission to hand-edit it.
Reuse captured summaries for unchanged suites. Record actual pass/skip/failure
counts and paths, not historical counts or JUnit totals mislabeled as passes.

Finish with stage diff identity, reviewer verdict/findings, mutation+restore proof,
exact tests/gates, docs/privacy/compatibility results and remaining limits. Stop
for owner implementation acceptance. Do not stage or commit anything or advance
to E01-6, P03, D01 or research experiments automatically.

## 5. Separate gates that remain closed

- **E01-6:** ADR-0027 is accepted, but its exact source/summary encodings, source
  IDs, golden fixtures, old-v3/new-v4 reader compatibility and bounded file plan
  still need the separate implementation-planning gate. Do not touch Bronze,
  schemas/validators, seeders, research protocol or provenance in P02. Current
  `unknown` is correct, and E01 remains incomplete.
- **D01:** licensing/retention, source access, spend and target-root authorization
  remain required. No provider/source/LLM processing or live fetch is authorized.
- **P00-2/P00-4:** no split support, ledger migration/backfill/reset, backup/restore
  automation or compaction. **P03/R00–R02/H00:** no UI, experiment, training/refit,
  deployment or public exposure follows from this pickup.
- No real/private account or broker access, source calls, runtime lake/model
  writes, W&B changes, commits or freshness widening. Worker/reviewer agent use
  is the engineering review process, not permission for application LLM/provider
  calls or data fabrication.

## 6. Paste into the next agent

```text
Pick up docs/handoffs/20261107-02-p02-fixture-only-implementation-pickup.md.
The bounded P02 plan is approved; ADR-0026 and ADR-0027 are Accepted.
Implement P02 ONLY under ADR-0024 + ADR-0026 and spec §5.3: deterministic
average-cost replay, unknown basis, stored raw as-of fixture marks/session
staleness, reconciliation, and working portfolio positions/summary/reconcile.

First inspect git status and preserve all carried E01b.1/E01b.2/E01c and docs,
including untracked files. Snapshot the dirty baseline outside the repo; do not
restart B00/F01–F04/P01a/P01b/E01a/b/c or restore from HEAD. Read the handoff and
ADRs fully, present the minimal allowed-file/test plan before code, then proceed
within the approved scope. Stop for approval of any deviation or ambiguous policy.

Use one writer, a separate adversarial reviewer per dirty package with a formal
PASS/NEEDS-REVISION/BLOCKED verdict, external checkpoints, coordinator mutation
proofs and serialized tests. Prove the NAV 2299 / gain 299 hand oracle, edge
cases, privacy and read/dry-run no-write behavior; capture final gates and stop
for owner implementation acceptance. No staging or commit is authorized.

ADR-0027 acceptance does NOT authorize E01-6 implementation; its own bounded
plan/encodings/compatibility gate is outstanding. Leave research provenance
unknown. D01, P00-2/P00-4 and P03 stay gated. No private account, provider/LLM
processing, real ingestion, refit, migration/backfill/reset, runtime data writes,
deployment, W&B changes or freshness widening.
```

## Dated outcome — 2026-11-07

P02a/P02b/P02c are **implemented, independently reviewed and accepted by the owner
(2026-11-07, "accept on the delivered items")**; nothing is staged or committed.
Acceptance is implementation acceptance only — it is not commit authorization and
not permission to continue into E01-6, P03 or D01.

- Delivered: `portfolio/projections.py`, `portfolio/marks.py`,
  `portfolio/reconcile.py`, additive `portfolio/__init__.py` exports, one
  `core/calendar.py` helper (`instrument_session_close`), and the frozen
  `portfolio positions|summary|reconcile` commands. No Settings key, dependency,
  catalog row, lake schema, `cli/_app.py` or import-contract change.
- Review: one independent adversarial reviewer per dirty package, formal verdict
  line each. P02a needed two revision rounds, P02b one, P02c three — every finding
  was a real defect (global-context arithmetic in aggregation, totals and
  valuation; fees lost after liquidation; duplicate/empty statement headers;
  identities, filenames and ledger metadata reaching stderr) and every fix has a
  named regression test. Final verdicts: PASS, PASS, PASS.
- Evidence: hand oracle NAV 2299 / unrealized 198 / total gain 299; three
  coordinator mutation+restore proofs (`diff -q` identical); serial suite 2356
  passed / 17 skipped / 97 deselected, parallel suite 2356 passed / 17 skipped;
  ruff, strict mypy, `make lint-md`, `git diff --check` clean; catalog
  regeneration byte-identical against an isolated `/tmp` path; LSP clean on the six
  changed source files. Logs and the stage checkpoint are under `/tmp/p02-*`.
- Carried work: a content-hash check against the stage-start snapshot shows only
  the 11 P02 paths differ, so E01b.1/E01b.2/E01c and the documentation carry over
  byte-identically.
- Residuals: no operational mark producer, split accounting, backup/migration or
  P03 console. Pre-existing and deliberately untouched: the canonical *import* CSV
  parser still accepts a duplicated header column name. E01-6 remains unimplemented
  and separately gated; research provenance stays `unknown`.
