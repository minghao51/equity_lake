# Documentation Index

This directory is organized by audience first, then by topic.

The active guides in `getting-started/`, `user-guide/`, and
`developer/architecture/` describe the code that currently ships in this
repository. Decision records live in `decisions/`; superseded and deprecated
material lives only in `archive/`.

When guidance conflicts, follow the order defined in `AGENTS.md`: enforced
contracts first, then accepted decision records in `decisions/`, then these
architecture pages and guides.

## Start Here

- New users: [Getting Started](getting-started/quickstart.md)
- Operators: [Pipeline Usage](user-guide/pipeline.md) — commands, config, scheduling
- Ingestion details: [Ingestion](user-guide/ingestion.md) — sources, destinations, retries, and recovery
- Feature generation: [Features and Enrichments](user-guide/20260829-features-and-enrichments.md)
- Health and alerts: [Monitoring and Alerting](user-guide/20260829-monitoring-and-alerting.md)
- Data quality: [Data Quality Validation](user-guide/20260829-data-quality-validation.md) and [Delta Lake Maintenance](user-guide/20260829-delta-maintenance.md)
- Environment and credentials: [API Keys And Credentials](20260406-api-keys.md)
- CLI operators: [CLI Reference](user-guide/20260406-cli-reference.md)
- Static hosting: [Dashboard Hosting](user-guide/20260406-dashboard-hosting.md)
- Contributors: [Project Structure](developer-guide/project-structure.md)
- Architecture: [Data Flow](developer/architecture/data-flow.md) and [Pipeline Contracts](developer/architecture/pipeline-contracts.md)
- Strategy users: [Backtesting Guide](user-guide/backtesting.md) and [Arena and Findings](user-guide/20260829-arena-and-findings.md)

## Sections

- [getting-started/](getting-started/) for installation and first-run setup
- [user-guide/](user-guide/) for day-to-day usage, signals, and backtesting
- [developer-guide/](developer-guide/) for package layout ([Project Structure](developer-guide/project-structure.md)) and developer tools ([Developer Tools](developer-guide/devtools.md))
- [developer/architecture/](developer/architecture/) for system design and subsystem docs
- [decisions/](decisions/) for architecture decision records (ADRs)
- [reports/](reports/) for current analysis and operational writeups
- [archive/](archive/) for superseded implementation notes and spent scripts
- [plans/20260804-portfolio-roadmap.md](plans/20260804-portfolio-roadmap.md) for the
  active roadmap (the archived `technical_roadmap.md` and its coverage map are
  superseded — see `archive/`)

## Notes

- `examples/` is reserved for runnable sample code only.
- New documentation should be added only to the active audience-based folders
  above.
- Boundary and architecture changes require an ADR in `decisions/` before
  implementation.
