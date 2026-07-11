# Documentation Index

This directory is organized by audience first, then by topic.

The active guides in `getting-started/`, `user-guide/`, and
`developer/architecture/` describe the code that currently ships in this
repository. Historical plans, superseded implementation notes, and older design
drafts belong in `developer/history/`.

## Start Here

- New users: [Getting Started](getting-started/quickstart.md)
- Operators: [Pipeline Usage](user-guide/pipeline.md) — commands, config, scheduling
- Ingestion details: [Ingestion](user-guide/ingestion.md) — sources, destinations, retries, and recovery
- Environment and credentials: [API Keys And Credentials](20260406-api-keys.md)
- CLI operators: [CLI Reference](user-guide/20260406-cli-reference.md)
- Static hosting: [Dashboard Hosting](user-guide/20260406-dashboard-hosting.md)
- Contributors: [Project Structure](developer-guide/project-structure.md)
- Architecture: [Data Flow](developer/architecture/data-flow.md) and [Pipeline Contracts](developer/architecture/pipeline-contracts.md)
- Strategy users: [Backtesting Guide](user-guide/backtesting.md)

## Sections

- [getting-started/](getting-started/) for installation and first-run setup
- [user-guide/](user-guide/) for day-to-day usage, signals, and backtesting
- [developer-guide/](developer-guide/) for package layout and contribution workflow
- [developer/architecture/](developer/architecture/) for system design and subsystem docs
- [developer/history/](developer/history/) for superseded implementation notes and decision records
- [reports/](reports/) for current analysis and operational writeups
- [technical_roadmap.md](technical_roadmap.md) for phased enhancement plan

## Notes

- `examples/` is reserved for runnable sample code only.
- New documentation should be added only to the active audience-based folders
  above.
