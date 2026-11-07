# Next Thread — Research Readers and Portfolio Projections

**Recorded:** 2026-11-06 UTC
**Status:** **Spent — superseded 2026-11-07.** E01b.1 (attempt records, latest-success
readers, no legacy writes), E01b.2 (`equity report backtest` migration) and E01c
(canonical universe, read-seam snapshots, protocol v3) were implemented from this
pickup and are complete but uncommitted. The live queue is
[20261107-01](20261107-01-portfolio-projections-and-open-items.md); the body below
is retained as the historical E01b specification and must not be restarted.
**Original status:** Current pickup and durable task queue; no implementation in this update.
**Checked baseline:** `603fc8d` on `main`; working tree was clean before this
handoff-only refresh. Inspect actual `git status` at pickup and preserve any
subsequent changes, including uncommitted documentation from this refresh.
**Plan and outcomes:** [Implementation specification / stage ledger](../plans/20261102-01-portfolio-and-research-implementation-spec.md).
**Evidence:** [A01–A11 audit](../finding/20261102-product-readiness-audit.md),
[D00 source-feasibility packet](../plans/20261103-01-d00-offline-source-feasibility.md).
**Earlier pickup:** [Nov02 handoff](20261102-01-portfolio-and-research-pickup.md)
retains the original dispatch protocol and historical baseline. Its B00/F01
starter is spent; use this document instead.

## 1. What is complete

| Stage | Delivered | Commit / record |
| --- | --- | --- |
| B00 / ADR-0023 | Carried dirty containment preserved, then committed separately | `1b380af`; accepted ADR-0023 remains binding |
| F01–F04 | Fixed-share benchmark, local/deterministic momentum plus tie fix, honest signed cost card, truthful arena exit/failure policy | `df4e423` |
| P00 / E00 | Accounting/privacy and immutable research-evidence decisions accepted | ADR-0024 / ADR-0025, accepted at `f726140` |
| D00 | Offline feasibility packet for US/yfinance and CN/akshare-efinance; independently corrected/reviewed | Packet linked above; no provider call |
| P01a | Private SQLite ledger core, exact Decimal events, idempotent atomic CSV import, no-write preview | `0f6d771` |
| P01b | `equity portfolio import`, help scans, user guide, MkDocs navigation | `aeabe4b` |
| E01a | Dated companion to equity Series, immutable per-pair evidence writer, manifests/checksums/validation | `603fc8d` |

E01a is a completed **write-side substage**, not completion of the whole E01
contract. Latest-success/last-attempt behavior and reader compatibility are not
implemented. Do not rerun B00 or rebuild the accepted F/P stages from scratch.

## 2. Owner decisions already made

- Account: one private **`us_equity` / USD** account; average-cost book
  accounting, explicit unknown basis, exact `(market, MIC, symbol)` identity.
- Import: **canonical CSV only**, no broker mapping/plugin framework. Four CLI
  names frozen: `import`, `positions`, `summary`, `reconcile`. Only `import`
  exists; the other three must land with P02 projections, not empty stubs.
- Splits: reject unsupported events and block affected valuation. A future split
  accounting amendment (**P00-2**) remains open; do not silently implement it.
- Backup/migration/compaction (**P00-4**) remains open, not authorized.
- E01 decisions are frozen in accepted ADR-0025: scope-only run identity, latest
  successful comparable evidence, legacy-read compatibility, read-time fixture
  hashing, W&B out of scope, no new export surface. Do not reopen settled policy
  merely because implementation needs more than one bounded stage.
- D01 candidate: US/yfinance, raw/unadjusted, subject to licensing/retention and
  a bounded plan. CN adjusted/action-dependent use remains evidence-blocked.
  **Candidate selection is not authorization for source calls or ingestion.**
- No paid market-data purchase. GenAI/LLM spend may be considered for enrichment,
  not to obtain/fabricate market data; no actual call or unlimited budget follows
  from this handoff. Dry-run stays non-generating and non-persisting.
- No private-account import, live delivery, training/refit, freshness widening,
  migration, lake reset or deployment is authorized. Keep the seven-day cap.

## 3. Remaining queue and entry gates

| Order / stage | Next deliverable | Entry gate / stopping rule |
| --- | --- | --- |
| **1 — E01b** | Latest-success and last-attempt accounting, read-only legacy compatibility, `/findings` and `/backtests` compatibility | Start with bounded read-only discovery; present a minimal file/behavior plan for approval before code. Preserve accepted ADR-0025; W&B stays out |
| **2 — P02** | Deterministic positions/cash/basis/P&L, stored marks, reconciliation; corresponding three CLI commands | Approve a bounded synthetic-fixture plan under ADR-0024/spec §5.3; freeze precision/ordering, mark/session staleness and statement tolerances; no provider dependency or real account needed |
| **D01 — separately gated** | Licensing/retention evidence and scoped US raw-data vertical slice | Present the plan and obtain explicit authorization for any network/source use, target root and spend; never relax strict adjusted/PIT proof |
| P03 | Loopback-only private Streamlit console | P02 plus explicit UI plan; research panel depends on completed E01 |
| R00 | Synthetic controls and compute-scale protocol | Completed E01 plus approved hypothesis, timing, seeds, metrics, numeric time/RSS budget; no operational fitting |
| R01 / R02 | Real baseline strategies, then incremental ML evidence | R00 and D01, frozen holdout/costs; no inference that synthetic success proves alpha |
| H00 | Sanitized public hosting | Separate privacy/routing/deployment approval; deferred |

E01b, P02 and D01 are **not** an instruction to implement everything in parallel.
Recommended next thread: E01b only; request the selected bounded implementation
plan, not another blanket approval of the roadmap.

## 4. E01b discovery checklist — do not treat missing fields as proof

Read accepted [ADR-0025](../decisions/0025-immutable-research-evidence-and-run-manifests.md),
plan §4 and the E01a outcome, then actual source and tests. At `603fc8d`:

- `backtesting/report.py::write_arena_artifacts_with_evidence` still writes
  legacy slug artifacts, benchmark and flat cards **first**, then adds immutable
  per-pair directories. Old evidence is still overwritable; legacy paths are
  not yet read-only views. E01b must change publication/read routing without
  rewriting historical artifacts or redirecting an old card to a new curve.
- `backtesting/evidence.py` uses `equity-run-manifest/2`. The `run_id` is a
  scope digest, not a unique invocation locator; timestamp/discriminator makes
  directories distinct. Clarify which identifier readers resolve, and preserve
  coexistence of identical scopes. `SCOPE_FIELD_ORDER` is documentation-only
  while `scope_payload()` is built explicitly; prevent drift with a named test.
- Actual costs are recorded on `ArenaRun.applied_costs`. Do not replace them
  with current defaults. Dated evidence is skipped/reported when dates or costs
  are unrecorded; this must not become a false successful latest projection.
- The arena writer currently builds manifest universe from `result.tickers`,
  re-instantiates registry defaults for parameters, and calls `finalise_run`
  without input snapshots/config digest. Helpers accept snapshots, but their
  existence is **not** proof of read-time binding on the production path.
  Audit canonical identity, actual parameters and read-seam binding; keep
  provenance `unknown` while evidence is absent. If this needs a distinct
  bounded write-side follow-up, identify it before reader work rather than
  certifying full ADR compliance from green tests.
- The normal arena path does not pass cards into each per-pair finalizer. Flat
  comparison cards and the shared benchmark still need coherent immutable
  evidence references. Decide their grouping in the approved E01b plan; do not
  claim a comparison card belongs to one pair without its other backing runs.
- Per-invocation durable failure/partial/ineligible accounting is still owed.
  The CLI's zero-completion guard skips the artifact writer; last-attempt
  visibility must therefore cover failed/empty invocations as well as successful
  writer calls. New attempt state needs a Pydantic write boundary and tests.
- `findings/writer.py::load_finding_cards`, `api/deps.py::list_backtests`, API
  routers, notebook/static consumers and `equity report backtest` need a reader/
  producer map. Preserve default response shapes and old undated status.
- Legacy JSON still uses permissive serialization and the engine's NaN
  open-position sentinel. New dated JSON is strict: metrics use explicit null
  plus availability; defective trade/card numbers refuse, with a narrow open-
  position sentinel conversion. Do not blanket-convert NaN to zero or rewrite
  historical files. W&B is explicitly **not** an E01b implementation target.

These are source-observed limitations and review obligations, not newly approved
policy changes or independently reproduced defects in this docs-only update.
An accepted write-side substage does not certify all research provenance.

### E01b acceptance focus for the proposed plan

- Two invocations coexist; scope-specific latest-success is selected only from
  validated intact evidence. Missing/corrupt/skipped/failed writes cannot advance
  it; previous comparable success and its age remain visible.
- Last attempt and last success remain separate, including partial and zero-
  completion invocations; successful pairs' evidence is not deleted.
- Old cards still resolve to their own historical evidence; new cards use
  immutable references; legacy files are not migrated, relabeled or overwritten.
- Existing `/findings` and `/backtests` shapes and offline notebook/static read
  paths are pinned by compatibility tests. Unknown provenance stays unknown.
- Boundary changes require an accepted ADR before implementation; identify any
  conflict in the frozen wording and ask rather than silently amend a decision.

## 5. Verification evidence and count correction

The E01a implementation gates were captured before `603fc8d`; they are
**historical evidence, not fresh tests run for this handoff**. Existing logs and
JUnit summaries were re-read on 2026-11-06:

| Frozen stage | Serial passed / skipped | Parallel passed / skipped |
| --- | --- | --- |
| F01–F04 | 1876 / 17 | 1876 / 17 |
| P01a | 1994 / 17 | 1994 / 17 |
| P01b | 2023 / 17 | 2023 / 17 |
| E01a | **2065 / 17** | **2065 / 17** |

All have zero failures/errors. Earlier summaries/commit messages incorrectly
called JUnit `tests` (which includes skips) the passed count: respectively 1893,
2011, 2040 and 2082 total cases. This refresh corrects the tracked ledger and
handoff; commits are not rewritten. Original ADR-0023 historical counts are not
re-audited here.

E01a logs: `/tmp/e01a-{serial,parallel}.{log,xml}` (421.3 s / 723.2 s), focused
42 evidence tests + 143 regressions, ruff check/format, mypy (180 files),
unchanged generated catalog, Markdown lint and diff checks. Local logs and
ignored checkpoints are conveniences, not prerequisites in another clone.
No Python suite or catalog generation is needed for this handoff-only edit.

## 6. Worker/reviewer and recovery contract

- One bounded writing worker at a time, explicit allowed paths, complete frozen
  contract/test matrix. Separate adversarial reviewer invocation/context per
  dirty stage diff; worker results cannot approve themselves. No worker commits.
- Inspect the actual installed presets and model availability. Last checked:
  `worker` and owner-selected `scout` use `opencode-go/deepseek-v4.1-flash`;
  `reviewer` preserves dirty work and performs read-only review. Do not change
  global presets, credentials or providers as a side effect.
- Current presets require agent-written progress files **outside the repository**.
  If so, supply a private `/tmp/...` checkpoint path, then have the coordinator
  persist a sanitized summary in ignored `.agent-progress/portfolio-research/`.
  Never override a read-only/preset restriction to force a checkpoint write.
- Initial checkpoint before extensive work; update after milestones and before
  long commands: status, scope, files read/changed, last completed step, exact
  command/exit, evidence path, blocker and next action. On abort, no report is
  no evidence: mark interrupted, inspect only session-owned children and retry
  only the bounded missing deliverable.
- Serialize **all** pytest and mutation activity. Coordinator mutations back up
  the current dirty file, prove a named test fails, restore and `diff -q` before
  any other worker runs. A mutation with no failure is not proof.
- Follow `AGENTS.md` and plan §9 gates; capture serial and parallel output and
  JUnit separately, run sequentially, subtract skipped/failed/error cases when
  reporting passes. Use LSP diagnostics for changed paths. Reuse captured
  unchanged-suite summaries, not repeated full runs. Coordinator commits only
  with permission and explicit file staging, never runtime/private data.

## 7. Paste into the next thread

```text
Pick up docs/handoffs/20261106-01-research-readers-and-portfolio-pickup.md.
Read its current queue, the linked implementation ledger and accepted ADR-0025.
Verify HEAD/status; preserve any dirty work. B00, F01-F04, P01a/P01b and E01a
are already committed; do not restart them. Next is E01b, not full E01 completion.
Start with bounded read-only discovery of producers/readers, attempt tracking,
canonical identity/actual parameters and snapshot binding. Present the minimal
E01b plan and any prerequisite gaps for approval before editing code.
Then use one writing worker and a separate adversarial reviewer per stage,
progress checkpoints compatible with preset restrictions, coordinator mutation
proofs and serialized tests. Keep W&B out of scope and preserve legacy evidence.
Do not access private accounts, call providers/LLMs, ingest real data, refit,
migrate, deploy or widen freshness without their separate authorization.
```
