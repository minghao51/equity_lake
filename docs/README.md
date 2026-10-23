# Documentation Index

This directory is organized by audience first, then by topic.

The active guides in `getting-started/`, `user-guide/`, and
`developer/architecture/` describe the code that currently ships in this
repository. Decision records live in `decisions/`; superseded and deprecated
material lives only in `archive/`.

When guidance conflicts, follow the order defined in `AGENTS.md`: enforced
contracts first, then accepted decision records in `decisions/`, then these
architecture pages and guides.

## Current continuation — 2026-10-22

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

- New users: [Getting Started](getting-started/quickstart.md)
- Operators: [Pipeline Usage](user-guide/pipeline.md) — commands, config, scheduling
- Ingestion details: [Ingestion](user-guide/ingestion.md) — sources, destinations, retries, and recovery
- Feature generation: [Features and Enrichments](user-guide/20260829-features-and-enrichments.md)
- Health and alerts: [Monitoring and Alerting](user-guide/20260829-monitoring-and-alerting.md)
- Data quality: [Data Quality Validation](user-guide/20260829-data-quality-validation.md) and [Delta Lake Maintenance](user-guide/20260829-delta-maintenance.md)
- Environment and credentials: [API Keys And Credentials](20260406-api-keys.md)
- CLI operators: [CLI Reference](user-guide/20260406-cli-reference.md)
- Private tracking: [Canonical watchlists, readiness, events, and delivery](user-guide/20261016-private-tracking-preview.md) — implementation/offline-contract review accepted; loopback-only API/UI and disabled-by-default delivery
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
- [finding/](finding/) for engineering audit evidence and resolution registers; these are not runtime `data/findings/` artifacts
- [DAG pipeline remediation](plans/20261006-dag-pipeline-00-handoff-index.md) for the ordered, approval-gated implementation handoffs
- [archive/](archive/) for superseded implementation notes and spent scripts
- [plans/20260804-portfolio-roadmap.md](plans/20260804-portfolio-roadmap.md) for the
  active roadmap (the archived `technical_roadmap.md` and its coverage map are
  superseded — see `archive/`)

## Notes

- ADR policy acceptance is distinct from implementation acceptance. C00–C10 implementation/offline-contract review is accepted; live operational readiness and production changes remain separately gated. The prediction-write investigation was authorized and completed read-only on 2026-10-18; the later I01 inspection, verified backup, and owner-authorized targeted cleanup are recorded in the [follow-up status](plans/20261021-prediction-follow-up-status.md), updated 2026-10-22. Row origin remains unknown; no further cleanup is planned.
- `examples/` is reserved for runnable sample code only.
- New documentation should be added only to the active audience-based folders
  above.
- Boundary and architecture changes require an ADR in `decisions/` before
  implementation.
