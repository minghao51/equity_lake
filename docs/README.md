# Documentation Index

This directory is organized by audience first, then by topic.

The active guides in `getting-started/`, `user-guide/`, and
`developer/architecture/` describe the code that currently ships in this
repository. Decision records live in `decisions/`; superseded and deprecated
material lives only in `archive/`.

When guidance conflicts, follow the order defined in `AGENTS.md`: enforced
contracts first, then accepted decision records in `decisions/`, then these
architecture pages and guides.

## Current status — 2026-11-23

The [residual specification and implementation plan](plans/20261122-01-residual-spec-and-implementation-plan.md)
§11 is the record. Its stages were implemented locally and merged one at a time, each merge re-verified in the
main checkout with targeted tests, ruff, mypy and the recursive Markdown ratchet: the disposable bootstrap, the
single manual H00 publisher, verified local backup export, source-clock qualification and real-root guards, the four
non-US offline market packs, the paper-shadow delivery loop, the recursive lint ratchet, the `total_return` refusal,
the R02 archival recomputation, and R00's frozen controls and scale measurements.

**Nothing is `operated`.** No publication, no live delivery, no real-ledger rehearsal, no data acquisition and no
model promotion happened. What remains needs an operator: H00 publication (platform reviewer-protection evidence, an
owners approval and a route ADR), a real-ledger rehearsal, a live delivery endpoint and scheduler host, an off-host
backup decision, a per-market source/licence packet plus an ADR-0030 amendment, and the H00 deployment receipt and
rollback bundles that the hosting guide documents as not implemented. H00 remains unpublished and R02 remains negative
with `promoted=false`.

The earlier claim that only an approval remained was too broad: the Pages workflow ran a cron-sync check after
removing its cron, an independent catalog deployment could replace the same site, and R00's protocol controls did not
establish measured controls or scale evidence. Those are now fixed or measured rather than reclassified.

The [prior specification](plans/20261102-01-portfolio-and-research-implementation-spec.md)
and [gap accounting record](handoffs/20261121-01-gap-accounting.md) preserve the delivered
scope and historical evidence.

## Continuation record — 2026-11-07 (historical)

**E01-6 (row-bound price origin) is implemented** in three sequential commits under
accepted ADR-0027 and decisions D1–D8: reader-first protocol `equity-run-manifest/4`
support, the nullable write-boundary envelope, and read-seam aggregation with derived
`data_provenance`. Every shipped path that carries no row evidence still reports
`unknown`, which remains the correct answer for legacy lakes and real-source adapters.
The pickup that produced this work is the
[E01-6 implementation handoff](handoffs/20261107-03-e01-6-implementation-pickup.md), now
spent, and the ledger is the
[implementation specification](plans/20261102-01-portfolio-and-research-implementation-spec.md)
(§10 records the three stage entries, reviewer verdicts and gate counts). The remote is
at `231d603`; all E01-6 and P03 stages are committed on local `main` and **not yet
pushed**. ADR-0024/0025/0026/0027 are accepted. Pick up remaining work from the
[next-agent handoff](handoffs/20261120-01-complete-remaining-stages.md). **P03 (private console) is implemented** in four reviewed stages (Portfolio, Data
Readiness, Research Runs, Signals; see the [private console guide](user-guide/20261107-private-console.md)),
with one configured private path and display-only guarantees. **P03's two follow-ups are closed** (populated-ledger no-write proof over a real ledger, tracked per-instrument coverage on Data Readiness) and recorded in the same §10 ledger. **D01 moved on 2026-11-14:** the owner recorded the source (`us_equity`/yfinance), free-tier spend, local-only/no-redistribution and `data/lake/` as the target root; a read-only probe then established reachability, complete 2024 coverage for a five-ticker sample against the repository's own calendar, and null action clocks — and falsified D00's "explicitly unadjusted" assumption, because the vendor's OHLC and volume are split-back-adjusted. The owner accepted [ADR-0028](decisions/0028-d01-real-source-slice.md) on 2026-11-14 and selected landing shape (a) reconstruct-raw-at-ingest — [ADR-0029](decisions/0029-split-treatment-ledger-and-raw-price-basis.md), also the P00-2 ledger amendment, which he then **accepted** — and chose a **second lake root** `data/lake_real/` with the same medallion layout instead of mixing real rows into the demo lake, specified in [ADR-0030](decisions/0030-second-real-source-lake-root.md) and **accepted 2026-11-15**. Both are implemented: the second root (`resolve_lake_root`, `--lake-dir`, alias/containment/inside-canonical/scope guards, all falsified by mutation) and the ledger split contract (`SPLIT` ratio in `quantity`, exact-reciprocal replacement inside the reversal's own account, pre-split-mark refusal). Round 1 returned `NEEDS-REVISION` on five findings, round 2 on four more, and **round 3 returned `PASS` on 2026-11-15** (independent reviewer, mutation-proofed); the owner **accepted both stages on 2026-11-15** — see the [D01 probe record](plans/20261114-01-d01-source-probe.md) and spec §10. One post-acceptance defect is recorded there: the store's v1 SQLite `CHECK` still rejects `SPLIT`, so *persisting* a split was closed on 2026-11-16 by [ADR-0031](decisions/0031-p00-4-ledger-backup-restore-migration-compaction.md) (P00-4, Accepted: schema v2 with `SPLIT` in the `CHECK`, verified backup, `equity portfolio migrate`; restore command and compaction still unimplemented). **The first D01 landing ran on 2026-11-17**
(owner authority, `us_equity` only: AAPL, MSFT, NVDA, JPM, KO over 2024-01-02..2024-12-31):
ADR-0029 **Part I** now reconstructs raw prices at the real-root write boundary and fails
closed on any unproven split action, `equity backfill --tickers` scopes a landing, and the
slice landed as 1,260 rows / 252 sessions under `data/lake_real/` with `data/lake/` and
`data/catalog.jsonl` byte-identical. Two defects were raised and **not** fixed, each needing
its own decision: `data ingest` cannot resolve yfinance identity (no `TickerConfig`), and
`equity monitor` still checks five markets plus a Gold store, so a `us_equity`-only real root
reports FAIL. **ADR-0032 is implemented (2026-11-19, decisions 1-9, no runs)** and **R00 is implemented** as
offline controls (`backtesting/protocol.py`: freeze universe/window/holdout/costs, refuse an
absent or changed assumption, dated manifest with digests; no ingestion, network or lake
write). Work order from here: the first authorized real-root feature run (its own scoped
authorization under ADR-0030 d10) → R01/R02 → H00 build. **D01 widening ran on 2026-11-20**
under the owner's universe (AAPL, MSFT, GOOGL, NVDA, ADBE, BABA, QQQ, SPY, GLD): a new
`equity widen` range path (one request per ticker) landed 49,784 rows for the eight covered
tickers, and ADR-0033 proved GLD's empty action scope (5,535 rows). The real root now holds
11 tickers / 58,653 rows, 0 duplicates; ADBE is capped at 2005 by its last action and BABA
starts 2023-12-20 because the vendor list does. `data/lake/` and the catalog are untouched. The ingest-identity fix, real-root monitor scoping,
P00-4's restore/compaction and the landing widening are **done**. The queue table in the Nov07-01 pickup (§4) is
**superseded**. Private-account/provider access, real ingestion, refits, migrations,
deployment and any further commit remain gated.

## Historical incident follow-up — 2026-10-22

The [durable prediction follow-up status](plans/20261021-prediction-follow-up-status.md) records unchanged I02/I03 implementation/offline-contract acceptance and the completed, owner-authorized I01 exact-file backup/isolated-copy verification and two-row targeted delete. The single Delta operation advanced v14→v15 at 2026-10-22T16:38:23Z and left zero active files; historical Delta files remain. Row origin, source actor, and MIC remain unknown. No further mutation is planned. The [2026-10-20 handoff](plans/20261020-prediction-incident-follow-up-handoff.md) retains its initial pending statuses and proposal instructions as historical; the Oct18 incident report remains the source for historical inspection facts and limits. C00–C10 acceptance remains valid for its tested scope, not production operational readiness.

## Historical incident status recorded 2026-10-18 13:20 UTC

This section preserves the incident status as recorded on Oct18; it is superseded for current I01–I03 status by the [follow-up update](plans/20261021-prediction-follow-up-status.md), updated 2026-10-22.

C00–C10 implementation/offline-contract review remains accepted; this is not
live operational readiness. A separately authorized read-only investigation
of the default Platinum predictions table is complete. Two fixture-marked
AAPL/MSFT rows are confirmed at the earliest retained Delta version, but their
origin and any pre-v0 state are unknown. Three follow-ups remained open at that
report date: fixture-row origin/remediation; raw predictions API reader-boundary
validity, identity, and provenance; and a source-only generic Delta-maintenance
path-containment risk requiring contract review (full-path support may be
intentional; no probe or production use was observed). No remediation was
authorized or performed then. See [the dated investigation report](finding/20261018-prediction-test-write-investigation.md)
and [the findings register](finding/20261016-repository-review-and-equity-alerts.md).
Historical C10 test results remain valid for their tested scope; they were not
rerun for this update and are not production-data-integrity proof.

## Start Here

- Current residual work: [specification and implementation plan — 2026-11-22](plans/20261122-01-residual-spec-and-implementation-plan.md); delivered-scope evidence: [gap accounting](handoffs/20261121-01-gap-accounting.md) and the [prior implementation specification](plans/20261102-01-portfolio-and-research-implementation-spec.md) §2 queue / §10 ledger — portfolio accounting, row-bound price-origin provenance, the real-source slice and the R01/R02 research evidence are all delivered; gate records: [ADR-0024 private portfolio ledger](decisions/0024-private-portfolio-ledger-and-accounting.md), [ADR-0025 research evidence](decisions/0025-immutable-research-evidence-and-run-manifests.md), [ADR-0026 portfolio projections](decisions/0026-portfolio-projection-precision-marks-and-reconciliation.md), [ADR-0027 price-origin provenance](decisions/0027-row-bound-price-source-provenance.md), [ADR-0032 adjusted-read coverage basis](decisions/0032-adjusted-read-basis-for-vendor-sourced-slices.md), [ADR-0033 proven-empty action scopes](decisions/0033-proven-empty-corporate-action-scopes.md) and [ADR-0034 vendor-stamped ML research](decisions/0034-vendor-stamped-ml-research-with-disclosure.md)
- New users: [Getting Started](getting-started/quickstart.md)
- Operators: [Pipeline Usage](user-guide/pipeline.md) — commands, config, scheduling
- Ingestion details: [Ingestion](user-guide/ingestion.md) — sources, destinations, retries, and recovery
- Feature generation: [Features and Enrichments](user-guide/20260829-features-and-enrichments.md)
- Health and alerts: [Monitoring and Alerting](user-guide/20260829-monitoring-and-alerting.md)
- Data quality: [Data Quality Validation](user-guide/20260829-data-quality-validation.md) and [Delta Lake Maintenance](user-guide/20260829-delta-maintenance.md)
- Environment and credentials: [API Keys And Credentials](20260406-api-keys.md)
- CLI operators: [CLI Reference](user-guide/20260406-cli-reference.md)
- Private tracking: [Canonical watchlists, readiness, events, and delivery](user-guide/20261016-private-tracking-preview.md) — implementation/offline-contract review accepted; loopback-only API/UI and disabled-by-default delivery
- Private console: [Loopback-only P03 console](user-guide/20261107-private-console.md) — Portfolio, Data Readiness, Research Runs and Signals; display-only, one configured private path, never exported
- Private portfolio: [Canonical CSV import, projections and reconciliation](user-guide/20261103-private-portfolio-ledger.md) — `equity portfolio import|positions|summary|reconcile`: canonical CSV, idempotent atomic commits, dry-run that writes nothing, stored book-mark valuation with exact-venue staleness, and statement reconciliation; verified manual backup, migration, restore and compaction are implemented; no automatic mark producer or remote backup
- Forecast provenance: [ML availability and provenance](user-guide/20261016-ml-availability-and-provenance.md) — point-in-time proof, refit cutoffs, and remaining source-availability gates
- Read API: [Private/local API operation](user-guide/20260811-read-api.md) — exposure consent and limits
- Static hosting: [Dashboard Hosting](user-guide/20260406-dashboard-hosting.md)
- Contributors: [Project Structure](developer-guide/project-structure.md)
- Architecture: [Data Flow](developer/architecture/data-flow.md) and [Pipeline Contracts](developer/architecture/pipeline-contracts.md)
- Repository review: [Usability, Equity Tracking, and Actionable Alerts](finding/20261016-repository-review-and-equity-alerts.md) — prioritized findings and recommended next steps
- Historical incident report: [Prediction-test write investigation](finding/20261018-prediction-test-write-investigation.md) — Oct18 bounded findings and methods, with the current status supplement linked there
- Historical implementation closure and superseded starter: [Luna `xhigh` ledger](plans/20261016-equity-alerts-remaining-scope-handoff.md) — C00–C10 evidence, independent reviews, and historical operational/safety gates
- Pipeline audit: [DAG Pipeline Findings](finding/20261006-dag-pipeline-audit.md) and [Agent Remediation Handoffs](plans/20261006-dag-pipeline-00-handoff-index.md)
- Strategy users: [Backtesting Guide](user-guide/backtesting.md) and [Arena and Findings](user-guide/20260829-arena-and-findings.md)

## Sections

- [getting-started/](getting-started/) for installation and first-run setup
- [user-guide/](user-guide/) for day-to-day usage, signals, and backtesting
- [developer-guide/](developer-guide/) for package layout ([Project Structure](developer-guide/project-structure.md)) and developer tools ([Developer Tools](developer-guide/devtools.md))
- [developer/architecture/](developer/architecture/) for system design and subsystem docs
- [decisions/](decisions/) for architecture decision records (ADRs)
- [reports/](reports/) for current analysis and operational writeups
- [handoffs/](handoffs/) for cross-thread records; the [20261121 gap accounting](handoffs/20261121-01-gap-accounting.md) preserves prior outcomes, while the [20261122 residual plan](plans/20261122-01-residual-spec-and-implementation-plan.md) is the current work queue. Earlier pickup instructions are historical, not pending implementation.
- [finding/](finding/) for engineering audit evidence and resolution registers; these are not runtime `data/findings/` artifacts
- [DAG pipeline remediation](plans/20261006-dag-pipeline-00-handoff-index.md) for the ordered, approval-gated implementation handoffs
- [archive/](archive/) for superseded implementation notes and spent scripts
- [Current residual implementation plan](plans/20261122-01-residual-spec-and-implementation-plan.md) for the ordered follow-up queue; [prior product specification](plans/20261102-01-portfolio-and-research-implementation-spec.md) for delivered-scope contracts and stage evidence
- [Portfolio showcase roadmap](plans/20260804-portfolio-roadmap.md) for the earlier career-portfolio/showcase context; the current product plan above governs this new work (the archived `technical_roadmap.md` and its coverage map remain superseded — see `archive/`)

## Notes

- ADR policy acceptance is distinct from implementation acceptance. C00–C10 implementation/offline-contract review is accepted; live operational readiness and production changes remain separately gated. The prediction-write investigation was authorized and completed read-only on 2026-10-18; the later I01 inspection, verified backup, and owner-authorized targeted cleanup are recorded in the [follow-up status](plans/20261021-prediction-follow-up-status.md), updated 2026-10-22. Row origin remains unknown; no further cleanup is planned.
- `examples/` is reserved for runnable sample code only.
- New documentation should be added only to the active audience-based folders
  above.
- Boundary and architecture changes require an ADR in `decisions/` before
  implementation.
