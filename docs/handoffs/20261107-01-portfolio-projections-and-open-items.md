# Next Thread — Portfolio Projections and Open Items

**Recorded:** 2026-11-07 UTC
**Status:** Current pickup. E01b.1, E01b.2 and E01c are implemented, independently
reviewed and gate-clean, but **uncommitted**. Preserve the working tree; do not
re-run or rebuild the completed stages.
**Checked baseline:** `603fc8d` on `main` plus the uncommitted stage inventory in
§2. Inspect actual `git status` at pickup (expect 24 modified + 8 untracked
including this file) and preserve every path that is not yours to change.
**Plan and outcomes:** [Implementation specification / stage ledger](../plans/20261102-01-portfolio-and-research-implementation-spec.md).
**Evidence:** [A01–A11 audit](../finding/20261102-product-readiness-audit.md),
[D00 source-feasibility packet](../plans/20261103-01-d00-offline-source-feasibility.md).
**Earlier pickups:** [Nov06 handoff](20261106-01-research-readers-and-portfolio-pickup.md)
is the **spent** E01b specification (retained historically); the
[Nov02 pickup](20261102-01-portfolio-and-research-pickup.md) still holds the
original approval and worker protocol.

## 1. What is complete

| Stage | Delivered | Commit / record |
| --- | --- | --- |
| B00 / ADR-0023 | Carried dirty containment preserved, committed separately | `1b380af` |
| F01–F04 | Fixed-share benchmark, momentum/tie fix, honest signed cost card, truthful arena exit/failure policy | `df4e423` |
| P00 / E00 / D00 | Accounting/privacy + immutable research-evidence ADRs; offline source-feasibility packet | ADR-0024/0025 accepted; D00 reviewed |
| P01a / P01b | Private SQLite ledger core with exact Decimal events, idempotent CSV import, no-write preview; `equity portfolio import` | `0f6d771`, `aeabe4b` |
| E01a | Dated evidence, immutable per-pair run writer, manifests/checksums/validation | `603fc8d` |
| E01b.1 | Attempt records (cards + benchmark per invocation), last-attempt vs latest-success readers, arena legacy-write removal | **Uncommitted** |
| E01b.2 | `equity report backtest` migrated to run + attempt evidence (last legacy writer removed) | **Uncommitted** |
| E01c | Protocol v3, canonical-universe basis, read-seam snapshots (Delta version / non-Delta content digest) | **Uncommitted** |

E01 is **not** complete: [E01-6](#6-e01-6-open-item) (a persisted price-source
marker) is the remaining blocker, and `provenance` stays `unknown` until it lands.
That is deliberate, not an oversight — see §6 before "fixing" it.

## 2. Uncommitted change inventory — verify, then build on it

24 modified paths and 7 untracked (including this handoff). Grouped:

- **New source/tests:** `backtesting/attempts.py`, `backtesting/evidence_view.py`,
  `tests/unit/test_{evidence_attempts,evidence_readers,evidence_snapshots,report_backtest}.py`.
- **Modified source:** `backtesting/{evidence,report,arena,engine,data_loader}.py`,
  `findings/writer.py`, `api/deps.py`, `cli/commands/arena.py`,
  `storage/delta.py`, `ingestion/corporate_actions.py`.
- **Modified tests:** `test_{api,arena,arena_failure_policy,cli_unified,evidence_runs}.py`.
- **Docs:** this handoff, the spent Nov06 handoff, `docs/README.md` (status line +
  handoff index), the ledger, `ARCHITECTURE.md`, and four user guides
  (`20260406-cli-reference`, `20260811-read-api`, `20260829-arena-and-findings`,
  `backtesting`).
- **Notebook:** `notebooks/11-strategy-lab.ipynb` (card reload import; outputs
  unchanged).

**Invariants now true — do not "restore" the old behaviour:**

- No command writes the legacy `<strategy>__<regime>/`, flat
  `benchmark__equity.parquet` or flat `<card-id>.json`. Legacy files are a frozen,
  readable view; nothing is migrated, relabelled or deleted.
- `equity arena run` publishes one immutable `runs/<ts>-<hash>/` per requested
  completed pair plus one `attempts/<ts>-<hash>/` owning the three comparison cards
  and the benchmark copy.
- `equity report backtest` publishes the same two artefacts; undated results are
  refused (exit 1, nothing written).
- `RUN_PROTOCOL = "equity-run-manifest/3"`; `RunManifest.universe_basis` is
  required (`canonical | ticker_only`) and is **not** in the run-ID preimage.
- Snapshot binding is taken at the read seam: Delta `(path, version)` from the
  table instance that produced the frame; non-Delta one aggregate content digest
  over the sorted scanned file set. `read_delta` never requires `version()`.
- `data_provenance` is `unknown` on every shipped path, including when snapshots
  exist.
- `/findings` and `/backtests` keep their response shapes; `/backtests` rows gain
  additive `last_success` / `last_attempt`. Card `evidence_refs` on new arena cards
  use `run:` / `attempt:` / `unevidenced:` and are **display-only** in E01 (no
  resolver).

## 3. Verified state of the uncommitted stage (historical evidence)

Captured before this handoff; not fresh runs for reading it.

| Gate | Result |
| --- | --- |
| Serial suite | 2155 passed / 0 failed / 17 skipped / 97 deselected (377.3 s) |
| Parallel suite (`-n auto`, run sequentially) | 2155 passed / 0 failed / 17 skipped (411.1 s) |
| ruff check / format, mypy | clean (182 source files at E01c) |
| Catalog regeneration | byte-identical |
| `make lint-md`, `git diff --check` | clean |
| Docs links | 99 relative links/anchors across the changed documents resolve |

Frozen stage history for comparison: E01a 2065 passed/17 skipped; E01b.1 2119;
E01b.2 2134; E01c 2155 (each with 17 skips and zero failures). Coordinator
mutation proofs for each stage are in the ledger. Local logs are under
`/tmp/e01{b,1,2,c}-*`; they are conveniences, not prerequisites in another clone.

**Process lesson worth keeping.** The E01c focused set was too narrow: the full
serial gate caught `read_delta` demanding `dt.version()` from a legitimate
versionless test double. Widen the focused set to include dependants
(`test_feature_jobs.py`, `test_tracking_readiness.py`, loader/adjustment tests),
and treat a full-gate failure found after review as a defect to fix, not a flake
to rerun.

## 4. Remaining queue and entry gates

| Order / stage | Next deliverable | Entry gate / stopping rule |
| --- | --- | --- |
| **1 — P02** | Deterministic positions/cash/basis/P&L, stored marks, reconciliation; the three frozen CLI commands | Approve a bounded synthetic-fixture plan under ADR-0024 / spec §5.3; freeze precision, ordering, mark staleness and statement tolerances **before** code. No provider dependency, no real account |
| 2 — E01-6 | Persisted price-source marker enabling an honest `synthetic`/`real` provenance | Needs a new ADR plus a storage/schema decision and a backfill answer; do not infer provenance from paths, identity columns or coverage `source` |
| 3 — D01 | Licensing/retention evidence and a scoped US raw-data slice | Separately gated: explicit authorization for network/source use, target root and spend. Never relax adjusted/PIT proof |
| P03 | Loopback-only private Streamlit console | P02 plus an explicit UI plan; the research panel also waits on E01 |
| R00 | Synthetic controls and compute-scale protocol | Completed E01 plus an approved hypothesis/timing/seed/metric/budget |
| R01 / R02 | Real baselines, then incremental ML evidence | R00 and D01; frozen holdout/costs; no inference that synthetic success proves alpha |
| H00 | Sanitized public hosting | Separate privacy/routing/deployment approval; deferred |
| P00-2 / P00-4 | Split accounting amendment; backup/migration/compaction | Both still open and **not** authorized |

Recommended next thread: **P02 planning only** — present the bounded fixture-only
plan (data model, oracle, ordering/precision, mark and tolerance rules, CLI
outputs) and stop for approval before code.

## 5. P02 discovery checklist — verify against source at pickup

Existing surface (P01a/P01b, committed):

- `src/equity_lake/portfolio/{models,store,csv_import}.py`: append-only versioned
  `LedgerEvent` with Decimal quantities/money/basis, explicit unknown basis,
  exact `(market, MIC, symbol)` identity, idempotent atomic import, oversell and
  timeline validation, redacted error text.
- `core/paths.py`: `PORTFOLIO_DIR = DATA_DIR / "portfolio"`,
  `PORTFOLIO_DB_PATH = .../portfolio.sqlite3` (auxiliary, not cataloged).
- CLI: only `equity portfolio import` exists. `positions`, `summary` and
  `reconcile` are **frozen names** and must land with the projections they read —
  never as empty stubs (P00-3).

To build (spec §5.3, ADR-0024 decisions 6, 8, 10, 13):

- Deterministic replay → quantity, average-cost basis, cash, realized/unrealized
  P&L, dividend/fee totals, as-of NAV and allocation. Unknown opening basis keeps
  basis-dependent P&L **unavailable**, never zero. Reject unlike-currency FX.
- Stored raw/unadjusted as-of marks with exact identity and venue session, visible
  source/as-of/staleness and `synthetic | unknown` mode; no future mark; no
  network fallback; missing/stale marks withhold complete NAV/P&L/allocation while
  showing cash/quantities and any marked subtotal, naming affected instruments.
- Reconciliation against explicit statement quantities/cash with documented
  precision/tolerances and matched / mismatched / unavailable outcomes; it never
  mutates ledger state to agree.
- The §5.3 average-cost hand oracle is the acceptance test (opening 10 @100 with
  cash 1000 through buy/sell/dividend/mark to NAV 2299). Splits stay blocked
  (P00-2): block affected valuation rather than misstate quantity. Do not claim
  time-weighted or money-weighted returns.

Constraints: fixtures only, no private-account import without separate
authorization; the `portfolio` package must stay independent of
`cli/dashboard/sources/ml/signals` (import-boundary test); Pydantic is the write
boundary; no new catalog rows.

## 6. E01-6 open item

A persisted **price-source** marker is the only honest route to a `synthetic`,
`real` or `mixed` provenance. Today bronze carries identity columns only; the
action-coverage `source` column is action provenance and the `data/sample` path is
a path, so any derivation from them would violate ADR-0021/0022 and ADR-0025
Decision 3. E01-6 therefore needs: an ADR (what the marker is, who writes it,
what it must never claim), a storage/schema decision, a backfill answer for
existing lakes, and tests proving a run without a marker stays `unknown`. Until
then, treat `provenance: unknown` as correct output, not a bug.

## 7. D01 (separately gated — no work without explicit authorization)

Candidate is the `us_equity` raw-price, explicitly-unadjusted slice, subject to a
licensing/retention check. No provider call, ingestion, target-root change or
spend follows from this handoff. CN adjusted/action-dependent use remains
evidence-blocked.

## 8. Worker / reviewer and recovery contract

Full protocol: [Nov02 pickup](20261102-01-portfolio-and-research-pickup.md). The
essentials, as exercised through E01b/E01c:

- One bounded writing worker at a time with explicit allowed paths; no worker
  commits. A **separate** adversarial reviewer invocation per dirty stage diff;
  workers cannot approve themselves.
- Presets (verify they are still installed): `worker` = deepseek-v4.1-flash,
  `reviewer` = gpt-6-luna (read-only tools). Do not change presets or credentials.
- Checkpoints live outside the repo (`/tmp/<stage>-worker-checkpoint.md`); the
  coordinator keeps a sanitized summary in ignored
  `.agent-progress/portfolio-research/`. Never stage either.
- Serialize all pytest/mutation activity. Coordinator mutations: back up the dirty
  file, prove a named test fails, restore, `diff -q` identical. A mutation that
  does not fail a test is not proof.
- Reviewer verdicts must start with exactly `PASS`, `NEEDS-REVISION` or `BLOCKED`.
  Twice in E01b/E01c a reviewer returned prose without a verdict line; re-request
  it with read-only verification **allowed** (forbidding commands yields
  `BLOCKED — no verification context`, not a verdict).
- Final gates: focused (widened to dependants), then serial, then parallel, plus
  ruff check/format, mypy, catalog regeneration, `make lint-md` and
  `git diff --check`. Reuse captured logs for unchanged suites; never report JUnit
  totals (which include skips) as passes.

## 9. Explicitly not authorized

No commit (unless the owner authorizes it with explicit file staging), no private
account or broker access, no provider/source/LLM calls, no real ingestion, no
training/refit, no ledger migration/backfill/reset, no deployment, no freshness
widening, no W&B change. Do not rewrite the owner's `docs/README.md` prose beyond
the status line, and do not touch `data/` or the four frozen `equity-run-manifest/2`
references that describe commit `603fc8d` historically.

## 10. Paste into the next thread

```text
Pick up docs/handoffs/20261107-01-portfolio-projections-and-open-items.md.
Read §1-§3 and the linked ledger; verify `git status` and preserve the uncommitted
E01b.1/E01b.2/E01c stage (24 modified + 7 untracked). B00, F01-F04, P01a/P01b,
E01a, E01b.1, E01b.2 and E01c are done; do not restart or rebuild them.
Next is P02 planning: present a bounded fixture-only plan for positions, cash,
average-cost basis/P&L, stored marks and reconciliation plus the three frozen CLI
commands (positions, summary, reconcile), grounded in ADR-0024 and spec §5.3, and
stop for approval before editing code. Freeze precision/ordering, mark staleness
and statement tolerances up front.
Use one writing worker, a separate adversarial reviewer per stage with a
PASS/NEEDS-REVISION/BLOCKED verdict line, checkpoints outside the repo, coordinator
mutation proofs and serialized tests. E01-6 needs its own ADR; D01 stays gated.
Do not commit, access private accounts, call providers/LLMs, ingest real data,
refit, migrate, redeploy or widen freshness without separate authorization.
```
